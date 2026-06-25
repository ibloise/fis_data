"""Shared ETL types for parsing raw records."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ParseStatus(StrEnum):
    """Parsing status values for raw text rows."""

    RAW_ONLY = "RAW_ONLY"
    PARSED_OK = "PARSED_OK"
    PARSE_ERROR = "PARSE_ERROR"


class ExcelParseStatus(StrEnum):
    """Parsing status values for raw Excel rows and Excel parse audits."""

    RAW_ONLY = "RAW_ONLY"
    PARSED_OK = "PARSED_OK"
    PARSE_ERROR = "PARSE_ERROR"
    SKIPPED_FILE_PROFILE = "SKIPPED_FILE_PROFILE"
    SKIPPED_SHEET_PROFILE = "SKIPPED_SHEET_PROFILE"
    SKIPPED_HEADER = "SKIPPED_HEADER"
    SKIPPED_METADATA = "SKIPPED_METADATA"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    UNSUPPORTED_ENTITY = "UNSUPPORTED_ENTITY"


@dataclass(frozen=True)
class RawTextLineRow:
    """Raw text row fetched for parsing."""

    line_id: int
    line_no: int
    raw_line: str


@dataclass(frozen=True)
class RawExcelRow:
    """Raw Excel row fetched for parsing."""

    row_id: int
    sheet_name: str
    row_no: int
    values: list[Any]


@dataclass(frozen=True)
class ExcelFileMetadata:
    """Registered Excel file metadata needed by parsers."""

    file_id: int
    source_name: str
    storage_path: str
    file_format: str
    sha256: str


@dataclass(frozen=True)
class ParsedRowUpdate:
    """Parsed row update to persist back to the raw table."""

    line_id: int
    payload_json: str
    status: ParseStatus
    error: str | None


@dataclass(frozen=True)
class ParsedExcelRowUpdate:
    """Parsed Excel row update to persist back to the raw table."""

    row_id: int
    payload_json: str | None
    status: ExcelParseStatus
    error: str | None


@dataclass(frozen=True)
class ExcelSheetParseStats:
    """Summary for a parsed Excel sheet."""

    sheet_name: str
    sheet_kind: str | None
    header_row_no: int | None
    status: ExcelParseStatus
    rows_seen: int
    rows_parsed: int
    rows_skipped: int
    rows_error: int
    error: str | None = None


@dataclass(frozen=True)
class ExcelParseStats:
    """Summary for a parsed Excel workbook."""

    file_id: int
    entity_name: str
    file_kind: str | None
    parser_name: str
    parser_version: str
    status: ExcelParseStatus
    sheets_seen: int
    sheets_parsed: int
    rows_seen: int
    rows_parsed: int
    rows_error: int
    error: str | None = None


@dataclass(frozen=True)
class HeaderSet:
    """Container for one or more raw header lines."""

    lines: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.lines)

    def __getitem__(self, index: int) -> str:
        return self.lines[index]

    def __iter__(self) -> Iterator[str]:
        return iter(self.lines)


@dataclass(frozen=True)
class ParseStats:
    """Summary for a parsed raw file."""

    file_id: int
    entity_name: str
    parsed_total: int
    parsed_ok: int
    parsed_error: int
    fixed_len: int
    atb_len: int
    has_atb_headers: bool
