"""Excel parser profile for isolate sequence type assignments."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..excel_profiles import ExcelEntityParser, FileProfile, SheetProfile
from ..types import ExcelParseStatus, ParsedExcelRowUpdate, RawExcelRow


ST_COLUMNS = {
    "sample_number": ("MUESTRA",),
    "microorganism_code": ("COD_MICROORGANISMO",),
    "microorganism_name": ("MICROORGANISMO",),
    "microorganism_name_raw": ("MICROORGANISMO_raw",),
    "st_value": ("ST",),
    "st_raw": ("ST_raw",),
}


def parse_st_row(
    row: RawExcelRow,
    header_index: Mapping[str, int],
    context: Mapping[str, Any],
) -> ParsedExcelRowUpdate:
    """Parse an ST row while keeping the source's raw name and ST values."""

    fields: dict[str, Any] = {}
    for field in ST_COLUMNS:
        index = header_index[field]
        fields[field] = row.values[index] if index < len(row.values) else None

    normalized = {
        field: _normalized_text(fields[field])
        for field in ("sample_number", "microorganism_code", "microorganism_name", "st_value")
    }
    missing = [
        field for field in ("sample_number", "microorganism_code", "st_value")
        if normalized[field] is None
    ]
    if missing:
        return ParsedExcelRowUpdate(
            row_id=row.row_id,
            payload_json=None,
            status=ExcelParseStatus.PARSE_ERROR,
            error=f"Missing required values: {', '.join(missing)}.",
        )

    payload = {
        "entity": "st",
        "file_kind": context["file_kind"],
        "sheet_kind": context["sheet_kind"],
        "fields": {
            **normalized,
            "microorganism_name_raw": _raw_text(fields["microorganism_name_raw"]),
            "st_raw": _raw_text(fields["st_raw"]),
        },
        "source": {
            "file_id": context["file_id"],
            "sheet_name": row.sheet_name,
            "row_no": row.row_no,
            "header_row_no": context["header_row_no"],
        },
    }
    return ParsedExcelRowUpdate(
        row_id=row.row_id,
        payload_json=json.dumps(
            payload, ensure_ascii=False, default=str, separators=(",", ":")
        ),
        status=ExcelParseStatus.PARSED_OK,
        error=None,
    )


def _normalized_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _raw_text(value: Any) -> str | None:
    return None if value is None else str(value)


ST_EXCEL_PARSER = ExcelEntityParser(
    entity_name="st",
    parser_name="st-excel",
    parser_version="0.1",
    file_profiles=(
        FileProfile(
            kind="sequence-types",
            filename_patterns=("*",),
            sheet_profiles=(
                SheetProfile(
                    kind="sequence-types",
                    sheet_name_patterns=("*",),
                    required_headers=tuple(ST_COLUMNS),
                    header_scan_rows=20,
                    column_aliases=ST_COLUMNS,
                    row_parser=parse_st_row,
                ),
            ),
        ),
    ),
)
