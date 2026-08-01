"""Phase 8: RAG knowledge base and three-layer session memory

Revision ID: c1d2e3f4a5b6
Revises: 8a9b0c1d2e3f
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "8a9b0c1d2e3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Phase 8: RAG知识库(knowledge_documents) + 会话摘要(conversation_summaries) + 长期偏好(user_preferences)。"""
    # 确保 pgvector 扩展已启用（pgvector/pgvector:pg18 镜像自带，此处显式启用）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # pg_trgm 用于关键词混合搜索
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── knowledge_documents ──
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("file_id", sa.String(64), nullable=False),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("file_type", sa.String(50), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("chunk_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("embedding", postgresql.ARRAY(sa.Float(), dimensions=1), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "content_hash", name="uq_kd_project_content_hash"),
    )
    op.create_index("ix_kd_project_id", "knowledge_documents", ["project_id"])
    op.create_index("ix_kd_file_id", "knowledge_documents", ["file_id"])

    # ── conversation_summaries ──
    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("round_start", sa.Integer(), nullable=False),
        sa.Column("round_end", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("is_full_merge", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("embedding", postgresql.ARRAY(sa.Float(), dimensions=1), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cs_conversation_id", "conversation_summaries", ["conversation_id"])

    # ── user_preferences ──
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column("embedding", postgresql.ARRAY(sa.Float(), dimensions=1), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("previous_version_id", sa.UUID(), nullable=True),
        sa.Column("conflict_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_up_project_category_active",
        "user_preferences",
        ["project_id", "category", "is_active"],
    )


def downgrade() -> None:
    """移除 Phase 8 数据结构。"""
    op.drop_table("user_preferences")
    op.drop_table("conversation_summaries")
    op.drop_table("knowledge_documents")
    # 不删除 vector 和 pg_trgm 扩展，可能被其他表使用