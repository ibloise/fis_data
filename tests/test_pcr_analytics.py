from __future__ import annotations

import json
from unittest.mock import Mock
from datetime import datetime

from click.testing import CliRunner
from sqlalchemy import create_engine, insert, text

from fis_data.cli import cli
from fis_data.etl.pcr.analytics.domain import (
    classify_run,
    classify_sample_name,
    evaluate_screening_target_qc,
    evaluate_target_qc,
    group_result_candidates,
    sample_key,
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
from fis_data.etl.pcr.melting import (
    MeltingParameters,
    calculate_peaks,
    canonical_target,
    interpret_curve,
)
from fis_data.etl.pcr.review.repository import PCRReviewRepository
from fis_data.etl.pcr.review.service import PCRReviewService
from fis_data.etl.pcr.scope_policy import experimental_before_from_environment
from fis_data.schema import (
    create_schema,
    ctl_etl_run,
    ctl_file_registry,
    ctl_source,
    pcr_cq_result,
    pcr_derivation_run,
    pcr_rfu_measurement,
    pcr_run,
    pcr_run_scope,
    pcr_run_attribute,
    pcr_well,
)


def test_experimental_scope_excludes_runs_from_analytics_candidates(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'scope.sqlite'}", future=True)
    create_schema(engine)
    with engine.begin() as conn:
        source_id = conn.execute(
            insert(ctl_source).values(source_name="PCR", source_type="PCR")
        ).lastrowid
        conn.execute(
            insert(pcr_run),
            [
                {
                    "source_id": source_id,
                    "run_name": "old",
                    "run_name_normalized": "old",
                    "run_started": datetime(2023, 6, 30, 23, 59, 59),
                },
                {
                    "source_id": source_id,
                    "run_name": "new",
                    "run_name_normalized": "new",
                    "run_started": datetime(2023, 7, 1),
                },
            ],
        )

    PCRAnalyticsJob(engine).run(
        algorithm_version="scope-policy",
        experimental_before=datetime(2023, 7, 1),
    )

    with engine.connect() as conn:
        scopes = dict(
            conn.execute(
                pcr_run_scope.join(
                    pcr_run, pcr_run_scope.c.pcr_run_id == pcr_run.c.pcr_run_id
                ).select().with_only_columns(
                    pcr_run.c.run_name, pcr_run_scope.c.analytical_scope
                )
            ).all()
        )
    assert scopes == {"old": "EXPERIMENTAL", "new": "ROUTINE"}
    assert [run.run_name for run in PCRAnalyticsRepository(engine).fetch_runs()] == ["new"]


def test_experimental_scope_uses_the_documented_default(monkeypatch) -> None:
    monkeypatch.delenv("PCR_EXPERIMENTAL_BEFORE", raising=False)

    assert experimental_before_from_environment() == datetime(2023, 7, 1)


def test_successful_derivation_is_atomically_promoted_as_current(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'current.sqlite'}", future=True)
    create_schema(engine)
    repository = PCRAnalyticsRepository(engine)

    first_id = repository.start_derivation(
        algorithm_version="first", parameters_json="{}"
    )
    repository.finish_derivation(first_id, status="SUCCESS", details_json="{}")
    failed_id = repository.start_derivation(
        algorithm_version="failed", parameters_json="{}"
    )
    repository.finish_derivation(failed_id, status="FAILED", details_json="{}")
    second_id = repository.start_derivation(
        algorithm_version="second", parameters_json="{}"
    )
    repository.finish_derivation(second_id, status="SUCCESS", details_json="{}")

    with engine.connect() as conn:
        rows = conn.execute(
            pcr_derivation_run.select().order_by(pcr_derivation_run.c.derivation_id)
        ).mappings()
        current_by_id = {
            int(row["derivation_id"]): bool(row["is_current"]) for row in rows
        }

    assert current_by_id == {first_id: False, failed_id: False, second_id: True}


def test_create_schema_initializes_current_derivation_on_legacy_schema(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.sqlite'}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE pcr_derivation_run (
                    derivation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    algorithm_version VARCHAR(100) NOT NULL,
                    rule_version VARCHAR(40) NOT NULL,
                    parameters_json TEXT NOT NULL,
                    status VARCHAR(30) NOT NULL,
                    details_json TEXT,
                    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO pcr_derivation_run (
                    algorithm_version, rule_version, parameters_json, status
                ) VALUES
                    ('first', '1.0', '{}', 'SUCCESS'),
                    ('failed', '1.0', '{}', 'FAILED'),
                    ('second', '1.0', '{}', 'SUCCESS')
                """
            )
        )

    create_schema(engine)

    with engine.connect() as conn:
        current_ids = conn.execute(
            text(
                """
                SELECT derivation_id FROM pcr_derivation_run
                WHERE is_current=1
                """
            )
        ).scalars()
        assert list(current_ids) == [3]


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


def test_screening_control_with_an_incompatible_peak_keeps_melting_trace() -> None:
    calls, _ = interpret_curve(
        source_target="Screening",
        cq=20,
        temperatures=[89.5, 90.0, 90.5],
        derivatives=[0, 500, 0],
        params=MeltingParameters(),
    )
    observations = [
        QCObservation(
            1,
            call.target,
            "Screening",
            "SCREENING",
            "POSITIVE_CONTROL",
            call.call,
            20,
            call.error,
        )
        for call in calls
    ] + [
        _screening_qc_observation("KPC", "NEGATIVE_CONTROL", "NEGATIVE", 25)
    ]

    assert {call.error for call in calls} == {"MELTING_INCOMPATIBLE"}
    assessment = evaluate_screening_target_qc(observations)
    assert assessment.failures == ("POSITIVE_CONTROL_MELTING_INCOMPATIBLE",)


def test_fail_target_only_invalidates_its_own_interpretation() -> None:
    assert classify_run(frozenset({canonical_target("FAIL-2"), "OXA48"})) == (
        "COMPENSATION_PARTIAL",
        None,
    )
    calls, peaks = interpret_curve(
        source_target="FAIL-2",
        cq=21.2,
        temperatures=[],
        derivatives=[],
        params=MeltingParameters(),
    )

    assert peaks == []
    assert calls[0].target == "FAIL"
    assert calls[0].call == "INDETERMINATE"
    assert calls[0].error == "TARGET FAILED"


def test_specific_target_preserves_cq_as_indeterminate_on_melting_mismatch() -> None:
    calls, peaks = interpret_curve(
        source_target="OXA48",
        cq=21.2,
        temperatures=[81.5, 82.0, 82.5, 83.0, 83.5],
        derivatives=[0, 100, 500, 100, 0],
        params=MeltingParameters(),
    )

    assert len(peaks) == 1
    assert calls[0].call == "INDETERMINATE"
    assert calls[0].error == "MELTING_INCOMPATIBLE"
    assert calls[0].peak_temperature == 82.5
    assert calls[0].compatible_targets == ("VIM",)


def test_melting_compatibility_uses_the_configured_half_degree_window() -> None:
    calls, _ = interpret_curve(
        source_target="KPC",
        cq=21.0,
        temperatures=[89.5, 90.0, 90.5],
        derivatives=[0, 500, 0],
        params=MeltingParameters(),
    )

    assert calls[0].call == "INDETERMINATE"
    assert calls[0].error == "MELTING_INCOMPATIBLE"
    assert calls[0].compatible_targets == ()


def test_numeric_sample_keys_preserve_leading_zeroes() -> None:
    role, normalized, rule, review = classify_sample_name(" 061991301 ")

    assert role == "REAL_SAMPLE"
    assert normalized == "061991301"
    assert rule == "NUMERIC"
    assert review is None
    assert sample_key("061991301") == "061991301"
    assert sample_key("61991301") == "61991301"
    assert sample_key("061991301") != sample_key("61991301")


def test_control_content_repairs_missing_sample_and_warns_on_real_sample() -> None:
    assert classify_sample_name(None, "Neg Ctrl")[:3] == (
        "NEGATIVE_CONTROL",
        "NEG_CTRL",
        "CONTENT_NEGATIVE_CONTROL",
    )
    assert classify_sample_name(None, "Pos Ctrl")[:3] == (
        "POSITIVE_CONTROL",
        "POS_CTRL",
        "CONTENT_POSITIVE_CONTROL",
    )
    role, normalized, rule, review = classify_sample_name("12345", "Pos Ctrl")
    assert (role, normalized, rule) == ("REAL_SAMPLE", "12345", "NUMERIC")
    assert review == "Content indicates a control for a real sample."


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


def test_screening_qc_accepts_global_positive_and_negative_controls() -> None:
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
    assert assessment.details["positive_control_positive"] is True
    assert assessment.details["negative_control_negative"] is True


def test_screening_qc_does_not_reject_a_positive_control_by_cq_alone() -> None:
    observations = [
        _screening_qc_observation("VIM", "POSITIVE_CONTROL", "POSITIVE", 41),
        _screening_qc_observation("VIM", "NEGATIVE_CONTROL", "NEGATIVE", 25),
    ]

    assessment = evaluate_screening_target_qc(observations)

    assert assessment.valid
    assert assessment.status == "VALID"
    assert assessment.failures == ()


def test_screening_qc_rejects_a_non_negative_negative_control() -> None:
    observations = [
        _screening_qc_observation("VIM", "POSITIVE_CONTROL", "POSITIVE", 20),
        _screening_qc_observation("VIM", "NEGATIVE_CONTROL", "INDETERMINATE", 25),
    ]

    assessment = evaluate_screening_target_qc(observations)

    assert not assessment.valid
    assert assessment.failures == ("NEGATIVE_CONTROL_NOT_NEGATIVE",)


def test_16s_positive_negative_control_is_valid_with_a_warning() -> None:
    assessment = evaluate_target_qc(
        {
            "POSITIVE_CONTROL": ["POSITIVE"],
            "NEGATIVE_CONTROL": ["POSITIVE"],
        },
        target="16S",
    )

    assert assessment.valid
    assert assessment.status == "WARNING"
    assert assessment.failures == ()
    assert assessment.warnings == ("NEGATIVE_CONTROL_POSITIVE",)


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
            run_type="SPECIFIC",
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
            run_type="SPECIFIC",
            valid=True,
        ),
    ]

    summary = summarize_results(candidates, target="KPC")

    assert summary.preferred == 2
    assert summary.concordance == "CONCORDANT"
    assert not summary.needs_review


def test_result_candidates_are_separated_by_global_run_type() -> None:
    specific = ResultCandidate(
        interpretation_id=1, result_id=1, sample_id=7, sample_key="7", target="KPC",
        call="POSITIVE", run_id=1, started=None, run_class="PANEL_KPC_NDM",
        run_type="SPECIFIC", valid=True,
    )
    screening = ResultCandidate(
        interpretation_id=2, result_id=2, sample_id=7, sample_key="7", target="KPC",
        call="NEGATIVE", run_id=2, started=None, run_class="SCREENING",
        run_type="SCREENING", valid=True,
    )

    assert set(group_result_candidates([specific, screening])) == {
        (7, "SPECIFIC", "KPC"),
        (7, "SCREENING", "KPC"),
    }


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
    repository.apply_legacy_screening_gate.return_value = 2
    repository.classify_promoted_sample_results.return_value = 6
    repository.summarize_sil_culture_markers.return_value = 7
    stages: list[tuple[str, bool]] = []

    stats = PCRAnalyticsJob(repository=repository, pipeline=pipeline).run(
        algorithm_version="test-facade",
        rebuild=True,
        on_stage=lambda label, completed: stages.append((label, completed)),
    )

    assert stats.derivation_id == 42
    assert stats.wells_classified == 10
    assert stats.result_groups == 5
    assert stats.sample_results == 6
    assert stats.legacy_screening_blocks == 2
    assert stats.sil_culture_summaries == 7
    assert stats.sil_links == repository.link_sil_results.return_value
    repository.start_derivation.assert_called_once()
    pipeline.classify_runs.assert_called_once_with(42)
    pipeline.set_overrides.assert_called_once_with([])
    repository.finish_derivation.assert_called_once()
    assert stages == [
        ("Classifying PCR wells", False),
        ("Classifying PCR wells", True),
        ("Validating PCR–Microb matches", False),
        ("Validating PCR–Microb matches", True),
        ("Classifying PCR runs", False),
        ("Classifying PCR runs", True),
        ("Applying legacy screening gate", False),
        ("Applying legacy screening gate", True),
        ("Interpreting melting curves", False),
        ("Interpreting melting curves", True),
        ("Evaluating PCR QC", False),
        ("Evaluating PCR QC", True),
        ("Associating PCR episodes", False),
        ("Associating PCR episodes", True),
        ("Selecting PCR results", False),
        ("Selecting PCR results", True),
        ("Classifying promoted PCR samples", False),
        ("Classifying promoted PCR samples", True),
        ("Linking PCR results to SIL evidence", False),
        ("Linking PCR results to SIL evidence", True),
        ("Aggregating direct SIL culture markers", False),
        ("Aggregating direct SIL culture markers", True),
    ]


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
        qc_evidence = conn.execute(
            text(
                """
                SELECT well_role, call, cq, peak_temperature, error
                FROM v_pcr_target_qc_evidence
                WHERE pcr_run_id=:run_id AND canonical_target='KPC'
                ORDER BY well_role
                """
            ),
            {"run_id": pcr_run_id},
        ).all()

    assert classifications == [
        ("00123", "REAL_SAMPLE"),
        ("CP", "POSITIVE_CONTROL"),
        ("CN", "NEGATIVE_CONTROL"),
        ("PRUEBA", "NON_SAMPLE_REVIEW"),
    ]
    assert target_qc == ("INVALID", 0)
    assert selection == ("00123", None, "NO_VALID_RESULT", 1)
    assert qc_evidence == [
        ("NEGATIVE_CONTROL", "POSITIVE", 20.0, 88.0, None),
        ("POSITIVE_CONTROL", "POSITIVE", 20.0, 88.0, None),
    ]

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
                WHERE sample_key='00123' AND canonical_target='KPC'
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
