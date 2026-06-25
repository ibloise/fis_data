"""Database schema for raw healthcare data ingestion."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

ctl_source = Table(
    "ctl_source",
    metadata,
    Column("source_id", Integer, primary_key=True, autoincrement=True),
    Column("source_name", String(100), nullable=False),
    Column("source_type", String(40), nullable=False),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint("source_name", name="uq_ctl_source_source_name"),
)

ctl_file_registry = Table(
    "ctl_file_registry",
    metadata,
    Column("file_id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, ForeignKey("ctl_source.source_id"), nullable=False),
    Column("source_name", String(100), nullable=False),
    Column("storage_path", String(1024), nullable=False),
    Column("file_format", String(30), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column(
        "received_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("status", String(20), nullable=False, server_default=text("'REGISTERED'")),
    UniqueConstraint("source_name", "sha256", name="uq_file_registry_source_sha"),
)

ctl_etl_run = Table(
    "ctl_etl_run",
    metadata,
    Column("run_id", Integer, primary_key=True, autoincrement=True),
    Column("pipeline_name", String(100), nullable=False),
    Column(
        "started_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("finished_at", DateTime, nullable=True),
    Column("status", String(20), nullable=False),
    Column("details_json", Text, nullable=True),
)

raw_text_lines = Table(
    "raw_text_lines",
    metadata,
    Column("line_id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, ForeignKey("ctl_source.source_id"), nullable=False),
    Column("run_id", Integer, ForeignKey("ctl_etl_run.run_id"), nullable=False),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column("entity_name", String(100), nullable=False),
    Column("line_no", Integer, nullable=False),
    Column("raw_line", Text, nullable=False),
    Column("raw_line_sha256", String(64), nullable=False),
    Column("is_header", Boolean, nullable=False, server_default=text("0")),
    Column(
        "parse_status",
        String(20),
        nullable=False,
        server_default=text("'RAW_ONLY'"),
    ),
    Column("parse_error", Text, nullable=True),
    Column("payload_json", Text, nullable=True),
    Column(
        "ingested_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint("file_id", "line_no", name="uq_raw_text_lines_file_line"),
)

raw_excel_rows = Table(
    "raw_excel_rows",
    metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, ForeignKey("ctl_source.source_id"), nullable=False),
    Column("run_id", Integer, ForeignKey("ctl_etl_run.run_id"), nullable=False),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column("entity_name", String(100), nullable=False),
    Column("sheet_name", String(255), nullable=False),
    Column("row_no", Integer, nullable=False),
    Column("raw_values_json", Text, nullable=False),
    Column("row_sha256", String(64), nullable=False),
    Column("is_header", Boolean, nullable=False, server_default=text("0")),
    Column(
        "parse_status",
        String(20),
        nullable=False,
        server_default=text("'RAW_ONLY'"),
    ),
    Column("parse_error", Text, nullable=True),
    Column("payload_json", Text, nullable=True),
    Column(
        "ingested_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint(
        "file_id",
        "sheet_name",
        "row_no",
        name="uq_raw_excel_rows_file_sheet_row",
    ),
)

ctl_excel_parse_file = Table(
    "ctl_excel_parse_file",
    metadata,
    Column("excel_file_parse_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, ForeignKey("ctl_etl_run.run_id"), nullable=False),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column("entity_name", String(100), nullable=False),
    Column("file_kind", String(100), nullable=True),
    Column("parser_name", String(100), nullable=False),
    Column("parser_version", String(40), nullable=False),
    Column("status", String(40), nullable=False),
    Column("sheets_seen", Integer, nullable=False, server_default=text("0")),
    Column("sheets_parsed", Integer, nullable=False, server_default=text("0")),
    Column("rows_seen", Integer, nullable=False, server_default=text("0")),
    Column("rows_parsed", Integer, nullable=False, server_default=text("0")),
    Column("rows_error", Integer, nullable=False, server_default=text("0")),
    Column("error", Text, nullable=True),
    Column("details_json", Text, nullable=True),
    Column(
        "started_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("finished_at", DateTime, nullable=True),
)

ctl_excel_parse_sheet = Table(
    "ctl_excel_parse_sheet",
    metadata,
    Column("excel_sheet_parse_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "excel_file_parse_id",
        Integer,
        ForeignKey("ctl_excel_parse_file.excel_file_parse_id"),
        nullable=False,
    ),
    Column("run_id", Integer, ForeignKey("ctl_etl_run.run_id"), nullable=False),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column("entity_name", String(100), nullable=False),
    Column("sheet_name", String(255), nullable=False),
    Column("sheet_kind", String(100), nullable=True),
    Column("header_row_no", Integer, nullable=True),
    Column("status", String(40), nullable=False),
    Column("rows_seen", Integer, nullable=False, server_default=text("0")),
    Column("rows_parsed", Integer, nullable=False, server_default=text("0")),
    Column("rows_skipped", Integer, nullable=False, server_default=text("0")),
    Column("rows_error", Integer, nullable=False, server_default=text("0")),
    Column("error", Text, nullable=True),
    Column("details_json", Text, nullable=True),
    Column(
        "started_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("finished_at", DateTime, nullable=True),
)

Index("idx_file_registry_source", ctl_file_registry.c.source_name)
Index(
    "idx_ctl_etl_run_pipeline_started",
    ctl_etl_run.c.pipeline_name,
    ctl_etl_run.c.started_at,
)
Index(
    "idx_raw_text_source_entity_run",
    raw_text_lines.c.source_id,
    raw_text_lines.c.entity_name,
    raw_text_lines.c.run_id,
)
Index("idx_raw_text_file_line", raw_text_lines.c.file_id, raw_text_lines.c.line_no)
Index(
    "idx_raw_excel_source_entity_run",
    raw_excel_rows.c.source_id,
    raw_excel_rows.c.entity_name,
    raw_excel_rows.c.run_id,
)
Index(
    "idx_raw_excel_file_sheet_row",
    raw_excel_rows.c.file_id,
    raw_excel_rows.c.sheet_name,
    raw_excel_rows.c.row_no,
)
Index(
    "idx_excel_parse_file_file_run",
    ctl_excel_parse_file.c.file_id,
    ctl_excel_parse_file.c.run_id,
)
Index(
    "idx_excel_parse_sheet_file_sheet",
    ctl_excel_parse_sheet.c.file_id,
    ctl_excel_parse_sheet.c.sheet_name,
)


def create_schema(engine: Engine) -> None:
    """Create all ETL tables if they do not already exist."""

    metadata.create_all(engine)
