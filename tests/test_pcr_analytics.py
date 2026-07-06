from __future__ import annotations

import json
from unittest.mock import Mock

from click.testing import CliRunner
from sqlalchemy import create_engine, insert, text

from fis_data.cli import cli
from fis_data.etl.pcr.analytics.domain import (
    evaluate_screening_target_qc,
    summarize_results,
)
from fis_data.etl.pcr.analytics.facade import (
    PCRAnalyticsJob,
    _episode_date_key,
    _run_components,
    _summarize_results,
)
from fis_data.etl.pcr.analytics.models import QCObservation, ResultCandidate
from fis_data.etl.pcr.analytics.pipeline import PCRAnalyticsPipeline
from fis_data.etl.pcr.analytics.repository import PCRAnalyticsRepository
from fis_data.etl.pcr.melting import MeltingParameters, calculate_peaks, interpret_curve
from fis_data.etl.pcr.review.repository import PCRReviewRepository
from fis_data.etl.pcr.review.service import PCRReviewService
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
        calculate_peaks(temperatures=[78, 79, 80], derivatives=[0, 0, 0], params=params)
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
    ) == [[(1, "SCREENING")], [(2, "PANEL_KPC_NDM")], [(3, "PANEL_OXA48_VIM")]]

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


def test_screening_run_is_never_paired_in_episode_association() -> None:
    repository = Mock(spec=PCRAnalyticsRepository)
    repository.fetch_episode_inputs.return_value = (
        [
            (1, "SCREENING", "2024-01-01"),
            (2, "PANEL_KPC_NDM", "2024-01-01"),
            (3, "PANEL_OXA48_VIM", "2024-01-01"),
        ],
        {1: {101, 102}, 2: {101, 102}, 3: {101, 102}},
    )
    pipeline = PCRAnalyticsPipeline(repository)

    assert pipeline.associate_episodes(7) == 2

    inserted = repository.insert_episode.call_args_list
    screening_episode = inserted[0].kwargs
    panel_episode = inserted[1].kwargs
    assert screening_episode["status"] == "AUTO"
    assert screening_episode["review"] is None
    assert [member.run_id for member in screening_episode["memberships"]] == [1]
    assert panel_episode["status"] == "AUTO"
    assert panel_episode["review"] is None
    assert {member.run_id for member in panel_episode["memberships"]} == {2, 3}


def test_full_overlap_panel_runs_pair_across_different_dates() -> None:
    repository = Mock(spec=PCRAnalyticsRepository)
    repository.fetch_episode_inputs.return_value = (
        [
            (1, "PANEL_KPC_NDM", "2024-01-01"),
            (2, "PANEL_OXA48_VIM", "2024-01-02"),
        ],
        {1: {101, 102}, 2: {101, 102}},
    )
    pipeline = PCRAnalyticsPipeline(repository)

    assert pipeline.associate_episodes(7) == 1

    episode = repository.insert_episode.call_args.kwargs
    assert episode["episode_key"] == "MIXED:01"
    assert episode["date_key"] is None
    assert episode["status"] == "AUTO"
    assert episode["review"] is None
    assert {member.run_id for member in episode["memberships"]} == {1, 2}


def test_full_overlap_compensation_pairs_across_different_dates() -> None:
    repository = Mock(spec=PCRAnalyticsRepository)
    repository.fetch_episode_inputs.return_value = (
        [
            (1, "PANEL_OXA48_VIM", "2024-01-01"),
            (2, "COMPENSATION_PARTIAL", "2024-01-02"),
        ],
        {1: {101, 102}, 2: {101, 102}},
    )
    pipeline = PCRAnalyticsPipeline(repository)

    assert pipeline.associate_episodes(7) == 1

    episode = repository.insert_episode.call_args.kwargs
    assert episode["episode_key"] == "MIXED:01"
    assert episode["date_key"] is None
    assert episode["status"] == "AUTO"
    assert episode["review"] is None
    assert {member.run_id for member in episode["memberships"]} == {1, 2}


def _screening_qc_observation(
    target: str, role: str, call: str, cq: float
) -> QCObservation:
    return QCObservation(1, target, "Screening", "SCREENING", role, call, cq)


def test_screening_qc_accepts_valid_cq_with_one_working_melting_target() -> None:
    observations = [
        _screening_qc_observation("KPC", "POSITIVE_CONTROL", "NEGATIVE", 20),
        _screening_qc_observation("NDM", "POSITIVE_CONTROL", "NEGATIVE", 20),
        _screening_qc_observation("OXA48", "POSITIVE_CONTROL", "NEGATIVE", 20),
        _screening_qc_observation("VIM", "POSITIVE_CONTROL", "POSITIVE", 20),
        _screening_qc_observation("KPC", "NEGATIVE_CONTROL", "NEGATIVE", 25),
        _screening_qc_observation("NDM", "NEGATIVE_CONTROL", "NEGATIVE", 25),
        _screening_qc_observation("OXA48", "NEGATIVE_CONTROL", "NEGATIVE", 25),
        _screening_qc_observation("VIM", "NEGATIVE_CONTROL", "NEGATIVE", 25),
    ]

    assessment = evaluate_screening_target_qc(observations)

    assert assessment.valid
    assert assessment.status == "VALID"
    assert assessment.failures == ()
    assert assessment.details["working_target"] == "VIM"
    assert assessment.details["working_targets"] == ["VIM"]


def test_screening_qc_rejects_positive_control_cq_outside_range() -> None:
    observations = [
        _screening_qc_observation("VIM", "POSITIVE_CONTROL", "POSITIVE", 41),
        _screening_qc_observation("VIM", "NEGATIVE_CONTROL", "NEGATIVE", 25),
    ]

    assessment = evaluate_screening_target_qc(observations)

    assert not assessment.valid
    assert assessment.status == "INVALID"
    assert assessment.failures == ("POSITIVE_CONTROL_NOT_POSITIVE",)
    assert assessment.details["positive_control_cq_valid"] is False


def test_partial_overlap_panel_runs_do_not_pair_across_different_dates() -> None:
    repository = Mock(spec=PCRAnalyticsRepository)
    repository.fetch_episode_inputs.return_value = (
        [
            (1, "PANEL_KPC_NDM", "2024-01-01"),
            (2, "PANEL_OXA48_VIM", "2024-01-02"),
        ],
        {1: {101, 102, 103}, 2: {101, 102, 104}},
    )
    pipeline = PCRAnalyticsPipeline(repository)

    assert pipeline.associate_episodes(7) == 2

    episodes = [call.kwargs for call in repository.insert_episode.call_args_list]
    assert [episode["status"] for episode in episodes] == ["REVIEW", "REVIEW"]
    memberships = [
        {member.run_id for member in episode["memberships"]} for episode in episodes
    ]
    assert memberships == [{1}, {2}]


def test_typed_result_selection_prefers_latest_valid_candidate() -> None:
    candidates = [
        ResultCandidate(
            interpretation_id=1,
            result_id=1,
            sample_id=7,
            sample_key="7",
            target="KPC",
            call="POSITIVE",
            run_id=1,
            started="2024-01-01 10:00:00",
            run_class="PANEL_KPC_NDM",
            valid=True,
        ),
        ResultCandidate(
            interpretation_id=2,
            result_id=2,
            sample_id=7,
            sample_key="7",
            target="KPC",
            call="POSITIVE",
            run_id=2,
            started="2024-01-02 10:00:00",
            run_class="PANEL_KPC_NDM",
            valid=True,
        ),
    ]

    summary = summarize_results(candidates, target="KPC")

    assert summary.preferred == 2
    assert summary.concordance == "CONCORDANT"
    assert not summary.needs_review


def test_analytics_job_is_a_facade_over_injected_collaborators() -> None:
    repository = Mock(spec=PCRAnalyticsRepository)
    pipeline = Mock(spec=PCRAnalyticsPipeline)
    repository.start_derivation.return_value = 42
    repository.count_review_items.return_value = 3
    pipeline.classify_wells.return_value = (10, 8)
    pipeline.interpret.return_value = 9
    pipeline.evaluate_qc.return_value = 4
    pipeline.associate_episodes.return_value = 2
    pipeline.select_results.return_value = 5

    stats = PCRAnalyticsJob(repository=repository, pipeline=pipeline).run(
        algorithm_version="test-facade",
        rebuild=True,
    )

    assert stats.derivation_id == 42
    assert stats.wells_classified == 10
    assert stats.result_groups == 5
    repository.start_derivation.assert_called_once()
    pipeline.classify_runs.assert_called_once_with(42)
    pipeline.set_overrides.assert_called_once_with([])
    repository.finish_derivation.assert_called_once()
    repository.create_views.assert_called_once_with()


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

    review_service = PCRReviewService(PCRReviewRepository(engine))
    review_items = review_service.list_items(limit=20)
    well_item = next(item for item in review_items if item.item_type == "WELL")
    episode_item = next(item for item in review_items if item.item_type == "EPISODE")
    qc_item = next(item for item in review_items if item.item_type == "TARGET_QC")
    result_item = next(item for item in review_items if item.item_type == "RESULT")
    review_service.override_well(
        subject_key=well_item.subject_key,
        fingerprint=well_item.fingerprint,
        derivation_id=well_item.derivation_id,
        role="REAL_SAMPLE",
        sample_key="999",
        reason="Known sample",
        reviewer="tester",
    )
    review_service.override_episode(
        subject_key=episode_item.subject_key,
        fingerprint=episode_item.fingerprint,
        derivation_id=episode_item.derivation_id,
        run_ids=(pcr_run_id,),
        reason="Confirmed episode",
        reviewer="tester",
    )
    review_service.override_qc(
        subject_key=qc_item.subject_key,
        fingerprint=qc_item.fingerprint,
        derivation_id=qc_item.derivation_id,
        status="VALID",
        valid=True,
        reason="Accepted controls",
        reviewer="tester",
    )
    review_service.override_result(
        subject_key=result_item.subject_key,
        fingerprint=result_item.fingerprint,
        derivation_id=result_item.derivation_id,
        preferred_result_id=cq_ids["00123"],
        call="NEGATIVE",
        reason="Manual final call",
        reviewer="tester",
    )

    PCRAnalyticsJob(engine).run(algorithm_version="test-override", rebuild=True)
    with engine.begin() as conn:
        overridden_selection = conn.execute(
            text(
                """
                SELECT preferred_interpretation_id, needs_review
                FROM pcr_result_selection
                WHERE canonical_target='KPC'
                """
            )
        ).one()
        effective_call = conn.execute(
            text(
                """
                SELECT call, has_override FROM v_pcr_effective_result
                WHERE sample_key='123' AND canonical_target='KPC'
                """
            )
        ).one()
        overridden_well = conn.execute(
            text(
                """
                SELECT well_role, normalized_name
                FROM pcr_well_classification
                WHERE pcr_well_id=:well_id
                """
            ),
            {"well_id": well_ids["PRUEBA"]},
        ).one()
        manual_episode = conn.execute(
            text(
                "SELECT episode_key FROM pcr_episode WHERE episode_key LIKE 'MANUAL:%'"
            )
        ).scalar_one()

    assert overridden_selection[0] is not None
    assert overridden_selection[1] == 0
    assert effective_call == ("NEGATIVE", 1)
    assert overridden_well == ("REAL_SAMPLE", "999")
    assert manual_episode.startswith("MANUAL:")

    cli_result = CliRunner().invoke(
        cli,
        [
            "derive-pcr",
            "--db-path",
            str(db_path),
            "--algorithm-version",
            "test-v2",
            "--rebuild",
            "--ignore-overrides",
        ],
    )
    assert cli_result.exit_code == 0, cli_result.output
    assert json.loads(cli_result.stdout)["algorithm_version"] == "test-v2"
    assert "PCR derivation complete" in cli_result.stderr
    with engine.connect() as conn:
        ignored_preferred = conn.execute(
            text(
                """
                SELECT preferred_interpretation_id
                FROM pcr_result_selection
                WHERE canonical_target='KPC'
                """
            )
        ).scalar_one()
        violations = conn.execute(text("PRAGMA foreign_key_check")).all()
    assert ignored_preferred is None
    analytical_tables = {
        "pcr_well_classification",
        "pcr_run_analysis",
        "pcr_interpretation",
        "pcr_target_qc",
        "pcr_run_qc",
        "pcr_episode",
        "pcr_episode_run",
        "pcr_result_selection",
        "pcr_review_action",
    }
    assert [row for row in violations if row[0] in analytical_tables] == []
