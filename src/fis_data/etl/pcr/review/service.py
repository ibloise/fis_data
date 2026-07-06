"""Application service for auditable PCR analytical review."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, replace
from datetime import date
from typing import Any

from ..enums import QCStatuses, ResultLevels, RunClasses, WellRoles
from .models import (
    ActiveOverride,
    NewReviewAction,
    OverrideKind,
    ReviewAction,
    ReviewActionType,
    ReviewItem,
    ReviewItemType,
    ReviewResolution,
)
from .repository import PCRReviewRepository


class PCRReviewService:
    """Validate review commands independently from CLI or future UI adapters."""

    def __init__(self, repository: PCRReviewRepository) -> None:
        self.repository = repository

    def list_items(
        self,
        *,
        item_type: str | None = None,
        status: str = "OPEN",
        limit: int = 100,
        offset: int = 0,
    ) -> list[ReviewItem]:
        """Return filtered current issues with folded review state."""

        if limit <= 0 or offset < 0:
            raise ValueError("limit must be positive and offset cannot be negative.")
        normalized_type = _enum_value(ReviewItemType, item_type) if item_type else None
        normalized_status = status.upper()
        if normalized_status not in {"OPEN", "RESOLVED", "ALL"}:
            raise ValueError("status must be OPEN, RESOLVED or ALL.")
        actions = self.repository.all_actions()
        states = _review_states(actions)
        items = []
        for item in self.repository.list_current_items():
            if normalized_type and item.item_type != normalized_type:
                continue
            current_status = states.get(
                (item.item_type, item.subject_key, item.fingerprint), "OPEN"
            )
            if normalized_status != "ALL" and current_status != normalized_status:
                continue
            items.append(replace(item, status=current_status))
        items.sort(key=lambda item: (item.item_type, item.subject_key))
        return items[offset : offset + limit]

    def show(self, *, item_type: str, subject_key: str) -> dict[str, Any]:
        """Return current issue context, history and active override."""

        item = self._current_item(item_type, subject_key)
        item = self.repository.enrich_item(item)
        history = self.repository.history(item.item_type, item.subject_key)
        active = self._active_override(item.item_type, item.subject_key)
        current = replace(item, status=_review_status(item, history))
        return {
            "item": asdict(current),
            "diagnosis": _diagnosis(current),
            "candidate_actions": _candidate_actions(current),
            "commands": _command_hints(current),
            "history": [asdict(action) for action in history],
            "active_override": asdict(active) if active else None,
        }

    def summary(self) -> dict[str, Any]:
        """Summarize current PCR review workload and active decisions."""

        derivation_id = self.repository.current_derivation_id()
        actions = self.repository.all_actions()
        states = _review_states(actions)
        active_overrides = self.repository.active_overrides()
        by_type = {
            member.value: {
                "item_type": member.value,
                "total": 0,
                "open": 0,
                "resolved": 0,
            }
            for member in ReviewItemType
        }
        reason_counts: Counter[tuple[str, str]] = Counter()
        for item in self.repository.list_current_items():
            status = states.get(
                (item.item_type, item.subject_key, item.fingerprint), "OPEN"
            )
            bucket = by_type[item.item_type]
            bucket["total"] += 1
            bucket[status.lower()] += 1
            if status == "OPEN":
                reason_counts[(item.item_type, item.reason)] += 1
        override_counts: Counter[str] = Counter(
            override.item_type for override in active_overrides
        )
        rows = []
        for item_type, bucket in by_type.items():
            rows.append({**bucket, "active_overrides": override_counts[item_type]})
        rows.sort(key=lambda row: row["item_type"])
        return {
            "derivation_id": derivation_id,
            "totals": {
                "current_items": sum(row["total"] for row in rows),
                "open": sum(row["open"] for row in rows),
                "resolved": sum(row["resolved"] for row in rows),
                "active_overrides": len(active_overrides),
                "actions": len(actions),
            },
            "by_type": rows,
            "top_open_reasons": [
                {"item_type": item_type, "reason": reason, "count": count}
                for (item_type, reason), count in sorted(
                    reason_counts.items(),
                    key=lambda entry: (-entry[1], entry[0][0], entry[0][1]),
                )[:10]
            ],
        }

    def resolve(
        self,
        *,
        item_type: str,
        subject_key: str,
        fingerprint: str,
        derivation_id: int,
        resolution: str,
        reason: str,
        reviewer: str,
    ) -> ReviewAction:
        """Resolve a current issue without modifying analytical values."""

        item = self._current_item(item_type, subject_key, fingerprint, derivation_id)
        resolution_value = _enum_value(ReviewResolution, resolution)
        if resolution_value == ReviewResolution.corrected:
            raise ValueError("Use a typed override for CORRECTED resolutions.")
        return self._append(
            item,
            action=ReviewActionType.resolve,
            resolution=resolution_value,
            reason=reason,
            reviewer=reviewer,
        )

    def reopen(
        self,
        *,
        item_type: str,
        subject_key: str,
        fingerprint: str,
        derivation_id: int,
        reason: str,
        reviewer: str,
    ) -> ReviewAction:
        """Reopen the current fingerprint after a prior resolution."""

        item = self._current_item(item_type, subject_key, fingerprint, derivation_id)
        return self._append(
            item,
            action=ReviewActionType.reopen,
            reason=reason,
            reviewer=reviewer,
        )

    def override_well(
        self,
        *,
        subject_key: str,
        fingerprint: str,
        derivation_id: int,
        role: str,
        sample_key: str | None,
        reason: str,
        reviewer: str,
    ) -> ReviewAction:
        """Override well role and optional canonical sample association."""

        item = self._current_item(
            ReviewItemType.well, subject_key, fingerprint, derivation_id
        )
        role_value = _enum_value(WellRoles, role)
        if role_value == WellRoles.real_sample and not sample_key:
            raise ValueError("sample_key is required for REAL_SAMPLE wells.")
        return self._override(
            item,
            OverrideKind.well,
            {"role": role_value, "sample_key": sample_key},
            reason,
            reviewer,
        )

    def override_run(
        self,
        *,
        subject_key: str,
        fingerprint: str,
        derivation_id: int,
        run_class: str,
        episode_date: str | None,
        reason: str,
        reviewer: str,
    ) -> ReviewAction:
        """Override run classification and optional episode date."""

        item = self._current_item(
            ReviewItemType.run, subject_key, fingerprint, derivation_id
        )
        run_class_value = _enum_value(RunClasses, run_class)
        if episode_date:
            date.fromisoformat(episode_date)
        return self._override(
            item,
            OverrideKind.run,
            {"run_class": run_class_value, "episode_date_key": episode_date},
            reason,
            reviewer,
        )

    def override_episode(
        self,
        *,
        subject_key: str,
        fingerprint: str,
        derivation_id: int,
        run_ids: tuple[int, ...],
        reason: str,
        reviewer: str,
    ) -> ReviewAction:
        """Replace one reviewed episode with explicit run membership."""

        item = self._current_item(
            ReviewItemType.episode, subject_key, fingerprint, derivation_id
        )
        unique_ids = sorted(set(run_ids))
        if not unique_ids:
            raise ValueError("At least one run_id is required.")
        if not set(unique_ids).issubset(self.repository.existing_run_ids()):
            raise ValueError("Episode override contains an unknown run.")
        occupied = {
            int(run_id)
            for override in self.repository.active_overrides()
            if override.item_type == ReviewItemType.episode
            and override.subject_key != subject_key
            for run_id in override.payload.get("run_ids", [])
        }
        if occupied & set(unique_ids):
            raise ValueError(
                "A run already belongs to another active episode override."
            )
        return self._override(
            item,
            OverrideKind.episode,
            {"run_ids": unique_ids},
            reason,
            reviewer,
        )

    def override_qc(
        self,
        *,
        subject_key: str,
        fingerprint: str,
        derivation_id: int,
        status: str,
        valid: bool,
        reason: str,
        reviewer: str,
    ) -> ReviewAction:
        """Override the QC decision for one run-target pair."""

        item = self._current_item(
            ReviewItemType.target_qc, subject_key, fingerprint, derivation_id
        )
        status_value = _enum_value(QCStatuses, status)
        if valid and status_value == QCStatuses.invalid:
            raise ValueError("INVALID QC cannot be marked valid.")
        return self._override(
            item,
            OverrideKind.qc,
            {"status": status_value, "valid": valid},
            reason,
            reviewer,
        )

    def override_result(
        self,
        *,
        subject_key: str,
        fingerprint: str,
        derivation_id: int,
        preferred_result_id: int | None,
        call: str | None,
        reason: str,
        reviewer: str,
    ) -> ReviewAction:
        """Override the preferred source interpretation or effective call."""

        item = self.repository.enrich_item(
            self._current_item(
                ReviewItemType.result, subject_key, fingerprint, derivation_id
            )
        )
        if preferred_result_id is None and call is None:
            raise ValueError("Provide preferred_result_id, call, or both.")
        result_ids = {int(row["result_id"]) for row in item.context["candidates"]}
        if preferred_result_id is not None and preferred_result_id not in result_ids:
            raise ValueError("preferred_result_id is not a candidate for this result.")
        call_value = _enum_value(ResultLevels, call) if call else None
        return self._override(
            item,
            OverrideKind.result,
            {"preferred_result_id": preferred_result_id, "call": call_value},
            reason,
            reviewer,
        )

    def revoke(
        self,
        *,
        item_type: str,
        subject_key: str,
        reason: str,
        reviewer: str,
    ) -> ReviewAction:
        """Revoke the current active override using another audit event."""

        normalized_type = _enum_value(ReviewItemType, item_type)
        active = self._active_override(normalized_type, subject_key)
        if active is None:
            raise ValueError("No active override exists for this subject.")
        history = self.repository.history(normalized_type, subject_key)
        latest = history[-1]
        return self.repository.append_action(
            NewReviewAction(
                item_type=normalized_type,
                subject_key=subject_key,
                fingerprint=latest.fingerprint,
                derivation_id=latest.derivation_id,
                action=ReviewActionType.revoke,
                resolution=None,
                override_kind=active.kind,
                payload=None,
                reason=_required(reason, "reason"),
                reviewer=_required(reviewer, "reviewer"),
            )
        )

    def history(self, *, item_type: str, subject_key: str) -> list[ReviewAction]:
        """Return the immutable action history for one subject."""

        normalized_type = _enum_value(ReviewItemType, item_type)
        return self.repository.history(normalized_type, subject_key)

    def active_overrides(self) -> list[ActiveOverride]:
        """Expose active overrides to the analytical pipeline."""

        return self.repository.active_overrides()

    def _current_item(
        self,
        item_type: str,
        subject_key: str,
        fingerprint: str | None = None,
        derivation_id: int | None = None,
    ) -> ReviewItem:
        normalized_type = _enum_value(ReviewItemType, item_type)
        for item in self.repository.list_current_items():
            if item.item_type == normalized_type and item.subject_key == subject_key:
                if fingerprint is not None and item.fingerprint != fingerprint:
                    raise ValueError("Review item is stale; reload it before acting.")
                if derivation_id is not None and item.derivation_id != derivation_id:
                    raise ValueError(
                        "Review derivation is stale; reload before acting."
                    )
                return item
        raise ValueError("Review item does not exist in the current derivation.")

    def _override(
        self,
        item: ReviewItem,
        kind: str,
        payload: dict[str, Any],
        reason: str,
        reviewer: str,
    ) -> ReviewAction:
        return self._append(
            item,
            action=ReviewActionType.override,
            resolution=ReviewResolution.corrected,
            override_kind=kind,
            payload=payload,
            reason=reason,
            reviewer=reviewer,
        )

    def _append(
        self,
        item: ReviewItem,
        *,
        action: str,
        reason: str,
        reviewer: str,
        resolution: str | None = None,
        override_kind: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ReviewAction:
        return self.repository.append_action(
            NewReviewAction(
                item_type=item.item_type,
                subject_key=item.subject_key,
                fingerprint=item.fingerprint,
                derivation_id=item.derivation_id,
                action=action,
                resolution=resolution,
                override_kind=override_kind,
                payload=payload,
                reason=_required(reason, "reason"),
                reviewer=_required(reviewer, "reviewer"),
            )
        )

    def _active_override(
        self, item_type: str, subject_key: str
    ) -> ActiveOverride | None:
        return next(
            (
                override
                for override in self.repository.active_overrides()
                if override.item_type == item_type
                and override.subject_key == subject_key
            ),
            None,
        )


def _review_status(item: ReviewItem, actions: list[ReviewAction]) -> str:
    return _review_states(actions).get(
        (item.item_type, item.subject_key, item.fingerprint), "OPEN"
    )


def _review_states(
    actions: list[ReviewAction],
) -> dict[tuple[str, str, str], str]:
    states: dict[tuple[str, str, str], str] = {}
    for action in actions:
        key = (action.item_type, action.subject_key, action.fingerprint)
        if action.action in {ReviewActionType.resolve, ReviewActionType.override}:
            states[key] = "RESOLVED"
        elif action.action in {ReviewActionType.reopen, ReviewActionType.revoke}:
            states[key] = "OPEN"
    return states


def _enum_value(enum_type, value: str) -> str:
    try:
        return enum_type(str(value).upper()).value
    except ValueError as exc:
        choices = ", ".join(member.value for member in enum_type)
        raise ValueError(
            f"Invalid value {value!r}; expected one of: {choices}."
        ) from exc


def _required(value: str, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} is required.")
    return normalized


def _diagnosis(item: ReviewItem) -> dict[str, Any]:
    context = item.context
    if item.item_type == ReviewItemType.episode:
        candidates = context.get("candidate_partner_runs", [])
        return {
            "summary": (
                "Analytics could not associate this run set with an unambiguous "
                "episode partner."
            ),
            "why_review": item.reason,
            "evidence_to_check": [
                "member_runs[].run_name and episode_date_key",
                "candidate_partner_runs[].overlap_with_subject",
                "candidate_partner_runs[].targets and run_class",
            ],
            "candidate_count": len(candidates),
            "recommended_decision": (
                "Use override episode with the reviewed run IDs plus any confirmed "
                "partner runs, or resolve as DISMISSED if this run must remain "
                "unpaired."
            ),
        }
    if item.item_type == ReviewItemType.well:
        return {
            "summary": (
                "The well name could not be classified as a real sample "
                "or known control."
            ),
            "why_review": item.reason,
            "evidence_to_check": [
                "context.source.sample_name",
                "context.source.content",
                "context.source.run_name",
            ],
            "recommended_decision": (
                "Use override well to set REAL_SAMPLE with sample_key, a control "
                "role, or NON_SAMPLE_REVIEW if it should stay excluded."
            ),
        }
    if item.item_type == ReviewItemType.run:
        return {
            "summary": "The run target set did not match a known PCR run class.",
            "why_review": item.reason,
            "evidence_to_check": [
                "context.source.run_name",
                "context.source.targets",
                "context.source.episode_date_key",
            ],
            "recommended_decision": (
                "Use override run to set run_class and optionally episode_date, "
                "or resolve as ACCEPTED/DISMISSED if no correction is needed."
            ),
        }
    if item.item_type == ReviewItemType.target_qc:
        return {
            "summary": "The target QC for this run-target pair is invalid.",
            "why_review": item.reason,
            "evidence_to_check": [
                "context.source.details.failures",
                "context.source.positive_controls",
                "context.source.negative_controls",
            ],
            "recommended_decision": (
                "Use override qc only after manual QC review, otherwise resolve "
                "as ACCEPTED to keep the invalid QC."
            ),
        }
    if item.item_type == ReviewItemType.result:
        return {
            "summary": "The final result selection is not fully automatic.",
            "why_review": item.reason,
            "evidence_to_check": [
                "context.candidates[].result_id",
                "context.candidates[].call",
                "context.candidates[].valid",
                "context.candidates[].cq",
            ],
            "recommended_decision": (
                "Use override result to select a preferred_result_id and/or final "
                "call, or resolve if the analytical selection is acceptable."
            ),
        }
    return {
        "summary": "Review item needs manual assessment.",
        "why_review": item.reason,
        "evidence_to_check": ["item.context"],
        "recommended_decision": "Inspect context and choose resolve or an override.",
    }


def _candidate_actions(item: ReviewItem) -> list[dict[str, str]]:
    return [
        {
            "action": "resolve ACCEPTED",
            "when": "The analytical finding is correct and no value should change.",
        },
        {
            "action": "resolve DISMISSED",
            "when": "The item should be acknowledged but not corrected.",
        },
        *_override_action(item),
    ]


def _override_action(item: ReviewItem) -> list[dict[str, str]]:
    if item.item_type == ReviewItemType.well:
        return [
            {
                "action": "override well",
                "when": "The well role or sample key needs a typed correction.",
            }
        ]
    if item.item_type == ReviewItemType.run:
        return [
            {
                "action": "override run",
                "when": "The run class or episode date should be corrected.",
            }
        ]
    if item.item_type == ReviewItemType.episode:
        return [
            {
                "action": "override episode",
                "when": "You can explicitly state the run IDs that form the episode.",
            }
        ]
    if item.item_type == ReviewItemType.target_qc:
        return [
            {
                "action": "override qc",
                "when": "Manual QC review justifies changing validity/status.",
            }
        ]
    if item.item_type == ReviewItemType.result:
        return [
            {
                "action": "override result",
                "when": "A candidate source result or final call should be forced.",
            }
        ]
    return []


def _command_hints(item: ReviewItem) -> dict[str, Any]:
    base = {
        "resolve_accepted": (
            "fis-data pcr-review resolve "
            f"--type {item.item_type} --subject {item.subject_key} "
            f"--derivation-id {item.derivation_id} "
            f"--fingerprint {item.fingerprint} "
            "--resolution ACCEPTED --reviewer <reviewer> --reason <reason>"
        ),
        "resolve_dismissed": (
            "fis-data pcr-review resolve "
            f"--type {item.item_type} --subject {item.subject_key} "
            f"--derivation-id {item.derivation_id} "
            f"--fingerprint {item.fingerprint} "
            "--resolution DISMISSED --reviewer <reviewer> --reason <reason>"
        ),
    }
    override = _override_command(item)
    if override:
        base["override"] = override
    return base


def _override_command(item: ReviewItem) -> str | None:
    prefix = (
        "fis-data pcr-review override {kind} "
        f"--subject {item.subject_key} "
        f"--derivation-id {item.derivation_id} "
        f"--fingerprint {item.fingerprint} "
    )
    suffix = "--reviewer <reviewer> --reason <reason>"
    if item.item_type == ReviewItemType.well:
        return (
            prefix.format(kind="well")
            + "--role <role> --sample-key <sample_key> "
            + suffix
        )
    if item.item_type == ReviewItemType.run:
        return (
            prefix.format(kind="run")
            + "--run-class <class> --episode-date <YYYY-MM-DD> "
            + suffix
        )
    if item.item_type == ReviewItemType.episode:
        run_ids = " ".join(
            f"--run-id {run_id}" for run_id in item.context.get("run_ids", [])
        )
        return (
            prefix.format(kind="episode")
            + f"{run_ids} --run-id <partner_run_id> "
            + suffix
        )
    if item.item_type == ReviewItemType.target_qc:
        return (
            prefix.format(kind="qc")
            + "--status <status> --valid|--invalid "
            + suffix
        )
    if item.item_type == ReviewItemType.result:
        return (
            prefix.format(kind="result")
            + "--preferred-result-id <result_id> --call <call> "
            + suffix
        )
    return None
