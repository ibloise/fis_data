from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert, inspect, select

from fis_data.etl.pcr.analytics.patient_episodes import (
    EpisodeSample,
    PatientEpisodeJob,
    group_patient_episodes,
)
from fis_data.schema import (
    create_schema,
    pcr_derivation_run,
    patient_episode_derivation,
)


def _sample(patient: int, pcr: int, microb: int, day: int) -> EpisodeSample:
    return EpisodeSample(
        patient, pcr, microb, datetime(2025, 1, 1) + timedelta(days=day - 1)
    )


def test_episode_gap_equal_to_limit_stays_and_larger_gap_splits() -> None:
    grouped = group_patient_episodes(
        [
            _sample(1, 1, 11, 1),
            _sample(1, 2, 12, 31),
            _sample(1, 3, 13, 31),
            _sample(1, 4, 14, 62),
        ],
        max_gap_days=30,
    )
    assert len(grouped) == 2
    assert [[sample.pcr_sample_id for sample, _ in members] for _, members in grouped] == [
        [1, 2, 3], [4]
    ]
    assert [gap for _, members in grouped for _, gap in members] == [None, 30, 0, 31]


def test_episode_grouping_never_crosses_patients() -> None:
    grouped = group_patient_episodes(
        [_sample(1, 1, 11, 1), _sample(2, 2, 12, 2)],
        max_gap_days=30,
    )
    assert [patient for patient, _ in grouped] == [1, 2]


def test_episode_grouping_rejects_ambiguous_sample_relations() -> None:
    with pytest.raises(ValueError, match="maps to multiple patient/culture pairs"):
        group_patient_episodes(
            [_sample(1, 1, 11, 1), _sample(1, 1, 12, 2)],
            max_gap_days=30,
        )
    with pytest.raises(ValueError, match="maps to multiple PCR samples"):
        group_patient_episodes(
            [_sample(1, 1, 11, 1), _sample(1, 2, 11, 2)],
            max_gap_days=30,
        )


def test_episode_job_records_parameters_and_promotes_success(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'episodes.sqlite'}")
    create_schema(engine)
    assert inspect(engine).get_pk_constraint(
        "patient_episode_sample"
    )["constrained_columns"] == ["episode_derivation_id", "pcr_sample_id"]
    with engine.begin() as conn:
        pcr_derivation_id = conn.execute(insert(pcr_derivation_run).values(
            algorithm_version="test", rule_version="1.0", parameters_json="{}",
            status="SUCCESS", is_current=True, details_json="{}",
        )).inserted_primary_key[0]

    result = PatientEpisodeJob(engine).run(max_gap_days=30)
    assert result["pcr_derivation_id"] == pcr_derivation_id
    assert result["samples"] == 0
    with engine.connect() as conn:
        derivation = conn.execute(select(patient_episode_derivation)).mappings().one()
    assert derivation["status"] == "SUCCESS"
    assert derivation["is_current"] is True
    assert '"max_gap_days": 30' in derivation["parameters_json"]
