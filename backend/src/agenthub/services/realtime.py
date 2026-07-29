"""会话事件的进程内实时分发。持久化补发由聊天服务负责。"""

import asyncio
import uuid

from agenthub.schemas.domain import EventEnvelope


class ConversationEventBroker:
    """按会话隔离订阅队列，不保存历史事件。"""

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue[EventEnvelope]]] = {}
        self._guard = asyncio.Lock()

    async def subscribe(self, conversation_id: uuid.UUID) -> asyncio.Queue[EventEnvelope]:
        """先注册队列再执行数据库补发，避免连接建立期间漏掉新事件。"""
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue()
        async with self._guard:
            self._subscribers.setdefault(conversation_id, set()).add(queue)
        return queue

    async def unsubscribe(
        self, conversation_id: uuid.UUID, queue: asyncio.Queue[EventEnvelope]
    ) -> None:
        """移除断开连接，空会话集合同时回收。"""
        async with self._guard:
            subscribers = self._subscribers.get(conversation_id)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(conversation_id, None)

    async def publish(self, event: EventEnvelope) -> None:
        """向当前会话连接广播；调用方必须已提交对应数据库记录。"""
        async with self._guard:
            subscribers = tuple(self._subscribers.get(event.conversation_id, ()))
        for queue in subscribers:
            queue.put_nowait(event)
