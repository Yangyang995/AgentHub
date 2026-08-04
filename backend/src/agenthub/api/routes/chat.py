"""Phase 4 单聊 REST 与 WebSocket 协议路由。"""

import asyncio
import uuid
from typing import Annotated, Never

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from pydantic import BaseModel, Field

from agenthub.core.limits import get_rate_limiter
from agenthub.schemas.domain import (
    ConversationCreate,
    ConversationResponse,
    GroupConversationResponse,
    GroupMessageSubmissionResponse,
    MessageResponse,
    MessageSubmissionResponse,
    UserMessageCreate,
)
from agenthub.services.chat import ChatConflictError, ChatNotFoundError, ChatService

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["chat"])

class PipelineResumeRequest(BaseModel):
    action: str = Field(description="accept | reject | modify")
    feedback: str = Field(default="", description="用户修改意见")


websocket_router = APIRouter(tags=["chat"])


def get_chat_service(request: Request) -> ChatService:
    """从应用状态读取服务，保持路由层无业务构造逻辑。"""
    service: ChatService = request.app.state.chat_service
    return service


ChatServiceDependency = Annotated[ChatService, Depends(get_chat_service)]


def _raise_http_error(error: Exception) -> Never:
    """将领域错误映射为不泄露跨项目资源信息的 HTTP 响应。"""
    if isinstance(error, ChatNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from None
    if isinstance(error, ChatConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    raise error


@router.post(
    "/conversations",
    response_model=ConversationResponse | GroupConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    project_id: uuid.UUID, data: ConversationCreate, service: ChatServiceDependency
) -> ConversationResponse | GroupConversationResponse:
    """创建单聊或群聊会话。"""
    try:
        return await service.create_conversation(project_id, data)
    except (ChatNotFoundError, ChatConflictError) as error:
        _raise_http_error(error)


@router.get("/conversations", response_model=list[ConversationResponse | GroupConversationResponse])
async def list_conversations(
    project_id: uuid.UUID, service: ChatServiceDependency
) -> list[ConversationResponse | GroupConversationResponse]:
    """列出项目中的会话。"""
    try:
        return await service.list_conversations(project_id)
    except ChatNotFoundError as error:
        _raise_http_error(error)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse | GroupConversationResponse,
)
async def get_conversation(
    project_id: uuid.UUID, conversation_id: uuid.UUID, service: ChatServiceDependency
) -> ConversationResponse | GroupConversationResponse:
    """读取项目内单个会话。"""
    try:
        return await service.get_conversation(project_id, conversation_id)
    except ChatNotFoundError as error:
        _raise_http_error(error)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    project_id: uuid.UUID, conversation_id: uuid.UUID, service: ChatServiceDependency
) -> None:
    """删除项目内历史会话，执行中的会话由服务层拒绝。"""
    try:
        await service.delete_conversation(project_id, conversation_id)
    except (ChatNotFoundError, ChatConflictError) as error:
        _raise_http_error(error)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    project_id: uuid.UUID, conversation_id: uuid.UUID, service: ChatServiceDependency
) -> list[MessageResponse]:
    """按稳定顺序读取完整消息。"""
    try:
        return await service.list_messages(project_id, conversation_id)
    except ChatNotFoundError as error:
        _raise_http_error(error)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageSubmissionResponse | GroupMessageSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_message(
    request: Request,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    data: UserMessageCreate,
    service: ChatServiceDependency,
) -> MessageSubmissionResponse | GroupMessageSubmissionResponse:
    """原子保存消息和执行记录，然后在响应后独立消费 Adapter。"""
    try:
        response = await service.submit_message(project_id, conversation_id, data)
    except (ChatNotFoundError, ChatConflictError) as error:
        _raise_http_error(error)
    if isinstance(response, MessageSubmissionResponse):
        task = asyncio.create_task(service.run_execution(response.execution.id))
    elif getattr(response, "pipeline", False):
        task = asyncio.create_task(
            service.run_pipeline([item.id for item in response.executions])
        )
    else:
        task = asyncio.create_task(
            service.run_executions([item.id for item in response.executions])
        )
    tasks: set[asyncio.Task[None]] = request.app.state.execution_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return response


@router.post("/executions/{execution_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_execution(
    project_id: uuid.UUID, execution_id: uuid.UUID, service: ChatServiceDependency
) -> dict[str, object]:
    """取消执行并返回已持久化的最终事件。"""
    try:
        event = await service.cancel_execution(project_id, execution_id)
        return event.model_dump(mode="json")
    except (ChatNotFoundError, ChatConflictError) as error:
        _raise_http_error(error)


@router.post(
    "/conversations/{conversation_id}/pipeline/resume",
    status_code=status.HTTP_200_OK,
)
async def resume_pipeline(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    data: PipelineResumeRequest,
    service: ChatServiceDependency,
) -> dict[str, object]:
    try:
        task = asyncio.create_task(
            service.resume_pipeline(conversation_id, data.action, data.feedback)
        )
        # 将 task 注册到 lifespan 管理
        from fastapi import Request as _Request
        # 简化：不使用 request.app.state，task 自行管理生命周期

        return {"status": "resumed", "action": data.action}
    except (ChatNotFoundError, ChatConflictError) as error:
        _raise_http_error(error)


@websocket_router.websocket("/ws/conversations/{conversation_id}")
async def conversation_events(
    websocket: WebSocket,
    conversation_id: uuid.UUID,
    project_id: Annotated[uuid.UUID, Query()],
    execution_id: Annotated[uuid.UUID | None, Query()] = None,
    last_sequence: Annotated[int, Query(ge=-1)] = -1,
    cursor: Annotated[list[str] | None, Query()] = None,
) -> None:
    """推送实时事件，并按一个或多个执行游标补发遗漏事件。"""
    limiter = get_rate_limiter()
    # Phase 11: 检查 WebSocket 并发连接限制
    try:
        await limiter.track_ws_connect(conversation_id)
    except Exception:
        await websocket.close(code=4429, reason="Too many connections")
        return

    service: ChatService = websocket.app.state.chat_service
    broker = websocket.app.state.event_broker
    queue = await broker.subscribe(conversation_id)
    sent_event_ids: set[uuid.UUID] = set()
    try:
        await service.get_conversation(project_id, conversation_id)
        await websocket.accept()
        replay_cursors: list[tuple[uuid.UUID, int]] = []
        if execution_id is not None:
            replay_cursors.append((execution_id, last_sequence))
        for value in cursor or []:
            try:
                raw_execution_id, raw_sequence = value.rsplit(":", 1)
                replay_cursors.append((uuid.UUID(raw_execution_id), int(raw_sequence)))
            except (ValueError, TypeError):
                await websocket.close(code=4400, reason="Invalid replay cursor")
                return
        for replay_execution_id, replay_sequence in replay_cursors:
            replay = await service.replay_events(
                project_id, conversation_id, replay_execution_id, replay_sequence
            )
            for event in replay:
                if event.event_id in sent_event_ids:
                    continue
                await websocket.send_json(event.model_dump(mode="json"))
                sent_event_ids.add(event.event_id)
        while True:
            # 同时等待事件和客户端帧；仅等待队列会让空闲连接无法及时感知断开。
            event_task = asyncio.create_task(queue.get())
            disconnect_task = asyncio.create_task(websocket.receive())
            done, pending = await asyncio.wait(
                {event_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if disconnect_task in done:
                message = disconnect_task.result()
                if message["type"] == "websocket.disconnect":
                    break
                continue
            event = event_task.result()
            if event.event_id in sent_event_ids:
                continue
            await websocket.send_json(event.model_dump(mode="json"))
            sent_event_ids.add(event.event_id)
    except ChatNotFoundError:
        await websocket.close(code=4404, reason="Resource not found")
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        # ASGI 服务器和 TestClient 均可能通过取消处理连接关闭，这是正常断连语义。
        pass
    finally:
        await broker.unsubscribe(conversation_id, queue)
        # Phase 11: 释放 WebSocket 连接计数
        await limiter.track_ws_disconnect(conversation_id)
