"""phase10_artifact_execution_id_nullable

Revision ID: a1b2c3d4e5f6
Revises: c1d2e3f4a5b6
Create Date: 2026-08-04

将 artifacts.execution_id 改为可空，支持预览上传等无关联执行的场景。
"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """artifacts.execution_id 改为可空。"""
    op.alter_column(
        "artifacts",
        "execution_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    """恢复 artifacts.execution_id 为非空。"""
    op.alter_column(
        "artifacts",
        "execution_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
