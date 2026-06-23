from __future__ import annotations

import pytest

from fis_data.etl.parse.header_index import HeaderIndex
from fis_data.etl.parse.microb_parser import (
    MicrobExportLine,
    MicrobParser,
    format_cycle_list,
)
from fis_data.etl.types import ParseStatus


def test_header_index_deduplicates_repeated_headers() -> None:
    headers = ("Cod.Observaciones", "Cod.Analisis", "Cod.Observaciones")
    index = HeaderIndex(headers=headers)

    assert index.keys == (
        "Cod.Observaciones",
        "Cod.Analisis",
        "Cod.Observaciones__2",
    )


def test_microb_export_line_strips_trailing_empty_field() -> None:
    parts = MicrobExportLine("a|b|c|")

    assert list(parts) == ["a", "b", "c"]
    assert len(parts) == 3


def test_format_cycle_list_groups_repeated_values() -> None:
    values = ["ATB1", "R", "ATB2", "S"]
    context = ["cod_atb", "cod_int"]

    assert format_cycle_list(values, context) == [
        {"cod_atb": "ATB1", "cod_int": "R"},
        {"cod_atb": "ATB2", "cod_int": "S"},
    ]


def test_format_cycle_list_rejects_non_divisible_values() -> None:
    with pytest.raises(ValueError):
        format_cycle_list(["ATB1", "R", "ATB2"], ["cod_atb", "cod_int"])


def test_microb_parser_without_atb_spec_parses_fixed_payload_only() -> None:
    parser = MicrobParser(
        fixed_header=MicrobExportLine("a|b|c|"),
        atb_spec=None,
    )

    result = parser.parse_line("v1|v2|v3|")

    assert result.status is ParseStatus.PARSED_OK
    assert result.error is None
    assert result.payload == {"a": "v1", "b": "v2", "c": "v3"}


def test_microb_parser_with_atb_spec_adds_extra_when_present() -> None:
    parser = MicrobParser(
        fixed_header=MicrobExportLine("a|b||c|"),
        atb_spec=MicrobExportLine("||x||"),
    )

    result = parser.parse_line("v1|v2||v4|ATB1|R|ATB2")

    assert result.status is ParseStatus.PARSED_OK
    assert result.error is None
    assert len(result.payload["extra"]) == 2


def test_microb_parser_preserves_duplicate_header_values() -> None:
    parser = MicrobParser(
        fixed_header=MicrobExportLine(
            "nºhistoria|Cod.Observaciones|Cod.Analisis|Cod.Observaciones|"
        ),
        atb_spec=None,
    )

    result = parser.parse_line("123|EU2|007|ZZ9|")

    assert result.status is ParseStatus.PARSED_OK
    assert result.payload["Cod.Observaciones"] == "EU2"
    assert result.payload["Cod.Observaciones__2"] == "ZZ9"
    assert result.payload["Cod.Analisis"] == "007"
