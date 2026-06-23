"""Microb export parsing utilities."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from ..types import ParseStatus
from .header_index import HeaderIndex


class MicrobExportLine:
    """Represent a parsed pipe-delimited line from a Microb export."""

    SEP = "|"

    def __init__(self, line: str, sep: str = "|") -> None:
        self.sep = sep if sep else self.SEP
        self.fields = line.strip().split(self.sep)
        if self.fields and not self.fields[-1]:
            self.fields.pop()

        if not self.fields or all(not field for field in self.fields):
            self.fields = None

    def __len__(self) -> int:
        return 0 if self.fields is None else len(self.fields)

    def __iter__(self):
        return iter(self.fields or [])

    def __bool__(self):
        return bool(self.fields)

    def __getitem__(self, index):
        if self.fields is None:
            raise IndexError("Empty MicrobExportLine")
        return self.fields[index]

    @property
    def empty_values(self) -> int:
        """Count empty fields."""

        if self.fields is None:
            return 0
        return len([field for field in self.fields if not field])

    @property
    def data_values(self) -> int:
        """Count non-empty fields."""

        if self.fields is None:
            return 0
        return len([field for field in self.fields if field])


def format_cycle_list(
    value_list: list[str],
    context: list[str],
) -> list[dict[str, str]]:
    """Group a flat value list into repeated records using context keys."""

    if len(value_list) % len(context) != 0:
        raise ValueError(
            "List and context must have divisible length: "
            f"length list: {len(value_list)}; length context: {len(context)}"
        )

    context_list = [context[i % len(context)] for i in range(len(value_list))]
    counter = 0
    record_dict: dict[str, str] = {}
    values_list: list[dict[str, str]] = []

    for key, value in zip(context_list, value_list, strict=False):
        record_dict[key] = value
        counter += 1
        if counter == len(context):
            values_list.append(record_dict)
            counter = 0
            record_dict = {}

    return values_list


def normalize_colname(name: str) -> str:
    """Normalize a raw column name into a parser-friendly token."""

    name = (name or "").strip()
    if not name:
        return ""

    name = unicodedata.normalize("NFKD", name)
    name = "".join(char for char in name if not unicodedata.combining(char))
    name = name.lower()
    name = name.replace("º", "n").replace("ª", "a")
    name = re.sub(r"[^\w]+", "_", name)
    return re.sub(r"_+", "_", name).strip("_")


def split_header_cols(header_line: str) -> list[str]:
    """Split a header line into normalized column names."""

    cols = header_line.split("|")
    while cols and cols[-1] == "":
        cols.pop()
    return [normalize_colname(col) for col in cols if col]


@dataclass(frozen=True)
class ParsedLineResult:
    """Result of parsing a raw Microb line."""

    payload: dict[str, Any]
    status: ParseStatus
    error: str | None

    def payload_json(self) -> str:
        """Serialize payload to JSON."""

        return json.dumps(self.payload, ensure_ascii=False)


class MicrobParser:
    """Parser for Microb export lines with optional antibiogram specification."""

    def __init__(
        self,
        *,
        fixed_header: MicrobExportLine,
        atb_spec: MicrobExportLine | None,
    ) -> None:
        self.fixed_header = fixed_header
        self.atb_spec = atb_spec
        self._fixed_keys = HeaderIndex.from_iterable(self.fixed_header).keys

    @property
    def fixed_len(self) -> int:
        """Number of fields in the fixed header."""

        return len(self.fixed_header)

    @property
    def atb_len(self) -> int:
        """Number of fields in the repeated antibiogram block."""

        return (len(self.atb_spec) + 1) if self.atb_spec else 0

    def parse_line(self, raw_line: str) -> ParsedLineResult:
        """Parse a raw data line into a JSON-ready payload."""

        parts = MicrobExportLine(raw_line)
        if not parts:
            return ParsedLineResult(
                {},
                ParseStatus.PARSE_ERROR,
                "PARSE ERROR: Empty line",
            )

        if self.atb_spec is None:
            payload = {
                header: value
                for header, value in zip(self._fixed_keys, parts, strict=False)
                if header
            }
            return ParsedLineResult(payload, ParseStatus.PARSED_OK, None)

        headers = self.fixed_header
        specification = self.atb_spec
        if len(headers) != len(specification):
            return ParsedLineResult(
                {},
                ParseStatus.PARSE_ERROR,
                "PARSE ERROR: Headers and specification must have the same length",
            )

        is_consistent = all(
            [
                headers.empty_values == specification.data_values,
                headers.data_values == specification.empty_values,
            ]
        )
        if not is_consistent:
            return ParsedLineResult(
                {},
                ParseStatus.PARSE_ERROR,
                "PARSE ERROR: Empty and data values of headers and specification "
                "must be consistent",
            )

        headers_checker = len(headers) + 1
        atb_start_position = len(headers) - specification.data_values

        clean_spec = [field for field in specification if field]
        clean_spec.insert(0, "Cod.Atb")

        payload = {
            header: value
            for header, value in zip(self._fixed_keys, parts, strict=False)
            if header
        }

        if len(parts) > headers_checker:
            extra_values = list(parts)[atb_start_position:]
            try:
                payload["extra"] = format_cycle_list(extra_values, clean_spec)
            except ValueError as exc:
                return ParsedLineResult(
                    payload,
                    ParseStatus.PARSE_ERROR,
                    f"PARSE ERROR: {exc}",
                )

        return ParsedLineResult(payload, ParseStatus.PARSED_OK, None)
