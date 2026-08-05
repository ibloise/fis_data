"""Database schema for raw healthcare data ingestion."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    inspect,
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

stg_microb_technique = Table(
    "stg_microb_technique",
    metadata,
    Column("stg_microb_technique_id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, ForeignKey("ctl_source.source_id"), nullable=False),
    Column("run_id", Integer, ForeignKey("ctl_etl_run.run_id"), nullable=False),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column("line_id", Integer, ForeignKey("raw_text_lines.line_id"), nullable=False),
    Column("line_no", Integer, nullable=False),
    Column("nhc", String(32)),
    Column("afi", String(32)),
    Column("sexo", String(8)),
    Column("fec_nacimiento_raw", String(32)),
    Column("nombre", String(255)),
    Column("apellido1", String(255)),
    Column("apellido2", String(255)),
    Column("num_muestra", String(32)),
    Column("aislamiento", String(16)),
    Column("cod_muestra", String(32)),
    Column("cod_localizacion", String(32)),
    Column("cod_procedencia", String(32)),
    Column("cod_servicio", String(32)),
    Column("planta", String(64)),
    Column("habitacion", String(64)),
    Column("box", String(32)),
    Column("fec_recep_raw", String(32)),
    Column("hora_recep_raw", String(32)),
    Column("fec_entrada_raw", String(32)),
    Column("hora_entrada_raw", String(32)),
    Column("fec_resultado_raw", String(32)),
    Column("hora_resultado_raw", String(32)),
    Column("cod_sig", String(32)),
    Column("cod_analisis", String(32)),
    Column("cod_resultado", String(32)),
    Column("cod_microorganismo", String(32)),
    Column("cod_recuento", String(32)),
    Column("cod_result_cualitativo", String(32)),
    Column("cod_result_cuantitativo", String(32)),
    Column("cod_tecnica", String(32)),
    Column("cod_obs", String(32)),
    Column("cod_obs_2", String(32)),
    Column("cod_observaciones", String(32)),
    Column("cod_observaciones_2", String(32)),
    Column("cod_diagnostico", String(32)),
    Column("cod_recr", String(32)),
    Column("cod_firma", String(32)),
    Column("loaded_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("file_id", "line_id", name="uq_stg_microb_technique_file_line"),
)

stg_microb_antibiogram = Table(
    "stg_microb_antibiogram",
    metadata,
    Column("stg_microb_antibiogram_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "stg_microb_technique_id",
        Integer,
        ForeignKey("stg_microb_technique.stg_microb_technique_id"),
        nullable=False,
    ),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column("line_id", Integer, ForeignKey("raw_text_lines.line_id"), nullable=False),
    Column("seq_no", Integer, nullable=False),
    Column("cod_atb", String(16), nullable=False),
    Column("cod_cmi", String(32)),
    Column("cod_int", String(8)),
    Column("des_cmi", String(64)),
    Column("loaded_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("file_id", "line_id", "seq_no", name="uq_stg_microb_atb_file_line_seq"),
)

ref_microb_catalog = Table(
    "ref_microb_catalog",
    metadata,
    Column("ref_microb_catalog_id", Integer, primary_key=True, autoincrement=True),
    Column("catalog_name", String(100), nullable=False),
    Column("source_path", String(1024), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("entry_count", Integer, nullable=False),
    Column("is_current", Boolean, nullable=False, server_default=text("0")),
    Column("loaded_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("catalog_name", "sha256", name="uq_ref_microb_catalog_name_sha"),
)

ref_microb_catalog_entry = Table(
    "ref_microb_catalog_entry",
    metadata,
    Column("ref_microb_catalog_entry_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "ref_microb_catalog_id",
        Integer,
        ForeignKey("ref_microb_catalog.ref_microb_catalog_id"),
        nullable=False,
    ),
    Column("code", String(100), nullable=False),
    Column("description", Text, nullable=True),
    Column("aliases_json", Text, nullable=False),
    Column("raw_fields_json", Text, nullable=False),
    UniqueConstraint(
        "ref_microb_catalog_id", "code", name="uq_ref_microb_catalog_entry_code"
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
    Column("run_started", DateTime, nullable=True),
    Column("run_ended", DateTime, nullable=True),
    Column("created_by_user", String(255), nullable=True),
    Column("base_serial_number", String(255), nullable=True),
    Column("optical_head_serial_number", String(255), nullable=True),
    Column("cfx_maestro_version", String(255), nullable=True),
    Column("protocol_file_name", String(255), nullable=True),
    Column("plate_setup_file_name", String(255), nullable=True),
    Column("sample_vol", String(255), nullable=True),
    Column("lid_temp", String(255), nullable=True),
    Column("notes", Text, nullable=True),
    Column("run_metadata_id", String(255), nullable=True),
    Column("source_file_name", String(255), nullable=True),
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

pcr_run_scope = Table(
    "pcr_run_scope",
    metadata,
    Column("pcr_run_id", Integer, ForeignKey("pcr_run.pcr_run_id"), primary_key=True),
    Column("analytical_scope", String(20), nullable=False),
    Column("reason", Text, nullable=False),
    Column("assigned_by", String(20), nullable=False),
    Column(
        "assigned_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
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
    UniqueConstraint("raw_excel_row_id", "pcr_well_id", name="uq_pcr_rfu_raw_row_well"),
)

pcr_derivation_run = Table(
    "pcr_derivation_run",
    metadata,
    Column("derivation_id", Integer, primary_key=True, autoincrement=True),
    Column("algorithm_version", String(100), nullable=False),
    Column("rule_version", String(40), nullable=False),
    Column("parameters_json", Text, nullable=False),
    Column("status", String(30), nullable=False),
    Column("is_current", Boolean, nullable=False, server_default=text("0")),
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
    Column("run_type", String(20), nullable=False),
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
        "derivation_id",
        "pcr_cq_result_id",
        "canonical_target",
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

pcr_target_qc_evidence = Table(
    "pcr_target_qc_evidence",
    metadata,
    Column("derivation_id", Integer, primary_key=True),
    Column("pcr_run_id", Integer, primary_key=True),
    Column("canonical_target", String(100), primary_key=True),
    Column(
        "interpretation_id",
        Integer,
        ForeignKey("pcr_interpretation.interpretation_id"),
        primary_key=True,
    ),
    Column("well_role", String(40), nullable=False),
    ForeignKeyConstraint(
        ["derivation_id", "pcr_run_id", "canonical_target"],
        [
            "pcr_target_qc.derivation_id",
            "pcr_target_qc.pcr_run_id",
            "pcr_target_qc.canonical_target",
        ],
        name="fk_pcr_target_qc_evidence_target_qc",
    ),
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
    Column("run_type", String(20), primary_key=True),
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

pcr_review_action = Table(
    "pcr_review_action",
    metadata,
    Column("review_action_id", Integer, primary_key=True, autoincrement=True),
    Column("item_type", String(30), nullable=False),
    Column("subject_key", String(255), nullable=False),
    Column("issue_fingerprint", String(64), nullable=False),
    Column("source_derivation_id", Integer, nullable=False),
    Column("action", String(30), nullable=False),
    Column("resolution", String(30), nullable=True),
    Column("override_kind", String(30), nullable=True),
    Column("payload_json", Text, nullable=True),
    Column("reason", Text, nullable=False),
    Column("reviewer", String(100), nullable=False),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
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
Index("idx_stg_microb_technique_file", stg_microb_technique.c.file_id)
Index("idx_stg_microb_technique_nhc", stg_microb_technique.c.nhc)
Index("idx_stg_microb_technique_sample", stg_microb_technique.c.num_muestra)
Index("idx_stg_microb_technique_microorganism", stg_microb_technique.c.cod_microorganismo)
Index("idx_stg_microb_antibiogram_technique", stg_microb_antibiogram.c.stg_microb_technique_id)
Index("idx_stg_microb_antibiogram_antibiotic", stg_microb_antibiogram.c.cod_atb)
Index("idx_ref_microb_catalog_current", ref_microb_catalog.c.catalog_name, ref_microb_catalog.c.is_current)
Index("idx_ref_microb_catalog_entry_code", ref_microb_catalog_entry.c.code)
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
Index("idx_pcr_run_scope_scope", pcr_run_scope.c.analytical_scope)
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
    "uq_pcr_derivation_run_current",
    pcr_derivation_run.c.is_current,
    unique=True,
    sqlite_where=pcr_derivation_run.c.is_current.is_(True),
)
Index(
    "idx_pcr_interpretation_run_target",
    pcr_interpretation.c.pcr_run_id,
    pcr_interpretation.c.canonical_target,
)
Index("idx_pcr_interpretation_well", pcr_interpretation.c.pcr_well_id)
Index("idx_pcr_well_classification_sample", pcr_well_classification.c.pcr_sample_id)
Index("idx_pcr_episode_run_run", pcr_episode_run.c.pcr_run_id)
Index(
    "idx_pcr_review_action_subject",
    pcr_review_action.c.item_type,
    pcr_review_action.c.subject_key,
    pcr_review_action.c.review_action_id,
)


def create_schema(engine: Engine) -> None:
    """Create all ETL tables if they do not already exist."""

    metadata.create_all(engine)
    _upgrade_pcr_run_metadata(engine)
    _normalize_pcr_run_datetimes(engine)
    _upgrade_pcr_run_type(engine)
    _upgrade_pcr_result_selection_run_type(engine)
    _upgrade_pcr_derivation_current(engine)
    for table in metadata.tables.values():
        for index in table.indexes:
            index.create(engine, checkfirst=True)
    create_views(engine)


def _upgrade_pcr_run_metadata(engine: Engine) -> None:
    """Add promoted PCR run metadata columns to existing databases."""

    columns = {str(column["name"]) for column in inspect(engine).get_columns("pcr_run")}
    additions = {
        "run_started": "DATETIME",
        "run_ended": "DATETIME",
        "created_by_user": "VARCHAR(255)",
        "base_serial_number": "VARCHAR(255)",
        "optical_head_serial_number": "VARCHAR(255)",
        "cfx_maestro_version": "VARCHAR(255)",
        "protocol_file_name": "VARCHAR(255)",
        "plate_setup_file_name": "VARCHAR(255)",
        "sample_vol": "VARCHAR(255)",
        "lid_temp": "VARCHAR(255)",
        "notes": "TEXT",
        "run_metadata_id": "VARCHAR(255)",
        "source_file_name": "VARCHAR(255)",
    }
    with engine.begin() as conn:
        for name, sql_type in additions.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE pcr_run ADD COLUMN {name} {sql_type}"))


def _normalize_pcr_run_datetimes(engine: Engine) -> None:
    """Convert CFX month/day/year timestamps stored in legacy run metadata."""

    with engine.begin() as conn:
        for column in ("run_started", "run_ended"):
            conn.execute(
                text(
                    f"""
                    UPDATE pcr_run
                    SET {column} = datetime(
                        substr({column}, 7, 4) || '-' || substr({column}, 1, 2) || '-' ||
                        substr({column}, 4, 2) || ' ' || substr({column}, 12, 8)
                    )
                    WHERE {column} GLOB '??/??/???? ??:??:?? UTC'
                    """
                )
            )


def _upgrade_pcr_run_type(engine: Engine) -> None:
    """Add global screening/specific type to legacy run analyses."""

    columns = {str(column["name"]) for column in inspect(engine).get_columns("pcr_run_analysis")}
    if "run_type" in columns:
        return
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE pcr_run_analysis ADD COLUMN run_type VARCHAR(20) NOT NULL DEFAULT 'SPECIFIC'")
        )
        conn.execute(
            text("UPDATE pcr_run_analysis SET run_type='SCREENING' WHERE run_class='SCREENING'")
        )


def _upgrade_pcr_result_selection_run_type(engine: Engine) -> None:
    """Rebuild legacy result selections with run type in their primary key."""

    columns = {str(column["name"]) for column in inspect(engine).get_columns("pcr_result_selection")}
    if "run_type" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE pcr_result_selection RENAME TO pcr_result_selection_legacy"))
        pcr_result_selection.create(conn)
        conn.execute(
            text(
                """
                INSERT INTO pcr_result_selection (
                    derivation_id, pcr_sample_id, run_type, canonical_target,
                    preferred_interpretation_id, occurrence_count, relation_kind,
                    concordance, needs_review, details_json
                )
                SELECT derivation_id, pcr_sample_id, 'SPECIFIC', canonical_target,
                       preferred_interpretation_id, occurrence_count, relation_kind,
                       concordance, needs_review, details_json
                FROM pcr_result_selection_legacy
                """
            )
        )
        conn.execute(text("DROP TABLE pcr_result_selection_legacy"))


def create_views(engine: Engine) -> None:
    """Create the public PCR views managed by the database schema."""

    current = """
        SELECT derivation_id FROM pcr_derivation_run
        WHERE status='SUCCESS' AND is_current=1
    """
    views = {
        "v_pcr_algorithmic_result": f"""
            SELECT sample.sample_key, selection.run_type, selection.canonical_target,
                   interpretation.call, interpretation.cq,
                   interpretation.peak_temperature, interpretation.pcr_run_id,
                   selection.occurrence_count, selection.relation_kind,
                   selection.concordance, selection.needs_review
            FROM pcr_result_selection AS selection
            JOIN pcr_sample AS sample
              ON sample.pcr_sample_id = selection.pcr_sample_id
            LEFT JOIN pcr_interpretation AS interpretation
              ON interpretation.interpretation_id =
                 selection.preferred_interpretation_id
            LEFT JOIN pcr_run_scope AS scope
              ON scope.pcr_run_id=interpretation.pcr_run_id
            WHERE selection.derivation_id = ({current})
              AND COALESCE(scope.analytical_scope, 'ROUTINE') != 'EXPERIMENTAL'
        """,
        "v_pcr_effective_result": f"""
            WITH ranked_override AS (
                SELECT item_type, subject_key, action, payload_json,
                       ROW_NUMBER() OVER (
                           PARTITION BY item_type, subject_key
                           ORDER BY review_action_id DESC
                       ) AS position
                FROM pcr_review_action
                WHERE item_type='RESULT' AND action IN ('OVERRIDE', 'REVOKE')
            )
            SELECT sample.sample_key, selection.run_type, selection.canonical_target,
                   COALESCE(json_extract(override.payload_json, '$.call'),
                            interpretation.call) AS call,
                   interpretation.cq, interpretation.peak_temperature,
                   interpretation.pcr_run_id, selection.occurrence_count,
                   selection.relation_kind, selection.concordance,
                   selection.needs_review,
                   CASE WHEN override.action='OVERRIDE' THEN 1 ELSE 0 END
                       AS has_override
            FROM pcr_result_selection AS selection
            JOIN pcr_sample AS sample
              ON sample.pcr_sample_id=selection.pcr_sample_id
            LEFT JOIN pcr_interpretation AS interpretation
              ON interpretation.interpretation_id=selection.preferred_interpretation_id
            LEFT JOIN ranked_override AS override
              ON override.position=1 AND override.action='OVERRIDE'
             AND override.subject_key=('sample:' || sample.sample_key ||
                                       ':run_type:' || selection.run_type ||
                                       ':target:' || selection.canonical_target)
            LEFT JOIN pcr_run_scope AS scope
              ON scope.pcr_run_id=interpretation.pcr_run_id
            WHERE selection.derivation_id=({current})
              AND COALESCE(scope.analytical_scope, 'ROUTINE') != 'EXPERIMENTAL'
        """,
        "v_pcr_result_history": f"""
            SELECT sample.sample_key, interpretation.canonical_target,
                   interpretation.call, interpretation.cq,
                   interpretation.peak_temperature, interpretation.pcr_run_id,
                   analysis.run_started, analysis.run_class, analysis.run_type
            FROM pcr_interpretation AS interpretation
            JOIN pcr_well_classification AS classification
              ON classification.derivation_id=interpretation.derivation_id
             AND classification.pcr_well_id=interpretation.pcr_well_id
            JOIN pcr_sample AS sample
              ON sample.pcr_sample_id=classification.pcr_sample_id
            JOIN pcr_run_analysis AS analysis
              ON analysis.derivation_id=interpretation.derivation_id
             AND analysis.pcr_run_id=interpretation.pcr_run_id
            LEFT JOIN pcr_run_scope AS scope
              ON scope.pcr_run_id=interpretation.pcr_run_id
            WHERE interpretation.derivation_id=({current})
              AND COALESCE(scope.analytical_scope, 'ROUTINE') != 'EXPERIMENTAL'
        """,
        "v_pcr_run_qc": f"""
            SELECT run_qc.*, run.run_name FROM pcr_run_qc AS run_qc
            JOIN pcr_run AS run ON run.pcr_run_id=run_qc.pcr_run_id
            WHERE run_qc.derivation_id=({current})
        """,
        "v_pcr_target_qc_evidence": f"""
            SELECT qc.pcr_run_id, run.run_name, qc.canonical_target,
                   qc.qc_status, qc.is_valid,
                   evidence.well_role, well.well, well.sample_name, well.content,
                   interpretation.interpretation_id,
                   interpretation.pcr_cq_result_id,
                   interpretation.source_target, interpretation.call,
                   interpretation.cq, interpretation.peak_temperature,
                   interpretation.peak_deriv_rfu, interpretation.peaks_json,
                   interpretation.metrics_json, interpretation.error
            FROM pcr_target_qc_evidence AS evidence
            JOIN pcr_target_qc AS qc
              ON qc.derivation_id=evidence.derivation_id
             AND qc.pcr_run_id=evidence.pcr_run_id
             AND qc.canonical_target=evidence.canonical_target
            JOIN pcr_interpretation AS interpretation
              ON interpretation.interpretation_id=evidence.interpretation_id
            JOIN pcr_well AS well ON well.pcr_well_id=interpretation.pcr_well_id
            JOIN pcr_run AS run ON run.pcr_run_id=qc.pcr_run_id
            WHERE evidence.derivation_id=({current})
        """,
        "v_pcr_episodes": f"""
            SELECT episode.episode_key, episode.association_status,
                   episode_run.pcr_run_id, episode_run.run_role,
                   episode_run.overlap_score
            FROM pcr_episode AS episode
            JOIN pcr_episode_run AS episode_run
              ON episode_run.episode_id=episode.episode_id
            WHERE episode.derivation_id=({current})
        """,
        "v_pcr_review_queue": f"""
            SELECT 'WELL' item_type, CAST(pcr_well_id AS TEXT) item_id,
                   review_reason reason FROM pcr_well_classification
            WHERE review_reason IS NOT NULL AND derivation_id=({current})
            UNION ALL
            SELECT 'RUN', CAST(pcr_run_id AS TEXT), review_reason
            FROM pcr_run_analysis
            WHERE review_reason IS NOT NULL AND derivation_id=({current})
            UNION ALL
            SELECT 'EPISODE', episode_key, review_reason FROM pcr_episode
            WHERE association_status='REVIEW' AND derivation_id=({current})
            UNION ALL
            SELECT 'TARGET_QC', CAST(pcr_run_id AS TEXT) || ':' || canonical_target,
                   details_json FROM pcr_target_qc
            WHERE qc_status='INVALID' AND derivation_id=({current})
            UNION ALL
            SELECT 'RESULT', CAST(pcr_sample_id AS TEXT) || ':' || run_type || ':' || canonical_target,
                   concordance FROM pcr_result_selection
            WHERE needs_review=1 AND derivation_id=({current})
        """,
    }
    with engine.begin() as conn:
        conn.execute(text("DROP VIEW IF EXISTS v_pcr_current_result"))
        for name, select_sql in views.items():
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
            conn.execute(text(f"CREATE VIEW {name} AS {select_sql}"))


def _upgrade_pcr_derivation_current(engine: Engine) -> None:
    """Add and initialize explicit derivation control on legacy schemas."""

    columns = {
        str(column["name"])
        for column in inspect(engine).get_columns("pcr_derivation_run")
    }
    if "is_current" in columns:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                ALTER TABLE pcr_derivation_run
                ADD COLUMN is_current BOOLEAN NOT NULL DEFAULT 0
                """
            )
        )
        conn.execute(
            text(
                """
                UPDATE pcr_derivation_run
                SET is_current=1
                WHERE derivation_id=(
                    SELECT MAX(derivation_id) FROM pcr_derivation_run
                    WHERE status='SUCCESS'
                )
                """
            )
        )
