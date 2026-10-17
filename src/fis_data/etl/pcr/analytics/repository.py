"""SQL persistence boundary for PCR analytical derivations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
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
from .sil_linking import IRRELEVANT_MICROORGANISM_PATTERN

RULE_VERSION = "1.0"


def _sil_relevant_isolate_predicate(alias: str) -> str:
    """Return the PostgreSQL predicate that excludes non-SIL-FIS organisms."""

    return f"""
        NOT EXISTS (
            SELECT 1
            FROM ref_microb_catalog_entry AS ignored_organism
            WHERE ignored_organism.ref_microb_catalog_entry_id={alias}.microorganism_id
              AND ignored_organism.description ~* :irrelevant_organism_pattern
        )
    """


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
                    RETURNING derivation_id
                    """
                ),
                {
                    "algorithm_version": algorithm_version,
                    "rule_version": RULE_VERSION,
                    "parameters": parameters_json,
                    "status": DerivationStatuses.running,
                },
            )
            return int(result.scalar_one())

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
                        SET is_current=FALSE
                        WHERE is_current=TRUE AND derivation_id<>:derivation_id
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

    def count_wells(self) -> int:
        """Count source wells included in the current analytical scope."""

        with self.engine.connect() as conn:
            return int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM pcr_well AS well
                        LEFT JOIN pcr_run_scope AS scope
                          ON scope.pcr_run_id=well.pcr_run_id
                        WHERE COALESCE(scope.analytical_scope, 'ROUTINE') != 'EXPERIMENTAL'
                        """
                    )
                ).scalar_one()
            )

    def iter_wells(self, *, batch_size: int = 5_000) -> Iterable[list[SourceWell]]:
        """Read scoped source wells in keyset batches without holding a cursor open."""

        last_well_id = 0
        while True:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT well.pcr_well_id, well.sample_name, well.content
                        FROM pcr_well AS well
                        LEFT JOIN pcr_run_scope AS scope
                          ON scope.pcr_run_id=well.pcr_run_id
                        WHERE well.pcr_well_id > :last_well_id
                          AND COALESCE(scope.analytical_scope, 'ROUTINE') != 'EXPERIMENTAL'
                        ORDER BY well.pcr_well_id
                        LIMIT :batch_size
                        """
                    ),
                    {"last_well_id": last_well_id, "batch_size": batch_size},
                ).all()
            if not rows:
                return
            last_well_id = int(rows[-1][0])
            yield [
                SourceWell(int(row[0]), _optional_str(row[1]), _optional_str(row[2]))
                for row in rows
            ]

    def ensure_samples(self, sample_keys: list[str]) -> dict[str, int]:
        """Upsert canonical samples and return identifiers keyed by sample key."""

        if not sample_keys:
            return {}
        with self.engine.begin() as conn:
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
                    text(
                        """
                        SELECT sample_key, pcr_sample_id
                        FROM pcr_sample
                        WHERE sample_key IN :sample_keys
                        """
                    ).bindparams(bindparam("sample_keys", expanding=True)),
                    {"sample_keys": sample_keys},
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

    def validate_microb_samples(self, derivation_id: int) -> None:
        """Audit exact PCR/Microb matches and gate clinical PCR results."""

        if self.engine.dialect.name != "postgresql":
            return
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    WITH pcr_input_samples AS (
                        SELECT DISTINCT sample.sample_key
                        FROM pcr_well_classification AS classification
                        JOIN pcr_sample AS sample
                          ON sample.pcr_sample_id = classification.pcr_sample_id
                        WHERE classification.derivation_id = :derivation_id
                    ),
                    matched_samples AS (
                        SELECT input.sample_key, microb.sample_id,
                               specimen.code = '068' AS specimen_ok,
                               EXISTS (
                                   SELECT 1
                                   FROM microb_sample_analysis AS analysis
                                   WHERE analysis.sample_id = microb.sample_id
                                     AND analysis.analysis_code = 'PMDR'
                                     AND analysis.analysis_kind = 'BACTERIOLOGICAL'
                               ) AS analysis_ok
                        FROM pcr_input_samples AS input
                        JOIN microb_sample AS microb
                          ON microb.sample_number = input.sample_key
                        LEFT JOIN ref_microb_catalog_entry AS specimen
                          ON specimen.ref_microb_catalog_entry_id = microb.specimen_type_id
                    ),
                    match_summary AS (
                        SELECT sample_key,
                               COUNT(*) AS match_count,
                               MIN(sample_id) AS microb_sample_id,
                               COALESCE(BOOL_OR(specimen_ok), FALSE) AS specimen_ok,
                               COALESCE(BOOL_OR(analysis_ok), FALSE) AS analysis_ok
                        FROM matched_samples
                        GROUP BY sample_key
                    )
                    INSERT INTO pcr_microb_well_validation (
                        derivation_id, pcr_well_id, pcr_sample_id, microb_sample_id,
                        validation_status, validation_reason, details_json
                    )
                    SELECT classification.derivation_id, classification.pcr_well_id,
                           classification.pcr_sample_id,
                           CASE WHEN COALESCE(summary.match_count, 0) = 1 THEN summary.microb_sample_id END,
                           CASE
                               WHEN classification.well_role <> 'REAL_SAMPLE' THEN 'CONTROL_EXCLUDED'
                               WHEN classification.review_reason IS NOT NULL THEN 'REAL_SAMPLE_REVIEW_EXCLUDED'
                               WHEN COALESCE(summary.match_count, 0) = 0 THEN 'REAL_SAMPLE_NO_MATCH'
                               WHEN summary.match_count > 1 THEN 'REAL_SAMPLE_AMBIGUOUS'
                               WHEN COALESCE(summary.specimen_ok, FALSE) AND COALESCE(summary.analysis_ok, FALSE) THEN 'MATCH_ELIGIBLE'
                               WHEN NOT COALESCE(summary.specimen_ok, FALSE) AND NOT COALESCE(summary.analysis_ok, FALSE) THEN 'MATCH_INVALID_SPECIMEN_AND_ANALYSIS'
                               WHEN NOT COALESCE(summary.specimen_ok, FALSE) THEN 'MATCH_INVALID_SPECIMEN'
                               ELSE 'MATCH_INVALID_ANALYSIS'
                           END,
                           CASE
                               WHEN classification.well_role <> 'REAL_SAMPLE' THEN 'PCR control or non-sample well.'
                               WHEN classification.review_reason IS NOT NULL THEN classification.review_reason
                               WHEN COALESCE(summary.match_count, 0) = 0 THEN 'No exact Microb sample_number match.'
                               WHEN summary.match_count > 1 THEN 'More than one Microb sample has this sample_number.'
                               WHEN NOT COALESCE(summary.specimen_ok, FALSE) AND NOT COALESCE(summary.analysis_ok, FALSE) THEN 'Microb match is neither specimen 068 nor bacteriological PMDR.'
                               WHEN NOT COALESCE(summary.specimen_ok, FALSE) THEN 'Microb match is not specimen 068 (RECTAL).'
                               WHEN NOT COALESCE(summary.analysis_ok, FALSE) THEN 'Microb match lacks bacteriological PMDR analysis.'
                               ELSE NULL
                           END,
                           jsonb_build_object('match_count', COALESCE(summary.match_count, 0),
                               'specimen_068', COALESCE(summary.specimen_ok, FALSE),
                               'bacteriological_pmdr', COALESCE(summary.analysis_ok, FALSE))
                    FROM pcr_well_classification AS classification
                    LEFT JOIN pcr_sample AS pcr_sample
                      ON pcr_sample.pcr_sample_id = classification.pcr_sample_id
                    LEFT JOIN match_summary AS summary
                      ON summary.sample_key = pcr_sample.sample_key
                    WHERE classification.derivation_id = :derivation_id
                    """
                ),
                {"derivation_id": derivation_id},
            )

    def apply_legacy_screening_gate(
        self, derivation_id: int, *, cutoff: datetime
    ) -> int:
        """Gate sample-specific runs after a pre-cutoff screening PCR.

        A legacy screening run blocks all later specific PCRs for the sample.
        The first routine screening run resets that block; only specific runs
        on or after that reset are eligible again.  Raw legacy evidence stays
        untouched and the decision is persisted for audit.
        """

        if self.engine.dialect.name != "postgresql":
            return 0
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    WITH derived_samples AS (
                        SELECT DISTINCT classification.pcr_sample_id
                        FROM pcr_well_classification AS classification
                        WHERE classification.derivation_id=:derivation_id
                          AND classification.pcr_sample_id IS NOT NULL
                    ), screening_runs AS (
                        SELECT DISTINCT sample.pcr_sample_id, run.pcr_run_id,
                               run.run_started
                        FROM derived_samples AS sample
                        JOIN pcr_sample AS pcr_sample
                          ON pcr_sample.pcr_sample_id=sample.pcr_sample_id
                        JOIN pcr_well AS well
                          ON btrim(well.sample_name)=pcr_sample.sample_key
                        JOIN pcr_run AS run ON run.pcr_run_id=well.pcr_run_id
                        JOIN pcr_cq_result AS cq
                          ON cq.pcr_run_id=run.pcr_run_id
                        WHERE well.sample_name ~ '^[0-9]+$'
                          AND run.run_started IS NOT NULL
                          AND upper(cq.target)='SCREENING'
                    ), legacy AS (
                        SELECT DISTINCT ON (pcr_sample_id) pcr_sample_id,
                               pcr_run_id, run_started
                        FROM screening_runs
                        WHERE run_started < :cutoff
                        ORDER BY pcr_sample_id, run_started DESC, pcr_run_id DESC
                    ), reset AS (
                        SELECT DISTINCT ON (screening.pcr_sample_id)
                               screening.pcr_sample_id, screening.pcr_run_id,
                               screening.run_started
                        FROM screening_runs AS screening
                        JOIN legacy ON legacy.pcr_sample_id=screening.pcr_sample_id
                        WHERE screening.run_started >= :cutoff
                        ORDER BY screening.pcr_sample_id, screening.run_started, screening.pcr_run_id
                    )
                    INSERT INTO pcr_sample_scope_gate (
                        derivation_id, pcr_sample_id, legacy_screening_run_id,
                        reset_screening_run_id, gate_status, reason, details_json
                    )
                    SELECT :derivation_id, sample.pcr_sample_id,
                           legacy.pcr_run_id, reset.pcr_run_id,
                           CASE WHEN legacy.pcr_run_id IS NULL THEN 'NOT_APPLICABLE'
                                WHEN reset.pcr_run_id IS NULL THEN 'LEGACY_SCREENING_BLOCKED'
                                ELSE 'RESET_BY_ROUTINE_SCREENING' END,
                           CASE WHEN legacy.pcr_run_id IS NULL THEN NULL
                                WHEN reset.pcr_run_id IS NULL THEN 'Pre-cutoff screening blocks all later specific PCR results.'
                                ELSE 'Pre-cutoff screening blocks specific PCR results until the routine screening reset.' END,
                           jsonb_build_object(
                             'cutoff', :cutoff,
                             'legacy_screening_started_at', legacy.run_started,
                             'reset_screening_started_at', reset.run_started
                           )
                    FROM derived_samples AS sample
                    LEFT JOIN legacy ON legacy.pcr_sample_id=sample.pcr_sample_id
                    LEFT JOIN reset ON reset.pcr_sample_id=sample.pcr_sample_id
                    """
                ),
                {"derivation_id": derivation_id, "cutoff": cutoff},
            )
            return int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM pcr_sample_scope_gate
                        WHERE derivation_id=:derivation_id
                          AND gate_status='LEGACY_SCREENING_BLOCKED'
                        """
                    ),
                    {"derivation_id": derivation_id},
                ).scalar_one()
            )

    def link_sil_results(
        self,
        derivation_id: int,
        *,
        policy_version: str,
        max_days: int,
        max_negatives: int,
    ) -> int:
        """Resolve one culture-level SIL profile for each eligible PCR sample.

        Direct evidence is the binary union of the current culture's relevant
        isolates.  When an uncharacterized current isolate starts a historical
        search, the first compatible prior sample found for *any* current
        organism becomes the sole retrospective evidence sample. Only markers
        attached to exact current organism codes are inherited. An unmatched
        current organism stays 0000; it never starts another search.
        """

        if self.engine.dialect.name != "postgresql":
            return 0
        params = {
            "derivation_id": derivation_id,
            "policy_version": policy_version,
            "max_days": max_days,
            "max_negatives": max_negatives,
            "irrelevant_organism_pattern": IRRELEVANT_MICROORGANISM_PATTERN,
        }
        relevant_isolate = _sil_relevant_isolate_predicate("isolate")
        relevant_current_isolate = _sil_relevant_isolate_predicate("current_isolate")
        relevant_prior_isolate = _sil_relevant_isolate_predicate("prior_isolate")
        with self.engine.begin() as conn:
            conn.execute(text(f"""
                WITH eligible AS (
                    SELECT DISTINCT validation.pcr_sample_id, validation.microb_sample_id
                    FROM pcr_microb_well_validation AS validation
                    LEFT JOIN pcr_sample_scope_gate AS gate
                      ON gate.derivation_id=validation.derivation_id
                     AND gate.pcr_sample_id=validation.pcr_sample_id
                    WHERE validation.derivation_id=:derivation_id
                      AND validation.validation_status='MATCH_ELIGIBLE'
                      AND COALESCE(gate.gate_status, 'NOT_APPLICABLE') <> 'LEGACY_SCREENING_BLOCKED'
                      AND validation.pcr_sample_id IS NOT NULL
                      AND validation.microb_sample_id IS NOT NULL
                ), pmdr AS (
                    SELECT eligible.microb_sample_id, analysis.sample_analysis_id
                    FROM eligible
                    JOIN microb_sample_analysis AS analysis
                      ON analysis.sample_id=eligible.microb_sample_id
                     AND analysis.analysis_code='PMDR'
                     AND analysis.analysis_kind='BACTERIOLOGICAL'
                ), classified AS (
                    SELECT pmdr.microb_sample_id, pmdr.sample_analysis_id,
                           CASE
                             WHEN EXISTS (
                               SELECT 1 FROM microb_analysis_result AS result
                               JOIN microb_isolate AS isolate
                                 ON isolate.analysis_result_id=result.analysis_result_id
                               WHERE result.sample_analysis_id=pmdr.sample_analysis_id
                                 AND isolate.microorganism_code IS NOT NULL
                                 AND {relevant_isolate}
                             ) THEN 'CULTURE_POSITIVE'
                             WHEN EXISTS (
                               SELECT 1 FROM microb_analysis_result AS result
                               JOIN microb_isolate AS isolate
                                 ON isolate.analysis_result_id=result.analysis_result_id
                               WHERE result.sample_analysis_id=pmdr.sample_analysis_id
                                 AND isolate.microorganism_code IS NOT NULL
                             ) THEN 'CULTURE_NEGATIVE'
                             WHEN NOT EXISTS (
                               SELECT 1 FROM microb_analysis_result AS result
                               JOIN microb_isolate AS isolate
                                 ON isolate.analysis_result_id=result.analysis_result_id
                               WHERE result.sample_analysis_id=pmdr.sample_analysis_id
                                 AND isolate.microorganism_code IS NOT NULL
                             ) AND EXISTS (
                               SELECT 1 FROM microb_analysis_result AS result
                               WHERE result.sample_analysis_id=pmdr.sample_analysis_id
                                 AND result.result_code IN ('NEG')
                             ) THEN 'CULTURE_NEGATIVE'
                             WHEN NOT EXISTS (
                               SELECT 1 FROM microb_analysis_result AS result
                               JOIN microb_isolate AS isolate
                                 ON isolate.analysis_result_id=result.analysis_result_id
                               WHERE result.sample_analysis_id=pmdr.sample_analysis_id
                             ) AND EXISTS (
                               SELECT 1 FROM microb_analysis_result AS result
                               WHERE result.sample_analysis_id=pmdr.sample_analysis_id
                                 AND result.result_code IN ('.04', '.05', '1', 'C06', 'NPR', 'PEN')
                             ) THEN 'CULTURE_NOT_PROCESSED'
                             ELSE 'CULTURE_UNCLASSIFIED'
                           END AS culture_category,
                           COALESCE((
                             SELECT jsonb_agg(DISTINCT result.result_code ORDER BY result.result_code)
                             FROM microb_analysis_result AS result
                             WHERE result.sample_analysis_id=pmdr.sample_analysis_id
                           ), '[]'::jsonb) AS result_codes
                    FROM pmdr
                )
                INSERT INTO pcr_sil_culture_classification (
                    derivation_id, microb_sample_id, sample_analysis_id,
                    culture_category, result_codes_json, policy_version, details_json
                )
                SELECT :derivation_id, classified.microb_sample_id, classified.sample_analysis_id,
                       classified.culture_category, classified.result_codes,
                       :policy_version,
                       jsonb_build_object(
                           'classification_source', 'PMDR_BACTERIOLOGICAL',
                           'irrelevant_organism_pattern', :irrelevant_organism_pattern
                       )
                FROM classified
            """), params)
            # Select one prior sample for the whole current culture.  The
            # current organisms are iterated as a set, but the nearest prior
            # sample containing any of them stops the search for all of them.
            conn.execute(text(f"""
                WITH eligible AS (
                    SELECT DISTINCT validation.pcr_sample_id, validation.microb_sample_id
                    FROM pcr_microb_well_validation AS validation
                    LEFT JOIN pcr_sample_scope_gate AS gate
                      ON gate.derivation_id=validation.derivation_id
                     AND gate.pcr_sample_id=validation.pcr_sample_id
                    WHERE validation.derivation_id=:derivation_id
                      AND validation.validation_status='MATCH_ELIGIBLE'
                      AND COALESCE(gate.gate_status, 'NOT_APPLICABLE') <> 'LEGACY_SCREENING_BLOCKED'
                ), current_organisms AS (
                    SELECT DISTINCT eligible.pcr_sample_id, eligible.microb_sample_id AS current_sample_id,
                           current.patient_id, COALESCE(current.received_at, current.entered_at) AS current_at,
                           isolate.isolate_id AS current_isolate_id, isolate.microorganism_code,
                           NOT EXISTS (
                               SELECT 1 FROM microb_susceptibility_result AS susceptibility
                               WHERE susceptibility.isolate_id=isolate.isolate_id
                                 AND susceptibility.interpretation_code='P'
                                 AND susceptibility.antibiotic_code IN ('BLE', 'OXA', 'KPC', 'NDM', 'VIM')
                           ) AS is_uncharacterized
                    FROM eligible
                    JOIN microb_sample AS current ON current.sample_id=eligible.microb_sample_id
                    JOIN pcr_sil_culture_classification AS classification
                      ON classification.derivation_id=:derivation_id
                     AND classification.microb_sample_id=current.sample_id
                     AND classification.culture_category='CULTURE_POSITIVE'
                    JOIN microb_analysis_result AS result ON result.sample_analysis_id=classification.sample_analysis_id
                    JOIN microb_isolate AS isolate
                      ON isolate.analysis_result_id=result.analysis_result_id
                     AND isolate.microorganism_code IS NOT NULL
                     AND {relevant_isolate}
                ), seeds AS (
                    SELECT DISTINCT pcr_sample_id, current_sample_id, patient_id, current_at
                    FROM current_organisms
                    WHERE patient_id IS NOT NULL AND current_at IS NOT NULL
                      AND is_uncharacterized
                ), previous_samples AS (
                    SELECT seeds.pcr_sample_id, seeds.current_sample_id,
                           previous.sample_id AS previous_sample_id,
                           COALESCE(previous.received_at, previous.entered_at) AS previous_at,
                           analysis.sample_analysis_id,
                           EXISTS (
                             SELECT 1 FROM microb_analysis_result AS prior_result
                             JOIN microb_isolate AS prior_isolate
                               ON prior_isolate.analysis_result_id=prior_result.analysis_result_id
                             WHERE prior_result.sample_analysis_id=analysis.sample_analysis_id
                               AND prior_isolate.microorganism_code IS NOT NULL
                               AND {relevant_prior_isolate}
                           ) AS is_positive,
                           EXISTS (
                             SELECT 1 FROM microb_analysis_result AS prior_result
                             WHERE prior_result.sample_analysis_id=analysis.sample_analysis_id
                               AND prior_result.result_code='NEG'
                           ) OR (
                             EXISTS (
                               SELECT 1 FROM microb_analysis_result AS prior_result
                               JOIN microb_isolate AS prior_isolate
                                 ON prior_isolate.analysis_result_id=prior_result.analysis_result_id
                               WHERE prior_result.sample_analysis_id=analysis.sample_analysis_id
                                 AND prior_isolate.microorganism_code IS NOT NULL
                             )
                             AND NOT EXISTS (
                               SELECT 1 FROM microb_analysis_result AS prior_result
                               JOIN microb_isolate AS prior_isolate
                                 ON prior_isolate.analysis_result_id=prior_result.analysis_result_id
                               WHERE prior_result.sample_analysis_id=analysis.sample_analysis_id
                                 AND prior_isolate.microorganism_code IS NOT NULL
                                 AND {relevant_prior_isolate}
                             )
                           ) AS is_negative,
                           EXISTS (
                             SELECT 1
                             FROM current_organisms AS current_organism
                             JOIN microb_analysis_result AS prior_result
                               ON prior_result.sample_analysis_id=analysis.sample_analysis_id
                             JOIN microb_isolate AS prior_isolate
                               ON prior_isolate.analysis_result_id=prior_result.analysis_result_id
                              AND prior_isolate.microorganism_code=current_organism.microorganism_code
                              AND {relevant_prior_isolate}
                             WHERE current_organism.pcr_sample_id=seeds.pcr_sample_id
                               AND current_organism.current_sample_id=seeds.current_sample_id
                           ) AS has_matching_organism,
                           (
                             SELECT current_organism.microorganism_code
                             FROM current_organisms AS current_organism
                             JOIN microb_analysis_result AS prior_result
                               ON prior_result.sample_analysis_id=analysis.sample_analysis_id
                             JOIN microb_isolate AS prior_isolate
                               ON prior_isolate.analysis_result_id=prior_result.analysis_result_id
                              AND prior_isolate.microorganism_code=current_organism.microorganism_code
                              AND {relevant_prior_isolate}
                             WHERE current_organism.pcr_sample_id=seeds.pcr_sample_id
                               AND current_organism.current_sample_id=seeds.current_sample_id
                             ORDER BY current_organism.microorganism_code, current_organism.current_isolate_id
                             LIMIT 1
                           ) AS matched_microorganism_code
                    FROM seeds
                    JOIN microb_sample AS previous
                      ON previous.patient_id=seeds.patient_id
                     AND COALESCE(previous.received_at, previous.entered_at) < seeds.current_at
                     AND COALESCE(previous.received_at, previous.entered_at) >= seeds.current_at - (:max_days * INTERVAL '1 day')
                    JOIN ref_microb_catalog_entry AS specimen
                      ON specimen.ref_microb_catalog_entry_id=previous.specimen_type_id
                     AND specimen.code='068'
                    JOIN microb_sample_analysis AS analysis
                      ON analysis.sample_id=previous.sample_id
                     AND analysis.analysis_code='PMDR'
                     AND analysis.analysis_kind='BACTERIOLOGICAL'
                ), timeline AS (
                    SELECT previous_samples.*,
                           SUM(CASE WHEN is_negative AND NOT is_positive THEN 1 ELSE 0 END) OVER (
                             PARTITION BY pcr_sample_id
                             ORDER BY previous_at DESC, previous_sample_id DESC
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                           ) AS negative_count,
                           MAX(CASE WHEN is_positive AND NOT has_matching_organism THEN 1 ELSE 0 END) OVER (
                             PARTITION BY pcr_sample_id
                             ORDER BY previous_at DESC, previous_sample_id DESC
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                           ) AS organism_barrier
                    FROM previous_samples
                ), selected AS (
                    SELECT timeline.*,
                           ROW_NUMBER() OVER (
                             PARTITION BY pcr_sample_id
                             ORDER BY previous_at DESC, previous_sample_id DESC
                           ) AS position
                    FROM timeline
                    WHERE has_matching_organism
                      AND organism_barrier=0
                      AND negative_count <= :max_negatives
                )
                INSERT INTO pcr_sil_retrospective_evidence (
                    derivation_id, pcr_sample_id, current_microb_sample_id,
                    evidence_microb_sample_id, matched_microorganism_code,
                    evidence_status, days_difference, negative_count,
                    policy_version, reason, details_json
                )
                SELECT :derivation_id, selected.pcr_sample_id, selected.current_sample_id,
                       selected.previous_sample_id, selected.matched_microorganism_code,
                       'ACCEPTED',
                       EXTRACT(DAY FROM (current_sample.current_at - selected.previous_at))::integer,
                       selected.negative_count, :policy_version,
                       'Nearest compatible prior 068/PMDR sample for a current organism.',
                       jsonb_build_object('search_scope', 'ALL_CURRENT_ORGANISMS',
                                          'stops_after_first_match', TRUE)
                FROM selected
                JOIN seeds AS current_sample ON current_sample.pcr_sample_id=selected.pcr_sample_id
                WHERE selected.position=1
            """), params)
            conn.execute(text("""
                WITH eligible AS (
                    SELECT DISTINCT validation.pcr_sample_id, validation.microb_sample_id
                    FROM pcr_microb_well_validation AS validation
                    LEFT JOIN pcr_sample_scope_gate AS gate
                      ON gate.derivation_id=validation.derivation_id
                     AND gate.pcr_sample_id=validation.pcr_sample_id
                    WHERE validation.derivation_id=:derivation_id
                      AND validation.validation_status='MATCH_ELIGIBLE'
                      AND COALESCE(gate.gate_status, 'NOT_APPLICABLE') <> 'LEGACY_SCREENING_BLOCKED'
                )
                INSERT INTO pcr_sil_retrospective_evidence (
                    derivation_id, pcr_sample_id, current_microb_sample_id,
                    evidence_microb_sample_id, matched_microorganism_code,
                    evidence_status, days_difference, negative_count,
                    policy_version, reason, details_json
                )
                SELECT :derivation_id, eligible.pcr_sample_id, eligible.microb_sample_id,
                       NULL, NULL,
                       CASE WHEN classification.culture_category IN ('CULTURE_NEGATIVE', 'CULTURE_NOT_PROCESSED')
                            THEN 'NOT_APPLICABLE' ELSE 'NO_EVIDENCE' END,
                       NULL, NULL,
                       :policy_version,
                       CASE WHEN classification.culture_category IN ('CULTURE_NEGATIVE', 'CULTURE_NOT_PROCESSED')
                              THEN 'Current culture category does not start retrospective search.'
                            ELSE 'No compatible prior sample for a current uncharacterized organism was found.' END,
                       jsonb_build_object('culture_category', classification.culture_category)
                FROM eligible
                JOIN pcr_sil_culture_classification AS classification
                  ON classification.derivation_id=:derivation_id
                 AND classification.microb_sample_id=eligible.microb_sample_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM pcr_sil_retrospective_evidence AS accepted
                    WHERE accepted.derivation_id=:derivation_id
                      AND accepted.pcr_sample_id=eligible.pcr_sample_id
                )
            """), params)
            # Inherit for ALL original organism codes present in the sole
            # selected sample. matched_microorganism_code only audits the
            # first match that selected that sample; it is not an inheritance
            # filter. Direct observations remain in their own summaries.
            conn.execute(text(f"""
                INSERT INTO pcr_sil_link_candidate (
                    derivation_id, pcr_sample_id, current_microb_sample_id,
                    current_isolate_id, microorganism_code, evidence_microb_sample_id,
                    evidence_isolate_id, marker_code, candidate_kind, candidate_status,
                    days_difference, negative_count, policy_version, reason, details_json
                )
                SELECT :derivation_id, evidence.pcr_sample_id, evidence.current_microb_sample_id,
                       current_isolate.isolate_id, current_isolate.microorganism_code,
                       evidence.evidence_microb_sample_id, prior_isolate.isolate_id,
                       susceptibility.antibiotic_code, 'PRIOR',
                       CASE WHEN susceptibility.antibiotic_code IS NOT NULL
                            THEN 'ACCEPTED' ELSE 'NO_EVIDENCE' END,
                       evidence.days_difference, evidence.negative_count, :policy_version,
                       CASE WHEN prior_isolate.isolate_id IS NULL
                              THEN 'Original organism absent from selected prior sample; no further search.'
                            WHEN susceptibility.antibiotic_code IS NULL
                              THEN 'Matching prior organism has no positive carbapenemase marker.'
                            ELSE 'Marker inherited by exact organism code from the sole selected prior sample.' END,
                       jsonb_build_object('organism_exact_match', prior_isolate.isolate_id IS NOT NULL,
                                          'single_prior_sample', TRUE)
                FROM pcr_sil_retrospective_evidence AS evidence
                JOIN pcr_sil_culture_classification AS current_classification
                  ON current_classification.derivation_id=evidence.derivation_id
                 AND current_classification.microb_sample_id=evidence.current_microb_sample_id
                JOIN microb_analysis_result AS current_result
                  ON current_result.sample_analysis_id=current_classification.sample_analysis_id
                JOIN microb_isolate AS current_isolate
                  ON current_isolate.analysis_result_id=current_result.analysis_result_id
                 AND current_isolate.microorganism_code IS NOT NULL
                 AND {relevant_current_isolate}
                JOIN microb_sample_analysis AS prior_analysis
                  ON prior_analysis.sample_id=evidence.evidence_microb_sample_id
                 AND prior_analysis.analysis_code='PMDR'
                 AND prior_analysis.analysis_kind='BACTERIOLOGICAL'
                JOIN microb_analysis_result AS prior_result
                  ON prior_result.sample_analysis_id=prior_analysis.sample_analysis_id
                LEFT JOIN microb_isolate AS prior_isolate
                  ON prior_isolate.analysis_result_id=prior_result.analysis_result_id
                 AND prior_isolate.microorganism_code=current_isolate.microorganism_code
                 AND {relevant_prior_isolate}
                LEFT JOIN microb_susceptibility_result AS susceptibility
                  ON susceptibility.isolate_id=prior_isolate.isolate_id
                 AND susceptibility.interpretation_code='P'
                 AND susceptibility.antibiotic_code IN ('OXA', 'KPC', 'NDM', 'VIM')
                WHERE evidence.derivation_id=:derivation_id
                  AND evidence.evidence_status='ACCEPTED'
            """), params)
            result = conn.execute(text(f"""
                WITH selections AS (
                    SELECT selection.derivation_id, selection.pcr_sample_id, selection.run_type,
                           selection.canonical_target, interpretation.call
                    FROM pcr_result_selection AS selection
                    LEFT JOIN pcr_interpretation AS interpretation
                      ON interpretation.interpretation_id=selection.preferred_interpretation_id
                    WHERE selection.derivation_id=:derivation_id
                ), direct_markers AS (
                    SELECT validation.pcr_sample_id, susceptibility.antibiotic_code AS marker_code
                    FROM pcr_microb_well_validation AS validation
                    LEFT JOIN pcr_sample_scope_gate AS gate
                      ON gate.derivation_id=validation.derivation_id
                     AND gate.pcr_sample_id=validation.pcr_sample_id
                    JOIN pcr_sil_culture_classification AS classification
                      ON classification.derivation_id=validation.derivation_id
                     AND classification.microb_sample_id=validation.microb_sample_id
                    JOIN microb_analysis_result AS current_result
                      ON current_result.sample_analysis_id=classification.sample_analysis_id
                    JOIN microb_isolate AS current_isolate
                      ON current_isolate.analysis_result_id=current_result.analysis_result_id
                     AND current_isolate.microorganism_code IS NOT NULL
                     AND {relevant_current_isolate}
                    JOIN microb_susceptibility_result AS susceptibility
                      ON susceptibility.isolate_id=current_isolate.isolate_id
                     AND susceptibility.interpretation_code='P'
                     AND susceptibility.antibiotic_code IN ('OXA', 'KPC', 'NDM', 'VIM')
                    WHERE validation.derivation_id=:derivation_id
                      AND validation.validation_status='MATCH_ELIGIBLE'
                      AND COALESCE(gate.gate_status, 'NOT_APPLICABLE') <> 'LEGACY_SCREENING_BLOCKED'
                ), retrospective_markers AS (
                    SELECT DISTINCT evidence.pcr_sample_id, candidate.marker_code
                    FROM pcr_sil_retrospective_evidence AS evidence
                    JOIN pcr_sil_link_candidate AS candidate
                      ON candidate.derivation_id=evidence.derivation_id
                     AND candidate.pcr_sample_id=evidence.pcr_sample_id
                     AND candidate.current_microb_sample_id=evidence.current_microb_sample_id
                     AND candidate.evidence_microb_sample_id=evidence.evidence_microb_sample_id
                     AND candidate.candidate_kind='PRIOR'
                     AND candidate.candidate_status='ACCEPTED'
                    WHERE evidence.derivation_id=:derivation_id
                      AND evidence.evidence_status='ACCEPTED'
                ), resolved AS (
                    SELECT selections.*, evidence.evidence_status,
                           CASE WHEN evidence.evidence_status='ACCEPTED'
                                THEN 'PRIOR_SINGLE_SAMPLE'
                                ELSE 'DIRECT_CURRENT_CULTURE' END AS evidence_scope,
                           CASE WHEN evidence.evidence_status='ACCEPTED'
                                THEN EXISTS (
                                  SELECT 1 FROM retrospective_markers AS marker
                                  WHERE marker.pcr_sample_id=selections.pcr_sample_id
                                    AND marker.marker_code=CASE selections.canonical_target
                                      WHEN 'OXA48' THEN 'OXA' ELSE selections.canonical_target END
                                )
                                ELSE EXISTS (
                                  SELECT 1 FROM direct_markers AS marker
                                  WHERE marker.pcr_sample_id=selections.pcr_sample_id
                                    AND marker.marker_code=CASE selections.canonical_target
                                      WHEN 'OXA48' THEN 'OXA' ELSE selections.canonical_target END
                                )
                           END AS marker_detected
                    FROM selections
                    LEFT JOIN pcr_sil_retrospective_evidence AS evidence
                      ON evidence.derivation_id=selections.derivation_id
                     AND evidence.pcr_sample_id=selections.pcr_sample_id
                )
                INSERT INTO pcr_sil_result_link (
                    derivation_id, pcr_sample_id, run_type, canonical_target, candidate_id,
                    link_type, concordance, policy_version, reason, details_json
                )
                SELECT derivation_id, pcr_sample_id, run_type, canonical_target, NULL,
                       CASE WHEN NOT marker_detected THEN 'NO_EVIDENCE'
                            WHEN call='NEGATIVE' THEN 'DISCORDANT'
                            WHEN evidence_scope='DIRECT_CURRENT_CULTURE' THEN 'DIRECT'
                            ELSE 'PRIOR_COMPATIBLE' END,
                       CASE WHEN NOT marker_detected THEN 'NOT_ASSESSABLE'
                            WHEN call='POSITIVE' THEN 'CONCORDANT'
                            WHEN call='NEGATIVE' THEN 'DISCORDANT'
                            ELSE 'NOT_ASSESSABLE' END,
                       :policy_version,
                       CASE WHEN NOT marker_detected THEN 'No compatible carbapenemase evidence in SIL.'
                            WHEN evidence_scope='DIRECT_CURRENT_CULTURE' THEN 'Binary evidence from the current SIL culture.'
                            ELSE 'Binary evidence from the sole selected prior SIL sample.' END,
                       jsonb_build_object('pcr_call', call,
                                          'marker_code', CASE canonical_target WHEN 'OXA48' THEN 'OXA' ELSE canonical_target END,
                                          'evidence_scope', evidence_scope)
                FROM resolved
            """), params)
            return int(result.rowcount if result.rowcount is not None else 0)

    def classify_promoted_sample_results(self, derivation_id: int) -> int:
        """Create the direct, promoted PCR category and per-target ΔCq metrics."""

        if self.engine.dialect.name != "postgresql":
            return 0
        with self.engine.begin() as conn:
            result = conn.execute(text("""
                WITH promoted AS (
                    SELECT selection.pcr_sample_id, selection.run_type,
                           selection.canonical_target, interpretation.call
                    FROM pcr_result_selection AS selection
                    JOIN pcr_interpretation AS interpretation
                      ON interpretation.interpretation_id=selection.preferred_interpretation_id
                    WHERE selection.derivation_id=:derivation_id
                ), grouped AS (
                    SELECT pcr_sample_id,
                           BOOL_OR(run_type='SPECIFIC') FILTER (
                             WHERE canonical_target IN ('OXA48', 'KPC', 'NDM', 'VIM')
                           ) AS has_specific,
                           BOOL_OR(run_type='SPECIFIC' AND call='POSITIVE') FILTER (
                             WHERE canonical_target IN ('OXA48', 'KPC', 'NDM', 'VIM')
                           ) AS specific_positive,
                           COUNT(DISTINCT canonical_target) FILTER (
                             WHERE run_type='SPECIFIC'
                               AND canonical_target IN ('OXA48', 'KPC', 'NDM', 'VIM')
                               AND call='NEGATIVE'
                           ) AS specific_negative_targets,
                           BOOL_OR(run_type='SCREENING' AND call='NEGATIVE') FILTER (
                             WHERE canonical_target IN ('OXA48', 'KPC', 'NDM', 'VIM')
                           ) AS screening_negative,
                           BOOL_OR(run_type='SCREENING' AND call='POSITIVE') FILTER (
                             WHERE canonical_target IN ('OXA48', 'KPC', 'NDM', 'VIM')
                           ) AS screening_positive
                    FROM promoted
                    GROUP BY pcr_sample_id
                )
                INSERT INTO pcr_sample_result (
                    derivation_id, pcr_sample_id, pcr_category, decision_source,
                    warning_code, details_json
                )
                SELECT :derivation_id, grouped.pcr_sample_id,
                       CASE
                         WHEN has_specific AND specific_positive THEN 'PCR_POSITIVE'
                         WHEN has_specific AND specific_negative_targets=4 THEN 'PCR_NEGATIVE'
                         WHEN has_specific THEN 'PCR_UNCLASSIFIED'
                         WHEN screening_negative THEN 'PCR_NEGATIVE'
                         WHEN screening_positive THEN 'PCR_NEGATIVE'
                         ELSE 'PCR_UNCLASSIFIED'
                       END,
                       CASE WHEN has_specific THEN 'SPECIFIC' ELSE 'SCREENING' END,
                       CASE WHEN NOT has_specific AND screening_positive
                            THEN 'SCREENING_POSITIVE_WITHOUT_SPECIFIC_PHASE' END,
                       jsonb_build_object(
                         'specific_positive', COALESCE(specific_positive, FALSE),
                         'specific_negative_targets', specific_negative_targets,
                         'screening_negative', COALESCE(screening_negative, FALSE),
                         'screening_positive', COALESCE(screening_positive, FALSE)
                       )
                FROM grouped
            """), {"derivation_id": derivation_id})
            conn.execute(text("""
                WITH promoted_target AS (
                    SELECT selection.pcr_sample_id, interpretation.pcr_run_id,
                           selection.canonical_target, interpretation.interpretation_id,
                           interpretation.cq AS target_cq
                    FROM pcr_result_selection AS selection
                    JOIN pcr_interpretation AS interpretation
                      ON interpretation.interpretation_id=selection.preferred_interpretation_id
                    WHERE selection.derivation_id=:derivation_id
                      AND selection.run_type='SPECIFIC'
                      AND selection.canonical_target IN ('OXA48', 'KPC', 'NDM', 'VIM')
                ), reference_16s AS (
                    SELECT classification.pcr_sample_id, interpretation.pcr_run_id,
                           interpretation.cq,
                           ROW_NUMBER() OVER (
                             PARTITION BY classification.pcr_sample_id, interpretation.pcr_run_id
                             ORDER BY interpretation.pcr_well_id, interpretation.interpretation_id
                           ) AS position
                    FROM pcr_interpretation AS interpretation
                    JOIN pcr_well_classification AS classification
                      ON classification.derivation_id=interpretation.derivation_id
                     AND classification.pcr_well_id=interpretation.pcr_well_id
                    JOIN pcr_target_qc AS qc
                      ON qc.derivation_id=interpretation.derivation_id
                     AND qc.pcr_run_id=interpretation.pcr_run_id
                     AND qc.canonical_target=interpretation.canonical_target
                     AND qc.is_valid=TRUE
                    WHERE interpretation.derivation_id=:derivation_id
                      AND interpretation.canonical_target='16S'
                      AND classification.pcr_sample_id IS NOT NULL
                )
                INSERT INTO pcr_sample_target_metric (
                    derivation_id, pcr_sample_id, pcr_run_id, canonical_target,
                    target_interpretation_id, target_cq, reference_16s_cq, delta_cq,
                    details_json
                )
                SELECT :derivation_id, target.pcr_sample_id, target.pcr_run_id,
                       target.canonical_target, target.interpretation_id, target.target_cq,
                       reference.cq,
                       CASE WHEN target.target_cq IS NOT NULL AND reference.cq IS NOT NULL
                            THEN (target.target_cq-reference.cq) * LOG(2.0) END,
                       jsonb_build_object(
                         'formula', 'log10(2^(target_cq-reference_16s_cq))',
                         'reference_found', reference.cq IS NOT NULL
                       )
                FROM promoted_target AS target
                LEFT JOIN reference_16s AS reference
                  ON reference.pcr_sample_id=target.pcr_sample_id
                 AND reference.pcr_run_id=target.pcr_run_id
                 AND reference.position=1
            """), {"derivation_id": derivation_id})
            return int(result.rowcount if result.rowcount is not None else 0)

    def summarize_sil_culture_markers(self, derivation_id: int) -> int:
        """Aggregate direct carbapenemase markers by isolate and culture.

        This deliberately reads the current culture only.  Retrospective
        candidates remain in ``pcr_sil_link_candidate`` and are not folded into
        an observed culture profile.
        """

        if self.engine.dialect.name != "postgresql":
            return 0
        relevant_isolate = _sil_relevant_isolate_predicate("isolate")
        with self.engine.begin() as conn:
            conn.execute(text(f"""
                WITH eligible AS (
                    SELECT DISTINCT validation.pcr_sample_id, validation.microb_sample_id
                    FROM pcr_microb_well_validation AS validation
                    LEFT JOIN pcr_sample_scope_gate AS gate
                      ON gate.derivation_id=validation.derivation_id
                     AND gate.pcr_sample_id=validation.pcr_sample_id
                    WHERE validation.derivation_id=:derivation_id
                      AND validation.validation_status='MATCH_ELIGIBLE'
                      AND COALESCE(gate.gate_status, 'NOT_APPLICABLE') <> 'LEGACY_SCREENING_BLOCKED'
                ), isolate_markers AS (
                    SELECT eligible.pcr_sample_id, eligible.microb_sample_id,
                           isolate.isolate_id, isolate.isolate_number, isolate.microorganism_code,
                           COALESCE(BOOL_OR(susceptibility.antibiotic_code='OXA'), FALSE) AS oxa_detected,
                           COALESCE(BOOL_OR(susceptibility.antibiotic_code='KPC'), FALSE) AS kpc_detected,
                           COALESCE(BOOL_OR(susceptibility.antibiotic_code='NDM'), FALSE) AS ndm_detected,
                           COALESCE(BOOL_OR(susceptibility.antibiotic_code='VIM'), FALSE) AS vim_detected,
                           COALESCE(
                             jsonb_agg(DISTINCT susceptibility.antibiotic_code)
                               FILTER (WHERE susceptibility.antibiotic_code IS NOT NULL),
                             '[]'::jsonb
                           ) AS marker_codes
                    FROM eligible
                    JOIN pcr_sil_culture_classification AS classification
                      ON classification.derivation_id=:derivation_id
                     AND classification.microb_sample_id=eligible.microb_sample_id
                    JOIN microb_analysis_result AS result
                      ON result.sample_analysis_id=classification.sample_analysis_id
                    JOIN microb_isolate AS isolate
                      ON isolate.analysis_result_id=result.analysis_result_id
                     AND isolate.microorganism_code IS NOT NULL
                     AND {relevant_isolate}
                    LEFT JOIN microb_susceptibility_result AS susceptibility
                      ON susceptibility.isolate_id=isolate.isolate_id
                     AND susceptibility.interpretation_code='P'
                     AND susceptibility.antibiotic_code IN ('OXA', 'KPC', 'NDM', 'VIM')
                    GROUP BY eligible.pcr_sample_id, eligible.microb_sample_id,
                             isolate.isolate_id, isolate.isolate_number, isolate.microorganism_code
                )
                INSERT INTO pcr_sil_isolate_marker_summary (
                    derivation_id, pcr_sample_id, microb_sample_id, isolate_id,
                    microorganism_code, isolate_number,
                    oxa_detected, kpc_detected, ndm_detected, vim_detected,
                    marker_codes_json, details_json
                )
                SELECT :derivation_id, pcr_sample_id, microb_sample_id, isolate_id,
                       microorganism_code, isolate_number,
                       oxa_detected, kpc_detected, ndm_detected, vim_detected,
                       marker_codes,
                       jsonb_build_object('evidence_scope', 'DIRECT_CURRENT_CULTURE')
                FROM isolate_markers
            """), {
                "derivation_id": derivation_id,
                "irrelevant_organism_pattern": IRRELEVANT_MICROORGANISM_PATTERN,
            })
            conn.execute(text("""
                WITH organism_markers AS (
                    SELECT pcr_sample_id, microb_sample_id, microorganism_code,
                           BOOL_OR(oxa_detected) AS oxa_detected,
                           BOOL_OR(kpc_detected) AS kpc_detected,
                           BOOL_OR(ndm_detected) AS ndm_detected,
                           BOOL_OR(vim_detected) AS vim_detected,
                           COUNT(*) AS isolate_count
                    FROM pcr_sil_isolate_marker_summary AS isolate
                    WHERE isolate.derivation_id=:derivation_id
                    GROUP BY pcr_sample_id, microb_sample_id, microorganism_code
                )
                INSERT INTO pcr_sil_organism_marker_summary (
                    derivation_id, pcr_sample_id, microb_sample_id, microorganism_code,
                    oxa_detected, kpc_detected, ndm_detected, vim_detected,
                    marker_codes_json, isolate_count, details_json
                )
                SELECT :derivation_id, pcr_sample_id, microb_sample_id, microorganism_code,
                       oxa_detected, kpc_detected, ndm_detected, vim_detected,
                       to_jsonb(ARRAY_REMOVE(ARRAY[
                         CASE WHEN oxa_detected THEN 'OXA' END,
                         CASE WHEN kpc_detected THEN 'KPC' END,
                         CASE WHEN ndm_detected THEN 'NDM' END,
                         CASE WHEN vim_detected THEN 'VIM' END
                       ], NULL)), isolate_count,
                       jsonb_build_object('evidence_scope', 'DIRECT_CURRENT_CULTURE',
                                          'aggregation', 'BINARY_UNION_ACROSS_ISOLATES')
                FROM organism_markers
            """), {"derivation_id": derivation_id})
            result = conn.execute(text("""
                WITH culture_markers AS (
                    SELECT pcr_sample_id, microb_sample_id,
                           BOOL_OR(oxa_detected) AS oxa_detected,
                           BOOL_OR(kpc_detected) AS kpc_detected,
                           BOOL_OR(ndm_detected) AS ndm_detected,
                           BOOL_OR(vim_detected) AS vim_detected,
                           COUNT(DISTINCT microorganism_code) AS organism_count,
                           COUNT(*) AS isolate_count
                    FROM pcr_sil_isolate_marker_summary
                    WHERE derivation_id=:derivation_id
                    GROUP BY pcr_sample_id, microb_sample_id
                )
                INSERT INTO pcr_sil_culture_marker_summary (
                    derivation_id, pcr_sample_id, microb_sample_id,
                    oxa_detected, kpc_detected, ndm_detected, vim_detected,
                    marker_codes_json, organism_count, isolate_count, details_json
                )
                SELECT :derivation_id, pcr_sample_id, microb_sample_id,
                       oxa_detected, kpc_detected, ndm_detected, vim_detected,
                       to_jsonb(ARRAY_REMOVE(ARRAY[
                         CASE WHEN oxa_detected THEN 'OXA' END,
                         CASE WHEN kpc_detected THEN 'KPC' END,
                         CASE WHEN ndm_detected THEN 'NDM' END,
                         CASE WHEN vim_detected THEN 'VIM' END
                       ], NULL)),
                       organism_count, isolate_count,
                       jsonb_build_object('evidence_scope', 'DIRECT_CURRENT_CULTURE',
                                          'aggregation', 'BINARY_UNION_ACROSS_ORGANISMS')
                FROM culture_markers
            """), {"derivation_id": derivation_id})
            return int(result.rowcount if result.rowcount is not None else 0)

    def fetch_runs(self) -> list[SourceRun]:
        """Load runs together with their distinct source targets."""

        with self.engine.connect() as conn:
            run_rows = conn.execute(
                text(
                    """
                    SELECT run.pcr_run_id, run.run_name, run.run_started
                    FROM pcr_run AS run
                    LEFT JOIN pcr_run_scope AS scope
                      ON scope.pcr_run_id=run.pcr_run_id
                    WHERE COALESCE(scope.analytical_scope, 'ROUTINE') != 'EXPERIMENTAL'
                    ORDER BY run.pcr_run_id
                    """
                )
            ).all()
            target_rows = conn.execute(
                text(
                    """
                    SELECT result.pcr_run_id, result.target
                    FROM pcr_cq_result AS result
                    LEFT JOIN pcr_run_scope AS scope
                      ON scope.pcr_run_id=result.pcr_run_id
                    WHERE COALESCE(scope.analytical_scope, 'ROUTINE') != 'EXPERIMENTAL'
                    GROUP BY result.pcr_run_id, result.target
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
                    SELECT result.pcr_cq_result_id, result.pcr_run_id,
                           result.pcr_well_id, result.target, result.cq
                    FROM pcr_cq_result AS result
                    LEFT JOIN pcr_run_scope AS scope
                      ON scope.pcr_run_id=result.pcr_run_id
                    WHERE COALESCE(scope.analytical_scope, 'ROUTINE') != 'EXPERIMENTAL'
                    ORDER BY result.pcr_well_id
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
                    LEFT JOIN pcr_run_scope AS scope
                      ON scope.pcr_run_id=pcr_rfu_measurement.pcr_run_id
                    WHERE measurement_kind = :measurement_kind
                      AND COALESCE(scope.analytical_scope, 'ROUTINE') != 'EXPERIMENTAL'
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
                    JOIN pcr_microb_well_validation AS validation
                      ON validation.derivation_id = classification.derivation_id
                     AND validation.pcr_well_id = classification.pcr_well_id
                    LEFT JOIN pcr_sample_scope_gate AS gate
                     ON gate.derivation_id=classification.derivation_id
                     AND gate.pcr_sample_id=classification.pcr_sample_id
                    JOIN pcr_run_analysis AS analysis
                      ON analysis.derivation_id=classification.derivation_id
                     AND analysis.pcr_run_id=well.pcr_run_id
                    WHERE classification.derivation_id = :derivation_id
                      AND classification.well_role = :real_sample
                      AND validation.validation_status = 'MATCH_ELIGIBLE'
                      AND (
                        analysis.run_type <> 'SPECIFIC'
                        OR gate.gate_status IS NULL
                        OR gate.gate_status='NOT_APPLICABLE'
                        OR (
                          gate.gate_status='RESET_BY_ROUTINE_SCREENING'
                          AND analysis.run_started >= (
                            SELECT run_started FROM pcr_run
                            WHERE pcr_run_id=gate.reset_screening_run_id
                          )
                        )
                      )
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
                    RETURNING episode_id
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
            episode_id = int(result.scalar_one())
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
                    JOIN pcr_microb_well_validation AS validation
                      ON validation.derivation_id = classification.derivation_id
                     AND validation.pcr_well_id = classification.pcr_well_id
                    LEFT JOIN pcr_target_qc AS qc
                      ON qc.derivation_id = interpretation.derivation_id
                     AND qc.pcr_run_id = interpretation.pcr_run_id
                     AND qc.canonical_target = interpretation.canonical_target
                    LEFT JOIN pcr_sample_scope_gate AS gate
                      ON gate.derivation_id=classification.derivation_id
                     AND gate.pcr_sample_id=classification.pcr_sample_id
                    LEFT JOIN pcr_run_scope AS scope
                      ON scope.pcr_run_id=interpretation.pcr_run_id
                    WHERE interpretation.derivation_id = :derivation_id
                      AND classification.well_role = :real_sample
                      AND validation.validation_status = 'MATCH_ELIGIBLE'
                      AND (
                        analysis.run_type <> 'SPECIFIC'
                        OR gate.gate_status IS NULL
                        OR gate.gate_status='NOT_APPLICABLE'
                        OR (
                          gate.gate_status='RESET_BY_ROUTINE_SCREENING'
                          AND analysis.run_started >= (
                            SELECT run_started FROM pcr_run
                            WHERE pcr_run_id=gate.reset_screening_run_id
                          )
                        )
                      )
                      AND COALESCE(scope.analytical_scope, 'ROUTINE') != 'EXPERIMENTAL'
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
                        WHERE derivation_id=:id AND needs_review=TRUE
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

    tables = (
        "pcr_sample_target_metric",
        "pcr_sample_result",
        "pcr_sample_scope_gate",
        "pcr_sil_culture_marker_summary",
        "pcr_sil_isolate_marker_summary",
        "pcr_sil_organism_marker_summary",
        "pcr_sil_result_link",
        "pcr_sil_link_candidate",
        "pcr_sil_retrospective_evidence",
        "pcr_sil_culture_classification",
        "pcr_microb_well_validation",
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
    )
    if conn.dialect.name == "postgresql":
        conn.execute(text(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY"))  # noqa: B608
        return
    for table in tables:
        conn.execute(text(f"DELETE FROM {table}"))  # noqa: B608
