"""Application pipeline coordinating pure PCR rules and persistence."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from typing import Any

from ..enums import (
    EpisodeAssociationStatuses,
    PCRTargets,
    ResultLevels,
    WellRoles,
)
from ..melting import (
    MeltingCall,
    MeltingParameters,
    Peak,
    canonical_target,
    interpret_curve,
)
from .domain import (
    aggregate_run_qc,
    classify_run,
    classify_sample_name,
    episode_date_key,
    evaluate_target_qc,
    group_result_candidates,
    is_real_sample,
    overlap,
    parse_cfx_datetime,
    run_components,
    sample_key,
    summarize_results,
)
from .models import (
    CQResult,
    EpisodeMembership,
    InterpretationInsert,
    ResultSelection,
    RunAnalysis,
    RunQC,
    TargetQC,
    TargetQCAssessment,
    WellClassification,
)
from .repository import PCRAnalyticsRepository


class PCRAnalyticsPipeline:
    """Execute analytical stages against an injected persistence boundary."""

    def __init__(self, repository: PCRAnalyticsRepository) -> None:
        self.repository = repository

    def classify_wells(self, derivation_id: int) -> tuple[int, int]:
        """Classify source wells and persist canonical sample associations."""

        wells = self.repository.fetch_wells()
        sample_keys = sorted(
            {
                sample_key(str(well.sample_name))
                for well in wells
                if is_real_sample(well.sample_name)
            }
        )
        sample_ids = self.repository.ensure_samples(sample_keys)
        classifications = []
        for well in wells:
            role, normalized, rule_key, review = classify_sample_name(well.sample_name)
            canonical_sample = (
                sample_key(str(well.sample_name))
                if role == WellRoles.real_sample
                else None
            )
            classifications.append(
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
            )
        self.repository.insert_well_classifications(classifications)
        return len(wells), len(sample_keys)

    def classify_runs(self, derivation_id: int) -> None:
        """Classify source runs from targets and normalized run metadata."""

        analyses = []
        for run in self.repository.fetch_runs():
            targets = frozenset(canonical_target(target) for target in run.targets)
            run_class, review = classify_run(targets)
            started_value = (
                json.loads(run.started_json) if run.started_json is not None else None
            )
            analyses.append(
                RunAnalysis(
                    derivation_id=derivation_id,
                    run_id=run.run_id,
                    run_class=run_class,
                    run_started=parse_cfx_datetime(started_value),
                    episode_key=episode_date_key(run.run_name),
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

        grouped: dict[tuple[int, str], dict[str, list[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for observation in self.repository.fetch_qc_observations(derivation_id):
            grouped[(observation.run_id, observation.target)][observation.role].append(
                observation.call
            )

        targets: list[TargetQC] = []
        by_run: dict[int, list[TargetQCAssessment]] = defaultdict(list)
        for (run_id, target), roles in grouped.items():
            assessment = evaluate_target_qc(roles)
            item = TargetQC(
                derivation_id=derivation_id,
                run_id=run_id,
                target=target,
                status=assessment.status,
                valid=assessment.valid,
                positive=assessment.positive,
                negative=assessment.negative,
                warnings=_json(assessment.warnings),
                details=_json({"failures": assessment.failures, "roles": roles}),
            )
            targets.append(item)
            by_run[run_id].append(assessment)
        runs = []
        for run_id, assessments in by_run.items():
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
        self.repository.insert_run_qc(runs)
        return len(targets)

    def associate_episodes(self, derivation_id: int) -> int:
        """Associate complementary runs into date-based analytical episodes."""

        run_rows, samples = self.repository.fetch_episode_inputs(derivation_id)
        by_date: dict[str | None, list[tuple[int, str]]] = defaultdict(list)
        for run_id, run_class, date_key in run_rows:
            by_date[date_key].append((run_id, run_class))

        episode_count = 0
        for date_key, candidates in by_date.items():
            components = (
                run_components(candidates, samples)
                if date_key
                else [[candidate] for candidate in candidates]
            )
            for position, component in enumerate(components, start=1):
                status = (
                    EpisodeAssociationStatuses.automatic
                    if date_key and len(component) > 1
                    else EpisodeAssociationStatuses.review
                )
                review = (
                    None
                    if status == EpisodeAssociationStatuses.automatic
                    else "No unambiguous run partner."
                )
                memberships = []
                for run_id, run_class in component:
                    scores = [
                        overlap(samples[run_id], samples[other_id])
                        for other_id, _ in component
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
                    episode_key=f"{date_key or 'UNPARSED'}:{position:02d}",
                    date_key=date_key,
                    status=status,
                    review=review,
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
        for (sample_id, target), items in grouped.items():
            summary = summarize_results(items, target=target)
            selections.append(
                ResultSelection(
                    derivation_id=derivation_id,
                    sample_id=sample_id,
                    target=target,
                    preferred=summary.preferred,
                    count=len(items),
                    relation=summary.relation,
                    concordance=summary.concordance,
                    review=summary.needs_review,
                    details=_json(
                        {
                            "interpretation_ids": [
                                item.interpretation_id for item in items
                            ],
                            "tie": summary.tie,
                        }
                    ),
                )
            )
        self.repository.insert_result_selections(selections)
        return len(selections)


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
        metrics=_json({"peak_count": len(peaks)}),
        error=call.error,
    )


def _json(value: Any) -> str:
    """Serialize analytical details consistently for JSON database columns."""

    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
