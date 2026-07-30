"""Regression tests for PCR Excel parsing edge cases."""

from __future__ import annotations

import json

from fis_data.etl.pcr.excel_parser import parse_quantification_cq_row
from fis_data.etl.types import ExcelParseStatus, RawExcelRow


def test_cq_parser_accepts_blank_sample_when_content_identifies_a_control() -> None:
    row = RawExcelRow(
        row_id=1,
        sheet_name="Sheet1",
        row_no=85,
        values=["G12", "SYBR", "Screening", "Neg Ctrl", "", None, 0, 0],
    )
    parsed = parse_quantification_cq_row(
        row,
        {
            "well": 0,
            "fluor": 1,
            "target": 2,
            "content": 3,
            "sample": 4,
            "cq": 5,
            "cq_mean": 6,
            "cq_std_dev": 7,
        },
        {
            "file_kind": "quantification_cq_results",
            "sheet_kind": "cq_results",
            "file_id": 1,
            "header_row_no": 1,
        },
    )

    assert parsed.status == ExcelParseStatus.PARSED_OK
    assert json.loads(parsed.payload_json or "{}")["fields"] == {
        "well": "G12",
        "fluor": "SYBR",
        "target": "Screening",
        "content": "Neg Ctrl",
        "sample": None,
        "cq": None,
        "cq_mean": 0.0,
        "cq_std_dev": 0.0,
    }
