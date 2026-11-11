"""Database schema for raw healthcare data ingestion."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Date,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.types import TypeDecorator

metadata = MetaData()

SIL_IRRELEVANT_MICROORGANISM_PATTERN = (
    "Candida|Enterococcus|Stenotrophomonas|Acinetobacter"
)

MANAGED_VIEW_NAMES = (
    "v_pcr_algorithmic_result",
    "v_pcr_effective_result",
    "v_pcr_result_history",
    "v_pcr_unpromoted_sample_errors",
    "v_pcr_sample_result",
    "v_pcr_sample_target_metric",
    "v_pcr_sil_organism_marker_summary",
    "v_pcr_sil_isolate_marker_summary",
    "v_pcr_sil_culture_marker_summary",
    "v_pcr_sil_result_link",
    "v_downstream_sil_pcr_global",
    "v_downstream_sil_pcr_organism",
    "v_downstream_pcr_sil_target",
    "v_downstream_sil_organism_target",
    "v_downstream_sil_organism_profile",
    "v_downstream_pcr_sil_sample_profile",
    "v_downstream_patient_episode",
    "v_downstream_patient_episode_sample",
    "v_pcr_run_qc",
    "v_pcr_target_qc_evidence",
    "v_pcr_episodes",
    "v_pcr_review_queue",
)


class JSONDocument(TypeDecorator[Any]):
    """Use JSONB in PostgreSQL while retaining legacy JSON text in SQLite."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        type_ = JSONB() if dialect.name == "postgresql" else Text()
        return dialect.type_descriptor(type_)

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None or dialect.name != "postgresql":
            return value
        return json.loads(value) if isinstance(value, str) else value

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

ctl_file_supersession = Table(
    "ctl_file_supersession",
    metadata,
    Column("superseded_file_id", Integer, ForeignKey("ctl_file_registry.file_id"), primary_key=True),
    Column("replacement_file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column("reason", Text, nullable=True),
    Column(
        "superseded_at", DateTime, nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
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
    Column("details_json", JSONDocument(), nullable=True),
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
    Column("is_header", Boolean, nullable=False, server_default=text("false")),
    Column(
        "parse_status",
        String(20),
        nullable=False,
        server_default=text("'RAW_ONLY'"),
    ),
    Column("parse_error", Text, nullable=True),
    Column("payload_json", JSONDocument(), nullable=True),
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
    Column("raw_values_json", JSONDocument(), nullable=False),
    Column("row_sha256", String(64), nullable=False),
    Column("is_header", Boolean, nullable=False, server_default=text("false")),
    Column(
        "parse_status",
        String(20),
        nullable=False,
        server_default=text("'RAW_ONLY'"),
    ),
    Column("parse_error", Text, nullable=True),
    Column("payload_json", JSONDocument(), nullable=True),
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
    Column("is_current", Boolean, nullable=False, server_default=text("false")),
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
    Column("aliases_json", JSONDocument(), nullable=False),
    Column("raw_fields_json", JSONDocument(), nullable=False),
    UniqueConstraint(
        "ref_microb_catalog_id", "code", name="uq_ref_microb_catalog_entry_code"
    ),
)

# Normalized Microb domain.  The source codes remain in the reference FK
# columns; staging is retained as the authoritative raw lineage.
microb_patient = Table(
    "microb_patient", metadata,
    Column("patient_id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, ForeignKey("ctl_source.source_id"), nullable=False),
    Column("nhc", String(32)), Column("afi", String(32)), Column("sex", String(8)),
    Column("birth_date", Date), Column("first_name", String(255)),
    Column("surname1", String(255)), Column("surname2", String(255)),
    UniqueConstraint("source_id", "nhc", name="uq_microb_patient_source_nhc"),
)

microb_sample = Table(
    "microb_sample", metadata,
    Column("sample_id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, ForeignKey("ctl_source.source_id"), nullable=False),
    Column("patient_id", Integer, ForeignKey("microb_patient.patient_id")),
    Column("sample_number", String(32), nullable=False),
    Column("received_at", DateTime), Column("entered_at", DateTime),
    Column("origin_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("service_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("hospital_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("specimen_type_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("specimen_location_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("floor", String(64)), Column("room", String(64)), Column("box", String(32)),
    UniqueConstraint("source_id", "sample_number", name="uq_microb_sample_source_number"),
)

microb_sample_analysis = Table(
    "microb_sample_analysis", metadata,
    Column("sample_analysis_id", Integer, primary_key=True, autoincrement=True),
    Column("sample_id", Integer, ForeignKey("microb_sample.sample_id"), nullable=False),
    Column("analysis_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("analysis_code", String(32), nullable=False),
    Column("analysis_kind", String(20)), Column("box_id", String(32)),
    Column("resulted_at", DateTime),
    UniqueConstraint("sample_id", "analysis_code", name="uq_microb_sample_analysis"),
)

microb_analysis_result = Table(
    "microb_analysis_result", metadata,
    Column("analysis_result_id", Integer, primary_key=True, autoincrement=True),
    Column("sample_analysis_id", Integer, ForeignKey("microb_sample_analysis.sample_analysis_id"), nullable=False),
    Column("result_code_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("result_code", String(32), nullable=False),
    UniqueConstraint("sample_analysis_id", "result_code", name="uq_microb_analysis_result"),
)

microb_isolate = Table(
    "microb_isolate", metadata,
    Column("isolate_id", Integer, primary_key=True, autoincrement=True),
    Column("analysis_result_id", Integer, ForeignKey("microb_analysis_result.analysis_result_id"), nullable=False),
    Column("isolate_number", String(16), nullable=False),
    Column("microorganism_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("microorganism_code", String(32)),
    UniqueConstraint("analysis_result_id", "isolate_number", name="uq_microb_isolate_result_number"),
)

stg_st_record = Table(
    "stg_st_record",
    metadata,
    Column("st_record_id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, ForeignKey("ctl_source.source_id"), nullable=False),
    Column("run_id", Integer, ForeignKey("ctl_etl_run.run_id"), nullable=False),
    Column("file_id", Integer, ForeignKey("ctl_file_registry.file_id"), nullable=False),
    Column("raw_excel_row_id", Integer, ForeignKey("raw_excel_rows.row_id"), nullable=False, unique=True),
    Column("sample_number", String(64), nullable=False),
    Column("microorganism_code", String(32), nullable=False),
    Column("microorganism_name", String(255)),
    Column("microorganism_name_raw", Text),
    Column("st_value", String(64), nullable=False),
    Column("st_raw", Text),
    Column("match_status", String(40), nullable=False),
    Column("matched_sample_count", Integer, nullable=False, server_default=text("0")),
    Column("matched_isolate_count", Integer, nullable=False, server_default=text("0")),
    Column("details_json", JSONDocument(), nullable=False),
    Column("loaded_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

microb_isolate_st_link = Table(
    "microb_isolate_st_link",
    metadata,
    Column("st_record_id", Integer, ForeignKey("stg_st_record.st_record_id"), primary_key=True),
    Column("isolate_id", Integer, ForeignKey("microb_isolate.isolate_id"), primary_key=True),
)

microb_sample_observation = Table(
    "microb_sample_observation", metadata,
    Column("sample_observation_id", Integer, primary_key=True, autoincrement=True),
    Column("sample_id", Integer, ForeignKey("microb_sample.sample_id"), nullable=False),
    Column("observation_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("observation_code", String(32), nullable=False),
    UniqueConstraint("sample_id", "observation_code", name="uq_microb_sample_observation"),
)

microb_isolate_observation = Table(
    "microb_isolate_observation", metadata,
    Column("isolate_observation_id", Integer, primary_key=True, autoincrement=True),
    Column("isolate_id", Integer, ForeignKey("microb_isolate.isolate_id"), nullable=False),
    Column("observation_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("observation_code", String(32), nullable=False),
    UniqueConstraint("isolate_id", "observation_code", name="uq_microb_isolate_observation"),
)

microb_determination = Table(
    "microb_determination", metadata,
    Column("determination_id", Integer, primary_key=True, autoincrement=True),
    Column("sample_analysis_id", Integer, ForeignKey("microb_sample_analysis.sample_analysis_id"), nullable=False),
    Column("technique_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("technique_code", String(32), nullable=False),
    Column("qualitative_result_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("qualitative_result_code", String(32)),
    Column("quantitative_result_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("quantitative_result", String(32)),
    Column("quantitative_result_literal", String(64)),
    UniqueConstraint("sample_analysis_id", "technique_code", name="uq_microb_determination"),
)

microb_susceptibility_result = Table(
    "microb_susceptibility_result", metadata,
    Column("susceptibility_result_id", Integer, primary_key=True, autoincrement=True),
    Column("isolate_id", Integer, ForeignKey("microb_isolate.isolate_id"), nullable=False),
    Column("antibiotic_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("antibiotic_code", String(16), nullable=False),
    Column("cmi_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("cmi_code", String(32)),
    Column("interpretation_id", Integer, ForeignKey("ref_microb_catalog_entry.ref_microb_catalog_entry_id")),
    Column("interpretation_code", String(8)), Column("cmi_description", String(64)),
    Column("stg_microb_antibiogram_id", Integer, ForeignKey("stg_microb_antibiogram.stg_microb_antibiogram_id"), nullable=False),
    UniqueConstraint("isolate_id", "stg_microb_antibiogram_id", name="uq_microb_susceptibility_source"),
)

microb_normalization_issue = Table(
    "microb_normalization_issue", metadata,
    Column("issue_id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, nullable=False), Column("run_id", Integer, nullable=False),
    Column("file_id", Integer, nullable=False), Column("line_id", Integer, nullable=False),
    Column("staging_table", String(100), nullable=False), Column("staging_id", Integer),
    Column("field_name", String(100), nullable=False), Column("code", String(100)),
    Column("severity", String(20), nullable=False), Column("message", Text, nullable=False),
    UniqueConstraint("staging_table", "staging_id", "field_name", "code", name="uq_microb_micro_issue"),
)

# Persistent pseudonym registries deliberately use natural source identifiers,
# not normalized Microb surrogate IDs that can change during a rebuild.
pseudonym_patient_registry = Table(
    "pseudonym_patient_registry",
    metadata,
    Column("registry_id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, ForeignKey("ctl_source.source_id"), nullable=False),
    Column("source_identifier", String(64), nullable=False),
    Column("pseudonym", String(32), nullable=False, unique=True),
    Column("assigned_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("source_identifier", name="uq_pseudonym_patient_identifier"),
)

pseudonym_sample_registry = Table(
    "pseudonym_sample_registry",
    metadata,
    Column("registry_id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, ForeignKey("ctl_source.source_id"), nullable=False),
    Column("source_identifier", String(64), nullable=False),
    Column("pseudonym", String(32), nullable=False, unique=True),
    Column("assigned_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("source_identifier", name="uq_pseudonym_sample_identifier"),
)

pseudonymization_counter = Table(
    "pseudonymization_counter",
    metadata,
    Column("entity_type", String(16), primary_key=True),
    Column("last_number", Integer, nullable=False, server_default=text("0")),
)

pseudonymization_audit = Table(
    "pseudonymization_audit",
    metadata,
    Column("audit_id", Integer, primary_key=True, autoincrement=True),
    Column("action", String(32), nullable=False),
    Column("patient_count", Integer, nullable=False, server_default=text("0")),
    Column("sample_count", Integer, nullable=False, server_default=text("0")),
    Column("reason", Text),
    Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

microb_entity_lineage = Table(
    "microb_entity_lineage", metadata,
    Column("lineage_id", Integer, primary_key=True, autoincrement=True),
    Column("entity_type", String(80), nullable=False), Column("entity_id", Integer, nullable=False),
    Column("source_id", Integer, nullable=False), Column("run_id", Integer, nullable=False),
    Column("file_id", Integer, nullable=False), Column("line_id", Integer, nullable=False),
    Column("staging_table", String(100), nullable=False), Column("staging_id", Integer, nullable=False),
    UniqueConstraint("entity_type", "entity_id", "staging_table", "staging_id", name="uq_microb_entity_lineage"),
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
    Column("details_json", JSONDocument(), nullable=True),
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
    Column("details_json", JSONDocument(), nullable=True),
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
    Column("target_json", JSONDocument(), nullable=True),
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
    Column("value_json", JSONDocument(), nullable=True),
    Column("is_canonical", Boolean, nullable=False, server_default=text("false")),
    Column("has_conflict", Boolean, nullable=False, server_default=text("false")),
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
    Column("parameters_json", JSONDocument(), nullable=False),
    Column("status", String(30), nullable=False),
    Column("is_current", Boolean, nullable=False, server_default=text("false")),
    Column("details_json", JSONDocument(), nullable=True),
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

pcr_microb_well_validation = Table(
    "pcr_microb_well_validation",
    metadata,
    Column("derivation_id", Integer, ForeignKey("pcr_derivation_run.derivation_id"), primary_key=True),
    Column("pcr_well_id", Integer, ForeignKey("pcr_well.pcr_well_id"), primary_key=True),
    Column("pcr_sample_id", Integer, ForeignKey("pcr_sample.pcr_sample_id")),
    Column("microb_sample_id", Integer, ForeignKey("microb_sample.sample_id")),
    Column("validation_status", String(50), nullable=False),
    Column("validation_reason", Text),
    Column("details_json", JSONDocument(), nullable=False),
    Column("validated_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

pcr_sample_scope_gate = Table(
    "pcr_sample_scope_gate",
    metadata,
    Column("derivation_id", Integer, ForeignKey("pcr_derivation_run.derivation_id"), primary_key=True),
    Column("pcr_sample_id", Integer, ForeignKey("pcr_sample.pcr_sample_id"), primary_key=True),
    Column("legacy_screening_run_id", Integer, ForeignKey("pcr_run.pcr_run_id")),
    Column("reset_screening_run_id", Integer, ForeignKey("pcr_run.pcr_run_id")),
    Column("gate_status", String(50), nullable=False),
    Column("reason", Text, nullable=True),
    Column("details_json", JSONDocument(), nullable=False),
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
    Column("canonical_targets_json", JSONDocument(), nullable=False),
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
    Column("peaks_json", JSONDocument(), nullable=False),
    Column("metrics_json", JSONDocument(), nullable=True),
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
    Column("warnings_json", JSONDocument(), nullable=False),
    Column("details_json", JSONDocument(), nullable=False),
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

patient_episode_derivation = Table(
    "patient_episode_derivation",
    metadata,
    Column("episode_derivation_id", Integer, primary_key=True, autoincrement=True),
    Column("pcr_derivation_id", Integer, ForeignKey("pcr_derivation_run.derivation_id"), nullable=False),
    Column("algorithm_version", String(64), nullable=False),
    Column("parameters_json", JSONDocument(), nullable=False),
    Column("status", String(20), nullable=False, server_default=text("'RUNNING'")),
    Column("is_current", Boolean, nullable=False, server_default=text("false")),
    Column("started_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("finished_at", DateTime),
    Column("sample_count", Integer, nullable=False, server_default=text("0")),
    Column("episode_count", Integer, nullable=False, server_default=text("0")),
    Column("details_json", JSONDocument(), nullable=False),
)

patient_episode = Table(
    "patient_episode",
    metadata,
    Column("patient_episode_id", Integer, primary_key=True, autoincrement=True),
    Column("episode_derivation_id", Integer, ForeignKey("patient_episode_derivation.episode_derivation_id"), nullable=False),
    Column("patient_id", Integer, ForeignKey("microb_patient.patient_id"), nullable=False),
    Column("episode_number", Integer, nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("ended_at", DateTime, nullable=False),
    Column("sample_count", Integer, nullable=False),
    Column("start_reason", String(32), nullable=False),
    UniqueConstraint("episode_derivation_id", "patient_id", "episode_number", name="uq_patient_episode_number"),
)

patient_episode_sample = Table(
    "patient_episode_sample",
    metadata,
    Column("episode_derivation_id", Integer, ForeignKey("patient_episode_derivation.episode_derivation_id"), primary_key=True),
    Column("patient_episode_id", Integer, ForeignKey("patient_episode.patient_episode_id"), nullable=False),
    Column("pcr_sample_id", Integer, ForeignKey("pcr_sample.pcr_sample_id"), primary_key=True),
    Column("microb_sample_id", Integer, ForeignKey("microb_sample.sample_id"), nullable=False),
    Column("clinical_date", DateTime, nullable=False),
    Column("days_since_previous", Integer),
    UniqueConstraint("episode_derivation_id", "pcr_sample_id", name="uq_patient_episode_sample_pcr"),
    UniqueConstraint("episode_derivation_id", "microb_sample_id", name="uq_patient_episode_sample_microb"),
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
    Column("details_json", JSONDocument(), nullable=False),
)

pcr_sil_culture_classification = Table(
    "pcr_sil_culture_classification",
    metadata,
    Column("derivation_id", Integer, ForeignKey("pcr_derivation_run.derivation_id"), primary_key=True),
    Column("microb_sample_id", Integer, ForeignKey("microb_sample.sample_id"), primary_key=True),
    Column("sample_analysis_id", Integer, ForeignKey("microb_sample_analysis.sample_analysis_id"), nullable=False),
    Column("culture_category", String(32), nullable=False),
    Column("result_codes_json", JSONDocument(), nullable=False),
    Column("policy_version", String(64), nullable=False),
    Column("details_json", JSONDocument(), nullable=False),
)

pcr_sil_retrospective_evidence = Table(
    "pcr_sil_retrospective_evidence",
    metadata,
    Column("derivation_id", Integer, ForeignKey("pcr_derivation_run.derivation_id"), primary_key=True),
    Column("pcr_sample_id", Integer, ForeignKey("pcr_sample.pcr_sample_id"), primary_key=True),
    Column("current_microb_sample_id", Integer, ForeignKey("microb_sample.sample_id"), nullable=False),
    Column("evidence_microb_sample_id", Integer, ForeignKey("microb_sample.sample_id")),
    Column("matched_microorganism_code", String(32)),
    Column("evidence_status", String(32), nullable=False),
    Column("days_difference", Integer),
    Column("negative_count", Integer),
    Column("policy_version", String(64), nullable=False),
    Column("reason", Text, nullable=False),
    Column("details_json", JSONDocument(), nullable=False),
)

pcr_sil_link_candidate = Table(
    "pcr_sil_link_candidate",
    metadata,
    Column("candidate_id", Integer, primary_key=True, autoincrement=True),
    Column("derivation_id", Integer, ForeignKey("pcr_derivation_run.derivation_id"), nullable=False),
    Column("pcr_sample_id", Integer, ForeignKey("pcr_sample.pcr_sample_id"), nullable=False),
    Column("current_microb_sample_id", Integer, ForeignKey("microb_sample.sample_id"), nullable=False),
    Column("current_isolate_id", Integer, ForeignKey("microb_isolate.isolate_id")),
    Column("microorganism_code", String(32)),
    Column("evidence_microb_sample_id", Integer, ForeignKey("microb_sample.sample_id")),
    Column("evidence_isolate_id", Integer, ForeignKey("microb_isolate.isolate_id")),
    Column("marker_code", String(16)),
    Column("candidate_kind", String(32), nullable=False),
    Column("candidate_status", String(40), nullable=False),
    Column("days_difference", Integer),
    Column("negative_count", Integer),
    Column("policy_version", String(64), nullable=False),
    Column("reason", Text, nullable=False),
    Column("details_json", JSONDocument(), nullable=False),
)

pcr_sil_result_link = Table(
    "pcr_sil_result_link",
    metadata,
    Column("derivation_id", Integer, ForeignKey("pcr_derivation_run.derivation_id"), primary_key=True),
    Column("pcr_sample_id", Integer, ForeignKey("pcr_sample.pcr_sample_id"), primary_key=True),
    Column("run_type", String(20), primary_key=True),
    Column("canonical_target", String(100), primary_key=True),
    Column("candidate_id", Integer, ForeignKey("pcr_sil_link_candidate.candidate_id")),
    Column("link_type", String(32), nullable=False),
    Column("concordance", String(32), nullable=False),
    Column("policy_version", String(64), nullable=False),
    Column("reason", Text, nullable=False),
    Column("details_json", JSONDocument(), nullable=False),
)

pcr_sil_organism_marker_summary = Table(
    "pcr_sil_organism_marker_summary",
    metadata,
    Column("derivation_id", Integer, ForeignKey("pcr_derivation_run.derivation_id"), primary_key=True),
    Column("pcr_sample_id", Integer, ForeignKey("pcr_sample.pcr_sample_id"), primary_key=True),
    Column("microb_sample_id", Integer, ForeignKey("microb_sample.sample_id"), primary_key=True),
    Column("microorganism_code", String(32), primary_key=True),
    Column("oxa_detected", Boolean, nullable=False),
    Column("kpc_detected", Boolean, nullable=False),
    Column("ndm_detected", Boolean, nullable=False),
    Column("vim_detected", Boolean, nullable=False),
    Column("marker_codes_json", JSONDocument(), nullable=False),
    Column("isolate_count", Integer, nullable=False),
    Column("details_json", JSONDocument(), nullable=False),
)

pcr_sil_isolate_marker_summary = Table(
    "pcr_sil_isolate_marker_summary",
    metadata,
    Column("derivation_id", Integer, ForeignKey("pcr_derivation_run.derivation_id"), primary_key=True),
    Column("pcr_sample_id", Integer, ForeignKey("pcr_sample.pcr_sample_id"), primary_key=True),
    Column("microb_sample_id", Integer, ForeignKey("microb_sample.sample_id"), primary_key=True),
    Column("isolate_id", Integer, ForeignKey("microb_isolate.isolate_id"), primary_key=True),
    Column("microorganism_code", String(32), nullable=False),
    Column("isolate_number", String(16), nullable=False),
    Column("oxa_detected", Boolean, nullable=False),
    Column("kpc_detected", Boolean, nullable=False),
    Column("ndm_detected", Boolean, nullable=False),
    Column("vim_detected", Boolean, nullable=False),
    Column("marker_codes_json", JSONDocument(), nullable=False),
    Column("details_json", JSONDocument(), nullable=False),
)

pcr_sil_culture_marker_summary = Table(
    "pcr_sil_culture_marker_summary",
    metadata,
    Column("derivation_id", Integer, ForeignKey("pcr_derivation_run.derivation_id"), primary_key=True),
    Column("pcr_sample_id", Integer, ForeignKey("pcr_sample.pcr_sample_id"), primary_key=True),
    Column("microb_sample_id", Integer, ForeignKey("microb_sample.sample_id"), primary_key=True),
    Column("oxa_detected", Boolean, nullable=False),
    Column("kpc_detected", Boolean, nullable=False),
    Column("ndm_detected", Boolean, nullable=False),
    Column("vim_detected", Boolean, nullable=False),
    Column("marker_codes_json", JSONDocument(), nullable=False),
    Column("organism_count", Integer, nullable=False),
    Column("isolate_count", Integer, nullable=False),
    Column("details_json", JSONDocument(), nullable=False),
)

pcr_sample_result = Table(
    "pcr_sample_result",
    metadata,
    Column("derivation_id", Integer, ForeignKey("pcr_derivation_run.derivation_id"), primary_key=True),
    Column("pcr_sample_id", Integer, ForeignKey("pcr_sample.pcr_sample_id"), primary_key=True),
    Column("pcr_category", String(32), nullable=False),
    Column("decision_source", String(20), nullable=False),
    Column("warning_code", String(80)),
    Column("details_json", JSONDocument(), nullable=False),
)

pcr_sample_target_metric = Table(
    "pcr_sample_target_metric",
    metadata,
    Column("derivation_id", Integer, ForeignKey("pcr_derivation_run.derivation_id"), primary_key=True),
    Column("pcr_sample_id", Integer, ForeignKey("pcr_sample.pcr_sample_id"), primary_key=True),
    Column("pcr_run_id", Integer, ForeignKey("pcr_run.pcr_run_id"), primary_key=True),
    Column("canonical_target", String(100), primary_key=True),
    Column("target_interpretation_id", Integer, ForeignKey("pcr_interpretation.interpretation_id"), nullable=False),
    Column("target_cq", Float),
    Column("reference_16s_cq", Float),
    Column("delta_cq", Float),
    Column("details_json", JSONDocument(), nullable=False),
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
    Column("payload_json", JSONDocument(), nullable=True),
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
Index("idx_microb_lineage_staging", microb_entity_lineage.c.staging_table, microb_entity_lineage.c.staging_id)
Index("idx_microb_susceptibility_staging", microb_susceptibility_result.c.stg_microb_antibiogram_id)
Index("idx_microb_sample_number", microb_sample.c.sample_number)
Index(
    "idx_microb_analysis_sample_match",
    microb_sample_analysis.c.sample_id,
    microb_sample_analysis.c.analysis_code,
    microb_sample_analysis.c.analysis_kind,
)
Index("idx_pcr_microb_validation_status", pcr_microb_well_validation.c.derivation_id, pcr_microb_well_validation.c.validation_status)
Index("idx_pcr_sample_scope_gate_status", pcr_sample_scope_gate.c.derivation_id, pcr_sample_scope_gate.c.gate_status)
Index("idx_microb_sample_patient_dates", microb_sample.c.patient_id, microb_sample.c.received_at, microb_sample.c.entered_at)
Index("idx_microb_analysis_pmdr", microb_sample_analysis.c.sample_id, microb_sample_analysis.c.analysis_code, microb_sample_analysis.c.analysis_kind)
Index("idx_microb_isolate_result_organism", microb_isolate.c.analysis_result_id, microb_isolate.c.microorganism_code)
Index("idx_stg_st_sample_code", stg_st_record.c.sample_number, stg_st_record.c.microorganism_code)
Index("idx_microb_isolate_st_isolate", microb_isolate_st_link.c.isolate_id)
Index("idx_microb_susceptibility_marker", microb_susceptibility_result.c.isolate_id, microb_susceptibility_result.c.antibiotic_code, microb_susceptibility_result.c.interpretation_code)
Index("idx_pcr_sil_classification_derivation", pcr_sil_culture_classification.c.derivation_id, pcr_sil_culture_classification.c.culture_category)
Index("idx_pcr_sil_candidate_sample", pcr_sil_link_candidate.c.derivation_id, pcr_sil_link_candidate.c.pcr_sample_id, pcr_sil_link_candidate.c.marker_code)
Index("idx_pcr_sil_retrospective_sample", pcr_sil_retrospective_evidence.c.derivation_id, pcr_sil_retrospective_evidence.c.evidence_status)
Index("idx_pcr_sil_isolate_marker", pcr_sil_isolate_marker_summary.c.derivation_id, pcr_sil_isolate_marker_summary.c.microorganism_code)
Index("idx_pcr_sil_organism_marker", pcr_sil_organism_marker_summary.c.derivation_id, pcr_sil_organism_marker_summary.c.microorganism_code)
Index("idx_pcr_sil_culture_marker", pcr_sil_culture_marker_summary.c.derivation_id, pcr_sil_culture_marker_summary.c.microb_sample_id)
Index("idx_pcr_sample_result_category", pcr_sample_result.c.derivation_id, pcr_sample_result.c.pcr_category)
Index("idx_pcr_sample_metric_target", pcr_sample_target_metric.c.derivation_id, pcr_sample_target_metric.c.canonical_target)
Index("idx_patient_episode_derivation_current", patient_episode_derivation.c.is_current)
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
    postgresql_where=pcr_derivation_run.c.is_current.is_(True),
    sqlite_where=pcr_derivation_run.c.is_current.is_(True),
)
Index(
    "uq_patient_episode_derivation_current",
    patient_episode_derivation.c.is_current,
    unique=True,
    postgresql_where=patient_episode_derivation.c.is_current.is_(True),
    sqlite_where=patient_episode_derivation.c.is_current.is_(True),
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
    _upgrade_json_documents(engine)
    _upgrade_pcr_run_metadata(engine)
    _normalize_pcr_run_datetimes(engine)
    _upgrade_pcr_run_type(engine)
    _upgrade_pcr_result_selection_run_type(engine)
    _upgrade_pcr_derivation_current(engine)
    _upgrade_microb_determination_catalogs(engine)
    _upgrade_microb_sample_analysis_kind(engine)
    _upgrade_microb_sample_hospital(engine)
    _upgrade_patient_episode_sample_primary_key(engine)
    _require_global_pseudonym_registry_keys(engine)
    for table in metadata.tables.values():
        for index in table.indexes:
            index.create(engine, checkfirst=True)
    create_views(engine)


def _upgrade_patient_episode_sample_primary_key(engine: Engine) -> None:
    """Allow multiple sample memberships per episode derivation."""

    if engine.dialect.name != "postgresql":
        return
    primary_key = inspect(engine).get_pk_constraint("patient_episode_sample")
    columns = tuple(primary_key.get("constrained_columns") or ())
    if columns == ("episode_derivation_id", "pcr_sample_id"):
        return
    if columns != ("episode_derivation_id",):
        raise RuntimeError(
            "patient_episode_sample has an unsupported primary key "
            f"{columns!r}; expected ('episode_derivation_id',) or "
            "('episode_derivation_id', 'pcr_sample_id')."
        )
    constraint_name = primary_key.get("name")
    if not constraint_name:
        raise RuntimeError(
            "Could not identify the existing patient_episode_sample primary key."
        )
    preparer = engine.dialect.identifier_preparer
    quoted_constraint = preparer.quote(constraint_name)
    with engine.begin() as conn:
        duplicates = conn.execute(text("""
            SELECT 1
            FROM patient_episode_sample
            GROUP BY episode_derivation_id, pcr_sample_id
            HAVING COUNT(*) > 1
            LIMIT 1
        """)).first()
        if duplicates:
            raise RuntimeError(
                "Cannot migrate patient_episode_sample: duplicate derivation/PCR "
                "sample memberships exist."
            )
        conn.execute(text(
            "ALTER TABLE patient_episode_sample DROP CONSTRAINT "
            f"{quoted_constraint}"
        ))
        conn.execute(text(
            "ALTER TABLE patient_episode_sample ADD CONSTRAINT "
            f"{quoted_constraint} PRIMARY KEY "
            "(episode_derivation_id, pcr_sample_id)"
        ))


def _upgrade_json_documents(engine: Engine) -> None:
    """Promote legacy PostgreSQL JSON text columns to native JSONB."""

    if engine.dialect.name != "postgresql":
        return
    inspector = inspect(engine)
    alterations = []
    for table in metadata.tables.values():
        existing = {
            str(column["name"]): str(column["type"]).upper()
            for column in inspector.get_columns(table.name)
        }
        alterations.extend(
            (table.name, column.name)
            for column in table.columns
            if isinstance(column.type, JSONDocument)
            and existing.get(column.name) != "JSONB"
        )
    if not alterations:
        return
    with engine.begin() as conn:
        _drop_managed_views(conn)
        for table_name, column_name in alterations:
            conn.execute(
                text(
                    f"ALTER TABLE {table_name} ALTER COLUMN {column_name} "
                    f"TYPE JSONB USING {column_name}::jsonb"
                )
            )


def _require_global_pseudonym_registry_keys(engine: Engine) -> None:
    """Refuse views over a legacy source-scoped pseudonym registry."""

    if engine.dialect.name != "postgresql":
        return
    inspector = inspect(engine)
    for table_name in (
        "pseudonym_patient_registry",
        "pseudonym_sample_registry",
    ):
        unique_columns = {
            tuple(constraint.get("column_names") or ())
            for constraint in inspector.get_unique_constraints(table_name)
        }
        if ("source_identifier",) not in unique_columns:
            detail = (
                "source-scoped identifiers"
                if ("source_id", "source_identifier") in unique_columns
                else "no global identifier constraint"
            )
            raise RuntimeError(
                f"The {table_name} still uses {detail}. "
                "Run `fis-data globalize-pseudonyms --reason ... "
                "--confirm-globalization GLOBALIZE-PSEUDONYMS` before using "
                "downstream views."
            )


def _drop_managed_views(conn) -> None:
    """Drop public views in dependency order before a table-type migration."""

    conn.execute(text("DROP VIEW IF EXISTS v_pcr_current_result"))
    for name in reversed(MANAGED_VIEW_NAMES):
        conn.execute(text(f"DROP VIEW IF EXISTS {name}"))


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
    """Convert legacy CFX timestamps stored as MM/DD/YYYY text."""

    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for column in ("run_started", "run_ended"):
            conn.execute(
                text(
                    f"""
                    UPDATE pcr_run
                    SET {column} = to_timestamp(
                        trim({column}::text), 'MM/DD/YYYY HH24:MI:SS TZ'
                    )::timestamp
                    WHERE trim({column}::text) ~
                        '^[0-9]{{2}}/[0-9]{{2}}/[0-9]{{4}} [0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}} UTC$'
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


def _upgrade_microb_determination_catalogs(engine: Engine) -> None:
    """Add the quantitative-result catalog link to existing PostgreSQL schemas."""

    columns = {
        str(column["name"])
        for column in inspect(engine).get_columns("microb_determination")
    }
    with engine.begin() as conn:
        if "quantitative_result_id" not in columns:
            conn.execute(
                text(
                    """
                    ALTER TABLE microb_determination
                    ADD COLUMN quantitative_result_id INTEGER
                    REFERENCES ref_microb_catalog_entry(ref_microb_catalog_entry_id)
                    """
                )
            )
        if "quantitative_result_literal" not in columns:
            conn.execute(
                text(
                    """
                    ALTER TABLE microb_determination
                    ADD COLUMN quantitative_result_literal VARCHAR(64)
                    """
                )
            )


def _upgrade_microb_sample_analysis_kind(engine: Engine) -> None:
    """Add the analysis semantic kind to existing Microb schemas."""

    columns = {
        str(column["name"])
        for column in inspect(engine).get_columns("microb_sample_analysis")
    }
    if "analysis_kind" in columns:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE microb_sample_analysis ADD COLUMN analysis_kind VARCHAR(20)"
            )
        )


def _upgrade_microb_sample_hospital(engine: Engine) -> None:
    """Add the catalog-backed hospital relation to existing Microb samples."""

    columns = {
        str(column["name"])
        for column in inspect(engine).get_columns("microb_sample")
    }
    if "hospital_id" in columns:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                ALTER TABLE microb_sample
                ADD COLUMN hospital_id INTEGER
                REFERENCES ref_microb_catalog_entry(ref_microb_catalog_entry_id)
                """
            )
        )


def create_views(engine: Engine) -> None:
    """Create the public PCR views managed by the database schema."""

    # Analytics imports schema tables during package initialization. Defer this
    # dependency until the schema module has finished defining those tables.
    from .etl.pcr.analytics.sil_linking import direct_characterization_exists_sql

    if engine.dialect.name != "postgresql":
        return
    current = """
        SELECT derivation_id FROM pcr_derivation_run
        WHERE status='SUCCESS' AND is_current=TRUE
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
                   COALESCE((override.payload_json::jsonb ->> 'call'),
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
        "v_pcr_unpromoted_sample_errors": f"""
            WITH control_evidence AS (
                SELECT evidence.derivation_id, evidence.pcr_run_id,
                       evidence.canonical_target,
                       jsonb_agg(
                           jsonb_build_object(
                               'role', evidence.well_role,
                               'well', well.well,
                               'sample_name', well.sample_name,
                               'content', well.content,
                               'call', interpretation.call,
                               'cq', interpretation.cq,
                               'peak_temperature', interpretation.peak_temperature,
                               'peak_deriv_rfu', interpretation.peak_deriv_rfu,
                               'melt_status', COALESCE(
                                   interpretation.metrics_json::jsonb ->> 'melt_status',
                                   CASE WHEN interpretation.error IS NULL
                                        THEN 'MELTING_COMPATIBLE'
                                        ELSE interpretation.error END
                               ),
                               'error', interpretation.error
                           )
                           ORDER BY evidence.well_role, well.well,
                                    interpretation.interpretation_id
                       ) AS control_evidence_json
                FROM pcr_target_qc_evidence AS evidence
                JOIN pcr_interpretation AS interpretation
                  ON interpretation.interpretation_id=evidence.interpretation_id
                JOIN pcr_well AS well
                  ON well.pcr_well_id=interpretation.pcr_well_id
                WHERE evidence.derivation_id=({current})
                GROUP BY evidence.derivation_id, evidence.pcr_run_id,
                         evidence.canonical_target
            ), failed AS (
                SELECT interpretation.derivation_id,
                       classification.pcr_sample_id,
                       sample.sample_key,
                       LOWER(analysis.run_type) AS semantic_type,
                       interpretation.canonical_target,
                       interpretation.pcr_run_id,
                       run.run_name,
                       analysis.run_class,
                       analysis.run_started,
                       BOOL_OR(
                           interpretation.call NOT IN ('POSITIVE', 'NEGATIVE')
                           OR COALESCE(qc.is_valid, FALSE)=FALSE
                           OR interpretation.error IS NOT NULL
                       ) AS is_failed,
                       STRING_AGG(DISTINCT NULLIF(interpretation.error, ''), ' | ')
                           AS interpretation_errors,
                       jsonb_agg(DISTINCT qc.details_json)
                           FILTER (WHERE qc.details_json IS NOT NULL)
                           AS target_qc_errors,
                       MAX(analysis.review_reason) AS run_error
                FROM pcr_interpretation AS interpretation
                JOIN pcr_well_classification AS classification
                  ON classification.derivation_id=interpretation.derivation_id
                 AND classification.pcr_well_id=interpretation.pcr_well_id
                 AND classification.well_role='REAL_SAMPLE'
                JOIN pcr_sample AS sample
                  ON sample.pcr_sample_id=classification.pcr_sample_id
                JOIN pcr_run_analysis AS analysis
                  ON analysis.derivation_id=interpretation.derivation_id
                 AND analysis.pcr_run_id=interpretation.pcr_run_id
                JOIN pcr_run AS run
                  ON run.pcr_run_id=interpretation.pcr_run_id
                LEFT JOIN pcr_target_qc AS qc
                  ON qc.derivation_id=interpretation.derivation_id
                 AND qc.pcr_run_id=interpretation.pcr_run_id
                 AND qc.canonical_target=interpretation.canonical_target
                LEFT JOIN pcr_run_scope AS scope
                  ON scope.pcr_run_id=interpretation.pcr_run_id
                WHERE interpretation.derivation_id=({current})
                  AND COALESCE(scope.analytical_scope, 'ROUTINE') != 'EXPERIMENTAL'
                GROUP BY interpretation.derivation_id, classification.pcr_sample_id,
                         sample.sample_key, analysis.run_type,
                         interpretation.canonical_target, interpretation.pcr_run_id,
                         run.run_name, analysis.run_class, analysis.run_started
            )
            SELECT failed.sample_key, failed.pcr_sample_id,
                   failed.semantic_type, failed.canonical_target,
                   failed.pcr_run_id, failed.run_name, failed.run_class,
                   failed.run_started, failed.interpretation_errors,
                   failed.target_qc_errors, failed.run_error,
                   controls.control_evidence_json,
                   COALESCE(
                       NULLIF(
                           CONCAT_WS(' | ', failed.interpretation_errors,
                                     failed.target_qc_errors::text, failed.run_error),
                           ''
                       ),
                       'No valid promotable result'
                   ) AS error
            FROM failed
            LEFT JOIN control_evidence AS controls
              ON controls.derivation_id=failed.derivation_id
             AND controls.pcr_run_id=failed.pcr_run_id
             AND controls.canonical_target=failed.canonical_target
            WHERE failed.is_failed=TRUE
              AND NOT EXISTS (
                  SELECT 1
                  FROM pcr_result_selection AS selection
                  WHERE selection.derivation_id=failed.derivation_id
                    AND selection.pcr_sample_id=failed.pcr_sample_id
                    AND LOWER(selection.run_type)=failed.semantic_type
                    AND selection.canonical_target=failed.canonical_target
                    AND selection.preferred_interpretation_id IS NOT NULL
              )
        """,
        "v_pcr_sample_result": f"""
            SELECT sample.sample_key, result.pcr_category, result.decision_source,
                   result.warning_code, result.details_json
            FROM pcr_sample_result AS result
            JOIN pcr_sample AS sample ON sample.pcr_sample_id=result.pcr_sample_id
            WHERE result.derivation_id=({current})
        """,
        "v_pcr_sample_target_metric": f"""
            SELECT sample.sample_key, metric.pcr_run_id, metric.canonical_target,
                   metric.target_cq, metric.reference_16s_cq, metric.delta_cq,
                   metric.details_json
            FROM pcr_sample_target_metric AS metric
            JOIN pcr_sample AS sample ON sample.pcr_sample_id=metric.pcr_sample_id
            WHERE metric.derivation_id=({current})
        """,
        "v_pcr_sil_organism_marker_summary": f"""
            SELECT sample.sample_key, summary.microorganism_code,
                   summary.oxa_detected, summary.kpc_detected,
                   summary.ndm_detected, summary.vim_detected,
                   summary.marker_codes_json, summary.isolate_count
            FROM pcr_sil_organism_marker_summary AS summary
            JOIN pcr_sample AS sample ON sample.pcr_sample_id=summary.pcr_sample_id
            WHERE summary.derivation_id=({current})
        """,
        "v_pcr_sil_isolate_marker_summary": f"""
            WITH st_by_isolate AS (
                SELECT link.isolate_id,
                       CASE WHEN COUNT(DISTINCT record.st_value) = 1
                            THEN MIN(record.st_value) END AS st
                FROM microb_isolate_st_link AS link
                JOIN stg_st_record AS record
                  ON record.st_record_id=link.st_record_id
                GROUP BY link.isolate_id
            )
            SELECT sample.sample_key, summary.isolate_id, summary.isolate_number,
                   summary.microorganism_code, summary.oxa_detected,
                   summary.kpc_detected, summary.ndm_detected, summary.vim_detected,
                   summary.marker_codes_json, st_by_isolate.st
            FROM pcr_sil_isolate_marker_summary AS summary
            JOIN pcr_sample AS sample ON sample.pcr_sample_id=summary.pcr_sample_id
            LEFT JOIN st_by_isolate ON st_by_isolate.isolate_id=summary.isolate_id
            WHERE summary.derivation_id=({current})
        """,
        "v_pcr_sil_culture_marker_summary": f"""
            SELECT sample.sample_key, summary.oxa_detected, summary.kpc_detected,
                   summary.ndm_detected, summary.vim_detected,
                   summary.marker_codes_json, summary.organism_count,
                   summary.isolate_count
            FROM pcr_sil_culture_marker_summary AS summary
            JOIN pcr_sample AS sample ON sample.pcr_sample_id=summary.pcr_sample_id
            WHERE summary.derivation_id=({current})
        """,
        "v_pcr_sil_result_link": f"""
            SELECT sample.sample_key, link.run_type, link.canonical_target,
                   link.link_type, link.concordance, link.policy_version,
                   link.reason, link.details_json,
                   candidate.candidate_kind, candidate.candidate_status,
                   candidate.microorganism_code, candidate.marker_code,
                   candidate.days_difference, candidate.negative_count,
                   retrospective.evidence_status,
                   retrospective.matched_microorganism_code,
                   COALESCE(direct_sample.sample_number, evidence.sample_number, candidate_evidence.sample_number) AS result_sample,
                   COALESCE(direct_sample.received_at, direct_sample.entered_at,
                            evidence.received_at, evidence.entered_at,
                            candidate_evidence.received_at, candidate_evidence.entered_at) AS result_entry_date,
                   CASE WHEN direct_sample.sample_id IS NOT NULL THEN TRUE
                        WHEN link.link_type='DIRECT' THEN TRUE
                        WHEN link.link_type='PRIOR_COMPATIBLE' THEN FALSE
                        ELSE NULL END AS result_itself
            FROM pcr_sil_result_link AS link
            JOIN pcr_sample AS sample ON sample.pcr_sample_id=link.pcr_sample_id
            LEFT JOIN pcr_sil_culture_marker_summary AS direct_culture
              ON direct_culture.derivation_id=link.derivation_id
             AND direct_culture.pcr_sample_id=link.pcr_sample_id
             AND {direct_characterization_exists_sql("link")}
            LEFT JOIN microb_sample AS direct_sample
              ON direct_sample.sample_id=direct_culture.microb_sample_id
            LEFT JOIN pcr_sil_link_candidate AS candidate
              ON candidate.candidate_id=link.candidate_id
            LEFT JOIN pcr_sil_retrospective_evidence AS retrospective
              ON retrospective.derivation_id=link.derivation_id
             AND retrospective.pcr_sample_id=link.pcr_sample_id
            LEFT JOIN microb_sample AS evidence
              ON evidence.sample_id=retrospective.evidence_microb_sample_id
            LEFT JOIN microb_sample AS candidate_evidence
              ON candidate_evidence.sample_id=candidate.evidence_microb_sample_id
            WHERE link.derivation_id=({current})
        """,
        "v_downstream_sil_pcr_global": f"""
            WITH matched AS (
                SELECT DISTINCT validation.pcr_sample_id, validation.microb_sample_id
                FROM pcr_microb_well_validation AS validation
                LEFT JOIN pcr_sample_scope_gate AS gate
                  ON gate.derivation_id=validation.derivation_id
                 AND gate.pcr_sample_id=validation.pcr_sample_id
                WHERE validation.derivation_id=({current})
                  AND validation.validation_status='MATCH_ELIGIBLE'
                  AND COALESCE(gate.gate_status, 'NOT_APPLICABLE') <> 'LEGACY_SCREENING_BLOCKED'
            ), pcr_flags AS (
                SELECT selection.pcr_sample_id,
                       MAX(CASE WHEN selection.preferred_interpretation_id IS NULL THEN -1 WHEN interpretation.call='POSITIVE' THEN 1 ELSE 0 END) FILTER (WHERE selection.run_type='SCREENING' AND selection.canonical_target='KPC') AS kpc_screening,
                       MAX(CASE WHEN selection.preferred_interpretation_id IS NULL THEN -1 WHEN interpretation.call='POSITIVE' THEN 1 ELSE 0 END) FILTER (WHERE selection.run_type='SCREENING' AND selection.canonical_target='NDM') AS ndm_screening,
                       MAX(CASE WHEN selection.preferred_interpretation_id IS NULL THEN -1 WHEN interpretation.call='POSITIVE' THEN 1 ELSE 0 END) FILTER (WHERE selection.run_type='SCREENING' AND selection.canonical_target='VIM') AS vim_screening,
                       MAX(CASE WHEN selection.preferred_interpretation_id IS NULL THEN -1 WHEN interpretation.call='POSITIVE' THEN 1 ELSE 0 END) FILTER (WHERE selection.run_type='SCREENING' AND selection.canonical_target='OXA48') AS oxa48_screening,
                       MAX(CASE WHEN selection.preferred_interpretation_id IS NULL THEN -1 WHEN interpretation.call='POSITIVE' THEN 1 ELSE 0 END) FILTER (WHERE selection.run_type='SPECIFIC' AND selection.canonical_target='KPC') AS kpc_specific,
                       MAX(CASE WHEN selection.preferred_interpretation_id IS NULL THEN -1 WHEN interpretation.call='POSITIVE' THEN 1 ELSE 0 END) FILTER (WHERE selection.run_type='SPECIFIC' AND selection.canonical_target='NDM') AS ndm_specific,
                       MAX(CASE WHEN selection.preferred_interpretation_id IS NULL THEN -1 WHEN interpretation.call='POSITIVE' THEN 1 ELSE 0 END) FILTER (WHERE selection.run_type='SPECIFIC' AND selection.canonical_target='VIM') AS vim_specific,
                       MAX(CASE WHEN selection.preferred_interpretation_id IS NULL THEN -1 WHEN interpretation.call='POSITIVE' THEN 1 ELSE 0 END) FILTER (WHERE selection.run_type='SPECIFIC' AND selection.canonical_target='OXA48') AS oxa48_specific
                FROM pcr_result_selection AS selection
                LEFT JOIN pcr_interpretation AS interpretation ON interpretation.interpretation_id=selection.preferred_interpretation_id
                WHERE selection.derivation_id=({current})
                GROUP BY selection.pcr_sample_id
            ), fis_flags AS (
                SELECT pcr_sample_id,
                       COALESCE(oxa48_specific, 0) AS oxa48,
                       COALESCE(vim_specific, 0) AS vim,
                       COALESCE(kpc_specific, 0) AS kpc,
                       COALESCE(ndm_specific, 0) AS ndm
                FROM pcr_flags
            ), metric_ranked AS (
                SELECT metric.*
                FROM pcr_sample_target_metric AS metric
                JOIN pcr_result_selection AS selected
                  ON selected.derivation_id=metric.derivation_id
                 AND selected.pcr_sample_id=metric.pcr_sample_id
                 AND selected.run_type='SPECIFIC'
                 AND selected.canonical_target=metric.canonical_target
                 AND selected.preferred_interpretation_id=metric.target_interpretation_id
                WHERE metric.derivation_id=({current})
            ), retrospective_markers AS (
                SELECT retrospective.pcr_sample_id,
                       COALESCE(BOOL_OR(candidate.marker_code='OXA'), FALSE) AS oxa_detected,
                       COALESCE(BOOL_OR(candidate.marker_code='KPC'), FALSE) AS kpc_detected,
                       COALESCE(BOOL_OR(candidate.marker_code='NDM'), FALSE) AS ndm_detected,
                       COALESCE(BOOL_OR(candidate.marker_code='VIM'), FALSE) AS vim_detected,
                       evidence.sample_number AS result_sample,
                       COALESCE(evidence.received_at, evidence.entered_at) AS result_entry_date
                FROM pcr_sil_retrospective_evidence AS retrospective
                JOIN microb_sample AS evidence
                  ON evidence.sample_id=retrospective.evidence_microb_sample_id
                LEFT JOIN pcr_sil_link_candidate AS candidate
                  ON candidate.derivation_id=retrospective.derivation_id
                 AND candidate.pcr_sample_id=retrospective.pcr_sample_id
                 AND candidate.current_microb_sample_id=retrospective.current_microb_sample_id
                 AND candidate.evidence_microb_sample_id=retrospective.evidence_microb_sample_id
                 AND candidate.candidate_kind='PRIOR'
                 AND candidate.candidate_status='ACCEPTED'
                WHERE retrospective.derivation_id=({current})
                  AND retrospective.evidence_status='ACCEPTED'
                  AND NOT {direct_characterization_exists_sql('retrospective')}
                GROUP BY retrospective.pcr_sample_id, evidence.sample_number,
                         evidence.received_at, evidence.entered_at
            ), resolved AS (
                SELECT matched.pcr_sample_id,
                       CASE WHEN retrospective.pcr_sample_id IS NOT NULL THEN retrospective.oxa_detected
                            ELSE COALESCE(culture.oxa_detected, FALSE) END AS oxa_detected,
                       CASE WHEN retrospective.pcr_sample_id IS NOT NULL THEN retrospective.kpc_detected
                            ELSE COALESCE(culture.kpc_detected, FALSE) END AS kpc_detected,
                       CASE WHEN retrospective.pcr_sample_id IS NOT NULL THEN retrospective.ndm_detected
                            ELSE COALESCE(culture.ndm_detected, FALSE) END AS ndm_detected,
                       CASE WHEN retrospective.pcr_sample_id IS NOT NULL THEN retrospective.vim_detected
                            ELSE COALESCE(culture.vim_detected, FALSE) END AS vim_detected,
                       retrospective.pcr_sample_id IS NOT NULL AS has_prior,
                       retrospective.pcr_sample_id IS NULL
                         AND culture.pcr_sample_id IS NOT NULL AS has_direct,
                       COALESCE(retrospective.result_sample, current_sample.sample_number) AS result_sample,
                       COALESCE(retrospective.result_entry_date,
                                current_sample.received_at, current_sample.entered_at) AS result_entry_date
                FROM matched
                JOIN microb_sample AS current_sample ON current_sample.sample_id=matched.microb_sample_id
                LEFT JOIN pcr_sil_culture_marker_summary AS culture
                  ON culture.derivation_id=({current})
                 AND culture.pcr_sample_id=matched.pcr_sample_id
                 AND culture.microb_sample_id=matched.microb_sample_id
                LEFT JOIN retrospective_markers AS retrospective
                  ON retrospective.pcr_sample_id=matched.pcr_sample_id
            )
            SELECT patient.nhc, sample.sample_number AS sample,
                   patient_pseudonym.pseudonym AS patient_pseudonym,
                   sample_pseudonym.pseudonym AS sample_pseudonym,
                   sample.entered_at AS entry_date,
                   classification.culture_category AS result_interpretate,
                   COALESCE(resolved.oxa_detected, FALSE)::int AS oxa48,
                   COALESCE(resolved.vim_detected, FALSE)::int AS vim,
                   COALESCE(resolved.kpc_detected, FALSE)::int AS kpc,
                   COALESCE(resolved.ndm_detected, FALSE)::int AS ndm,
                   (COALESCE(resolved.oxa_detected, FALSE)::int::text || COALESCE(resolved.vim_detected, FALSE)::int::text || COALESCE(resolved.kpc_detected, FALSE)::int::text || COALESCE(resolved.ndm_detected, FALSE)::int::text) AS global_encoded,
                   resolved.result_sample, resolved.result_entry_date,
                   CASE WHEN resolved.has_prior THEN FALSE ELSE TRUE END AS result_itself,
                   origin.code AS origin_code, service.code AS service_code,
                   service.description AS desc_service, origin.description AS desc_proc,
                   patient.sex, patient.birth_date,
                   EXTRACT(YEAR FROM AGE(COALESCE(sample.entered_at, sample.received_at), patient.birth_date))::int AS age,
                   pcr_flags.kpc_screening, pcr_flags.ndm_screening,
                   pcr_flags.vim_screening, pcr_flags.oxa48_screening,
                   CASE WHEN -1 IN (pcr_flags.oxa48_screening, pcr_flags.vim_screening, pcr_flags.kpc_screening, pcr_flags.ndm_screening) THEN 'INVALID'
                        ELSE (COALESCE(pcr_flags.oxa48_screening, 0)::text || COALESCE(pcr_flags.vim_screening, 0)::text || COALESCE(pcr_flags.kpc_screening, 0)::text || COALESCE(pcr_flags.ndm_screening, 0)::text) END AS encode_carba_screening,
                   pcr_flags.vim_specific, pcr_flags.ndm_specific,
                   pcr_flags.oxa48_specific, pcr_flags.kpc_specific,
                   CASE WHEN -1 IN (pcr_flags.oxa48_specific, pcr_flags.vim_specific, pcr_flags.kpc_specific, pcr_flags.ndm_specific) THEN 'INVALID'
                        ELSE (COALESCE(pcr_flags.oxa48_specific, 0)::text || COALESCE(pcr_flags.vim_specific, 0)::text || COALESCE(pcr_flags.kpc_specific, 0)::text || COALESCE(pcr_flags.ndm_specific, 0)::text) END AS encode_carba_specific,
                   CASE WHEN fis.vim=1 THEN vim_metric.delta_cq END AS vim_log_delta,
                   CASE WHEN fis.ndm=1 THEN ndm_metric.delta_cq END AS ndm_log_delta,
                   CASE WHEN fis.oxa48=1 THEN oxa_metric.delta_cq END AS oxa48_log_delta,
                   CASE WHEN fis.kpc=1 THEN kpc_metric.delta_cq END AS kpc_log_delta,
                   result.pcr_category, result.warning_code,
                   CASE
                     WHEN pcr_flags.oxa48_specific IS NULL
                      AND pcr_flags.vim_specific IS NULL
                      AND pcr_flags.kpc_specific IS NULL
                      AND pcr_flags.ndm_specific IS NULL
                       THEN 'SPECIFIC_NOT_PERFORMED'
                     WHEN pcr_flags.oxa48_specific IS NOT NULL
                      AND pcr_flags.vim_specific IS NOT NULL
                      AND pcr_flags.kpc_specific IS NOT NULL
                      AND pcr_flags.ndm_specific IS NOT NULL
                       THEN 'SPECIFIC_COMPLETED'
                     ELSE 'SPECIFIC_PARTIAL'
                   END AS specific_status,
                   CASE
                     WHEN pcr_flags.oxa48_specific IN (0, 1)
                      AND pcr_flags.vim_specific IN (0, 1)
                      AND pcr_flags.kpc_specific IN (0, 1)
                      AND pcr_flags.ndm_specific IN (0, 1)
                       THEN 'SPECIFIC_VALID'
                     ELSE 'SPECIFIC_INVALID'
                   END AS specific_validation,
                   fis.oxa48 AS fis_oxa48,
                   fis.vim AS fis_vim,
                   fis.kpc AS fis_kpc,
                   fis.ndm AS fis_ndm,
                   CASE WHEN -1 IN (fis.oxa48, fis.vim, fis.kpc, fis.ndm) THEN 'INVALID'
                        ELSE (COALESCE(fis.oxa48, 0)::text || COALESCE(fis.vim, 0)::text || COALESCE(fis.kpc, 0)::text || COALESCE(fis.ndm, 0)::text) END AS encode_fis,
                   COALESCE(culture.oxa_detected, FALSE)::int AS direct_oxa48,
                   COALESCE(culture.vim_detected, FALSE)::int AS direct_vim,
                   COALESCE(culture.kpc_detected, FALSE)::int AS direct_kpc,
                   COALESCE(culture.ndm_detected, FALSE)::int AS direct_ndm,
                   (COALESCE(culture.oxa_detected, FALSE)::int::text || COALESCE(culture.vim_detected, FALSE)::int::text || COALESCE(culture.kpc_detected, FALSE)::int::text || COALESCE(culture.ndm_detected, FALSE)::int::text) AS direct_global_encoded
            FROM pcr_sample_result AS result
            JOIN matched ON matched.pcr_sample_id=result.pcr_sample_id
            JOIN microb_sample AS sample ON sample.sample_id=matched.microb_sample_id
            LEFT JOIN microb_patient AS patient ON patient.patient_id=sample.patient_id
            LEFT JOIN pseudonym_patient_registry AS patient_pseudonym
              ON patient_pseudonym.source_identifier=patient.nhc
            LEFT JOIN pseudonym_sample_registry AS sample_pseudonym
              ON sample_pseudonym.source_identifier=sample.sample_number
            LEFT JOIN ref_microb_catalog_entry AS origin ON origin.ref_microb_catalog_entry_id=sample.origin_id
            LEFT JOIN ref_microb_catalog_entry AS service ON service.ref_microb_catalog_entry_id=sample.service_id
            LEFT JOIN pcr_sil_culture_classification AS classification ON classification.derivation_id=result.derivation_id AND classification.microb_sample_id=sample.sample_id
            LEFT JOIN pcr_sil_culture_marker_summary AS culture ON culture.derivation_id=result.derivation_id AND culture.pcr_sample_id=result.pcr_sample_id AND culture.microb_sample_id=sample.sample_id
            LEFT JOIN pcr_flags ON pcr_flags.pcr_sample_id=result.pcr_sample_id
            LEFT JOIN fis_flags AS fis ON fis.pcr_sample_id=result.pcr_sample_id
            LEFT JOIN resolved ON resolved.pcr_sample_id=result.pcr_sample_id
            LEFT JOIN metric_ranked AS vim_metric ON vim_metric.pcr_sample_id=result.pcr_sample_id AND vim_metric.canonical_target='VIM'
            LEFT JOIN metric_ranked AS ndm_metric ON ndm_metric.pcr_sample_id=result.pcr_sample_id AND ndm_metric.canonical_target='NDM'
            LEFT JOIN metric_ranked AS oxa_metric ON oxa_metric.pcr_sample_id=result.pcr_sample_id AND oxa_metric.canonical_target='OXA48'
            LEFT JOIN metric_ranked AS kpc_metric ON kpc_metric.pcr_sample_id=result.pcr_sample_id AND kpc_metric.canonical_target='KPC'
            WHERE result.derivation_id=({current})
        """,
        "v_downstream_sil_pcr_organism": f"""
            WITH st_by_isolate AS (
                SELECT link.isolate_id,
                       CASE WHEN COUNT(DISTINCT record.st_value)=1
                            THEN MIN(record.st_value) END AS st,
                       COUNT(DISTINCT record.st_value)>1 AS has_conflict
                FROM microb_isolate_st_link AS link
                JOIN stg_st_record AS record
                  ON record.st_record_id=link.st_record_id
                GROUP BY link.isolate_id
            ), st_conflict_key AS (
                SELECT DISTINCT sample_number, microorganism_code
                FROM stg_st_record
                WHERE match_status='CONFLICTING_ST_VALUES'
            ), st_by_organism AS (
                SELECT isolate_summary.pcr_sample_id,
                       isolate_summary.microb_sample_id,
                       isolate_summary.microorganism_code,
                       CASE
                         WHEN BOOL_OR(COALESCE(st_by_isolate.has_conflict, FALSE))
                           OR BOOL_OR(st_conflict_key.sample_number IS NOT NULL)
                           THEN NULL
                         ELSE STRING_AGG(DISTINCT st_by_isolate.st, ', '
                                         ORDER BY st_by_isolate.st)
                       END AS st
                FROM pcr_sil_isolate_marker_summary AS isolate_summary
                JOIN microb_sample AS sample
                  ON sample.sample_id=isolate_summary.microb_sample_id
                LEFT JOIN st_by_isolate
                  ON st_by_isolate.isolate_id=isolate_summary.isolate_id
                LEFT JOIN st_conflict_key
                  ON st_conflict_key.sample_number=sample.sample_number
                 AND st_conflict_key.microorganism_code=isolate_summary.microorganism_code
                WHERE isolate_summary.derivation_id=({current})
                GROUP BY isolate_summary.pcr_sample_id,
                         isolate_summary.microb_sample_id,
                         isolate_summary.microorganism_code
            ), resolved_organism AS (
                SELECT organism.pcr_sample_id, organism.microb_sample_id AS current_microb_sample_id,
                       organism.microorganism_code,
                       CASE WHEN evidence.evidence_status='ACCEPTED'
                            THEN COALESCE(BOOL_OR(candidate.marker_code='OXA'), FALSE)
                            ELSE organism.oxa_detected END AS oxa_detected,
                       CASE WHEN evidence.evidence_status='ACCEPTED'
                            THEN COALESCE(BOOL_OR(candidate.marker_code='KPC'), FALSE)
                            ELSE organism.kpc_detected END AS kpc_detected,
                       CASE WHEN evidence.evidence_status='ACCEPTED'
                            THEN COALESCE(BOOL_OR(candidate.marker_code='NDM'), FALSE)
                            ELSE organism.ndm_detected END AS ndm_detected,
                       CASE WHEN evidence.evidence_status='ACCEPTED'
                            THEN COALESCE(BOOL_OR(candidate.marker_code='VIM'), FALSE)
                            ELSE organism.vim_detected END AS vim_detected
                FROM pcr_sil_organism_marker_summary AS organism
                LEFT JOIN pcr_sil_retrospective_evidence AS evidence
                  ON evidence.derivation_id=organism.derivation_id
                 AND evidence.pcr_sample_id=organism.pcr_sample_id
                 AND evidence.current_microb_sample_id=organism.microb_sample_id
                 AND NOT {direct_characterization_exists_sql("organism")}
                LEFT JOIN pcr_sil_link_candidate AS candidate
                  ON candidate.derivation_id=organism.derivation_id
                 AND candidate.pcr_sample_id=organism.pcr_sample_id
                 AND candidate.current_microb_sample_id=organism.microb_sample_id
                 AND candidate.microorganism_code=organism.microorganism_code
                 AND candidate.evidence_microb_sample_id=evidence.evidence_microb_sample_id
                 AND candidate.candidate_kind='PRIOR'
                 AND candidate.candidate_status='ACCEPTED'
                WHERE organism.derivation_id=({current})
                GROUP BY organism.pcr_sample_id, organism.microb_sample_id,
                         organism.microorganism_code, evidence.evidence_status,
                         organism.oxa_detected, organism.kpc_detected,
                         organism.ndm_detected, organism.vim_detected
            )
            SELECT summary_global.*, organism.microorganism_code AS microorg_code,
                   microorganism.description AS microorg_name,
                   COALESCE(resolved_organism.oxa_detected, FALSE)::int AS organism_oxa48,
                   COALESCE(resolved_organism.vim_detected, FALSE)::int AS organism_vim,
                   COALESCE(resolved_organism.kpc_detected, FALSE)::int AS organism_kpc,
                   COALESCE(resolved_organism.ndm_detected, FALSE)::int AS organism_ndm,
                   (COALESCE(resolved_organism.oxa_detected, FALSE)::int::text || COALESCE(resolved_organism.vim_detected, FALSE)::int::text || COALESCE(resolved_organism.kpc_detected, FALSE)::int::text || COALESCE(resolved_organism.ndm_detected, FALSE)::int::text) AS encoded_carba_org,
                   organism.isolate_count,
                   COALESCE(organism.oxa_detected, FALSE)::int AS direct_organism_oxa48,
                   COALESCE(organism.vim_detected, FALSE)::int AS direct_organism_vim,
                   COALESCE(organism.kpc_detected, FALSE)::int AS direct_organism_kpc,
                   COALESCE(organism.ndm_detected, FALSE)::int AS direct_organism_ndm,
                   st_by_organism.st
            FROM v_downstream_sil_pcr_global AS summary_global
            JOIN pcr_sample AS pcr_sample ON pcr_sample.sample_key=summary_global.sample
            LEFT JOIN pcr_sil_organism_marker_summary AS organism
              ON organism.derivation_id=({current}) AND organism.pcr_sample_id=pcr_sample.pcr_sample_id
            LEFT JOIN resolved_organism
              ON resolved_organism.pcr_sample_id=organism.pcr_sample_id
             AND resolved_organism.current_microb_sample_id=organism.microb_sample_id
             AND resolved_organism.microorganism_code=organism.microorganism_code
            LEFT JOIN st_by_organism
              ON st_by_organism.pcr_sample_id=organism.pcr_sample_id
             AND st_by_organism.microb_sample_id=organism.microb_sample_id
             AND st_by_organism.microorganism_code=organism.microorganism_code
            LEFT JOIN LATERAL (
                SELECT entry.description
                FROM ref_microb_catalog_entry AS entry
                JOIN ref_microb_catalog AS catalog
                  ON catalog.ref_microb_catalog_id=entry.ref_microb_catalog_id
                WHERE catalog.catalog_name='microorg'
                  AND entry.code=organism.microorganism_code
                ORDER BY catalog.is_current DESC, entry.ref_microb_catalog_entry_id DESC
                LIMIT 1
            ) AS microorganism ON TRUE
        """,
        "v_downstream_pcr_sil_target": f"""
            SELECT summary.sample_pseudonym, summary.patient_pseudonym,
                   target.canonical_target, target.marker_name,
                   target.fis_result, target.screening_result, target.specific_result,
                   summary.specific_status, summary.specific_validation,
                   target.sil_result, target.direct_sil_result,
                   target.log_delta, summary.pcr_category,
                   summary.result_interpretate AS culture_category,
                   summary.result_itself,
                   link.link_type, link.concordance AS sil_concordance,
                   link.policy_version AS sil_policy_version,
                   link.reason AS sil_link_reason
            FROM v_downstream_sil_pcr_global AS summary
            CROSS JOIN LATERAL (VALUES
                ('OXA48', 'OXA-48', summary.fis_oxa48, summary.oxa48_screening,
                 summary.oxa48_specific, summary.oxa48, summary.direct_oxa48,
                 summary.oxa48_log_delta),
                ('VIM', 'VIM', summary.fis_vim, summary.vim_screening,
                 summary.vim_specific, summary.vim, summary.direct_vim,
                 summary.vim_log_delta),
                ('KPC', 'KPC', summary.fis_kpc, summary.kpc_screening,
                 summary.kpc_specific, summary.kpc, summary.direct_kpc,
                 summary.kpc_log_delta),
                ('NDM', 'NDM', summary.fis_ndm, summary.ndm_screening,
                 summary.ndm_specific, summary.ndm, summary.direct_ndm,
                 summary.ndm_log_delta)
            ) AS target(canonical_target, marker_name, fis_result,
                        screening_result, specific_result, sil_result,
                        direct_sil_result, log_delta)
            LEFT JOIN pcr_sample AS sample
              ON sample.sample_key=summary.sample
            LEFT JOIN pcr_sil_result_link AS link
              ON link.derivation_id=({current})
             AND link.pcr_sample_id=sample.pcr_sample_id
             AND link.run_type='SPECIFIC'
             AND link.canonical_target=target.canonical_target
        """,
        "v_downstream_sil_organism_target": f"""
            SELECT organism.sample_pseudonym, organism.patient_pseudonym,
                   organism.microorg_code, organism.microorg_name,
                   target.canonical_target, target.marker_name,
                   target.organism_result, target.direct_organism_result,
                   organism.isolate_count, organism.st
            FROM v_downstream_sil_pcr_organism AS organism
            CROSS JOIN LATERAL (VALUES
                ('OXA48', 'OXA-48', organism.organism_oxa48, organism.direct_organism_oxa48),
                ('VIM', 'VIM', organism.organism_vim, organism.direct_organism_vim),
                ('KPC', 'KPC', organism.organism_kpc, organism.direct_organism_kpc),
                ('NDM', 'NDM', organism.organism_ndm, organism.direct_organism_ndm)
            ) AS target(canonical_target, marker_name,
                        organism_result, direct_organism_result)
            WHERE organism.microorg_code IS NOT NULL
        """,
        "v_downstream_sil_organism_profile": f"""
            SELECT sample_pseudonym, patient_pseudonym, microorg_code,
                   microorg_name, encoded_carba_org AS reconciled_profile,
                   (direct_organism_oxa48::text || direct_organism_vim::text ||
                    direct_organism_kpc::text || direct_organism_ndm::text) AS direct_profile,
                   isolate_count, st
            FROM v_downstream_sil_pcr_organism
            WHERE microorg_code IS NOT NULL
        """,
        "v_downstream_pcr_sil_sample_profile": f"""
            WITH organism_counts AS (
                SELECT sample_pseudonym,
                       COUNT(DISTINCT microorg_code) AS microorganism_count,
                       SUM(isolate_count) AS isolate_count,
                       COUNT(DISTINCT microorg_code) FILTER (
                           WHERE organism_oxa48=1 OR organism_vim=1
                              OR organism_kpc=1 OR organism_ndm=1
                       ) AS carrier_microorganism_count
                FROM v_downstream_sil_pcr_organism
                WHERE microorg_code IS NOT NULL
                GROUP BY sample_pseudonym
            )
            SELECT summary.sample_pseudonym, summary.patient_pseudonym,
                   summary.encode_fis AS fis_profile,
                   summary.global_encoded AS reconciled_culture_profile,
                   summary.direct_global_encoded AS direct_culture_profile,
                   summary.pcr_category, summary.result_interpretate AS culture_category,
                   COALESCE(counts.microorganism_count, 0) AS microorganism_count,
                   COALESCE(counts.carrier_microorganism_count, 0) AS carrier_microorganism_count,
                   COALESCE(counts.isolate_count, 0) AS isolate_count
            FROM v_downstream_sil_pcr_global AS summary
            LEFT JOIN organism_counts AS counts
              ON counts.sample_pseudonym=summary.sample_pseudonym
        """,
        "v_downstream_patient_episode": f"""
            SELECT derivation.episode_derivation_id, derivation.pcr_derivation_id,
                   derivation.algorithm_version, derivation.parameters_json,
                   episode.patient_episode_id, patient_map.pseudonym AS patient_pseudonym,
                   episode.episode_number, episode.started_at, episode.ended_at,
                   episode.sample_count, episode.start_reason
            FROM patient_episode_derivation AS derivation
            JOIN patient_episode AS episode
              ON episode.episode_derivation_id=derivation.episode_derivation_id
            JOIN microb_patient AS patient
              ON patient.patient_id=episode.patient_id
            LEFT JOIN pseudonym_patient_registry AS patient_map
              ON patient_map.source_identifier=patient.nhc
            WHERE derivation.status='SUCCESS' AND derivation.is_current=TRUE
        """,
        "v_downstream_patient_episode_sample": f"""
            SELECT membership.episode_derivation_id,
                   derivation.pcr_derivation_id,
                   patient_map.pseudonym AS patient_pseudonym,
                   sample_map.pseudonym AS sample_pseudonym,
                   episode.episode_number, membership.clinical_date,
                   membership.days_since_previous
            FROM patient_episode_sample AS membership
            JOIN patient_episode_derivation AS derivation
              ON derivation.episode_derivation_id=membership.episode_derivation_id
            JOIN patient_episode AS episode
              ON episode.patient_episode_id=membership.patient_episode_id
            JOIN microb_sample AS sample
              ON sample.sample_id=membership.microb_sample_id
            JOIN microb_patient AS patient
              ON patient.patient_id=sample.patient_id
            LEFT JOIN pseudonym_patient_registry AS patient_map
              ON patient_map.source_identifier=patient.nhc
            LEFT JOIN pseudonym_sample_registry AS sample_map
              ON sample_map.source_identifier=sample.sample_number
            WHERE derivation.status='SUCCESS' AND derivation.is_current=TRUE
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
                   details_json::text FROM pcr_target_qc
            WHERE qc_status='INVALID' AND derivation_id=({current})
            UNION ALL
            SELECT 'RESULT', CAST(pcr_sample_id AS TEXT) || ':' || run_type || ':' || canonical_target,
                   concordance FROM pcr_result_selection
            WHERE needs_review=TRUE AND derivation_id=({current})
            UNION ALL
            SELECT 'SAMPLE_SCOPE', CAST(sample.pcr_sample_id AS TEXT), gate.reason
            FROM pcr_sample_scope_gate AS gate
            JOIN pcr_sample AS sample ON sample.pcr_sample_id=gate.pcr_sample_id
            WHERE gate.derivation_id=({current})
              AND gate.gate_status='LEGACY_SCREENING_BLOCKED'
        """,
    }
    with engine.begin() as conn:
        conn.execute(text("DROP VIEW IF EXISTS v_pcr_current_result"))
        # Some public views build on preceding views (for example, the
        # organism-level downstream view uses the global downstream view).
        # Drop in reverse declaration order, then recreate in declaration
        # order, so refreshes remain explicit and never need DROP CASCADE.
        for name in reversed(views):
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
        for name, select_sql in views.items():
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
                SET is_current=TRUE
                WHERE derivation_id=(
                    SELECT MAX(derivation_id) FROM pcr_derivation_run
                    WHERE status='SUCCESS'
                )
                """
            )
        )
