import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class PortableUUID(TypeDecorator):
    """Store UUIDs natively where the database supports them, and accept either UUID objects or strings."""

    impl = Uuid(as_uuid=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class Base(DeclarativeBase):
    pass


class Import(Base):
    __tablename__ = "imports"

    id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    original_filename: Mapped[str] = mapped_column(Text)
    stored_path: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(String(8))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    related_import_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    locale: Mapped[str] = mapped_column(String(16), default="en-US")
    transform_version: Mapped[str] = mapped_column(String(32))
    configuration: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    inspection: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)


class ImportRow(Base):
    __tablename__ = "import_rows"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    import_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("imports.id", ondelete="CASCADE"), index=True)
    sheet_name: Mapped[str] = mapped_column(Text)
    source_row: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    skip_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict] = mapped_column(JSON)
    transformed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    issues: Mapped[list] = mapped_column(JSON)


class MappingVersion(Base):
    __tablename__ = "mapping_versions"

    id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    version_number: Mapped[int] = mapped_column(Integer, unique=True)
    original_filename: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MappingEntry(Base):
    __tablename__ = "mapping_entries"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    version_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("mapping_versions.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str] = mapped_column(Text)
    source_extra: Mapped[str | None] = mapped_column(Text, nullable=True)
    mapped_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)


class UdsObservation(Base):
    __tablename__ = "uds_observations"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    import_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("imports.id", ondelete="CASCADE"), index=True)
    report_id: Mapped[str] = mapped_column(Text, index=True)
    order_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    facility: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    payer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    encounter_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    result_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    reviewed_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    order_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_cancelled: Mapped[str | None] = mapped_column(String(16), nullable=True)
    order_received: Mapped[str | None] = mapped_column(String(16), nullable=True)
    order_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    practice: Mapped[str | None] = mapped_column(Text, nullable=True)
    payer_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_class: Mapped[str] = mapped_column(String(32), default="unclassified")
    lab_from_order: Mapped[str | None] = mapped_column(Text, nullable=True)
    lab_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_lab: Mapped[str | None] = mapped_column(Text, nullable=True)
    lab_source: Mapped[str] = mapped_column(String(32), default="none")
    mapping_version_id: Mapped[uuid.UUID | None] = mapped_column(PortableUUID(), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    identity_conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    selection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyte_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    classification: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class UdsLine(Base):
    __tablename__ = "uds_lines"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("uds_observations.id", ondelete="CASCADE"), index=True)
    source_row: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    lab_attribute: Mapped[str | None] = mapped_column(Text, nullable=True)
    lab_attribute_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    icd_code: Mapped[str | None] = mapped_column(Text, nullable=True)


class UdsIssue(Base):
    __tablename__ = "uds_issues"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    import_id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), ForeignKey("imports.id", ondelete="CASCADE"), index=True)
    observation_id: Mapped[int | None] = mapped_column(ForeignKey("uds_observations.id", ondelete="CASCADE"), nullable=True)
    report_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    source_rows: Mapped[list] = mapped_column(JSON)


class UdsLink(Base):
    __tablename__ = "uds_links"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    screening_report_id: Mapped[str] = mapped_column(Text, index=True)
    confirmation_report_id: Mapped[str] = mapped_column(Text, index=True)
    status: Mapped[str] = mapped_column(String(16), default="suggested")
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UdsResolution(Base):
    __tablename__ = "uds_resolutions"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    report_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(16))
    decision: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
