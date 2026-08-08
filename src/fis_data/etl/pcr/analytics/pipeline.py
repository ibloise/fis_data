"""Application pipeline coordinating pure PCR rules and persistence."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import Any

from ..enums import (
    EpisodeAssociationStatuses,
    PCRTargets,
    ResultLevels,
    RunClasses,
    WellRoles,
)
from ..melting import (
    MeltingCall,
    MeltingParameters,
    Peak,
    canonical_target,
    interpret_curve,
)
from ..review.models import ActiveOverride, ReviewItemType
from .domain import (
    aggregate_run_qc,
    classify_run,
    classify_sample_name,
    episode_components,
    episode_date_key,
    evaluate_screening_target_qc,
    evaluate_target_qc,
    group_result_candidates,
    is_real_sample,
    overlap,
    sample_key,
    summarize_results,
    run_type_for_class,
)
from .models import (
    CQResult,
    EpisodeMembership,
    InterpretationInsert,
    QCObservation,
    ResultSelection,
    RunAnalysis,
    RunQC,
    TargetQC,
    TargetQCAssessment,
    TargetQCEvidence,
    WellClassification,
)
from .repository import PCRAnalyticsRepository


class PCRAnalyticsPipeline:
    """Execute analytical stages against an injected persistence boundary."""

    def __init__(
        self,
        repository: PCRAnalyticsRepository,
        overrides: list[ActiveOverride] | None = None,
    ) -> None:
        self.repository = repository
        self.set_overrides(overrides or [])

    def set_overrides(self, overrides: list[ActiveOverride]) -> None:
        """Replace the active override snapshot used by all stages."""

        self.overrides = {
            (override.item_type, override.subject_key): override
            for override in overrides
        }

    def classify_wells(
        self,
        derivation_id: int,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> tuple[int, int]:
        """Classify source wells in bounded batches and persist each batch."""

        well_overrides = {
            int(key.removeprefix("well:")): override.payload
            for (item_type, key), override in self.overrides.items()
            if item_type == ReviewItemType.well and key.startswith("well:")
        }
        total_wells = self.repository.count_wells()
        processed_wells = 0
        real_sample_keys: set[str] = set()
        for wells in self.repository.iter_wells():
            prepared = []
            batch_sample_keys: set[str] = set()
            for well in wells:
                role, normalized, rule_key, review = classify_sample_name(
                    well.sample_name, well.content
                )
                override = well_overrides.get(well.well_id)
                if override:
                    role = str(override["role"])
                    normalized = override.get("sample_key") or normalized
                    rule_key = "MANUAL_OVERRIDE"
                    review = None
                canonical_sample = (
                    str(override["sample_key"])
                    if override and override.get("sample_key")
                    else sample_key(str(well.sample_name))
                    if role == WellRoles.real_sample
                    else None
                )
                if canonical_sample is not None:
                    batch_sample_keys.add(canonical_sample)
                    real_sample_keys.add(canonical_sample)
                prepared.append(
                    (well, role, normalized, rule_key, review, canonical_sample)
                )
            sample_ids = self.repository.ensure_samples(sorted(batch_sample_keys))
            self.repository.insert_well_classifications(
                [
                    WellClassification(
                        derivation_id=derivation_id,
                        well_id=well.well_id,
                        sample_id=sample_ids.get(canonical_sample),
                        raw_name=well.sample_name,
                        normalized=normalized,
                        role=role,
                        rule_key=rule_key,
                        content=well.content,
                        review=review,
                    )
                    for well, role, normalized, rule_key, review, canonical_sample in prepared
                ]
            )
            processed_wells += len(wells)
            if on_progress is not None:
                on_progress(processed_wells, total_wells)
        return processed_wells, len(real_sample_keys)

    def classify_runs(self, derivation_id: int) -> None:
        """Classify source runs from targets and normalized run metadata."""

        analyses = []
        for run in self.repository.fetch_runs():
            targets = frozenset(canonical_target(target) for target in run.targets)
            run_class, review = classify_run(targets)
            run_override = self._override(ReviewItemType.run, f"run:{run.run_id}")
            run_episode_key = episode_date_key(run.run_name)
            if run_override:
                run_class = str(run_override.payload["run_class"])
                run_episode_key = (
                    run_override.payload.get("episode_date_key") or run_episode_key
                )
                review = None
            analyses.append(
                RunAnalysis(
                    derivation_id=derivation_id,
                    run_id=run.run_id,
                    run_class=run_class,
                    run_type=run_type_for_class(run_class),
                    run_started=run.run_started,
                    episode_key=run_episode_key,
                    targets=_json(sorted(targets)),
                    review=review,
                )
            )
        self.repository.insert_run_analyses(analyses)

    def interpret(self, derivation_id: int, params: MeltingParameters) -> int:
        """Interpret each Cq result using any available derivative curve."""

        cq_by_well = {row.well_id: row for row in self.repository.fetch_cq_results()}
        curves = {row.well_id: row for row in self.repository.fetch_derivative_curves()}
        inserts: list[InterpretationInsert] = []
        for well_id, cq_result in cq_by_well.items():
            curve = curves.get(well_id)
            if curve is not None:
                inserts.extend(
                    _interpretation_records(
                        derivation_id,
                        cq_result,
                        list(curve.temperatures),
                        list(curve.derivatives),
                        params,
                    )
                )
                continue
            target = canonical_target(cq_result.target)
            if target in {
                PCRTargets.control_16s,
                PCRTargets.fail,
                PCRTargets.fail_2,
            }:
                inserts.extend(
                    _interpretation_records(derivation_id, cq_result, [], [], params)
                )
            else:
                inserts.append(
                    _call_record(
                        derivation_id,
                        cq_result,
                        MeltingCall(
                            target=target,
                            call=ResultLevels.error,
                            error="Missing melt-curve derivative data.",
                        ),
                        [],
                    )
                )
        self.repository.insert_interpretations(inserts)
        return len(inserts)

    def evaluate_qc(self, derivation_id: int) -> int:
        """Evaluate control calls and persist target and run QC summaries."""

        grouped: dict[tuple[int, str], list[QCObservation]] = defaultdict(list)
        by_run: dict[int, list[QCObservation]] = defaultdict(list)
        for observation in self.repository.fetch_qc_observations(derivation_id):
            grouped[(observation.run_id, observation.target)].append(observation)
            by_run[observation.run_id].append(observation)

        targets: list[TargetQC] = []
        evidence: list[TargetQCEvidence] = []
        qc_by_run: dict[int, list[TargetQCAssessment]] = defaultdict(list)
        for (run_id, target), observations in grouped.items():
            roles: dict[str, list[str]] = defaultdict(list)
            for observation in observations:
                roles[observation.role].append(observation.call)
            run_class = observations[0].run_class
            positive_melting_incompatible = any(
                observation.role == WellRoles.positive_control
                and observation.error == "MELTING_INCOMPATIBLE"
                for observation in observations
            )
            assessment = (
                evaluate_screening_target_qc(by_run[run_id])
                if run_class == RunClasses.screening
                else evaluate_target_qc(
                    roles,
                    target=target,
                    positive_melting_incompatible=positive_melting_incompatible,
                )
            )
            qc_override = self._override(
                ReviewItemType.target_qc,
                f"run:{run_id}:target:{target}",
            )
            if qc_override:
                assessment = replace(
                    assessment,
                    status=str(qc_override.payload["status"]),
                    valid=bool(qc_override.payload["valid"]),
                )
            details = {
                "failures": assessment.failures,
                "roles": roles,
                **assessment.details,
            }
            item = TargetQC(
                derivation_id=derivation_id,
                run_id=run_id,
                target=target,
                status=assessment.status,
                valid=assessment.valid,
                positive=assessment.positive,
                negative=assessment.negative,
                warnings=_json(assessment.warnings),
                details=_json(details),
            )
            targets.append(item)
            evidence.extend(
                TargetQCEvidence(
                    derivation_id=derivation_id,
                    run_id=run_id,
                    target=target,
                    interpretation_id=observation.interpretation_id,
                    well_role=observation.role,
                )
                for observation in observations
                if observation.role
                in {WellRoles.positive_control, WellRoles.negative_control}
            )
            qc_by_run[run_id].append(assessment)
        runs = []
        for run_id, assessments in qc_by_run.items():
            aggregate = aggregate_run_qc(assessments)
            runs.append(
                RunQC(
                    derivation_id=derivation_id,
                    run_id=run_id,
                    status=aggregate.status,
                    valid=aggregate.valid,
                    invalid=aggregate.invalid,
                    warnings=aggregate.warnings,
                )
            )
        self.repository.insert_target_qc(targets)
        self.repository.insert_target_qc_evidence(evidence)
        self.repository.insert_run_qc(runs)
        return len(targets)

    def associate_episodes(self, derivation_id: int) -> int:
        """Associate complementary runs into date-based analytical episodes."""

        run_rows, samples = self.repository.fetch_episode_inputs(derivation_id)
        run_classes = {run_id: run_class for run_id, run_class, _ in run_rows}
        manual_overrides = [
            override
            for (item_type, _), override in self.overrides.items()
            if item_type == ReviewItemType.episode
        ]
        manual_run_ids = {
            int(run_id)
            for override in manual_overrides
            for run_id in override.payload.get("run_ids", [])
        }
        candidates: list[tuple[int, str, str | None]] = []
        for run_id, run_class, date_key in run_rows:
            if run_id in manual_run_ids:
                continue
            candidates.append((run_id, run_class, date_key))

        episode_count = 0
        for position, component in enumerate(
            episode_components(candidates, samples), start=1
        ):
            component_date = _component_date_key(component)
            component_runs = [(run_id, run_class) for run_id, run_class, _ in component]
            status = _episode_association_status(component_runs)
            review = (
                None
                if status == EpisodeAssociationStatuses.automatic
                else "No unambiguous run partner."
            )
            memberships = []
            for run_id, run_class in component_runs:
                scores = [
                    overlap(samples[run_id], samples[other_id])
                    for other_id, _ in component_runs
                    if other_id != run_id
                ]
                memberships.append(
                    EpisodeMembership(
                        run_id=run_id,
                        role=run_class,
                        score=max(scores) if scores else None,
                    )
                )
            self.repository.insert_episode(
                derivation_id=derivation_id,
                episode_key=f"{_component_key_prefix(component)}:{position:02d}",
                date_key=component_date,
                status=status,
                review=review,
                memberships=memberships,
            )
            episode_count += 1
        for override in manual_overrides:
            run_ids = [
                int(run_id)
                for run_id in override.payload.get("run_ids", [])
                if int(run_id) in run_classes
            ]
            memberships = [
                EpisodeMembership(
                    run_id=run_id,
                    role=run_classes[run_id],
                    score=None,
                )
                for run_id in run_ids
            ]
            if memberships:
                self.repository.insert_episode(
                    derivation_id=derivation_id,
                    episode_key=f"MANUAL:{override.action_id}",
                    date_key=None,
                    status=EpisodeAssociationStatuses.automatic,
                    review=None,
                    memberships=memberships,
                )
                episode_count += 1
        return episode_count

    def select_results(self, derivation_id: int) -> int:
        """Select preferred interpretations for each sample and target."""

        grouped = group_result_candidates(
            self.repository.fetch_result_candidates(derivation_id)
        )
        selections = []
        for (sample_id, run_type, target), items in grouped.items():
            summary = summarize_results(items, target=target)
            result_override = self._override(
                ReviewItemType.result,
                f"sample:{items[0].sample_key}:run_type:{run_type}:target:{target}",
            )
            preferred = summary.preferred
            needs_review = summary.needs_review
            if result_override:
                preferred_result_id = result_override.payload.get("preferred_result_id")
                if preferred_result_id is not None:
                    preferred = next(
                        (
                            item.interpretation_id
                            for item in items
                            if item.result_id == int(preferred_result_id)
                        ),
                        preferred,
                    )
                needs_review = False
            selections.append(
                ResultSelection(
                    derivation_id=derivation_id,
                    sample_id=sample_id,
                    run_type=run_type,
                    target=target,
                    preferred=preferred,
                    count=len(items),
                    relation=summary.relation,
                    concordance=summary.concordance,
                    review=needs_review,
                    details=_json(
                        {
                            "interpretation_ids": [
                                item.interpretation_id for item in items
                            ],
                            "tie": summary.tie,
                            "review_override_id": (
                                result_override.action_id if result_override else None
                            ),
                        }
                    ),
                )
            )
        self.repository.insert_result_selections(selections)
        return len(selections)

    def _override(self, item_type: str, subject_key: str) -> ActiveOverride | None:
        """Return one active override from the current snapshot."""

        return self.overrides.get((item_type, subject_key))


def _episode_association_status(component: list[tuple[int, str]]) -> str:
    """Decide whether a run component is a complete episode or needs review."""

    run_classes = {run_class for _, run_class in component}
    if run_classes == {RunClasses.screening}:
        return EpisodeAssociationStatuses.automatic
    if len(component) > 1:
        return EpisodeAssociationStatuses.automatic
    return EpisodeAssociationStatuses.review


def _component_date_key(component: list[tuple[int, str, str | None]]) -> str | None:
    """Return the shared episode date only when every run has the same one."""

    dates = {date_key for _, _, date_key in component if date_key}
    if len(dates) == 1 and all(date_key for _, _, date_key in component):
        return next(iter(dates))
    return None


def _component_key_prefix(component: list[tuple[int, str, str | None]]) -> str:
    """Return a stable episode key prefix for dated, mixed or undated groups."""

    dates = {date_key for _, _, date_key in component if date_key}
    if len(dates) == 1 and all(date_key for _, _, date_key in component):
        return next(iter(dates))
    if dates:
        return "MIXED"
    return "UNPARSED"


def _interpretation_records(
    derivation_id: int,
    cq_result: CQResult,
    temperatures: list[float],
    derivatives: list[float],
    params: MeltingParameters,
) -> list[InterpretationInsert]:
    """Interpret a curve and convert calls to persistence records."""

    calls, peaks = interpret_curve(
        source_target=cq_result.target,
        cq=cq_result.cq,
        temperatures=temperatures,
        derivatives=derivatives,
        params=params,
    )
    return [_call_record(derivation_id, cq_result, call, peaks) for call in calls]


def _call_record(
    derivation_id: int,
    cq_result: CQResult,
    call: MeltingCall,
    peaks: list[Peak],
) -> InterpretationInsert:
    """Convert one domain melting call to a typed persistence record."""

    return InterpretationInsert(
        derivation_id=derivation_id,
        run_id=cq_result.run_id,
        well_id=cq_result.well_id,
        result_id=cq_result.result_id,
        source_target=cq_result.target,
        target=call.target,
        call=call.call,
        cq=cq_result.cq,
        peak_temp=call.peak_temperature,
        peak_rfu=call.peak_deriv_rfu,
        peaks=_json([asdict(peak) for peak in peaks]),
        metrics=_json(
            {
                "peak_count": len(peaks),
                "melt_status": call.error or "MELTING_COMPATIBLE",
                "compatible_targets_within_window": list(call.compatible_targets),
            }
        ),
        error=call.error,
    )


def _json(value: Any) -> str:
    """Serialize analytical details consistently for JSON database columns."""

    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
