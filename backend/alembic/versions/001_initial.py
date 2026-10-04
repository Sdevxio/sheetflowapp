"""Initial import schema.

Revision ID: 001_initial
Revises:
Create Date: 2026-10-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "imports",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("extension", sa.String(length=8), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duplicate_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("related_import_ids", sa.JSON(), nullable=True),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="en-US"),
        sa.Column("transform_version", sa.String(length=32), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=True),
        sa.Column("inspection", sa.JSON(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_imports_content_hash", "imports", ["content_hash"])
    op.create_index("ix_imports_status", "imports", ["status"])
    op.create_index("ix_imports_created_at", "imports", ["created_at"])

    op.create_table(
        "import_rows",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("import_id", sa.Uuid(as_uuid=True), sa.ForeignKey("imports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sheet_name", sa.Text(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("skip_reason", sa.String(length=64), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("transformed", sa.JSON(), nullable=True),
        sa.Column("issues", sa.JSON(), nullable=False),
    )
    op.create_index("ix_import_rows_lookup", "import_rows", ["import_id", "sheet_name", "status"])
    op.create_index("ix_import_rows_import_id", "import_rows", ["import_id"])


def downgrade() -> None:
    op.drop_table("import_rows")
    op.drop_table("imports")
