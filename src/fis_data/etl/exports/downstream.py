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

FORMATS: Final = {ANALYTICAL_CORE_TABLES.name: ANALYTICAL_CORE_TABLES}


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
                worksheet.append(list(row))
                count += 1
            counts[definition.name] = count
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return counts


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
    parameters = json.loads(str(row["parameters_json"]))
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
