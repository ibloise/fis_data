from __future__ import annotations

from fis_data.etl.microb.normalization import CATALOGS, MicrobNormalizer


def _row() -> dict[str, int]:
    return {
        "source_id": 1,
        "run_id": 2,
        "file_id": 3,
        "line_id": 4,
        "stg_microb_technique_id": 5,
    }


def test_qualitative_results_only_use_rescualt() -> None:
    assert CATALOGS["qualitative_result_id"] == ("rescualt",)


def test_bacteriological_result_in_serological_catalog_is_audited() -> None:
    issues: list[dict[str, object]] = []

    result = MicrobNormalizer(None)._analysis_result(  # type: ignore[arg-type]
        {"resultados": {}, "rescualt": {"NSP": 17}},
        "BACTERIOLOGICAL",
        "NSP",
        _row(),
        issues,
    )

    assert result is None
    assert issues == [
        {
            "source_id": 1,
            "run_id": 2,
            "file_id": 3,
            "line_id": 4,
            "staging_table": "stg_microb_technique",
            "staging_id": 5,
            "field_name": "cod_resultado",
            "code": "NSP",
            "severity": "WARNING",
            "message": "Resultado incompatible: análisis BACTERIOLOGICAL con código de catálogo rescualt",
        }
    ]


def test_serological_result_in_bacteriological_catalog_is_audited() -> None:
    issues: list[dict[str, object]] = []

    result = MicrobNormalizer(None)._analysis_result(  # type: ignore[arg-type]
        {"resultados": {"POS": 29}, "rescualt": {}},
        "SEROLOGICAL",
        "POS",
        _row(),
        issues,
    )

    assert result is None
    assert issues[0]["severity"] == "WARNING"
    assert issues[0]["message"] == "Resultado incompatible: análisis SEROLOGICAL con código de catálogo resultados"


def test_unknown_observation_is_accepted_as_free_text() -> None:
    issues: list[dict[str, object]] = []

    observation_id = MicrobNormalizer(None)._observation(  # type: ignore[arg-type]
        {"observacion": {}, "observamic": {}}, "Nota libre", _row(), issues
    )

    assert observation_id is None
    assert issues[0]["severity"] == "INFO"
    assert issues[0]["field_name"] == "observation_id"
    assert issues[0]["message"] == "Observación no catalogada conservada como texto libre"


def test_serological_analysis_does_not_require_cod_resultado() -> None:
    assert not MicrobNormalizer._requires_result_code("SEROLOGICAL")
    assert MicrobNormalizer._requires_result_code("BACTERIOLOGICAL")
    assert MicrobNormalizer._requires_result_code(None)


def test_hospital_prefers_the_origin_auxiliary_catalog() -> None:
    hospital_id = MicrobNormalizer._hospital_id(
        {
            ("procedencias_extra", "G04C"): 10,
            ("servicios_extra", "DIGG"): 20,
        },
        "G04C",
        "DIGG",
    )

    assert hospital_id == 10
