"""Audited longitudinal patient episodes built from PCR--Microb matches."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import Engine

from ....schema import patient_episode, patient_episode_derivation, patient_episode_sample


@dataclass(frozen=True)
class EpisodeSample:
    patient_id: int
    pcr_sample_id: int
    microb_sample_id: int
    clinical_date: datetime


def group_patient_episodes(
    samples: list[EpisodeSample], *, max_gap_days: int
) -> list[tuple[int, list[tuple[EpisodeSample, int | None]]]]:
    """Group samples by patient when consecutive clinical dates are close enough."""

    if max_gap_days < 0:
        raise ValueError("max_gap_days must be non-negative")
    by_patient: dict[int, list[EpisodeSample]] = defaultdict(list)
    seen_pcr: dict[int, tuple[int, int]] = {}
    seen_microb: dict[int, tuple[int, int]] = {}
    for item in samples:
        pcr_key = (item.patient_id, item.microb_sample_id)
        microb_key = (item.patient_id, item.pcr_sample_id)
        if item.pcr_sample_id in seen_pcr and seen_pcr[item.pcr_sample_id] != pcr_key:
            raise ValueError(
                f"PCR sample {item.pcr_sample_id} maps to multiple patient/culture pairs."
            )
        if item.microb_sample_id in seen_microb and seen_microb[item.microb_sample_id] != microb_key:
            raise ValueError(
                f"Microb sample {item.microb_sample_id} maps to multiple PCR samples."
            )
        seen_pcr[item.pcr_sample_id] = pcr_key
        seen_microb[item.microb_sample_id] = microb_key
        by_patient[item.patient_id].append(item)

    episodes: list[tuple[int, list[tuple[EpisodeSample, int | None]]]] = []
    for patient_id, patient_samples in sorted(by_patient.items()):
        ordered = sorted(
            patient_samples,
            key=lambda item: (item.clinical_date, item.pcr_sample_id, item.microb_sample_id),
        )
        current: list[tuple[EpisodeSample, int | None]] = []
        previous: datetime | None = None
        for item in ordered:
            gap = (item.clinical_date.date() - previous.date()).days if previous else None
            if previous is not None and gap is not None and gap > max_gap_days:
                episodes.append((patient_id, current))
                current = []
            current.append((item, gap))
            previous = item.clinical_date
        if current:
            episodes.append((patient_id, current))
    return episodes


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class PatientEpisodeJob:
    """Build and audit a versioned longitudinal episode derivation."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def run(self, *, max_gap_days: int = 30, algorithm_version: str = "patient-gap-v1") -> dict[str, int]:
        if max_gap_days < 0:
            raise ValueError("max_gap_days must be non-negative")
        with self.engine.begin() as conn:
            pcr_derivation_id = conn.execute(text(
                "SELECT derivation_id FROM pcr_derivation_run "
                "WHERE status='SUCCESS' AND is_current=TRUE"
            )).scalar_one_or_none()
            if pcr_derivation_id is None:
                raise ValueError("A current successful PCR derivation is required.")
            episode_derivation_id = conn.execute(insert(patient_episode_derivation).values(
                pcr_derivation_id=pcr_derivation_id,
                algorithm_version=algorithm_version,
                parameters_json=json.dumps({
                    "max_gap_days": max_gap_days,
                    "date_rule": "received_at_then_entered_at",
                    "boundary": "gap_equal_to_maximum_stays_in_episode",
                    "sample_population": "current_derivation_match_eligible",
                }, sort_keys=True),
                status="RUNNING", is_current=False,
                details_json="{}",
            )).inserted_primary_key[0]

        try:
            samples, skipped = self._fetch_samples(pcr_derivation_id)
            episodes = group_patient_episodes(samples, max_gap_days=max_gap_days)
            with self.engine.begin() as conn:
                if conn.dialect.name == "postgresql":
                    conn.execute(text(
                        "LOCK TABLE patient_episode_derivation "
                        "IN SHARE ROW EXCLUSIVE MODE"
                    ))
                for patient_id in sorted({patient for patient, _ in episodes}):
                    patient_episodes = [members for owner, members in episodes if owner == patient_id]
                    for number, members in enumerate(patient_episodes, start=1):
                        episode_id = conn.execute(insert(patient_episode).values(
                            episode_derivation_id=episode_derivation_id,
                            patient_id=patient_id,
                            episode_number=number,
                            started_at=members[0][0].clinical_date,
                            ended_at=members[-1][0].clinical_date,
                            sample_count=len(members),
                            start_reason="FIRST_SAMPLE" if members[0][1] is None else "GAP_EXCEEDED",
                        )).inserted_primary_key[0]
                        conn.execute(insert(patient_episode_sample), [
                            {
                                "episode_derivation_id": episode_derivation_id,
                                "patient_episode_id": episode_id,
                                "pcr_sample_id": sample.pcr_sample_id,
                                "microb_sample_id": sample.microb_sample_id,
                                "clinical_date": sample.clinical_date,
                                "days_since_previous": gap,
                            }
                            for sample, gap in members
                        ])
                conn.execute(update(patient_episode_derivation).where(
                    patient_episode_derivation.c.is_current.is_(True)
                ).values(is_current=False))
                conn.execute(update(patient_episode_derivation).where(
                    patient_episode_derivation.c.episode_derivation_id == episode_derivation_id
                ).values(
                    status="SUCCESS", is_current=True,
                    finished_at=datetime.utcnow(), sample_count=len(samples),
                    episode_count=len(episodes),
                    details_json=json.dumps({
                        "patients": len({item.patient_id for item in samples}),
                        **skipped,
                    }, sort_keys=True),
                ))
            return {
                "episode_derivation_id": episode_derivation_id,
                "pcr_derivation_id": pcr_derivation_id,
                "patients": len({item.patient_id for item in samples}),
                "samples": len(samples),
                "episodes": len(episodes),
            }
        except Exception as exc:
            with self.engine.begin() as conn:
                conn.execute(update(patient_episode_derivation).where(
                    patient_episode_derivation.c.episode_derivation_id == episode_derivation_id
                ).values(
                    status="FAILED", finished_at=datetime.utcnow(),
                    details_json=json.dumps({"error": str(exc)}, ensure_ascii=False),
                ))
            raise

    def _fetch_samples(self, derivation_id: int) -> tuple[list[EpisodeSample], dict[str, int]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT validation.pcr_sample_id,
                       validation.microb_sample_id, sample.patient_id,
                       COALESCE(sample.received_at, sample.entered_at) AS clinical_date,
                       patient.nhc
                FROM pcr_microb_well_validation AS validation
                LEFT JOIN pcr_sample_scope_gate AS gate
                  ON gate.derivation_id=validation.derivation_id
                 AND gate.pcr_sample_id=validation.pcr_sample_id
                JOIN microb_sample AS sample
                  ON sample.sample_id=validation.microb_sample_id
                LEFT JOIN microb_patient AS patient
                  ON patient.patient_id=sample.patient_id
                WHERE validation.derivation_id=:derivation_id
                  AND validation.validation_status='MATCH_ELIGIBLE'
                  AND COALESCE(gate.gate_status, 'NOT_APPLICABLE')
                      <> 'LEGACY_SCREENING_BLOCKED'
            """), {"derivation_id": derivation_id}).mappings().all()
        found: list[EpisodeSample] = []
        skipped = {"samples_without_patient": 0, "samples_without_date": 0}
        for row in rows:
            clinical_date = _as_datetime(row["clinical_date"])
            if clinical_date is None:
                skipped["samples_without_date"] += 1
                continue
            if row["patient_id"] is None or row["nhc"] is None or not str(row["nhc"]).strip():
                skipped["samples_without_patient"] += 1
                continue
            found.append(EpisodeSample(
                patient_id=int(row["patient_id"]),
                pcr_sample_id=int(row["pcr_sample_id"]),
                microb_sample_id=int(row["microb_sample_id"]),
                clinical_date=clinical_date,
            ))
        return found, skipped
