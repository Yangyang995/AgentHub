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

from agenthub.schemas.domain import (
    ConversationCreate,
    ConversationResponse,
    MessageResponse,
    MessageSubmissionResponse,
    UserMessageCreate,
)
from agenthub.services.chat import ChatConflictError, ChatNotFoundError, ChatService

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["chat"])
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
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    project_id: uuid.UUID, data: ConversationCreate, service: ChatServiceDependency
) -> ConversationResponse:
    """创建绑定一个已启用 Agent 的单聊会话。"""
    try:
        return await service.create_conversation(project_id, data)
    except (ChatNotFoundError, ChatConflictError) as error:
        _raise_http_error(error)


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    project_id: uuid.UUID, service: ChatServiceDependency
) -> list[ConversationResponse]:
    """列出项目中的会话。"""
    try:
        return await service.list_conversations(project_id)
    except ChatNotFoundError as error:
        _raise_http_error(error)


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    project_id: uuid.UUID, conversation_id: uuid.UUID, service: ChatServiceDependency
) -> ConversationResponse:
    """读取项目内单个会话。"""
    try:
        return await service.get_conversation(project_id, conversation_id)
    except ChatNotFoundError as error:
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
    response_model=MessageSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_message(
    request: Request,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    data: UserMessageCreate,
    service: ChatServiceDependency,
) -> MessageSubmissionResponse:
    """原子保存消息和执行记录，然后在响应后独立消费 Adapter。"""
    try:
        response = await service.submit_message(project_id, conversation_id, data)
    except (ChatNotFoundError, ChatConflictError) as error:
        _raise_http_error(error)
    task = asyncio.create_task(service.run_execution(response.execution.id))
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


@websocket_router.websocket("/ws/conversations/{conversation_id}")
async def conversation_events(
    websocket: WebSocket,
    conversation_id: uuid.UUID,
    project_id: Annotated[uuid.UUID, Query()],
    execution_id: Annotated[uuid.UUID | None, Query()] = None,
    last_sequence: Annotated[int, Query(ge=-1)] = -1,
) -> None:
    """推送实时事件，并按执行游标补发遗漏事件且在连接内按 event_id 去重。"""
    service: ChatService = websocket.app.state.chat_service
    broker = websocket.app.state.event_broker
    queue = await broker.subscribe(conversation_id)
    sent_event_ids: set[uuid.UUID] = set()
    try:
        await service.get_conversation(project_id, conversation_id)
        await websocket.accept()
        if execution_id is not None:
            replay = await service.replay_events(
                project_id, conversation_id, execution_id, last_sequence
            )
            for event in replay:
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
