"""SQL persistence boundary for PCR analytical derivations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ..enums import DerivationStatuses, PCRMeasurementKinds, WellRoles
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
    WellClassification,
)

RULE_VERSION = "1.0"


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
        """Mark a derivation terminal and store its execution details."""

        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE pcr_derivation_run
                    SET status=:status, details_json=:details,
                        finished_at=CURRENT_TIMESTAMP
                    WHERE derivation_id=:derivation_id
                    """
                ),
                {
                    "status": status,
                    "details": details_json,
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
                    SELECT run.pcr_run_id, run.run_name, started.value_json
                    FROM pcr_run AS run
                    LEFT JOIN pcr_run_attribute AS started
                      ON started.pcr_run_id = run.pcr_run_id
                     AND started.attribute_key_normalized = 'run_started'
                     AND started.is_canonical = 1
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
                started_json=_optional_str(row[2]),
                targets=frozenset(targets_by_run[int(row[0])]),
            )
            for row in run_rows
        ]

    def insert_run_analyses(self, rows: list[RunAnalysis]) -> None:
        """Persist run classifications and normalized metadata."""

        self._insert_many(
            """
            INSERT INTO pcr_run_analysis (
                derivation_id, pcr_run_id, run_class, run_started,
                episode_date_key, canonical_targets_json, review_reason
            ) VALUES (
                :derivation_id, :run_id, :run_class, :run_started,
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
                    SELECT interpretation.pcr_run_id,
                           interpretation.canonical_target,
                           classification.well_role, interpretation.call
                    FROM pcr_interpretation AS interpretation
                    JOIN pcr_well_classification AS classification
                      ON classification.derivation_id = interpretation.derivation_id
                     AND classification.pcr_well_id = interpretation.pcr_well_id
                    WHERE interpretation.derivation_id = :derivation_id
                    """
                ),
                {"derivation_id": derivation_id},
            ).all()
        return [
            QCObservation(int(row[0]), str(row[1]), str(row[2]), str(row[3]))
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
                           classification.pcr_sample_id,
                           interpretation.canonical_target, interpretation.call,
                           interpretation.pcr_run_id, analysis.run_started,
                           analysis.run_class, qc.is_valid
                    FROM pcr_interpretation AS interpretation
                    JOIN pcr_well_classification AS classification
                      ON classification.derivation_id = interpretation.derivation_id
                     AND classification.pcr_well_id = interpretation.pcr_well_id
                    JOIN pcr_run_analysis AS analysis
                      ON analysis.derivation_id = interpretation.derivation_id
                     AND analysis.pcr_run_id = interpretation.pcr_run_id
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
                sample_id=int(row[1]),
                target=str(row[2]),
                call=str(row[3]),
                run_id=int(row[4]),
                started=_optional_str(row[5]),
                run_class=str(row[6]),
                valid=bool(row[7]),
            )
            for row in rows
        ]

    def insert_result_selections(self, rows: list[ResultSelection]) -> None:
        """Persist preferred interpretations and concordance metadata."""

        self._insert_many(
            """
            INSERT INTO pcr_result_selection (
                derivation_id, pcr_sample_id, canonical_target,
                preferred_interpretation_id, occurrence_count,
                relation_kind, concordance, needs_review, details_json
            ) VALUES (
                :derivation_id, :sample_id, :target, :preferred, :count,
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

    def create_views(self) -> None:
        """Recreate public views over the latest successful derivation."""

        views = _analytical_views()
        with self.engine.begin() as conn:
            for name, select_sql in views.items():
                conn.execute(text(f"DROP VIEW IF EXISTS {name}"))  # noqa: B608
                conn.execute(text(f"CREATE VIEW {name} AS {select_sql}"))  # noqa: B608

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
        "pcr_target_qc",
        "pcr_interpretation",
        "pcr_run_analysis",
        "pcr_well_classification",
        "pcr_sample",
        "pcr_derivation_run",
    ):
        conn.execute(text(f"DELETE FROM {table}"))  # noqa: B608


def _analytical_views() -> dict[str, str]:
    """Return SQL definitions for stable public PCR views."""

    latest = "SELECT MAX(derivation_id) FROM pcr_derivation_run WHERE status='SUCCESS'"
    return {
        "v_pcr_current_result": f"""
            SELECT sample.sample_key, selection.canonical_target,
                   interpretation.call, interpretation.cq,
                   interpretation.peak_temperature, interpretation.pcr_run_id,
                   selection.occurrence_count, selection.relation_kind,
                   selection.concordance, selection.needs_review
            FROM pcr_result_selection AS selection
            JOIN pcr_sample AS sample
              ON sample.pcr_sample_id = selection.pcr_sample_id
            LEFT JOIN pcr_interpretation AS interpretation
              ON interpretation.interpretation_id =
                 selection.preferred_interpretation_id
            WHERE selection.derivation_id = ({latest})
        """,
        "v_pcr_result_history": f"""
            SELECT sample.sample_key, interpretation.canonical_target,
                   interpretation.call, interpretation.cq,
                   interpretation.peak_temperature, interpretation.pcr_run_id,
                   analysis.run_started, analysis.run_class
            FROM pcr_interpretation AS interpretation
            JOIN pcr_well_classification AS classification
              ON classification.derivation_id=interpretation.derivation_id
             AND classification.pcr_well_id=interpretation.pcr_well_id
            JOIN pcr_sample AS sample
              ON sample.pcr_sample_id=classification.pcr_sample_id
            JOIN pcr_run_analysis AS analysis
              ON analysis.derivation_id=interpretation.derivation_id
             AND analysis.pcr_run_id=interpretation.pcr_run_id
            WHERE interpretation.derivation_id=({latest})
        """,
        "v_pcr_run_qc": f"""
            SELECT run_qc.*, run.run_name FROM pcr_run_qc AS run_qc
            JOIN pcr_run AS run ON run.pcr_run_id=run_qc.pcr_run_id
            WHERE run_qc.derivation_id=({latest})
        """,
        "v_pcr_episodes": f"""
            SELECT episode.episode_key, episode.association_status,
                   episode_run.pcr_run_id, episode_run.run_role,
                   episode_run.overlap_score
            FROM pcr_episode AS episode
            JOIN pcr_episode_run AS episode_run
              ON episode_run.episode_id=episode.episode_id
            WHERE episode.derivation_id=({latest})
        """,
        "v_pcr_review_queue": f"""
            SELECT 'WELL' item_type, CAST(pcr_well_id AS TEXT) item_id,
                   review_reason reason FROM pcr_well_classification
            WHERE review_reason IS NOT NULL AND derivation_id=({latest})
            UNION ALL
            SELECT 'RUN', CAST(pcr_run_id AS TEXT), review_reason
            FROM pcr_run_analysis
            WHERE review_reason IS NOT NULL AND derivation_id=({latest})
            UNION ALL
            SELECT 'EPISODE', episode_key, review_reason FROM pcr_episode
            WHERE association_status='REVIEW' AND derivation_id=({latest})
            UNION ALL
            SELECT 'TARGET_QC', CAST(pcr_run_id AS TEXT) || ':' || canonical_target,
                   details_json FROM pcr_target_qc
            WHERE qc_status='INVALID' AND derivation_id=({latest})
            UNION ALL
            SELECT 'RESULT', CAST(pcr_sample_id AS TEXT) || ':' || canonical_target,
                   concordance FROM pcr_result_selection
            WHERE needs_review=1 AND derivation_id=({latest})
        """,
    }
