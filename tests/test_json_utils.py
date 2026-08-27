from fis_data.json_utils import loads_json


def test_loads_json_preserves_native_postgresql_scalar_strings() -> None:
    assert loads_json("SCR-041023.pcrd") == "SCR-041023.pcrd"


def test_loads_json_decodes_legacy_json_text() -> None:
    assert loads_json('"SCR-041023.pcrd"') == "SCR-041023.pcrd"
