"""Declarative Excel parser profiles."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path


def normalize_profile_token(value: str) -> str:
    """Normalize filenames, sheet names, and headers for profile matching."""

    normalized = value.casefold()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    return normalized.strip("_")


def normalize_profile_pattern(value: str) -> str:
    """Normalize glob patterns while preserving wildcards."""

    normalized = value.casefold()
    normalized = re.sub(r"[^a-z0-9*?]+", "_", normalized)
    return normalized.strip("_")


@dataclass(frozen=True)
class SheetProfile:
    """Describe a sheet variant inside an Excel file profile."""

    kind: str
    sheet_name_patterns: tuple[str, ...] = ("*",)
    required_headers: tuple[str, ...] = ()
    header_scan_rows: int = 20
    stub_status: str = "SKIPPED_METADATA"

    def matches_sheet_name(self, sheet_name: str) -> bool:
        normalized_sheet = normalize_profile_token(sheet_name)
        return any(
            fnmatch.fnmatch(normalized_sheet, normalize_profile_pattern(pattern))
            for pattern in self.sheet_name_patterns
        )


@dataclass(frozen=True)
class FileProfile:
    """Describe a workbook variant inside an Excel entity parser."""

    kind: str
    filename_patterns: tuple[str, ...] = ("*",)
    sheet_profiles: tuple[SheetProfile, ...] = ()

    def matches_path(self, storage_path: str) -> bool:
        filename = Path(storage_path).name
        normalized_filename = normalize_profile_token(filename)
        return any(
            fnmatch.fnmatch(normalized_filename, normalize_profile_pattern(pattern))
            for pattern in self.filename_patterns
        )

    def match_sheet(self, sheet_name: str) -> SheetProfile | None:
        for profile in self.sheet_profiles:
            if profile.matches_sheet_name(sheet_name):
                return profile
        return None


@dataclass(frozen=True)
class ExcelEntityParser:
    """Collection of file profiles for one logical Excel entity."""

    entity_name: str
    parser_name: str
    parser_version: str
    file_profiles: tuple[FileProfile, ...]

    def match_file(self, storage_path: str) -> FileProfile | None:
        for profile in self.file_profiles:
            if profile.matches_path(storage_path):
                return profile
        return None


DEFAULT_SHEET_PROFILE = SheetProfile(kind="default", sheet_name_patterns=("*",))

FARMACIA_EXCEL_PARSER = ExcelEntityParser(
    entity_name="farmacia",
    parser_name="farmacia-excel",
    parser_version="0.1",
    file_profiles=(
        FileProfile(
            kind="default",
            filename_patterns=("*",),
            sheet_profiles=(DEFAULT_SHEET_PROFILE,),
        ),
    ),
)

PCR_EXCEL_PARSER = ExcelEntityParser(
    entity_name="pcr",
    parser_name="pcr-excel",
    parser_version="0.1",
    file_profiles=(
        FileProfile(
            kind="quantification_cq_results",
            filename_patterns=("*quantification*cq*results*", "*cq*results*"),
            sheet_profiles=(DEFAULT_SHEET_PROFILE,),
        ),
    ),
)

EXCEL_ENTITY_PARSERS = {
    FARMACIA_EXCEL_PARSER.entity_name: FARMACIA_EXCEL_PARSER,
    PCR_EXCEL_PARSER.entity_name: PCR_EXCEL_PARSER,
}
