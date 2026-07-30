"""SQL persistence boundary for PCR analytical derivations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ..enums import DerivationStatuses, PCRMeasurementKinds, PCRTargets, WellRoles
from .models import (
    CQResult,
    DerivativeCurve,
    EpisodeMembership,
    InterpretationInsert,
    QCObservation,
    ResultCandidate,
    ResultSelection,
    RunAnalysis,
    RunQC,
    SourceRun,
    SourceWell,
    TargetQC,
    TargetQCEvidence,
    WellClassification,
)

RULE_VERSION = "1.0"


def _as_datetime(value: Any) -> datetime | None:
    """Return a database timestamp while tolerating legacy SQLite text values."""

    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class PCRAnalyticsRepository:
    """Read and persist PCR analytics records without applying business rules."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def clear_derived_model(self) -> None:
        """Delete all rebuildable analytical tables in dependency order."""

        with self.engine.begin() as conn:
            _clear_derived_model(conn)

    def start_derivation(
        self,
        *,
        algorithm_version: str,
        parameters_json: str,
        rebuild: bool = False,
    ) -> int:
        """Optionally rebuild and atomically create a running derivation."""

        with self.engine.begin() as conn:
            if rebuild:
                _clear_derived_model(conn)
            result = conn.execute(
                text(
                    """
                    INSERT INTO pcr_derivation_run (
                        algorithm_version, rule_version, parameters_json, status
                    ) VALUES (:algorithm_version, :rule_version, :parameters, :status)
                    """
                ),
                {
                    "algorithm_version": algorithm_version,
                    "rule_version": RULE_VERSION,
                    "parameters": parameters_json,
                    "status": DerivationStatuses.running,
                },
            )
            return int(result.lastrowid)

    def finish_derivation(
        self, derivation_id: int, *, status: str, details_json: str
    ) -> None:
        """Finish a derivation and atomically promote it when successful."""

        with self.engine.begin() as conn:
            if status == DerivationStatuses.success:
                conn.execute(
                    text(
                        """
                        UPDATE pcr_derivation_run
                        SET is_current=0
                        WHERE is_current=1 AND derivation_id<>:derivation_id
                        """
                    ),
                    {"derivation_id": derivation_id},
                )
            conn.execute(
                text(
                    """
                    UPDATE pcr_derivation_run
                    SET status=:status, details_json=:details,
                        is_current=:is_current, finished_at=CURRENT_TIMESTAMP
                    WHERE derivation_id=:derivation_id
                    """
                ),
                {
                    "status": status,
                    "details": details_json,
                    "is_current": status == DerivationStatuses.success,
                    "derivation_id": derivation_id,
                },
            )

    def fetch_wells(self) -> list[SourceWell]:
        """Load source wells required by sample classification."""

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT pcr_well_id, sample_name, content
                    FROM pcr_well ORDER BY pcr_well_id
                    """
                )
            ).all()
        return [
            SourceWell(int(row[0]), _optional_str(row[1]), _optional_str(row[2]))
            for row in rows
        ]

    def ensure_samples(self, sample_keys: list[str]) -> dict[str, int]:
        """Upsert canonical samples and return identifiers keyed by sample key."""

        with self.engine.begin() as conn:
            if sample_keys:
                conn.execute(
                    text(
                        """
                        INSERT INTO pcr_sample (sample_key) VALUES (:sample_key)
                        ON CONFLICT(sample_key) DO NOTHING
                        """
                    ),
                    [{"sample_key": key} for key in sample_keys],
                )
            return {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    text("SELECT sample_key, pcr_sample_id FROM pcr_sample")
                )
            }

    def insert_well_classifications(self, rows: list[WellClassification]) -> None:
        """Persist derived well classifications in batches."""

        self._insert_many(
            """
            INSERT INTO pcr_well_classification (
                derivation_id, pcr_well_id, pcr_sample_id, raw_sample_name,
                normalized_name, well_role, rule_key, content_evidence,
                review_reason
            ) VALUES (
                :derivation_id, :well_id, :sample_id, :raw_name,
                :normalized, :role, :rule_key, :content, :review
            )
            """,
            rows,
        )

    def fetch_runs(self) -> list[SourceRun]:
        """Load runs together with their distinct source targets."""

        with self.engine.connect() as conn:
            run_rows = conn.execute(
                text(
                    """
                    SELECT run.pcr_run_id, run.run_name, run.run_started
                    FROM pcr_run AS run
                    ORDER BY run.pcr_run_id
                    """
                )
            ).all()
            target_rows = conn.execute(
                text(
                    """
                    SELECT pcr_run_id, target FROM pcr_cq_result
                    GROUP BY pcr_run_id, target
                    """
                )
            ).all()
        targets_by_run: dict[int, set[str]] = defaultdict(set)
        for run_id, target in target_rows:
            targets_by_run[int(run_id)].add(str(target))
        return [
            SourceRun(
                run_id=int(row[0]),
                run_name=str(row[1]),
                run_started=_as_datetime(row[2]),
                targets=frozenset(targets_by_run[int(row[0])]),
            )
            for row in run_rows
        ]

    def insert_run_analyses(self, rows: list[RunAnalysis]) -> None:
        """Persist run classifications and normalized metadata."""

        self._insert_many(
            """
            INSERT INTO pcr_run_analysis (
                derivation_id, pcr_run_id, run_class, run_type, run_started,
                episode_date_key, canonical_targets_json, review_reason
            ) VALUES (
                :derivation_id, :run_id, :run_class, :run_type, :run_started,
                :episode_key, :targets, :review
            )
            """,
            rows,
        )

    def fetch_cq_results(self) -> list[CQResult]:
        """Load Cq results required by melting interpretation."""

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT pcr_cq_result_id, pcr_run_id, pcr_well_id, target, cq
                    FROM pcr_cq_result ORDER BY pcr_well_id
                    """
                )
            ).all()
        return [
            CQResult(
                result_id=int(row[0]),
                run_id=int(row[1]),
                well_id=int(row[2]),
                target=str(row[3]),
                cq=float(row[4]) if row[4] is not None else None,
            )
            for row in rows
        ]

    def fetch_derivative_curves(self) -> list[DerivativeCurve]:
        """Stream derivative measurements and group them into typed curves."""

        curves: list[DerivativeCurve] = []
        with self.engine.connect() as conn:
            result = conn.execution_options(stream_results=True).execute(
                text(
                    """
                    SELECT pcr_well_id, axis_value, rfu
                    FROM pcr_rfu_measurement
                    WHERE measurement_kind = :measurement_kind
                    ORDER BY pcr_well_id, axis_value
                    """
                ),
                {"measurement_kind": PCRMeasurementKinds.melt_curve_derivative},
            )
            current_well: int | None = None
            temperatures: list[float] = []
            derivatives: list[float] = []
            for row in result:
                well_id = int(row[0])
                if current_well is not None and well_id != current_well:
                    curves.append(
                        DerivativeCurve(
                            current_well, tuple(temperatures), tuple(derivatives)
                        )
                    )
                    temperatures, derivatives = [], []
                current_well = well_id
                temperatures.append(float(row[1]))
                derivatives.append(float(row[2]))
            if current_well is not None:
                curves.append(
                    DerivativeCurve(
                        current_well, tuple(temperatures), tuple(derivatives)
                    )
                )
        return curves

    def insert_interpretations(self, rows: list[InterpretationInsert]) -> None:
        """Persist melting interpretations in batches."""

        self._insert_many(
            """
            INSERT INTO pcr_interpretation (
                derivation_id, pcr_run_id, pcr_well_id, pcr_cq_result_id,
                source_target, canonical_target, call, cq,
                peak_temperature, peak_deriv_rfu, peaks_json, metrics_json, error
            ) VALUES (
                :derivation_id, :run_id, :well_id, :result_id,
                :source_target, :target, :call, :cq,
                :peak_temp, :peak_rfu, :peaks, :metrics, :error
            )
            """,
            rows,
        )

    def fetch_qc_observations(self, derivation_id: int) -> list[QCObservation]:
        """Load control calls used by target quality rules."""

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT interpretation.interpretation_id,
                           interpretation.pcr_run_id,
                           interpretation.canonical_target,
                           interpretation.source_target,
                           analysis.run_class,
                           classification.well_role,
                           interpretation.call,
                           interpretation.cq,
                           interpretation.error
                    FROM pcr_interpretation AS interpretation
                    JOIN pcr_run_analysis AS analysis
                      ON analysis.derivation_id = interpretation.derivation_id
                     AND analysis.pcr_run_id = interpretation.pcr_run_id
                    JOIN pcr_well_classification AS classification
                      ON classification.derivation_id = interpretation.derivation_id
                     AND classification.pcr_well_id = interpretation.pcr_well_id
                    WHERE interpretation.derivation_id = :derivation_id
                      AND interpretation.canonical_target != :failed_target
                    """
                ),
                {
                    "derivation_id": derivation_id,
                    "failed_target": PCRTargets.fail,
                },
            ).all()
        return [
            QCObservation(
                interpretation_id=int(row[0]),
                run_id=int(row[1]),
                target=str(row[2]),
                source_target=str(row[3]),
                run_class=str(row[4]),
                role=str(row[5]),
                call=str(row[6]),
                cq=float(row[7]) if row[7] is not None else None,
                error=_optional_str(row[8]),
            )
            for row in rows
        ]

    def insert_target_qc(self, rows: list[TargetQC]) -> None:
        """Persist per-target quality assessments."""

        self._insert_many(
            """
            INSERT INTO pcr_target_qc (
                derivation_id, pcr_run_id, canonical_target, qc_status,
                is_valid, positive_controls, negative_controls,
                warnings_json, details_json
            ) VALUES (
                :derivation_id, :run_id, :target, :status,
                :valid, :positive, :negative, :warnings, :details
            )
            """,
            rows,
        )

    def insert_target_qc_evidence(self, rows: list[TargetQCEvidence]) -> None:
        """Persist the control interpretations used by each target QC assessment."""

        self._insert_many(
            """
            INSERT INTO pcr_target_qc_evidence (
                derivation_id, pcr_run_id, canonical_target, interpretation_id,
                well_role
            ) VALUES (
                :derivation_id, :run_id, :target, :interpretation_id, :well_role
            )
            """,
            rows,
        )

    def insert_run_qc(self, rows: list[RunQC]) -> None:
        """Persist aggregate run quality assessments."""

        self._insert_many(
            """
            INSERT INTO pcr_run_qc (
                derivation_id, pcr_run_id, qc_status, valid_targets,
                invalid_targets, warning_count
            ) VALUES (
                :derivation_id, :run_id, :status, :valid,
                :invalid, :warnings
            )
            """,
            rows,
        )

    def fetch_episode_inputs(
        self, derivation_id: int
    ) -> tuple[list[tuple[int, str, str | None]], dict[int, set[int]]]:
        """Load classified runs and their real sample membership."""

        with self.engine.connect() as conn:
            run_rows = conn.execute(
                text(
                    """
                    SELECT pcr_run_id, run_class, episode_date_key
                    FROM pcr_run_analysis WHERE derivation_id = :derivation_id
                    """
                ),
                {"derivation_id": derivation_id},
            ).all()
            sample_rows = conn.execute(
                text(
                    """
                    SELECT well.pcr_run_id, classification.pcr_sample_id
                    FROM pcr_well AS well
                    JOIN pcr_well_classification AS classification
                      ON classification.pcr_well_id = well.pcr_well_id
                    WHERE classification.derivation_id = :derivation_id
                      AND classification.well_role = :real_sample
                    GROUP BY well.pcr_run_id, classification.pcr_sample_id
                    """
                ),
                {
                    "derivation_id": derivation_id,
                    "real_sample": WellRoles.real_sample,
                },
            ).all()
        samples: dict[int, set[int]] = defaultdict(set)
        for run_id, sample_id in sample_rows:
            samples[int(run_id)].add(int(sample_id))
        runs = [(int(row[0]), str(row[1]), _optional_str(row[2])) for row in run_rows]
        return runs, samples

    def insert_episode(
        self,
        *,
        derivation_id: int,
        episode_key: str,
        date_key: str | None,
        status: str,
        review: str | None,
        memberships: list[EpisodeMembership],
    ) -> int:
        """Atomically persist an episode and all its run memberships."""

        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO pcr_episode (
                        derivation_id, episode_key, episode_date_key,
                        association_status, review_reason
                    ) VALUES (
                        :derivation_id, :episode_key, :date_key, :status, :review
                    )
                    """
                ),
                {
                    "derivation_id": derivation_id,
                    "episode_key": episode_key,
                    "date_key": date_key,
                    "status": status,
                    "review": review,
                },
            )
            episode_id = int(result.lastrowid)
            conn.execute(
                text(
                    """
                    INSERT INTO pcr_episode_run (
                        episode_id, pcr_run_id, run_role,
                        overlap_score, association_status
                    ) VALUES (
                        :episode_id, :run_id, :role, :score, :status
                    )
                    """
                ),
                [
                    {
                        "episode_id": episode_id,
                        "run_id": membership.run_id,
                        "role": membership.role,
                        "score": membership.score,
                        "status": status,
                    }
                    for membership in memberships
                ],
            )
            return episode_id

    def fetch_result_candidates(self, derivation_id: int) -> list[ResultCandidate]:
        """Load real-sample interpretations eligible for result selection."""

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT interpretation.interpretation_id,
                           interpretation.pcr_cq_result_id,
                           classification.pcr_sample_id,
                           sample.sample_key,
                           interpretation.canonical_target, interpretation.call,
                           interpretation.pcr_run_id, analysis.run_started,
                           analysis.run_class, analysis.run_type, qc.is_valid
                    FROM pcr_interpretation AS interpretation
                    JOIN pcr_well_classification AS classification
                      ON classification.derivation_id = interpretation.derivation_id
                     AND classification.pcr_well_id = interpretation.pcr_well_id
                    JOIN pcr_run_analysis AS analysis
                      ON analysis.derivation_id = interpretation.derivation_id
                     AND analysis.pcr_run_id = interpretation.pcr_run_id
                    JOIN pcr_sample AS sample
                      ON sample.pcr_sample_id=classification.pcr_sample_id
                    LEFT JOIN pcr_target_qc AS qc
                      ON qc.derivation_id = interpretation.derivation_id
                     AND qc.pcr_run_id = interpretation.pcr_run_id
                     AND qc.canonical_target = interpretation.canonical_target
                    WHERE interpretation.derivation_id = :derivation_id
                      AND classification.well_role = :real_sample
                    """
                ),
                {
                    "derivation_id": derivation_id,
                    "real_sample": WellRoles.real_sample,
                },
            ).all()
        return [
            ResultCandidate(
                interpretation_id=int(row[0]),
                result_id=int(row[1]),
                sample_id=int(row[2]),
                sample_key=str(row[3]),
                target=str(row[4]),
                call=str(row[5]),
                run_id=int(row[6]),
                started=_optional_str(row[7]),
                run_class=str(row[8]),
                run_type=str(row[9]),
                valid=bool(row[10]),
            )
            for row in rows
        ]

    def insert_result_selections(self, rows: list[ResultSelection]) -> None:
        """Persist preferred interpretations and concordance metadata."""

        self._insert_many(
            """
            INSERT INTO pcr_result_selection (
                derivation_id, pcr_sample_id, run_type, canonical_target,
                preferred_interpretation_id, occurrence_count,
                relation_kind, concordance, needs_review, details_json
            ) VALUES (
                :derivation_id, :sample_id, :run_type, :target, :preferred, :count,
                :relation, :concordance, :review, :details
            )
            """,
            rows,
        )

    def count_review_items(self, derivation_id: int) -> int:
        """Count all reviewable records produced by a derivation."""

        with self.engine.connect() as conn:
            return sum(
                int(value)
                for value in conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM pcr_well_classification
                        WHERE derivation_id=:id AND review_reason IS NOT NULL
                        UNION ALL
                        SELECT COUNT(*) FROM pcr_run_analysis
                        WHERE derivation_id=:id AND review_reason IS NOT NULL
                        UNION ALL
                        SELECT COUNT(*) FROM pcr_episode
                        WHERE derivation_id=:id AND association_status='REVIEW'
                        UNION ALL
                        SELECT COUNT(*) FROM pcr_result_selection
                        WHERE derivation_id=:id AND needs_review=1
                        """
                    ),
                    {"id": derivation_id},
                ).scalars()
            )

    def _insert_many(self, statement: str, rows: Iterable[Any]) -> None:
        """Serialize dataclass rows and execute a batched insert transaction."""

        payload = [asdict(row) for row in rows]
        if not payload:
            return
        query = text(statement)
        with self.engine.begin() as conn:
            for offset in range(0, len(payload), 2000):
                conn.execute(query, payload[offset : offset + 2000])


def _optional_str(value: Any) -> str | None:
    """Convert a nullable database value to its string representation."""

    return None if value is None else str(value)


def _clear_derived_model(conn: Connection) -> None:
    """Delete rebuildable tables using an existing transaction."""

    for table in (
        "pcr_result_selection",
        "pcr_episode_run",
        "pcr_episode",
        "pcr_run_qc",
        "pcr_target_qc_evidence",
        "pcr_target_qc",
        "pcr_interpretation",
        "pcr_run_analysis",
        "pcr_well_classification",
        "pcr_sample",
        "pcr_derivation_run",
    ):
        conn.execute(text(f"DELETE FROM {table}"))  # noqa: B608
