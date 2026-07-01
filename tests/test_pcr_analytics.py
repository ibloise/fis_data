from __future__ import annotations

import json

from click.testing import CliRunner
from sqlalchemy import create_engine, insert, text

from fis_data.cli import cli
from fis_data.etl.pcr_analytics import (
    PCRAnalyticsJob,
    _episode_date_key,
    _run_components,
    _summarize_results,
)
from fis_data.etl.pcr_melting import MeltingParameters, calculate_peaks, interpret_curve
from fis_data.schema import (
    create_schema,
    ctl_etl_run,
    ctl_file_registry,
    ctl_source,
    pcr_cq_result,
    pcr_rfu_measurement,
    pcr_run,
    pcr_run_attribute,
    pcr_well,
)


def test_melting_handles_zero_curve_and_screening_multimodal() -> None:
    params = MeltingParameters()
    assert (
        calculate_peaks(
            temperatures=[78, 79, 80], derivatives=[0, 0, 0], params=params
        )
        == []
    )

    temperatures = [78, 79, 79.5, 80, 81, 82, 83, 85, 86, 87, 88, 89]
    derivatives = [0, 0, 200, 0, 0, 180, 0, 0, 220, 0, 210, 0]
    calls, peaks = interpret_curve(
        source_target="Screening",
        cq=20,
        temperatures=temperatures,
        derivatives=derivatives,
        params=params,
    )

    assert len(peaks) == 4
    assert {call.target: call.call for call in calls} == {
        "OXA48": "POSITIVE",
        "VIM": "POSITIVE",
        "NDM": "POSITIVE",
        "KPC": "POSITIVE",
    }


def test_episode_pairing_and_latest_valid_result_rules() -> None:
    assert _episode_date_key("SCR-010124.pcrd") == "2024-01-01"
    assert _episode_date_key("PCR_CRIBADO_03042023.pcrd") == "2023-04-03"
    assert _run_components(
        [(1, "SCREENING"), (2, "PANEL_KPC_NDM"), (3, "PANEL_OXA48_VIM")],
        {1: {1, 2, 3}, 2: {1, 2, 3}, 3: {10}},
    ) == [[(1, "SCREENING"), (2, "PANEL_KPC_NDM")], [(3, "PANEL_OXA48_VIM")]]

    summary = _summarize_results(
        [
            {
                "id": 1,
                "call": "POSITIVE",
                "run_id": 1,
                "started": "2024-01-01 10:00:00",
                "run_class": "PANEL_KPC_NDM",
                "valid": True,
            },
            {
                "id": 2,
                "call": "NEGATIVE",
                "run_id": 2,
                "started": "2024-01-02 10:00:00",
                "run_class": "COMPENSATION_PARTIAL",
                "valid": False,
            },
        ],
        target="KPC",
    )
    assert summary["preferred"] == 1
    assert summary["relation"] == "CORRECTION"
    assert summary["concordance"] == "CONCORDANT"


def test_pcr_analytics_classifies_controls_and_invalidates_target(tmp_path) -> None:
    db_path = tmp_path / "analytics.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    create_schema(engine)
    with engine.begin() as conn:
        source_id = int(
            conn.execute(
                insert(ctl_source).values(source_name="PCR", source_type="dummy")
            ).inserted_primary_key[0]
        )
        etl_run_id = int(
            conn.execute(
                insert(ctl_etl_run).values(pipeline_name="seed", status="SUCCESS")
            ).inserted_primary_key[0]
        )
        file_id = int(
            conn.execute(
                insert(ctl_file_registry).values(
                    source_id=source_id,
                    source_name="PCR",
                    storage_path="ESP-010124-KPC.pcrd",
                    file_format="excel",
                    sha256="a" * 64,
                )
            ).inserted_primary_key[0]
        )
        pcr_run_id = int(
            conn.execute(
                insert(pcr_run).values(
                    source_id=source_id,
                    run_name="ESP-010124-KPC.pcrd",
                    run_name_normalized="esp_010124_kpc_pcrd",
                )
            ).inserted_primary_key[0]
        )
        conn.execute(
            insert(pcr_run_attribute).values(
                pcr_run_id=pcr_run_id,
                file_id=file_id,
                raw_excel_row_id=1,
                run_id=etl_run_id,
                attribute_key="Run Started",
                attribute_key_normalized="run_started",
                value_json=json.dumps("01/01/2024 10:00:00 UTC"),
                is_canonical=True,
                has_conflict=False,
            )
        )

        well_ids = {}
        for index, sample_name in enumerate(("00123", "CP", "CN", "PRUEBA"), start=1):
            well_ids[sample_name] = int(
                conn.execute(
                    insert(pcr_well).values(
                        pcr_run_id=pcr_run_id,
                        well=f"A{index:02d}",
                        well_normalized=f"A{index:02d}",
                        sample_name=sample_name,
                        content="Unkn",
                        file_id=file_id,
                        raw_excel_row_id=index,
                        run_id=etl_run_id,
                    )
                ).inserted_primary_key[0]
            )

        cq_ids = {}
        for index, sample_name in enumerate(("00123", "CP", "CN"), start=1):
            cq_ids[sample_name] = int(
                conn.execute(
                    insert(pcr_cq_result).values(
                        pcr_run_id=pcr_run_id,
                        pcr_well_id=well_ids[sample_name],
                        file_id=file_id,
                        raw_excel_row_id=index,
                        run_id=etl_run_id,
                        target="KPC",
                        sample_name=sample_name,
                        cq=20,
                    )
                ).inserted_primary_key[0]
            )
        conn.execute(
            insert(pcr_cq_result).values(
                pcr_run_id=pcr_run_id,
                pcr_well_id=well_ids["PRUEBA"],
                file_id=file_id,
                raw_excel_row_id=4,
                run_id=etl_run_id,
                target="16S",
                sample_name="PRUEBA",
                cq=20,
            )
        )

        measurements = []
        for sample_name in ("00123", "CP", "CN"):
            for offset, (temperature, value) in enumerate(
                zip([87, 87.5, 88, 88.5, 89], [0, 10, 200, 10, 0], strict=True)
            ):
                measurements.append(
                    {
                        "pcr_run_id": pcr_run_id,
                        "pcr_well_id": well_ids[sample_name],
                        "file_id": file_id,
                        "raw_excel_row_id": 100 + offset + well_ids[sample_name] * 10,
                        "run_id": etl_run_id,
                        "measurement_kind": "melt_curve_derivative",
                        "axis_kind": "temperature",
                        "axis_value": temperature,
                        "rfu": value,
                    }
                )
        conn.execute(insert(pcr_rfu_measurement), measurements)

    stats = PCRAnalyticsJob(engine).run(
        algorithm_version="test-v1",
        rebuild=True,
    )

    assert stats.wells_classified == 4
    assert stats.real_samples == 1
    with engine.begin() as conn:
        classifications = conn.execute(
            text(
                """
                SELECT raw_sample_name, well_role FROM pcr_well_classification
                ORDER BY pcr_well_id
                """
            )
        ).all()
        target_qc = conn.execute(
            text(
                """
                SELECT qc_status, is_valid FROM pcr_target_qc
                WHERE canonical_target='KPC'
                """
            )
        ).one()
        selection = conn.execute(
            text(
                """
                SELECT sample.sample_key, selection.preferred_interpretation_id,
                       selection.concordance, selection.needs_review
                FROM pcr_result_selection AS selection
                JOIN pcr_sample AS sample
                  ON sample.pcr_sample_id=selection.pcr_sample_id
                WHERE selection.canonical_target='KPC'
                """
            )
        ).one()

    assert classifications == [
        ("00123", "REAL_SAMPLE"),
        ("CP", "POSITIVE_CONTROL"),
        ("CN", "NEGATIVE_CONTROL"),
        ("PRUEBA", "NON_SAMPLE_REVIEW"),
    ]
    assert target_qc == ("INVALID", 0)
    assert selection == ("123", None, "NO_VALID_RESULT", 1)

    cli_result = CliRunner().invoke(
        cli,
        [
            "derive-pcr",
            "--db-path",
            str(db_path),
            "--algorithm-version",
            "test-v2",
            "--rebuild",
        ],
    )
    assert cli_result.exit_code == 0, cli_result.output
    assert json.loads(cli_result.stdout)["algorithm_version"] == "test-v2"
    assert "PCR derivation complete" in cli_result.stderr
