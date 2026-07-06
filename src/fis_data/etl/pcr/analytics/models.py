"""Typed records exchanged by PCR analytics domain and persistence layers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceWell:
    """Raw well metadata required for analytical classification."""

    well_id: int
    sample_name: str | None
    content: str | None


@dataclass(frozen=True)
class WellClassification:
    """Domain classification assigned to one PCR well."""

    derivation_id: int
    well_id: int
    sample_id: int | None
    raw_name: str | None
    normalized: str | None
    role: str
    rule_key: str
    content: str | None
    review: str | None


@dataclass(frozen=True)
class SourceRun:
    """Run metadata and canonical targets used by run classification."""

    run_id: int
    run_name: str
    started_json: str | None
    targets: frozenset[str]


@dataclass(frozen=True)
class RunAnalysis:
    """Derived run classification ready for persistence."""

    derivation_id: int
    run_id: int
    run_class: str
    run_started: str | None
    episode_key: str | None
    targets: str
    review: str | None


@dataclass(frozen=True)
class CQResult:
    """Cq result required to interpret a well."""

    result_id: int
    run_id: int
    well_id: int
    target: str
    cq: float | None


@dataclass(frozen=True)
class DerivativeCurve:
    """Ordered melting derivative measurements for one well."""

    well_id: int
    temperatures: tuple[float, ...]
    derivatives: tuple[float, ...]


@dataclass(frozen=True)
class InterpretationInsert:
    """One interpreted PCR result ready for persistence."""

    derivation_id: int
    run_id: int
    well_id: int
    result_id: int
    source_target: str
    target: str
    call: str
    cq: float | None
    peak_temp: float | None
    peak_rfu: float | None
    peaks: str
    metrics: str
    error: str | None


@dataclass(frozen=True)
class QCObservation:
    """Control call observed for one run and canonical target."""

    run_id: int
    target: str
    source_target: str
    run_class: str
    role: str
    call: str
    cq: float | None


@dataclass(frozen=True)
class TargetQCAssessment:
    """Pure target quality decision before persistence serialization."""

    status: str
    valid: bool
    positive: int
    negative: int
    warnings: tuple[str, ...]
    failures: tuple[str, ...]
    details: dict[str, object]


@dataclass(frozen=True)
class RunQCAssessment:
    """Pure aggregate quality decision for a run."""

    status: str
    valid: int
    invalid: int
    warnings: int


@dataclass(frozen=True)
class TargetQC:
    """Quality assessment for one target within a run."""

    derivation_id: int
    run_id: int
    target: str
    status: str
    valid: bool
    positive: int
    negative: int
    warnings: str
    details: str


@dataclass(frozen=True)
class RunQC:
    """Aggregate quality assessment for one PCR run."""

    derivation_id: int
    run_id: int
    status: str
    valid: int
    invalid: int
    warnings: int


@dataclass(frozen=True)
class EpisodeMembership:
    """One run's role and overlap within a derived episode."""

    run_id: int
    role: str
    score: float | None


@dataclass(frozen=True)
class ResultCandidate:
    """Persisted interpretation considered during result selection."""

    interpretation_id: int
    result_id: int
    sample_id: int
    sample_key: str
    target: str
    call: str
    run_id: int
    started: str | None
    run_class: str
    valid: bool


@dataclass(frozen=True)
class ResultSummary:
    """Selection decision for a group of equivalent target results."""

    preferred: int | None
    relation: str
    concordance: str
    needs_review: bool
    tie: bool


@dataclass(frozen=True)
class ResultSelection:
    """Result selection row ready for persistence."""

    derivation_id: int
    sample_id: int
    target: str
    preferred: int | None
    count: int
    relation: str
    concordance: str
    review: bool
    details: str
