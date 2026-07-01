"""Database schema for raw healthcare data ingestion."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
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

ctl_materialization_item = Table(
    "ctl_materialization_item",
    metadata,
    Column("materialization_item_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, ForeignKey("ctl_etl_run.run_id"), nullable=False),
    Column("source_table", String(100), nullable=False),
    Column("source_row_id", Integer, nullable=False),
    Column("entity_name", String(100), nullable=False),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column("materializer_name", String(100), nullable=False),
    Column("materializer_version", String(40), nullable=False),
    Column("status", String(20), nullable=False),
    Column("error", Text, nullable=True),
    Column("target_json", Text, nullable=True),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint(
        "source_table",
        "source_row_id",
        "materializer_name",
        name="uq_materialization_item_source_materializer",
    ),
)

pcr_run = Table(
    "pcr_run",
    metadata,
    Column("pcr_run_id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, ForeignKey("ctl_source.source_id"), nullable=False),
    Column("run_name", String(255), nullable=False),
    Column("run_name_normalized", String(255), nullable=False),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint("source_id", "run_name_normalized", name="uq_pcr_run_source_name"),
)

pcr_run_file = Table(
    "pcr_run_file",
    metadata,
    Column("pcr_run_file_id", Integer, primary_key=True, autoincrement=True),
    Column("pcr_run_id", Integer, ForeignKey("pcr_run.pcr_run_id"), nullable=False),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column("run_id", Integer, ForeignKey("ctl_etl_run.run_id"), nullable=False),
    UniqueConstraint("file_id", name="uq_pcr_run_file_file"),
)

pcr_run_attribute = Table(
    "pcr_run_attribute",
    metadata,
    Column("pcr_run_attribute_id", Integer, primary_key=True, autoincrement=True),
    Column("pcr_run_id", Integer, ForeignKey("pcr_run.pcr_run_id"), nullable=False),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column(
        "raw_excel_row_id",
        Integer,
        ForeignKey("raw_excel_rows.row_id"),
        nullable=False,
    ),
    Column("run_id", Integer, ForeignKey("ctl_etl_run.run_id"), nullable=False),
    Column("attribute_key", String(255), nullable=False),
    Column("attribute_key_normalized", String(255), nullable=False),
    Column("value_json", Text, nullable=True),
    Column("is_canonical", Boolean, nullable=False, server_default=text("0")),
    Column("has_conflict", Boolean, nullable=False, server_default=text("0")),
    UniqueConstraint(
        "file_id", "attribute_key_normalized", name="uq_pcr_run_attribute_file_key"
    ),
)

pcr_well = Table(
    "pcr_well",
    metadata,
    Column("pcr_well_id", Integer, primary_key=True, autoincrement=True),
    Column("pcr_run_id", Integer, ForeignKey("pcr_run.pcr_run_id"), nullable=False),
    Column("well", String(20), nullable=False),
    Column("well_normalized", String(20), nullable=False),
    Column("sample_name", String(255), nullable=True),
    Column("content", String(100), nullable=True),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column(
        "raw_excel_row_id",
        Integer,
        ForeignKey("raw_excel_rows.row_id"),
        nullable=False,
    ),
    Column("run_id", Integer, ForeignKey("ctl_etl_run.run_id"), nullable=False),
    UniqueConstraint("pcr_run_id", "well_normalized", name="uq_pcr_well_run_well"),
)

pcr_cq_result = Table(
    "pcr_cq_result",
    metadata,
    Column("pcr_cq_result_id", Integer, primary_key=True, autoincrement=True),
    Column("pcr_run_id", Integer, ForeignKey("pcr_run.pcr_run_id"), nullable=False),
    Column("pcr_well_id", Integer, ForeignKey("pcr_well.pcr_well_id"), nullable=False),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column(
        "raw_excel_row_id",
        Integer,
        ForeignKey("raw_excel_rows.row_id"),
        nullable=False,
    ),
    Column("run_id", Integer, ForeignKey("ctl_etl_run.run_id"), nullable=False),
    Column("target", String(255), nullable=False),
    Column("fluor", String(100), nullable=True),
    Column("sample_name", String(255), nullable=True),
    Column("content", String(100), nullable=True),
    Column("cq", Float, nullable=True),
    Column("cq_mean", Float, nullable=True),
    Column("cq_std_dev", Float, nullable=True),
    UniqueConstraint("raw_excel_row_id", name="uq_pcr_cq_result_raw_row"),
)

pcr_rfu_measurement = Table(
    "pcr_rfu_measurement",
    metadata,
    Column("pcr_rfu_measurement_id", Integer, primary_key=True, autoincrement=True),
    Column("pcr_run_id", Integer, ForeignKey("pcr_run.pcr_run_id"), nullable=False),
    Column("pcr_well_id", Integer, ForeignKey("pcr_well.pcr_well_id"), nullable=False),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column(
        "raw_excel_row_id",
        Integer,
        ForeignKey("raw_excel_rows.row_id"),
        nullable=False,
    ),
    Column("run_id", Integer, ForeignKey("ctl_etl_run.run_id"), nullable=False),
    Column("measurement_kind", String(80), nullable=False),
    Column("axis_kind", String(40), nullable=False),
    Column("axis_value", Float, nullable=False),
    Column("rfu", Float, nullable=True),
    UniqueConstraint(
        "raw_excel_row_id", "pcr_well_id", name="uq_pcr_rfu_raw_row_well"
    ),
)

pcr_derivation_run = Table(
    "pcr_derivation_run",
    metadata,
    Column("derivation_id", Integer, primary_key=True, autoincrement=True),
    Column("algorithm_version", String(100), nullable=False),
    Column("rule_version", String(40), nullable=False),
    Column("parameters_json", Text, nullable=False),
    Column("status", String(30), nullable=False),
    Column("details_json", Text, nullable=True),
    Column(
        "started_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("finished_at", DateTime, nullable=True),
)

pcr_sample = Table(
    "pcr_sample",
    metadata,
    Column("pcr_sample_id", Integer, primary_key=True, autoincrement=True),
    Column("sample_key", String(100), nullable=False, unique=True),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
)

pcr_well_classification = Table(
    "pcr_well_classification",
    metadata,
    Column(
        "derivation_id",
        Integer,
        ForeignKey("pcr_derivation_run.derivation_id"),
        primary_key=True,
    ),
    Column(
        "pcr_well_id",
        Integer,
        ForeignKey("pcr_well.pcr_well_id"),
        primary_key=True,
    ),
    Column(
        "pcr_sample_id",
        Integer,
        ForeignKey("pcr_sample.pcr_sample_id"),
        nullable=True,
    ),
    Column("raw_sample_name", String(255), nullable=True),
    Column("normalized_name", String(255), nullable=True),
    Column("well_role", String(40), nullable=False),
    Column("rule_key", String(100), nullable=False),
    Column("content_evidence", String(100), nullable=True),
    Column("review_reason", Text, nullable=True),
)

pcr_run_analysis = Table(
    "pcr_run_analysis",
    metadata,
    Column(
        "derivation_id",
        Integer,
        ForeignKey("pcr_derivation_run.derivation_id"),
        primary_key=True,
    ),
    Column("pcr_run_id", Integer, ForeignKey("pcr_run.pcr_run_id"), primary_key=True),
    Column("run_class", String(40), nullable=False),
    Column("run_started", DateTime, nullable=True),
    Column("episode_date_key", String(10), nullable=True),
    Column("canonical_targets_json", Text, nullable=False),
    Column("review_reason", Text, nullable=True),
)

pcr_interpretation = Table(
    "pcr_interpretation",
    metadata,
    Column("interpretation_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "derivation_id",
        Integer,
        ForeignKey("pcr_derivation_run.derivation_id"),
        nullable=False,
    ),
    Column("pcr_run_id", Integer, ForeignKey("pcr_run.pcr_run_id"), nullable=False),
    Column("pcr_well_id", Integer, ForeignKey("pcr_well.pcr_well_id"), nullable=False),
    Column(
        "pcr_cq_result_id",
        Integer,
        ForeignKey("pcr_cq_result.pcr_cq_result_id"),
        nullable=False,
    ),
    Column("source_target", String(100), nullable=False),
    Column("canonical_target", String(100), nullable=False),
    Column("call", String(30), nullable=False),
    Column("cq", Float, nullable=True),
    Column("peak_temperature", Float, nullable=True),
    Column("peak_deriv_rfu", Float, nullable=True),
    Column("peaks_json", Text, nullable=False),
    Column("metrics_json", Text, nullable=True),
    Column("error", Text, nullable=True),
    UniqueConstraint(
        "derivation_id", "pcr_cq_result_id", "canonical_target",
        name="uq_pcr_interpretation_derivation_result_target",
    ),
)

pcr_target_qc = Table(
    "pcr_target_qc",
    metadata,
    Column(
        "derivation_id",
        Integer,
        ForeignKey("pcr_derivation_run.derivation_id"),
        primary_key=True,
    ),
    Column("pcr_run_id", Integer, ForeignKey("pcr_run.pcr_run_id"), primary_key=True),
    Column("canonical_target", String(100), primary_key=True),
    Column("qc_status", String(30), nullable=False),
    Column("is_valid", Boolean, nullable=False),
    Column("positive_controls", Integer, nullable=False),
    Column("negative_controls", Integer, nullable=False),
    Column("warnings_json", Text, nullable=False),
    Column("details_json", Text, nullable=False),
)

pcr_run_qc = Table(
    "pcr_run_qc",
    metadata,
    Column(
        "derivation_id",
        Integer,
        ForeignKey("pcr_derivation_run.derivation_id"),
        primary_key=True,
    ),
    Column("pcr_run_id", Integer, ForeignKey("pcr_run.pcr_run_id"), primary_key=True),
    Column("qc_status", String(30), nullable=False),
    Column("valid_targets", Integer, nullable=False),
    Column("invalid_targets", Integer, nullable=False),
    Column("warning_count", Integer, nullable=False),
)

pcr_episode = Table(
    "pcr_episode",
    metadata,
    Column("episode_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "derivation_id",
        Integer,
        ForeignKey("pcr_derivation_run.derivation_id"),
        nullable=False,
    ),
    Column("episode_key", String(100), nullable=False),
    Column("episode_date_key", String(10), nullable=True),
    Column("association_status", String(30), nullable=False),
    Column("review_reason", Text, nullable=True),
    UniqueConstraint(
        "derivation_id",
        "episode_key",
        name="uq_pcr_episode_derivation_key",
    ),
)

pcr_episode_run = Table(
    "pcr_episode_run",
    metadata,
    Column(
        "episode_id",
        Integer,
        ForeignKey("pcr_episode.episode_id"),
        primary_key=True,
    ),
    Column("pcr_run_id", Integer, ForeignKey("pcr_run.pcr_run_id"), primary_key=True),
    Column("run_role", String(40), nullable=False),
    Column("overlap_score", Float, nullable=True),
    Column("association_status", String(30), nullable=False),
)

pcr_result_selection = Table(
    "pcr_result_selection",
    metadata,
    Column(
        "derivation_id",
        Integer,
        ForeignKey("pcr_derivation_run.derivation_id"),
        primary_key=True,
    ),
    Column(
        "pcr_sample_id",
        Integer,
        ForeignKey("pcr_sample.pcr_sample_id"),
        primary_key=True,
    ),
    Column("canonical_target", String(100), primary_key=True),
    Column(
        "preferred_interpretation_id",
        Integer,
        ForeignKey("pcr_interpretation.interpretation_id"),
        nullable=True,
    ),
    Column("occurrence_count", Integer, nullable=False),
    Column("relation_kind", String(40), nullable=False),
    Column("concordance", String(30), nullable=False),
    Column("needs_review", Boolean, nullable=False),
    Column("details_json", Text, nullable=False),
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
Index(
    "idx_materialization_entity_file_status",
    ctl_materialization_item.c.entity_name,
    ctl_materialization_item.c.file_id,
    ctl_materialization_item.c.status,
)
Index(
    "idx_materialization_file_materializer",
    ctl_materialization_item.c.file_id,
    ctl_materialization_item.c.materializer_name,
)
Index("idx_pcr_run_file_run", pcr_run_file.c.pcr_run_id)
Index(
    "idx_pcr_cq_run_well",
    pcr_cq_result.c.pcr_run_id,
    pcr_cq_result.c.pcr_well_id,
)
Index("idx_pcr_cq_file", pcr_cq_result.c.file_id)
Index("idx_pcr_cq_well", pcr_cq_result.c.pcr_well_id)
Index(
    "idx_pcr_rfu_run_kind_axis",
    pcr_rfu_measurement.c.pcr_run_id,
    pcr_rfu_measurement.c.measurement_kind,
    pcr_rfu_measurement.c.axis_value,
)
Index("idx_pcr_rfu_file", pcr_rfu_measurement.c.file_id)
Index("idx_pcr_rfu_well", pcr_rfu_measurement.c.pcr_well_id)
Index(
    "idx_pcr_interpretation_run_target",
    pcr_interpretation.c.pcr_run_id,
    pcr_interpretation.c.canonical_target,
)
Index("idx_pcr_interpretation_well", pcr_interpretation.c.pcr_well_id)
Index("idx_pcr_well_classification_sample", pcr_well_classification.c.pcr_sample_id)
Index("idx_pcr_episode_run_run", pcr_episode_run.c.pcr_run_id)


def create_schema(engine: Engine) -> None:
    """Create all ETL tables if they do not already exist."""

    metadata.create_all(engine)
    for table in metadata.tables.values():
        for index in table.indexes:
            index.create(engine, checkfirst=True)
