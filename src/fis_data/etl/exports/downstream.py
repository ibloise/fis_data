"""Registered downstream export frames.

New downstream layouts add a :class:`ExportFormat` to ``FORMATS`` rather than
changing the common CLI, workbook writer, or existing analytical exports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ...json_utils import loads_json

@dataclass(frozen=True)
class ExportSheet:
    """One worksheet backed by a read-only analytical query."""

    name: str
    query: str


@dataclass(frozen=True)
class ExportFormat:
    """A named, versionable downstream layout."""

    name: str
    description: str
    sheets: tuple[ExportSheet, ...]


ANALYTICAL_CORE_TABLES: Final = ExportFormat(
    name="analytical-core-tables",
    description="Normalized PCR, culture, organism, metric, and link evidence tables.",
    sheets=(
        ExportSheet(
            "SIL PCR global",
            "SELECT * FROM v_downstream_sil_pcr_global ORDER BY sample",
        ),
        ExportSheet(
            "SIL PCR organisms",
            "SELECT * FROM v_downstream_sil_pcr_organism "
            "ORDER BY sample, microorg_code",
        ),
        ExportSheet("PCR samples support", "SELECT * FROM v_pcr_sample_result ORDER BY sample_key"),
        ExportSheet(
            "PCR target metrics",
            "SELECT * FROM v_pcr_sample_target_metric "
            "ORDER BY sample_key, pcr_run_id, canonical_target",
        ),
        ExportSheet(
            "Culture global support",
            "SELECT * FROM v_pcr_sil_culture_marker_summary ORDER BY sample_key",
        ),
        ExportSheet(
            "Culture organisms support",
            "SELECT * FROM v_pcr_sil_organism_marker_summary "
            "ORDER BY sample_key, microorganism_code",
        ),
        ExportSheet(
            "PCR SIL links",
            "SELECT * FROM v_pcr_sil_result_link "
            "ORDER BY sample_key, run_type, canonical_target",
        ),
    ),
)


PCR_QC_ERRORS: Final = ExportFormat(
    name="pcr-qc-errors",
    description=(
        "PCR runs with invalid QC, their invalid targets and controls, and "
        "unpromoted affected sample-targets with the other run context."
    ),
    sheets=(
        ExportSheet(
            "QC runs",
            """
            SELECT qc.*, analysis.run_type, analysis.run_class,
                   analysis.run_started, analysis.review_reason
            FROM v_pcr_run_qc AS qc
            JOIN pcr_run_analysis AS analysis
              ON analysis.derivation_id=qc.derivation_id
             AND analysis.pcr_run_id=qc.pcr_run_id
            WHERE qc.qc_status IN ('INVALID', 'PARTIALLY_INVALID')
            ORDER BY analysis.run_started DESC, qc.run_name
            """,
        ),
        ExportSheet(
            "QC targets",
            """
            WITH real_samples AS (
                SELECT DISTINCT qc.derivation_id, qc.pcr_run_id,
                       qc.canonical_target, classification.pcr_sample_id
                FROM pcr_target_qc AS qc
                JOIN pcr_derivation_run AS derivation
                  ON derivation.derivation_id=qc.derivation_id
                 AND derivation.status='SUCCESS' AND derivation.is_current=TRUE
                JOIN pcr_well AS well ON well.pcr_run_id=qc.pcr_run_id
                JOIN pcr_well_classification AS classification
                  ON classification.derivation_id=qc.derivation_id
                 AND classification.pcr_well_id=well.pcr_well_id
                 AND classification.well_role='REAL_SAMPLE'
                 AND classification.pcr_sample_id IS NOT NULL
                WHERE qc.is_valid=FALSE
            ), promotion_coverage AS (
                SELECT samples.derivation_id, samples.pcr_run_id,
                       samples.canonical_target,
                       COUNT(*) AS real_sample_count,
                       COUNT(*) FILTER (
                           WHERE selection.preferred_interpretation_id IS NOT NULL
                       ) AS promoted_sample_count
                FROM real_samples AS samples
                JOIN pcr_run_analysis AS analysis
                  ON analysis.derivation_id=samples.derivation_id
                 AND analysis.pcr_run_id=samples.pcr_run_id
                LEFT JOIN pcr_result_selection AS selection
                  ON selection.derivation_id=samples.derivation_id
                 AND selection.pcr_sample_id=samples.pcr_sample_id
                 AND selection.run_type=analysis.run_type
                 AND selection.canonical_target=samples.canonical_target
                GROUP BY samples.derivation_id, samples.pcr_run_id,
                         samples.canonical_target
            )
            SELECT qc.pcr_run_id, run.run_name, analysis.run_type,
                   analysis.run_class, analysis.run_started,
                   qc.canonical_target, qc.qc_status, qc.is_valid,
                   qc.positive_controls, qc.negative_controls,
                   qc.warnings_json, qc.details_json,
                   CASE
                       WHEN COALESCE(coverage.real_sample_count, 0)=0
                           THEN 'NO_REAL_SAMPLES'
                       WHEN coverage.promoted_sample_count=coverage.real_sample_count
                           THEN 'ALL_PROMOTED'
                       WHEN coverage.promoted_sample_count=0 THEN 'NONE_PROMOTED'
                       ELSE 'PARTIAL'
                   END AS promotion_coverage_status,
                   ('{"real_sample_count":' ||
                    CAST(COALESCE(coverage.real_sample_count, 0) AS TEXT) ||
                    ',"promoted_sample_count":' ||
                    CAST(COALESCE(coverage.promoted_sample_count, 0) AS TEXT) ||
                    ',"unpromoted_sample_count":' ||
                    CAST(
                        COALESCE(coverage.real_sample_count, 0)
                        - COALESCE(coverage.promoted_sample_count, 0) AS TEXT
                    ) || '}') AS promotion_coverage_json
            FROM pcr_target_qc AS qc
            JOIN pcr_derivation_run AS derivation
              ON derivation.derivation_id=qc.derivation_id
             AND derivation.status='SUCCESS' AND derivation.is_current=TRUE
            JOIN pcr_run AS run ON run.pcr_run_id=qc.pcr_run_id
            JOIN pcr_run_analysis AS analysis
              ON analysis.derivation_id=qc.derivation_id
             AND analysis.pcr_run_id=qc.pcr_run_id
            LEFT JOIN promotion_coverage AS coverage
              ON coverage.derivation_id=qc.derivation_id
             AND coverage.pcr_run_id=qc.pcr_run_id
             AND coverage.canonical_target=qc.canonical_target
            WHERE qc.is_valid=FALSE
            ORDER BY analysis.run_started DESC, run.run_name, qc.canonical_target
            """,
        ),
        ExportSheet(
            "Affected sample targets",
            """
            SELECT failed.*,
                   CASE failed.semantic_type
                       WHEN 'screening' THEN 'specific'
                       ELSE 'screening'
                   END AS other_context,
                   CASE
                       WHEN other_selection.pcr_sample_id IS NULL THEN 'NOT_PERFORMED'
                       WHEN other_selection.preferred_interpretation_id IS NULL
                           THEN 'NOT_PROMOTABLE'
                       ELSE 'PROMOTED'
                   END AS other_context_status,
                   other_result.call AS other_context_call,
                   other_result.cq AS other_context_cq,
                   other_result.peak_temperature AS other_context_peak_temperature,
                   other_result.pcr_run_id AS other_context_pcr_run_id,
                   other_run.run_name AS other_context_run_name,
                   other_analysis.run_started AS other_context_run_started,
                   other_result.concordance AS other_context_concordance,
                   other_result.needs_review AS other_context_needs_review,
                   other_result.has_override AS other_context_has_override
            FROM v_pcr_unpromoted_sample_errors AS failed
            JOIN v_pcr_run_qc AS failed_qc
              ON failed_qc.pcr_run_id=failed.pcr_run_id
             AND failed_qc.qc_status IN ('INVALID', 'PARTIALLY_INVALID')
            LEFT JOIN pcr_result_selection AS other_selection
              ON other_selection.derivation_id=failed_qc.derivation_id
             AND other_selection.pcr_sample_id=failed.pcr_sample_id
             AND other_selection.canonical_target=failed.canonical_target
             AND other_selection.run_type=CASE failed.semantic_type
                 WHEN 'screening' THEN 'SPECIFIC'
                 ELSE 'SCREENING'
             END
            LEFT JOIN v_pcr_effective_result AS other_result
              ON other_result.sample_key=failed.sample_key
             AND other_result.canonical_target=failed.canonical_target
             AND other_result.run_type=CASE failed.semantic_type
                 WHEN 'screening' THEN 'SPECIFIC'
                 ELSE 'SCREENING'
             END
            LEFT JOIN pcr_run AS other_run
              ON other_run.pcr_run_id=other_result.pcr_run_id
            LEFT JOIN pcr_run_analysis AS other_analysis
              ON other_analysis.derivation_id=failed_qc.derivation_id
             AND other_analysis.pcr_run_id=other_result.pcr_run_id
            ORDER BY failed.run_started DESC, failed.run_name,
                     failed.sample_key, failed.canonical_target
            """,
        ),
        ExportSheet(
            "Affected raw results",
            """
            WITH reference_16s AS (
                SELECT classification.derivation_id, classification.pcr_sample_id,
                       interpretation.pcr_run_id, interpretation.cq,
                       interpretation.call, interpretation.error,
                       qc.qc_status, qc.is_valid,
                       ROW_NUMBER() OVER (
                           PARTITION BY classification.derivation_id,
                                        classification.pcr_sample_id,
                                        interpretation.pcr_run_id
                           ORDER BY interpretation.pcr_well_id,
                                    interpretation.interpretation_id
                       ) AS position
                FROM pcr_interpretation AS interpretation
                JOIN pcr_well_classification AS classification
                  ON classification.derivation_id=interpretation.derivation_id
                 AND classification.pcr_well_id=interpretation.pcr_well_id
                 AND classification.well_role='REAL_SAMPLE'
                LEFT JOIN pcr_target_qc AS qc
                  ON qc.derivation_id=interpretation.derivation_id
                 AND qc.pcr_run_id=interpretation.pcr_run_id
                 AND qc.canonical_target=interpretation.canonical_target
                WHERE interpretation.canonical_target='16S'
            )
            SELECT failed.sample_key, failed.semantic_type,
                   failed.canonical_target, failed.pcr_run_id, failed.run_name,
                   failed.run_started, well.well, well.sample_name,
                   interpretation.source_target,
                   interpretation.call AS raw_call,
                   interpretation.cq AS raw_cq,
                   interpretation.peak_temperature AS raw_peak_temperature,
                   interpretation.peak_deriv_rfu AS raw_peak_deriv_rfu,
                   interpretation.peaks_json AS raw_peaks_json,
                   interpretation.metrics_json AS raw_metrics_json,
                   interpretation.error AS raw_interpretation_error,
                   reference.cq AS raw_reference_16s_cq,
                   reference.call AS raw_reference_16s_call,
                   reference.error AS raw_reference_16s_error,
                   reference.qc_status AS raw_reference_16s_qc_status,
                   reference.is_valid AS raw_reference_16s_qc_valid,
                   CASE
                       WHEN failed.semantic_type='specific'
                        AND interpretation.cq IS NOT NULL
                        AND reference.cq IS NOT NULL
                           THEN (interpretation.cq-reference.cq) * LOG(2.0)
                   END AS raw_delta_cq,
                   CASE
                       WHEN failed.semantic_type <> 'specific' THEN 'NOT_APPLICABLE'
                       WHEN interpretation.cq IS NULL THEN 'TARGET_CQ_MISSING'
                       WHEN reference.cq IS NULL THEN 'REFERENCE_16S_MISSING'
                       WHEN COALESCE(reference.is_valid, FALSE)=FALSE
                           THEN 'RAW_REFERENCE_16S_QC_INVALID'
                       ELSE 'AVAILABLE'
                   END AS raw_delta_cq_status
            FROM v_pcr_unpromoted_sample_errors AS failed
            JOIN v_pcr_run_qc AS failed_qc
              ON failed_qc.pcr_run_id=failed.pcr_run_id
             AND failed_qc.qc_status IN ('INVALID', 'PARTIALLY_INVALID')
            JOIN pcr_well_classification AS classification
              ON classification.derivation_id=failed_qc.derivation_id
             AND classification.pcr_sample_id=failed.pcr_sample_id
             AND classification.well_role='REAL_SAMPLE'
            JOIN pcr_interpretation AS interpretation
              ON interpretation.derivation_id=failed_qc.derivation_id
             AND interpretation.pcr_run_id=failed.pcr_run_id
             AND interpretation.pcr_well_id=classification.pcr_well_id
             AND interpretation.canonical_target=failed.canonical_target
            JOIN pcr_well AS well
              ON well.pcr_well_id=interpretation.pcr_well_id
            LEFT JOIN reference_16s AS reference
              ON reference.derivation_id=failed_qc.derivation_id
             AND reference.pcr_sample_id=failed.pcr_sample_id
             AND reference.pcr_run_id=failed.pcr_run_id
             AND reference.position=1
            ORDER BY failed.run_started DESC, failed.run_name,
                     failed.sample_key, failed.canonical_target, well.well
            """,
        ),
        ExportSheet(
            "Promoted after QC failure",
            """
            SELECT sample.sample_key,
                   source_analysis.run_type, source.canonical_target,
                   source.pcr_run_id AS source_pcr_run_id,
                   source_run.run_name AS source_run_name,
                   source_analysis.run_started AS source_run_started,
                   source.call AS source_raw_call,
                   source.cq AS source_raw_cq,
                   source.peak_temperature AS source_raw_peak_temperature,
                   source.error AS source_raw_interpretation_error,
                   source_qc.qc_status AS source_target_qc_status,
                   source_qc.is_valid AS source_target_qc_valid,
                   promoted.pcr_run_id AS promoted_pcr_run_id,
                   promoted_run.run_name AS promoted_run_name,
                   promoted_analysis.run_started AS promoted_run_started,
                   promoted.call AS promoted_call,
                   promoted.cq AS promoted_cq,
                   promoted.peak_temperature AS promoted_peak_temperature,
                   selection.relation_kind AS promotion_relation_kind,
                   selection.concordance AS promotion_concordance,
                   selection.needs_review AS promotion_needs_review
            FROM pcr_interpretation AS source
            JOIN pcr_well_classification AS classification
              ON classification.derivation_id=source.derivation_id
             AND classification.pcr_well_id=source.pcr_well_id
             AND classification.well_role='REAL_SAMPLE'
            JOIN pcr_sample AS sample
              ON sample.pcr_sample_id=classification.pcr_sample_id
            JOIN pcr_run_analysis AS source_analysis
              ON source_analysis.derivation_id=source.derivation_id
             AND source_analysis.pcr_run_id=source.pcr_run_id
            JOIN pcr_run AS source_run ON source_run.pcr_run_id=source.pcr_run_id
            JOIN v_pcr_run_qc AS source_run_qc
              ON source_run_qc.derivation_id=source.derivation_id
             AND source_run_qc.pcr_run_id=source.pcr_run_id
             AND source_run_qc.qc_status IN ('INVALID', 'PARTIALLY_INVALID')
            LEFT JOIN pcr_target_qc AS source_qc
              ON source_qc.derivation_id=source.derivation_id
             AND source_qc.pcr_run_id=source.pcr_run_id
             AND source_qc.canonical_target=source.canonical_target
            LEFT JOIN pcr_run_scope AS source_scope
              ON source_scope.pcr_run_id=source.pcr_run_id
            JOIN pcr_result_selection AS selection
              ON selection.derivation_id=source.derivation_id
             AND selection.pcr_sample_id=classification.pcr_sample_id
             AND selection.run_type=source_analysis.run_type
             AND selection.canonical_target=source.canonical_target
             AND selection.preferred_interpretation_id IS NOT NULL
            JOIN pcr_interpretation AS promoted
              ON promoted.interpretation_id=selection.preferred_interpretation_id
             AND promoted.pcr_run_id <> source.pcr_run_id
            JOIN pcr_run AS promoted_run
              ON promoted_run.pcr_run_id=promoted.pcr_run_id
            JOIN pcr_run_analysis AS promoted_analysis
              ON promoted_analysis.derivation_id=source.derivation_id
             AND promoted_analysis.pcr_run_id=promoted.pcr_run_id
            WHERE (
                  source.call NOT IN ('POSITIVE', 'NEGATIVE')
               OR COALESCE(source_qc.is_valid, FALSE)=FALSE
               OR source.error IS NOT NULL
            )
              AND COALESCE(source_scope.analytical_scope, 'ROUTINE') != 'EXPERIMENTAL'
            ORDER BY source_analysis.run_started DESC, source_run.run_name,
                     sample.sample_key, source.canonical_target,
                     promoted_analysis.run_started DESC
            """,
        ),
        ExportSheet(
            "QC control evidence",
            """
            SELECT evidence.*
            FROM v_pcr_target_qc_evidence AS evidence
            WHERE evidence.is_valid=FALSE
            ORDER BY evidence.pcr_run_id, evidence.canonical_target,
                     evidence.well_role, evidence.well
            """,
        ),
    ),
)


FORMATS: Final = {
    ANALYTICAL_CORE_TABLES.name: ANALYTICAL_CORE_TABLES,
    PCR_QC_ERRORS.name: PCR_QC_ERRORS,
}


def format_names() -> tuple[str, ...]:
    """Return registered downstream export formats in stable order."""

    return tuple(FORMATS)


def export_downstream(engine: Engine, *, format_name: str, output_path: Path) -> dict[str, int]:
    """Write one registered export frame to an XLSX workbook.

    Callers must prepare the managed schema/views first; this function only
    reads the selected analytical frame.
    """

    try:
        export_format = FORMATS[format_name]
    except KeyError as exc:
        raise ValueError(f"Unknown downstream export format: {format_name}") from exc

    workbook = Workbook(write_only=True)
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        _write_report_sheet(workbook, conn, export_format)
        for definition in export_format.sheets:
            worksheet = workbook.create_sheet(title=definition.name)
            result = conn.execute(text(definition.query))
            headers = list(result.keys())
            worksheet.append(headers)
            count = 0
            for row in result:
                worksheet.append([_xlsx_value(value) for value in row])
                count += 1
            counts[definition.name] = count
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return counts


def _xlsx_value(value: object) -> object:
    """Convert native JSON values returned by PostgreSQL into Excel cells."""

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _write_report_sheet(workbook: Workbook, conn, export_format: ExportFormat) -> None:
    """Write provenance and the versioned melting/link policies for the export."""

    worksheet = workbook.create_sheet(title="Report")
    row = conn.execute(
        text(
            """
            SELECT derivation_id, algorithm_version, rule_version, parameters_json,
                   started_at, finished_at
            FROM pcr_derivation_run
            WHERE status='SUCCESS' AND is_current=TRUE
            """
        )
    ).mappings().one_or_none()
    worksheet.append(["field", "value"])
    worksheet.append(["generated_at_utc", datetime.now(timezone.utc).isoformat()])
    worksheet.append(["export_format", export_format.name])
    worksheet.append(["export_description", export_format.description])
    if row is None:
        worksheet.append(["derivation_status", "No current successful derivation."])
        return
    parameters = loads_json(row["parameters_json"])
    worksheet.append(["derivation_id", row["derivation_id"]])
    worksheet.append(["algorithm_version", row["algorithm_version"]])
    worksheet.append(["rule_version", row["rule_version"]])
    worksheet.append(["started_at", row["started_at"]])
    worksheet.append(["finished_at", row["finished_at"]])
    worksheet.append(["melting_policy", json.dumps(
        {key: value for key, value in parameters.items() if key != "pcr_sil_linking"},
        ensure_ascii=False, sort_keys=True,
    )])
    worksheet.append(["pcr_sil_linking_policy", json.dumps(
        parameters.get("pcr_sil_linking", {}), ensure_ascii=False, sort_keys=True,
    )])
