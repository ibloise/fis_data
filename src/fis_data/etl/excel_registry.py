"""Registry for Excel entity parsers."""

from __future__ import annotations

from .excel_profiles import FARMACIA_EXCEL_PARSER, ExcelEntityParser
from .pcr_excel_parser import PCR_EXCEL_PARSER

EXCEL_ENTITY_PARSERS = {
    FARMACIA_EXCEL_PARSER.entity_name: FARMACIA_EXCEL_PARSER,
    PCR_EXCEL_PARSER.entity_name: PCR_EXCEL_PARSER,
}


def get_excel_entity_parser(entity_name: str) -> ExcelEntityParser | None:
    """Return an Excel entity parser using case-insensitive entity matching."""

    return EXCEL_ENTITY_PARSERS.get(entity_name.casefold())
