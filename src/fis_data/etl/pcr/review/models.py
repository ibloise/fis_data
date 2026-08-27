"""Typed records for PCR analytical review."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ReviewItemType(StrEnum):
    well = "WELL"
    run = "RUN"
    episode = "EPISODE"
    target_qc = "TARGET_QC"
    result = "RESULT"


class ReviewActionType(StrEnum):
    resolve = "RESOLVE"
    reopen = "REOPEN"
    override = "OVERRIDE"
    revoke = "REVOKE"


class ReviewResolution(StrEnum):
    accepted = "ACCEPTED"
    dismissed = "DISMISSED"
    corrected = "CORRECTED"


class OverrideKind(StrEnum):
    well = "WELL"
    run = "RUN"
    episode = "EPISODE"
    qc = "QC"
    result = "RESULT"


@dataclass(frozen=True)
class ReviewItem:
    """Current analytical issue enriched with its review status."""

    item_type: str
    subject_key: str
    derivation_id: int
    fingerprint: str
    reason: str
    context: dict[str, Any]
    status: str = "OPEN"


@dataclass(frozen=True)
class ReviewAction:
    """One immutable review event."""

    action_id: int
    item_type: str
    subject_key: str
    fingerprint: str
    derivation_id: int
    action: str
    resolution: str | None
    override_kind: str | None
    payload: dict[str, Any] | None
    reason: str
    reviewer: str
    created_at: str


@dataclass(frozen=True)
class NewReviewAction:
    """Validated review event ready for persistence."""

    item_type: str
    subject_key: str
    fingerprint: str
    derivation_id: int
    action: str
    resolution: str | None
    override_kind: str | None
    payload: dict[str, Any] | None
    reason: str
    reviewer: str


@dataclass(frozen=True)
class QCOverrideRequest:
    """One reviewed target-QC decision submitted for batch application."""

    subject_key: str
    fingerprint: str
    derivation_id: int
    status: str
    valid: bool
    reason: str
    reviewer: str


@dataclass(frozen=True)
class ActiveOverride:
    """Latest non-revoked override for one analytical subject."""

    action_id: int
    item_type: str
    subject_key: str
    kind: str
    payload: dict[str, Any]
