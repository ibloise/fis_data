"""Shared ETL types for parsing raw records."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum


class ParseStatus(StrEnum):
    """Parsing status values for raw text rows."""

    RAW_ONLY = "RAW_ONLY"
    PARSED_OK = "PARSED_OK"
    PARSE_ERROR = "PARSE_ERROR"


@dataclass(frozen=True)
class RawTextLineRow:
    """Raw text row fetched for parsing."""

    line_id: int
    line_no: int
    raw_line: str


@dataclass(frozen=True)
class ParsedRowUpdate:
    """Parsed row update to persist back to the raw table."""

    line_id: int
    payload_json: str
    status: ParseStatus
    error: str | None


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
