"""phase6 group conversations

Revision ID: 8a9b0c1d2e3f
Revises: 5f6a7b8c9d0e
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8a9b0c1d2e3f"
down_revision: str | None = "5f6a7b8c9d0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加群聊参与者和最近一次并发批次状态。"""
    op.add_column(
        "conversations",
        sa.Column("status", sa.String(length=30), nullable=False, server_default="idle"),
    )
    op.create_table(
        "conversation_participants",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id", "agent_id"),
    )
    op.create_index(
        "ix_conversation_participants_project",
        "conversation_participants",
        ["project_id", "conversation_id"],
    )


def downgrade() -> None:
    """按依赖逆序移除 Phase 6 数据结构。"""
    op.drop_table("conversation_participants")
    op.drop_column("conversations", "status")
