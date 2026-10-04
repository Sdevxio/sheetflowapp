"""UDS orders, mappings, and review records.

Revision ID: 002_uds
Revises: 001_initial
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_uds"
down_revision: Union[str, Sequence[str], None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mapping_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mapping_versions_number", "mapping_versions", ["version_number"], unique=True)
    op.create_table(
        "mapping_entries",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("version_id", sa.Uuid(as_uuid=True), sa.ForeignKey("mapping_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("source_extra", sa.Text(), nullable=True),
        sa.Column("mapped_value", sa.Text(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("conflict", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_mapping_entries_version", "mapping_entries", ["version_id"])
    op.create_table(
        "uds_observations",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("import_id", sa.Uuid(as_uuid=True), sa.ForeignKey("imports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_id", sa.Text(), nullable=False),
        sa.Column("order_name", sa.Text(), nullable=True),
        sa.Column("facility", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("payer_name", sa.Text(), nullable=True),
        sa.Column("account_id", sa.Text(), nullable=True),
        sa.Column("encounter_date", sa.String(length=10), nullable=True),
        sa.Column("result_date", sa.String(length=10), nullable=True),
        sa.Column("reviewed_date", sa.String(length=10), nullable=True),
        sa.Column("order_status", sa.Text(), nullable=True),
        sa.Column("order_cancelled", sa.String(length=16), nullable=True),
        sa.Column("order_received", sa.String(length=16), nullable=True),
        sa.Column("order_result", sa.Text(), nullable=True),
        sa.Column("practice", sa.Text(), nullable=True),
        sa.Column("payer_category", sa.Text(), nullable=True),
        sa.Column("order_class", sa.String(length=32), nullable=False),
        sa.Column("lab_from_order", sa.Text(), nullable=True),
        sa.Column("lab_code", sa.Text(), nullable=True),
        sa.Column("expected_lab", sa.Text(), nullable=True),
        sa.Column("lab_source", sa.String(length=32), nullable=False),
        sa.Column("mapping_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("identity_conflict", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("selection_reason", sa.Text(), nullable=True),
        sa.Column("analyte_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("classification", sa.JSON(), nullable=True),
    )
    op.create_index("ix_uds_observations_import", "uds_observations", ["import_id"])
    op.create_index("ix_uds_observations_report", "uds_observations", ["report_id"])
    op.create_table(
        "uds_lines",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("observation_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("uds_observations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("lab_attribute", sa.Text(), nullable=True),
        sa.Column("lab_attribute_value", sa.Text(), nullable=True),
        sa.Column("icd_code", sa.Text(), nullable=True),
    )
    op.create_index("ix_uds_lines_observation", "uds_lines", ["observation_id"])
    op.create_table(
        "uds_issues",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("import_id", sa.Uuid(as_uuid=True), sa.ForeignKey("imports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observation_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("uds_observations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("report_id", sa.Text(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source_rows", sa.JSON(), nullable=False),
    )
    op.create_index("ix_uds_issues_import", "uds_issues", ["import_id"])
    op.create_index("ix_uds_issues_code", "uds_issues", ["code"])
    op.create_table(
        "uds_links",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("screening_report_id", sa.Text(), nullable=False),
        sa.Column("confirmation_report_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_uds_links_screening", "uds_links", ["screening_report_id"])
    op.create_table(
        "uds_resolutions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.Text(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_uds_resolutions_report", "uds_resolutions", ["report_id"])


def downgrade() -> None:
    op.drop_table("uds_resolutions")
    op.drop_table("uds_links")
    op.drop_table("uds_issues")
    op.drop_table("uds_lines")
    op.drop_table("uds_observations")
    op.drop_table("mapping_entries")
    op.drop_table("mapping_versions")
