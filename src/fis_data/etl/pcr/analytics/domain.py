"""Pure business rules for PCR analytical derivation."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from ..enums import (
    ClassificationRules,
    ConcordanceLevels,
    PCRTargets,
    QCIssues,
    QCStatuses,
    ResultLevels,
    ResultRelations,
    RunClasses,
    WellRoles,
)
from .models import (
    ResultCandidate,
    ResultSummary,
    RunQCAssessment,
    TargetQCAssessment,
)

POSITIVE_CONTROLS = {"CP", "C+", "CPNEW", "CPOLD"}
NEGATIVE_CONTROLS = {"CN", "C-", "CNEG"}
CARBAPENEMASE_TARGETS = {
    PCRTargets.oxa48,
    PCRTargets.vim,
    PCRTargets.kpc,
    PCRTargets.ndm,
}
EPISODE_FULL_OVERLAP_CLASSES = {
    RunClasses.compensation_partial,
    RunClasses.panel_oxa48_vim,
    RunClasses.panel_kpc_ndm,
}


def classify_sample_name(
    value: Any,
) -> tuple[str, str | None, str, str | None]:
    """Classify a raw sample name and return role, normalization and evidence."""

    if value is None or not str(value).strip():
        return (
            WellRoles.non_sample_review,
            None,
            ClassificationRules.empty,
            "Missing sample name.",
        )
    raw = str(value).strip()
    if raw.isdigit():
        return WellRoles.real_sample, sample_key(raw), ClassificationRules.numeric, None
    normalized = re.sub(r"\s+", "", raw.upper())
    if normalized in POSITIVE_CONTROLS:
        return WellRoles.positive_control, normalized, normalized, None
    if normalized in NEGATIVE_CONTROLS:
        return WellRoles.negative_control, normalized, normalized, None
    return (
        WellRoles.non_sample_review,
        normalized,
        ClassificationRules.unknown_text,
        "Non-numeric sample name is not a known control.",
    )


def is_real_sample(value: Any) -> bool:
    """Return whether a raw sample name identifies a real numeric sample."""

    return value is not None and str(value).strip().isdigit()


def sample_key(value: str) -> str:
    """Canonicalize a numeric sample identifier without losing zero itself."""

    return value.lstrip("0") or "0"


def classify_run(targets: set[str] | frozenset[str]) -> tuple[str, str | None]:
    """Classify a run from its canonical target set."""

    if PCRTargets.screening in targets:
        return RunClasses.screening, None
    if {
        PCRTargets.control_16s,
        PCRTargets.oxa48,
        PCRTargets.vim,
    }.issubset(targets):
        return RunClasses.panel_oxa48_vim, None
    if {
        PCRTargets.control_16s,
        PCRTargets.kpc,
        PCRTargets.ndm,
    }.issubset(targets):
        return RunClasses.panel_kpc_ndm, None
    if targets & CARBAPENEMASE_TARGETS:
        return RunClasses.compensation_partial, None
    return RunClasses.other, f"Unrecognized target set: {sorted(targets)}"


def parse_cfx_datetime(value: Any) -> str | None:
    """Parse the CFX timestamp format into an ISO database value."""

    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value), "%m/%d/%Y %H:%M:%S UTC")
        return parsed.isoformat(sep=" ")
    except ValueError:
        return None


def episode_date_key(run_name: str) -> str | None:
    """Extract the embedded day-first date used to associate PCR runs."""

    match = re.search(r"(?<!\d)(\d{8}|\d{6})(?!\d)", run_name)
    if match is None:
        return None
    raw = match.group(1)
    for fmt in ("%d%m%Y", "%d%m%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def run_components(
    candidates: list[tuple[int, str]], samples: dict[int, set[int]]
) -> list[list[tuple[int, str]]]:
    """Build connected non-screening run groups by sample overlap."""

    remaining = {run_id for run_id, _ in candidates}
    classes = dict(candidates)
    components = []
    while remaining:
        first = min(remaining)
        remaining.remove(first)
        pending = [first]
        component_ids = set(pending)
        while pending:
            current = pending.pop()
            if classes[current] == RunClasses.screening:
                continue
            linked = {
                other
                for other in remaining
                if classes[other] != RunClasses.screening
                and overlap(samples[current], samples[other]) >= 0.8
            }
            remaining -= linked
            pending.extend(linked)
            component_ids |= linked
        components.append(
            [(run_id, classes[run_id]) for run_id in sorted(component_ids)]
        )
    return components


def episode_components(
    candidates: list[tuple[int, str, str | None]], samples: dict[int, set[int]]
) -> list[list[tuple[int, str, str | None]]]:
    """Build episode run groups using date matches or full sample overlap."""

    remaining = {run_id for run_id, _, _ in candidates}
    rows = {run_id: (run_class, date_key) for run_id, run_class, date_key in candidates}
    components = []
    while remaining:
        first = min(remaining)
        remaining.remove(first)
        pending = [first]
        component_ids = set(pending)
        while pending:
            current = pending.pop()
            current_class, current_date = rows[current]
            if current_class == RunClasses.screening:
                continue
            linked = {
                other
                for other in remaining
                if _episode_runs_link(
                    current_class=current_class,
                    current_date=current_date,
                    current_samples=samples[current],
                    other_class=rows[other][0],
                    other_date=rows[other][1],
                    other_samples=samples[other],
                )
            }
            remaining -= linked
            pending.extend(linked)
            component_ids |= linked
        components.append(
            [
                (run_id, rows[run_id][0], rows[run_id][1])
                for run_id in sorted(component_ids)
            ]
        )
    return components


def _episode_runs_link(
    *,
    current_class: str,
    current_date: str | None,
    current_samples: set[int],
    other_class: str,
    other_date: str | None,
    other_samples: set[int],
) -> bool:
    """Return whether two non-screening runs belong to one episode."""

    if current_class == RunClasses.screening or other_class == RunClasses.screening:
        return False
    sample_overlap = overlap(current_samples, other_samples)
    if (
        sample_overlap == 1.0
        and current_class in EPISODE_FULL_OVERLAP_CLASSES
        and other_class in EPISODE_FULL_OVERLAP_CLASSES
    ):
        return True
    return bool(current_date and current_date == other_date and sample_overlap >= 0.8)


def overlap(left: set[int], right: set[int]) -> float:
    """Measure sample overlap relative to the smaller run."""

    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def summarize_results(items: list[ResultCandidate], *, target: str) -> ResultSummary:
    """Choose the preferred valid result and describe group concordance."""

    valid = [
        item
        for item in items
        if item.valid and item.call in {ResultLevels.positive, ResultLevels.negative}
    ]
    valid.sort(key=lambda item: (str(item.started or ""), item.run_id), reverse=True)
    calls = {item.call for item in valid}
    classes = {item.run_class for item in items}
    if len(items) == 1:
        relation = ResultRelations.single
    elif RunClasses.compensation_partial in classes:
        relation = ResultRelations.correction
    elif target == PCRTargets.control_16s and {
        RunClasses.panel_oxa48_vim,
        RunClasses.panel_kpc_ndm,
    }.issubset(classes):
        relation = ResultRelations.panel_complement
    else:
        relation = ResultRelations.historical_repeat
    tie = bool(
        len(valid) > 1
        and valid[0].started is not None
        and valid[0].started == valid[1].started
    )
    return ResultSummary(
        preferred=valid[0].interpretation_id if valid else None,
        relation=relation,
        concordance=(
            ConcordanceLevels.no_valid_result
            if not valid
            else (
                ConcordanceLevels.concordant
                if len(calls) <= 1
                else ConcordanceLevels.discordant
            )
        ),
        needs_review=not valid or len(calls) > 1 or tie,
        tie=tie,
    )


def group_result_candidates(
    candidates: list[ResultCandidate],
) -> dict[tuple[int, str], list[ResultCandidate]]:
    """Group result candidates by sample and canonical target."""

    grouped: dict[tuple[int, str], list[ResultCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate.sample_id, candidate.target)].append(candidate)
    return grouped


def evaluate_target_qc(roles: dict[str, list[str]]) -> TargetQCAssessment:
    """Evaluate positive and negative control calls for one target."""

    positive = roles.get(WellRoles.positive_control, [])
    negative = roles.get(WellRoles.negative_control, [])
    warnings = []
    failures = []
    if not positive:
        warnings.append(QCIssues.missing_positive_control)
    if not negative:
        warnings.append(QCIssues.missing_negative_control)
    if any(call != ResultLevels.positive for call in positive):
        failures.append(QCIssues.positive_control_not_positive)
    if any(call == ResultLevels.positive for call in negative):
        failures.append(QCIssues.negative_control_positive)
    if any(call == ResultLevels.indeterminate for call in negative):
        warnings.append(QCIssues.negative_control_indeterminate)
    return TargetQCAssessment(
        status=(
            QCStatuses.invalid
            if failures
            else (QCStatuses.warning if warnings else QCStatuses.valid)
        ),
        valid=not failures,
        positive=len(positive),
        negative=len(negative),
        warnings=tuple(warnings),
        failures=tuple(failures),
    )


def aggregate_run_qc(items: list[TargetQCAssessment]) -> RunQCAssessment:
    """Aggregate target assessments into a run-level QC decision."""

    invalid = sum(not item.valid for item in items)
    warning_count = sum(bool(item.warnings) for item in items)
    if invalid == len(items):
        status = QCStatuses.invalid
    elif invalid:
        status = QCStatuses.partially_invalid
    elif warning_count:
        status = QCStatuses.valid_with_warnings
    else:
        status = QCStatuses.valid
    return RunQCAssessment(
        status=status,
        valid=len(items) - invalid,
        invalid=invalid,
        warnings=warning_count,
    )
