"""phase4 direct conversations and replayable events

Revision ID: 5f6a7b8c9d0e
Revises: d32b245c800d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5f6a7b8c9d0e"
down_revision: str | None = "d32b245c800d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加单聊 Agent 绑定和可重放事件存储。"""
    op.add_column(
        "conversations",
        sa.Column("agent_id", sa.UUID(), nullable=True, comment="单聊绑定的 Agent"),
    )
    op.create_foreign_key(
        "fk_conversations_agent_id_agents",
        "conversations",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_conversations_agent_id", "conversations", ["agent_id"])
    op.create_table(
        "execution_events",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("execution_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_id"], ["agent_executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("execution_id", "sequence", name="uq_execution_events_sequence"),
    )
    op.create_index("ix_execution_events_conversation_id", "execution_events", ["conversation_id"])
    op.create_index("ix_execution_events_execution_id", "execution_events", ["execution_id"])
    op.create_index(
        "ix_execution_events_replay",
        "execution_events",
        ["conversation_id", "execution_id", "sequence"],
    )


def downgrade() -> None:
    """按依赖逆序移除 Phase 4 数据结构。"""
    op.drop_table("execution_events")
    op.drop_index("ix_conversations_agent_id", table_name="conversations")
    op.drop_constraint("fk_conversations_agent_id_agents", "conversations", type_="foreignkey")
    op.drop_column("conversations", "agent_id")
