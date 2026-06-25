"""PCR-specific Excel parser profiles and row parsers."""

from __future__ import annotations

import json
import re
from typing import Any

from .excel_profiles import ExcelEntityParser, FileProfile, SheetProfile
from .types import ExcelParseStatus, ParsedExcelRowUpdate, RawExcelRow

PCR_QUANTIFICATION_CQ_COLUMNS = {
    "well": ("well", "well position", "position"),
    "fluor": ("fluor", "fluorophore", "dye"),
    "target": ("target", "target name", "assay"),
    "content": ("content",),
    "sample": ("sample", "sample name", "sample id", "sample_id"),
    "cq": ("cq", "ct", "c t", "cycle threshold"),
    "cq_mean": ("cq mean", "ct mean"),
    "cq_std_dev": ("cq std dev", "cq std. dev.", "ct std dev", "ct std. dev."),
}


def parse_quantification_cq_row(
    row: RawExcelRow,
    header_index: dict[str, int],
    context: dict[str, Any],
) -> ParsedExcelRowUpdate:
    """Parse one PCR Quantification Cq Results data row."""

    try:
        well = _required_text(row, header_index, "well")
        fluor = _optional_text(row, header_index, "fluor")
        target = _required_text(row, header_index, "target")
        content = _optional_text(row, header_index, "content")
        sample = _required_text(row, header_index, "sample")
        cq = _optional_float(row, header_index, "cq")
        cq_mean = _optional_float(row, header_index, "cq_mean")
        cq_std_dev = _optional_float(row, header_index, "cq_std_dev")
    except ValueError as exc:
        return ParsedExcelRowUpdate(
            row_id=row.row_id,
            payload_json=None,
            status=ExcelParseStatus.PARSE_ERROR,
            error=str(exc),
        )

    payload = {
        "entity": "pcr",
        "file_kind": context["file_kind"],
        "sheet_kind": context["sheet_kind"],
        "fields": {
            "well": well,
            "fluor": fluor,
            "target": target,
            "content": content,
            "sample": sample,
            "cq": cq,
            "cq_mean": cq_mean,
            "cq_std_dev": cq_std_dev,
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
            payload,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ),
        status=ExcelParseStatus.PARSED_OK,
        error=None,
    )


def parse_amplification_sybr_row(
    row: RawExcelRow,
    header_index: dict[str, int],
    context: dict[str, Any],
) -> ParsedExcelRowUpdate:
    """Parse one PCR Quantification Amplification SYBR cycle row."""

    return _parse_wide_rfu_row(
        row=row,
        header_index=header_index,
        context=context,
        axis_field="cycle",
        axis_parser=_required_int,
    )


def parse_melt_curve_sybr_row(
    row: RawExcelRow,
    header_index: dict[str, int],
    context: dict[str, Any],
) -> ParsedExcelRowUpdate:
    """Parse one PCR Melt Curve SYBR temperature row."""

    return _parse_wide_rfu_row(
        row=row,
        header_index=header_index,
        context=context,
        axis_field="temperature",
        axis_parser=_required_float,
    )


def _parse_wide_rfu_row(
    *,
    row: RawExcelRow,
    header_index: dict[str, int],
    context: dict[str, Any],
    axis_field: str,
    axis_parser,
) -> ParsedExcelRowUpdate:
    """Parse one wide RFU row keyed by a cycle-like axis and well columns."""

    try:
        axis_value = axis_parser(row, header_index, axis_field)
        rfu_by_well = _rfu_by_well(row, header_index)
    except ValueError as exc:
        return ParsedExcelRowUpdate(
            row_id=row.row_id,
            payload_json=None,
            status=ExcelParseStatus.PARSE_ERROR,
            error=str(exc),
        )

    payload = {
        "entity": "pcr",
        "file_kind": context["file_kind"],
        "sheet_kind": context["sheet_kind"],
        "fields": {
            axis_field: axis_value,
            "rfu_by_well": rfu_by_well,
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
            payload,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ),
        status=ExcelParseStatus.PARSED_OK,
        error=None,
    )


def _required_text(
    row: RawExcelRow,
    header_index: dict[str, int],
    field: str,
) -> str:
    value = _value_for(row, header_index, field)
    if value is None or str(value).strip() == "":
        raise ValueError(f"Missing required value for {field}.")
    return str(value).strip()


def _required_int(
    row: RawExcelRow,
    header_index: dict[str, int],
    field: str,
) -> int:
    value = _value_for(row, header_index, field)
    if value is None or str(value).strip() == "":
        raise ValueError(f"Missing required value for {field}.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer value for {field}: {value!r}.") from exc
    if not parsed.is_integer():
        raise ValueError(f"Invalid integer value for {field}: {value!r}.")
    return int(parsed)


def _optional_text(
    row: RawExcelRow,
    header_index: dict[str, int],
    field: str,
) -> str | None:
    value = _value_for(row, header_index, field)
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _optional_float(
    row: RawExcelRow,
    header_index: dict[str, int],
    field: str,
) -> float | None:
    value = _value_for(row, header_index, field)
    if value is None or str(value).strip() == "":
        return None
    return _parse_float(value=value, field=field)


def _required_float(
    row: RawExcelRow,
    header_index: dict[str, int],
    field: str,
) -> float:
    value = _value_for(row, header_index, field)
    if value is None or str(value).strip() == "":
        raise ValueError(f"Missing required value for {field}.")
    return _parse_float(value=value, field=field)


def _parse_float(*, value: object, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric value for {field}: {value!r}.") from exc


def _rfu_by_well(
    row: RawExcelRow,
    header_index: dict[str, int],
) -> dict[str, float | None]:
    rfu_by_well: dict[str, float | None] = {}
    for header, index in sorted(header_index.items(), key=lambda item: item[1]):
        if not _is_well_id(header):
            continue

        value = row.values[index] if index < len(row.values) else None
        well = header.upper()
        if value is None or str(value).strip() == "":
            rfu_by_well[well] = None
            continue
        try:
            rfu_by_well[well] = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid RFU value for well {well}: {value!r}.") from exc

    if not rfu_by_well:
        raise ValueError("No well RFU columns found.")
    return rfu_by_well


def _is_well_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-h][0-9]{1,2}", value.casefold()))


def _value_for(
    row: RawExcelRow,
    header_index: dict[str, int],
    field: str,
) -> object | None:
    index = header_index[field]
    if index >= len(row.values):
        return None
    return row.values[index]


def parse_run_information_row(
    row: RawExcelRow,
    header_index: dict[str, int],
    context: dict[str, Any],
) -> ParsedExcelRowUpdate:
    """Parse one Run Information key/value row."""

    key = _optional_text(row, header_index, "key")
    value = _value_for(row, header_index, "value")
    if key is None:
        return ParsedExcelRowUpdate(
            row_id=row.row_id,
            payload_json=None,
            status=ExcelParseStatus.SKIPPED_METADATA,
            error=None,
        )

    payload = {
        "entity": "pcr",
        "file_kind": context["file_kind"],
        "sheet_kind": context["sheet_kind"],
        "fields": {
            "key": key,
            "value": value,
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
            payload,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ),
        status=ExcelParseStatus.PARSED_OK,
        error=None,
    )


QUANTIFICATION_CQ_RESULTS_SHEET = SheetProfile(
    kind="cq_results",
    sheet_name_patterns=("0", "*sheet1*", "*results*", "*cq*"),
    required_headers=(
        "well",
        "fluor",
        "target",
        "content",
        "sample",
        "cq",
        "cq_mean",
        "cq_std_dev",
    ),
    header_scan_rows=20,
    column_aliases=PCR_QUANTIFICATION_CQ_COLUMNS,
    row_parser=parse_quantification_cq_row,
)

AMPLIFICATION_SYBR_SHEET = SheetProfile(
    kind="amplification_sybr",
    sheet_name_patterns=("sybr",),
    required_headers=("cycle",),
    header_scan_rows=20,
    column_aliases={"cycle": ("cycle",)},
    row_parser=parse_amplification_sybr_row,
)

MELT_CURVE_SYBR_SHEET = SheetProfile(
    kind="melt_curve_sybr",
    sheet_name_patterns=("sybr",),
    required_headers=("temperature",),
    header_scan_rows=20,
    column_aliases={"temperature": ("temperature",)},
    row_parser=parse_melt_curve_sybr_row,
)

MELT_CURVE_DERIVATIVE_SYBR_SHEET = SheetProfile(
    kind="melt_curve_derivative_sybr",
    sheet_name_patterns=("sybr",),
    required_headers=("temperature",),
    header_scan_rows=20,
    column_aliases={"temperature": ("temperature",)},
    row_parser=parse_melt_curve_sybr_row,
)

RUN_INFORMATION_SHEET = SheetProfile(
    kind="run_information",
    sheet_name_patterns=("run information",),
    required_headers=(),
    header_scan_rows=1,
    headerless_columns=("key", "value"),
    column_aliases={"key": ("key",), "value": ("value",)},
    row_parser=parse_run_information_row,
)

PCR_EXCEL_PARSER = ExcelEntityParser(
    entity_name="pcr",
    parser_name="pcr-excel",
    parser_version="0.2",
    file_profiles=(
        FileProfile(
            kind="quantification_cq_results",
            filename_patterns=(),
            filename_regexes=(r".*Quantification Cq Results\.xlsx$",),
            sheet_profiles=(QUANTIFICATION_CQ_RESULTS_SHEET, RUN_INFORMATION_SHEET),
        ),
        FileProfile(
            kind="quantification_amplification_results",
            filename_patterns=(),
            filename_regexes=(r".*Quantification Amplification Results\.xlsx$",),
            sheet_profiles=(AMPLIFICATION_SYBR_SHEET, RUN_INFORMATION_SHEET),
        ),
        FileProfile(
            kind="melt_curve_rfu_results",
            filename_patterns=(),
            filename_regexes=(r".*Melt Curve RFU Results\.xlsx$",),
            sheet_profiles=(MELT_CURVE_SYBR_SHEET, RUN_INFORMATION_SHEET),
        ),
        FileProfile(
            kind="melt_curve_derivative_results",
            filename_patterns=(),
            filename_regexes=(r".*Melt Curve Derivative Results\.xlsx$",),
            sheet_profiles=(
                MELT_CURVE_DERIVATIVE_SYBR_SHEET,
                RUN_INFORMATION_SHEET,
            ),
        ),
    ),
)
