import asyncio
import sys
import uuid

sys.path.insert(0, "src")

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agenthub.core.config import get_settings
from agenthub.services.chat import ChatService


# Mock broker
class MockBroker:
    async def publish(self, event):
        print(f"  [BROKER] {event.type} status={event.payload.get('status','?')}")

    async def subscribe(self, cid):
        return None
    async def unsubscribe(self, cid, q):
        pass

async def test():
    s = get_settings()
    engine = create_async_engine(s.database_url.get_secret_value())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    from agenthub.main import _default_adapter_resolver
    resolver = _default_adapter_resolver(s)
    broker = MockBroker()
    service = ChatService(factory, broker, resolver)
    
    # Create conversation and submit message like the API does
    from agenthub.models.orm import Project
    from agenthub.schemas.domain import ConversationCreate, UserMessageCreate
    
    async with factory() as session:
        project = await session.get(Project, uuid.UUID("4d7fe4c0-7244-4f3b-ac95-dfcb0032cff3"))
    
    conv_data = ConversationCreate(provider="deepseek")
    conv = await service.create_conversation(project.id, conv_data)
    print(f"Conv: {conv.id}")
    
    msg_data = UserMessageCreate(content="say hello in one word")
    resp = await service.submit_message(project.id, conv.id, msg_data)
    exec_id = resp.execution.id
    print(f"Exec: {exec_id}")
    
    # Now call run_execution directly
    print("\nCalling run_execution...")
    try:
        await service.run_execution(exec_id)
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    
    # Check messages
    msgs = await service.list_messages(project.id, conv.id)
    print(f"\nMessages: {len(msgs)}")
    for m in msgs:
        print(f"  [{m.role}] {m.content[:200]}")

asyncio.run(test())
