"""PostgreSQL Checkpoint ?? ?? LangGraph Pipeline ??????"""

import asyncio
import json
import uuid
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class PostgresSaver(BaseCheckpointSaver):
    """LangGraph Checkpoint ? PostgreSQL ???

    ?? async_sessionmaker ?????????????? asyncio.run()
    ????????LangGraph ? graph.ainvoke() ??????????????

    ????
    - pipeline_checkpoints: thread_id, checkpoint_ns, checkpoint_id,
      parent_checkpoint_id, checkpoint (JSONB), metadata (JSONB)
    - pipeline_checkpoint_writes: thread_id, checkpoint_ns, checkpoint_id,
      task_id, idx, channel, type, value
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__()
        self._sessions = session_factory

    async def setup(self) -> None:
        """?? checkpoint ????????"""
        async with self._sessions() as session:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS pipeline_checkpoints (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    checkpoint_id TEXT NOT NULL,
                    parent_checkpoint_id TEXT,
                    type TEXT,
                    checkpoint JSONB NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}',
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                )
            """))
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS pipeline_checkpoint_writes (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    checkpoint_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    type TEXT,
                    value BYTEA,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
                )
            """))
            await session.commit()

    # ---- ?????? LangGraph ?????? ----

    def get_tuple(self, config: dict) -> CheckpointTuple | None:
        """????? checkpoint???????"""
        return asyncio.run(self._aget_tuple(config))

    def put(
        self,
        config: dict,
        checkpoint: dict,
        metadata: dict,
        new_versions: dict,
    ) -> dict:
        """?? checkpoint???????"""
        return asyncio.run(self._aput(config, checkpoint, metadata, new_versions))

    def put_writes(
        self,
        config: dict,
        writes: list[tuple[str, Any]],
        task_id: str,
    ) -> None:
        """?? pending writes???????"""
        return asyncio.run(self._aput_writes(config, writes, task_id))

    # ---- ???? ----

    async def _aget_tuple(self, config: dict) -> CheckpointTuple | None:
        thread_id = config.get("configurable", {}).get("thread_id", "")
        checkpoint_ns = config.get("configurable", {}).get("checkpoint_ns", "")
        async with self._sessions() as session:
            result = await session.execute(
                text("""
                    SELECT thread_id, checkpoint_ns, checkpoint_id,
                           parent_checkpoint_id, type, checkpoint, metadata
                    FROM pipeline_checkpoints
                    WHERE thread_id = :thread_id AND checkpoint_ns = :checkpoint_ns
                    ORDER BY checkpoint_id DESC
                    LIMIT 1
                """),
                {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns},
            )
            row = result.fetchone()
            if row is None:
                return None
            return CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.checkpoint_id,
                    }
                },
                checkpoint=row.checkpoint,
                metadata=row.metadata or {},
                parent_config=(
                    {"configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }}
                    if row.parent_checkpoint_id
                    else None
                ),
            )

    async def _aput(
        self,
        config: dict,
        checkpoint: dict,
        metadata: dict,
        new_versions: dict,
    ) -> dict:
        thread_id = config.get("configurable", {}).get("thread_id", "")
        checkpoint_ns = config.get("configurable", {}).get("checkpoint_ns", "")
        parent_checkpoint_id = config.get("configurable", {}).get("checkpoint_id")
        checkpoint_id = str(uuid.uuid4())
        async with self._sessions() as session:
            await session.execute(
                text("""
                    INSERT INTO pipeline_checkpoints
                        (thread_id, checkpoint_ns, checkpoint_id,
                         parent_checkpoint_id, type, checkpoint, metadata)
                    VALUES
                        (:thread_id, :checkpoint_ns, :checkpoint_id,
                         :parent_checkpoint_id, :type, :checkpoint, :metadata)
                    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id) DO UPDATE
                    SET checkpoint = EXCLUDED.checkpoint,
                        metadata = EXCLUDED.metadata,
                        parent_checkpoint_id = EXCLUDED.parent_checkpoint_id
                """),
                {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "parent_checkpoint_id": parent_checkpoint_id,
                    "type": "checkpoint",
                    "checkpoint": json.dumps(checkpoint),
                    "metadata": json.dumps(metadata),
                },
            )
            await session.commit()
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def _aput_writes(
        self,
        config: dict,
        writes: list[tuple[str, Any]],
        task_id: str,
    ) -> None:
        thread_id = config.get("configurable", {}).get("thread_id", "")
        checkpoint_ns = config.get("configurable", {}).get("checkpoint_ns", "")
        checkpoint_id = config.get("configurable", {}).get("checkpoint_id", "")
        async with self._sessions() as session:
            for idx, (channel, value) in enumerate(writes):
                await session.execute(
                    text("""
                        INSERT INTO pipeline_checkpoint_writes
                            (thread_id, checkpoint_ns, checkpoint_id,
                             task_id, idx, channel, type, value)
                        VALUES
                            (:thread_id, :checkpoint_ns, :checkpoint_id,
                             :task_id, :idx, :channel, :type, :value)
                        ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
                        DO NOTHING
                    """),
                    {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": checkpoint_id,
                        "task_id": task_id,
                        "idx": idx,
                        "channel": channel,
                        "type": type(value).__name__ if value is not None else None,
                        "value": (
                        json.dumps(value).encode("utf-8")
                        if not isinstance(value, bytes)
                        else value
                    ),
                    },
                )
            await session.commit()
