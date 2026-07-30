from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine, text

from fis_data.cli import cli
from fis_data.etl.pcr.analytics.models import SourceRun
from fis_data.etl.pcr.analytics.pipeline import PCRAnalyticsPipeline
from fis_data.etl.pcr.analytics.repository import PCRAnalyticsRepository
from fis_data.etl.pcr.review.models import ActiveOverride
from fis_data.etl.pcr.review.repository import PCRReviewRepository
from fis_data.etl.pcr.review.service import PCRReviewService
from fis_data.schema import create_schema


def _review_engine(tmp_path):
    db_path = tmp_path / "review.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    create_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO pcr_derivation_run (
                    derivation_id, algorithm_version, rule_version,
                    parameters_json, status, is_current
                ) VALUES (1, 'test', '1.0', '{}', 'SUCCESS', 1)
                """
            )
        )
        conn.execute(text("INSERT INTO pcr_sample VALUES (1, 'S1', CURRENT_TIMESTAMP)"))
        conn.execute(
            text(
                """
                INSERT INTO pcr_well_classification VALUES (
                    1, 10, 1, 'UNKNOWN', 'UNKNOWN', 'NON_SAMPLE_REVIEW',
                    'UNKNOWN_TEXT', NULL, 'Unknown sample'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO pcr_run_analysis VALUES (
                    1, 1, 'OTHER', 'SPECIFIC', NULL, NULL, '[]', 'Unknown run targets'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO pcr_interpretation VALUES (
                    100, 1, 1, 10, 1000, 'KPC', 'KPC', 'POSITIVE', 20,
                    88, 200, '[]', '{}', NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO pcr_target_qc VALUES (
                    1, 1, 'KPC', 'INVALID', 0, 1, 1, '[]',
                    '{"failures":["CONTROL"]}'
                )
                """
            )
        )
        conn.execute(text("INSERT INTO pcr_run_qc VALUES (1, 1, 'INVALID', 0, 1, 0)"))
        conn.execute(
            text(
                """
                INSERT INTO pcr_episode VALUES (
                    1, 1, 'UNPARSED:01', NULL, 'REVIEW', 'No partner'
                )
                """
            )
        )
        conn.execute(
            text("INSERT INTO pcr_episode_run VALUES (1, 1, 'OTHER', NULL, 'REVIEW')")
        )
        conn.execute(
            text(
                """
                INSERT INTO pcr_result_selection VALUES (
                    1, 1, 'SPECIFIC', 'KPC', 100, 1, 'SINGLE', 'DISCORDANT', 1, '{}'
                )
                """
            )
        )
    return engine, db_path


def test_review_actions_are_append_only_and_survive_analytics_rebuild(tmp_path) -> None:
    engine, _ = _review_engine(tmp_path)
    repository = PCRReviewRepository(engine)
    service = PCRReviewService(repository)
    items = service.list_items(limit=20)

    assert {item.item_type for item in items} == {
        "WELL",
        "RUN",
        "EPISODE",
        "TARGET_QC",
        "RESULT",
    }
    result = next(item for item in items if item.item_type == "RESULT")
    service.resolve(
        item_type=result.item_type,
        subject_key=result.subject_key,
        fingerprint=result.fingerprint,
        derivation_id=result.derivation_id,
        resolution="ACCEPTED",
        reason="Reviewed",
        reviewer="tester",
    )
    assert not service.list_items(item_type="RESULT", limit=20)
    service.reopen(
        item_type=result.item_type,
        subject_key=result.subject_key,
        fingerprint=result.fingerprint,
        derivation_id=result.derivation_id,
        reason="Needs another look",
        reviewer="tester",
    )
    assert len(service.list_items(item_type="RESULT", limit=20)) == 1

    PCRAnalyticsRepository(engine).clear_derived_model()

    assert len(repository.history("RESULT", result.subject_key)) == 2


def test_typed_overrides_validate_candidates_and_can_be_revoked(tmp_path) -> None:
    engine, _ = _review_engine(tmp_path)
    service = PCRReviewService(PCRReviewRepository(engine))
    items = {item.item_type: item for item in service.list_items(limit=20)}

    service.override_well(
        subject_key=items["WELL"].subject_key,
        fingerprint=items["WELL"].fingerprint,
        derivation_id=items["WELL"].derivation_id,
        role="REAL_SAMPLE",
        sample_key="S1",
        reason="Known sample",
        reviewer="tester",
    )
    service.override_run(
        subject_key=items["RUN"].subject_key,
        fingerprint=items["RUN"].fingerprint,
        derivation_id=items["RUN"].derivation_id,
        run_class="PANEL_KPC_NDM",
        episode_date="2024-01-01",
        reason="Known panel",
        reviewer="tester",
    )
    service.override_episode(
        subject_key=items["EPISODE"].subject_key,
        fingerprint=items["EPISODE"].fingerprint,
        derivation_id=items["EPISODE"].derivation_id,
        run_ids=(1,),
        reason="Manual episode",
        reviewer="tester",
    )
    service.override_qc(
        subject_key=items["TARGET_QC"].subject_key,
        fingerprint=items["TARGET_QC"].fingerprint,
        derivation_id=items["TARGET_QC"].derivation_id,
        status="VALID",
        valid=True,
        reason="Controls accepted",
        reviewer="tester",
    )

    with pytest.raises(ValueError, match="not a candidate"):
        service.override_result(
            subject_key=items["RESULT"].subject_key,
            fingerprint=items["RESULT"].fingerprint,
            derivation_id=items["RESULT"].derivation_id,
            preferred_result_id=9999,
            call=None,
            reason="Wrong candidate",
            reviewer="tester",
        )
    action = service.override_result(
        subject_key=items["RESULT"].subject_key,
        fingerprint=items["RESULT"].fingerprint,
        derivation_id=items["RESULT"].derivation_id,
        preferred_result_id=1000,
        call="NEGATIVE",
        reason="Manual interpretation",
        reviewer="tester",
    )
    assert action.payload == {"preferred_result_id": 1000, "call": "NEGATIVE"}
    assert len(service.active_overrides()) == 5

    service.revoke(
        item_type="RESULT",
        subject_key=items["RESULT"].subject_key,
        reason="Withdraw correction",
        reviewer="tester",
    )
    assert len(service.active_overrides()) == 4
    assert (
        len(
            service.history(item_type="RESULT", subject_key=items["RESULT"].subject_key)
        )
        == 2
    )


def test_stale_fingerprint_is_rejected(tmp_path) -> None:
    engine, _ = _review_engine(tmp_path)
    service = PCRReviewService(PCRReviewRepository(engine))
    item = service.list_items(limit=1)[0]

    with pytest.raises(ValueError, match="stale"):
        service.resolve(
            item_type=item.item_type,
            subject_key=item.subject_key,
            fingerprint="0" * 64,
            derivation_id=item.derivation_id,
            resolution="DISMISSED",
            reason="Stale action",
            reviewer="tester",
        )
    with pytest.raises(ValueError, match="derivation is stale"):
        service.resolve(
            item_type=item.item_type,
            subject_key=item.subject_key,
            fingerprint=item.fingerprint,
            derivation_id=item.derivation_id + 1,
            resolution="DISMISSED",
            reason="Stale derivation",
            reviewer="tester",
        )


def test_changed_issue_fingerprint_reappears_as_open(tmp_path) -> None:
    engine, _ = _review_engine(tmp_path)
    service = PCRReviewService(PCRReviewRepository(engine))
    item = next(
        value for value in service.list_items(limit=20) if value.item_type == "RESULT"
    )
    service.resolve(
        item_type=item.item_type,
        subject_key=item.subject_key,
        fingerprint=item.fingerprint,
        derivation_id=item.derivation_id,
        resolution="ACCEPTED",
        reason="Reviewed",
        reviewer="tester",
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE pcr_result_selection
                SET concordance='NO_VALID_RESULT'
                WHERE derivation_id=1 AND pcr_sample_id=1
                """
            )
        )

    changed = service.list_items(item_type="RESULT", limit=20)

    assert len(changed) == 1
    assert changed[0].status == "OPEN"
    assert changed[0].fingerprint != item.fingerprint


def test_review_summary_counts_statuses_and_overrides(tmp_path) -> None:
    engine, _ = _review_engine(tmp_path)
    service = PCRReviewService(PCRReviewRepository(engine))
    items = {item.item_type: item for item in service.list_items(limit=20)}
    service.resolve(
        item_type=items["RESULT"].item_type,
        subject_key=items["RESULT"].subject_key,
        fingerprint=items["RESULT"].fingerprint,
        derivation_id=items["RESULT"].derivation_id,
        resolution="ACCEPTED",
        reason="Reviewed",
        reviewer="tester",
    )
    service.override_well(
        subject_key=items["WELL"].subject_key,
        fingerprint=items["WELL"].fingerprint,
        derivation_id=items["WELL"].derivation_id,
        role="REAL_SAMPLE",
        sample_key="S1",
        reason="Known sample",
        reviewer="tester",
    )

    summary = service.summary()

    assert summary["totals"] == {
        "current_items": 5,
        "open": 3,
        "resolved": 2,
        "active_overrides": 1,
        "actions": 2,
    }
    by_type = {row["item_type"]: row for row in summary["by_type"]}
    assert by_type["WELL"]["resolved"] == 1
    assert by_type["WELL"]["active_overrides"] == 1
    assert by_type["RESULT"]["resolved"] == 1
    assert summary["top_open_reasons"][0]["count"] >= 1


def test_run_override_is_consumed_by_analytics_pipeline() -> None:
    repository = Mock(spec=PCRAnalyticsRepository)
    repository.fetch_runs.return_value = [
        SourceRun(
            run_id=8,
            run_name="UNKNOWN",
            started_json=None,
            targets=frozenset(),
        )
    ]
    pipeline = PCRAnalyticsPipeline(
        repository,
        overrides=[
            ActiveOverride(
                action_id=4,
                item_type="RUN",
                subject_key="run:8",
                kind="RUN",
                payload={
                    "run_class": "PANEL_KPC_NDM",
                    "episode_date_key": "2024-01-01",
                },
            )
        ],
    )

    pipeline.classify_runs(3)

    analysis = repository.insert_run_analyses.call_args.args[0][0]
    assert analysis.run_class == "PANEL_KPC_NDM"
    assert analysis.episode_key == "2024-01-01"
    assert analysis.review is None


def test_review_cli_lists_json_and_requires_reviewer(tmp_path) -> None:
    _, db_path = _review_engine(tmp_path)
    runner = CliRunner()
    listed = runner.invoke(
        cli,
        [
            "pcr-review",
            "--db-path",
            str(db_path),
            "list",
            "--format",
            "json",
            "--limit",
            "10",
        ],
    )

    assert listed.exit_code == 0, listed.output
    payload = json.loads(listed.stdout)
    assert {item["item_type"] for item in payload} == {
        "WELL",
        "RUN",
        "EPISODE",
        "TARGET_QC",
        "RESULT",
    }
    unresolved = runner.invoke(
        cli,
        [
            "pcr-review",
            "--db-path",
            str(db_path),
            "resolve",
            "--type",
            payload[0]["item_type"],
            "--subject",
            payload[0]["subject_key"],
            "--fingerprint",
            payload[0]["fingerprint"],
            "--derivation-id",
            str(payload[0]["derivation_id"]),
            "--resolution",
            "ACCEPTED",
            "--reason",
            "Reviewed",
        ],
    )
    assert unresolved.exit_code != 0
    assert "--reviewer" in unresolved.output


def test_review_cli_show_explains_episode_actions(tmp_path) -> None:
    _, db_path = _review_engine(tmp_path)
    runner = CliRunner()
    listed = runner.invoke(
        cli,
        [
            "pcr-review",
            "--db-path",
            str(db_path),
            "list",
            "--type",
            "EPISODE",
            "--format",
            "json",
        ],
    )
    episode = json.loads(listed.stdout)[0]

    shown = runner.invoke(
        cli,
        [
            "pcr-review",
            "--db-path",
            str(db_path),
            "show",
            "--type",
            "EPISODE",
            "--subject",
            episode["subject_key"],
        ],
    )
    assert shown.exit_code == 0, shown.output
    assert "DIAGNOSIS" in shown.output
    assert "override episode" in shown.output
    assert "--run-id 1 --run-id <partner_run_id>" in shown.output

    shown_json = runner.invoke(
        cli,
        [
            "pcr-review",
            "--db-path",
            str(db_path),
            "show",
            "--type",
            "EPISODE",
            "--subject",
            episode["subject_key"],
            "--format",
            "json",
        ],
    )
    payload = json.loads(shown_json.stdout)
    assert payload["diagnosis"]["candidate_count"] == 0
    assert "candidate_actions" in payload
    assert "commands" in payload


def test_review_cli_summary_outputs_stable_json(tmp_path) -> None:
    _, db_path = _review_engine(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "pcr-review",
            "--db-path",
            str(db_path),
            "summary",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["derivation_id"] == 1
    assert payload["totals"]["current_items"] == 5
    assert {row["item_type"] for row in payload["by_type"]} == {
        "EPISODE",
        "RESULT",
        "RUN",
        "TARGET_QC",
        "WELL",
    }
    assert payload["top_open_reasons"]
