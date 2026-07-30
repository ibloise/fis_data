"""SQLite persistence and read models for PCR review."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from .models import (
    ActiveOverride,
    NewReviewAction,
    ReviewAction,
    ReviewActionType,
    ReviewItem,
    ReviewItemType,
)


class PCRReviewRepository:
    """Load current review issues and append immutable review actions."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def current_derivation_id(self) -> int:
        """Return the explicitly selected successful analytical derivation."""

        with self.engine.connect() as conn:
            value = conn.execute(
                text(
                    """
                    SELECT derivation_id FROM pcr_derivation_run
                    WHERE status='SUCCESS' AND is_current=1
                    """
                )
            ).scalar_one()
        if value is None:
            raise ValueError("No successful PCR analytical derivation exists.")
        return int(value)

    def list_current_items(self) -> list[ReviewItem]:
        """Build the current heterogeneous review queue with stable keys."""

        derivation_id = self.current_derivation_id()
        items: list[ReviewItem] = []
        with self.engine.connect() as conn:
            well_rows = conn.execute(
                text(
                    """
                    SELECT pcr_well_id, review_reason, raw_sample_name,
                           well_role, normalized_name
                    FROM pcr_well_classification
                    WHERE derivation_id=:id AND review_reason IS NOT NULL
                    """
                ),
                {"id": derivation_id},
            ).all()
            run_rows = conn.execute(
                text(
                    """
                    SELECT pcr_run_id, review_reason, run_class,
                           episode_date_key, canonical_targets_json
                    FROM pcr_run_analysis
                    WHERE derivation_id=:id AND review_reason IS NOT NULL
                    """
                ),
                {"id": derivation_id},
            ).all()
            episode_rows = conn.execute(
                text(
                    """
                    SELECT episode.episode_key, episode.review_reason,
                           episode.association_status,
                           GROUP_CONCAT(episode_run.pcr_run_id)
                    FROM pcr_episode AS episode
                    JOIN pcr_episode_run AS episode_run
                      ON episode_run.episode_id=episode.episode_id
                    WHERE episode.derivation_id=:id
                      AND episode.association_status='REVIEW'
                    GROUP BY episode.episode_id
                    """
                ),
                {"id": derivation_id},
            ).all()
            qc_rows = conn.execute(
                text(
                    """
                    SELECT pcr_run_id, canonical_target, qc_status,
                           is_valid, details_json
                    FROM pcr_target_qc
                    WHERE derivation_id=:id AND qc_status='INVALID'
                    """
                ),
                {"id": derivation_id},
            ).all()
            result_rows = conn.execute(
                text(
                    """
                    SELECT sample.sample_key, selection.run_type, selection.canonical_target,
                           selection.concordance, selection.relation_kind,
                           selection.occurrence_count
                    FROM pcr_result_selection AS selection
                    JOIN pcr_sample AS sample
                      ON sample.pcr_sample_id=selection.pcr_sample_id
                    WHERE selection.derivation_id=:id
                      AND selection.needs_review=1
                    """
                ),
                {"id": derivation_id},
            ).all()

        for row in well_rows:
            items.append(
                _item(
                    ReviewItemType.well,
                    f"well:{int(row[0])}",
                    derivation_id,
                    str(row[1]),
                    {
                        "well_id": int(row[0]),
                        "raw_sample_name": row[2],
                        "role": str(row[3]),
                        "normalized_name": row[4],
                    },
                )
            )
        for row in run_rows:
            items.append(
                _item(
                    ReviewItemType.run,
                    f"run:{int(row[0])}",
                    derivation_id,
                    str(row[1]),
                    {
                        "run_id": int(row[0]),
                        "run_class": str(row[2]),
                        "episode_date_key": row[3],
                        "targets": json.loads(str(row[4])),
                    },
                )
            )
        for row in episode_rows:
            run_ids = sorted(int(value) for value in str(row[3]).split(","))
            items.append(
                _item(
                    ReviewItemType.episode,
                    "runs:" + ",".join(str(value) for value in run_ids),
                    derivation_id,
                    str(row[1] or "Episode requires review."),
                    {
                        "episode_key": str(row[0]),
                        "association_status": str(row[2]),
                        "run_ids": run_ids,
                    },
                    fingerprint_context={
                        "association_status": str(row[2]),
                        "run_ids": run_ids,
                    },
                )
            )
        for row in qc_rows:
            items.append(
                _item(
                    ReviewItemType.target_qc,
                    f"run:{int(row[0])}:target:{str(row[1])}",
                    derivation_id,
                    str(row[4]),
                    {
                        "run_id": int(row[0]),
                        "target": str(row[1]),
                        "status": str(row[2]),
                        "valid": bool(row[3]),
                    },
                )
            )
        for row in result_rows:
            items.append(
                _item(
                    ReviewItemType.result,
                    f"sample:{str(row[0])}:run_type:{str(row[1])}:target:{str(row[2])}",
                    derivation_id,
                    str(row[3]),
                    {
                        "sample_key": str(row[0]),
                        "run_type": str(row[1]),
                        "target": str(row[2]),
                        "concordance": str(row[3]),
                        "relation": str(row[4]),
                        "occurrences": int(row[5]),
                    },
                )
            )
        return items

    def enrich_item(self, item: ReviewItem) -> ReviewItem:
        """Add candidate details needed to inspect or validate an item."""

        if item.item_type == ReviewItemType.well:
            return self._enrich_well_item(item)
        if item.item_type == ReviewItemType.run:
            return self._enrich_run_item(item)
        if item.item_type == ReviewItemType.episode:
            return self._enrich_episode_item(item)
        if item.item_type == ReviewItemType.target_qc:
            return self._enrich_qc_item(item)
        if item.item_type != ReviewItemType.result:
            return item
        return self._enrich_result_item(item)

    def _enrich_well_item(self, item: ReviewItem) -> ReviewItem:
        """Attach source well and run details for a well review item."""

        well_id = int(item.context["well_id"])
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT well.well, well.sample_name, well.content,
                           run.pcr_run_id, run.run_name,
                           analysis.run_class, analysis.episode_date_key
                    FROM pcr_well AS well
                    LEFT JOIN pcr_run AS run
                      ON run.pcr_run_id=well.pcr_run_id
                    LEFT JOIN pcr_run_analysis AS analysis
                      ON analysis.derivation_id=:derivation_id
                     AND analysis.pcr_run_id=well.pcr_run_id
                    WHERE well.pcr_well_id=:well_id
                    """
                ),
                {"derivation_id": item.derivation_id, "well_id": well_id},
            ).one_or_none()
        if row is None:
            return item
        context = dict(item.context)
        context["source"] = {
            "well": row[0],
            "sample_name": row[1],
            "content": row[2],
            "run_id": int(row[3]) if row[3] is not None else None,
            "run_name": row[4],
            "run_class": row[5],
            "episode_date_key": row[6],
        }
        return ReviewItem(**{**asdict(item), "context": context})

    def _enrich_run_item(self, item: ReviewItem) -> ReviewItem:
        """Attach source run details and sample counts for a run review item."""

        run_id = int(item.context["run_id"])
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT run.run_name, analysis.run_class,
                           analysis.episode_date_key,
                           analysis.canonical_targets_json,
                           COUNT(DISTINCT classification.pcr_sample_id)
                    FROM pcr_run_analysis AS analysis
                    LEFT JOIN pcr_run AS run
                      ON run.pcr_run_id=analysis.pcr_run_id
                    LEFT JOIN pcr_well AS well
                      ON well.pcr_run_id=analysis.pcr_run_id
                    LEFT JOIN pcr_well_classification AS classification
                      ON classification.derivation_id=analysis.derivation_id
                     AND classification.pcr_well_id=well.pcr_well_id
                     AND classification.pcr_sample_id IS NOT NULL
                    WHERE analysis.derivation_id=:derivation_id
                      AND analysis.pcr_run_id=:run_id
                    GROUP BY run.run_name, analysis.run_class,
                             analysis.episode_date_key,
                             analysis.canonical_targets_json
                    """
                ),
                {"derivation_id": item.derivation_id, "run_id": run_id},
            ).one_or_none()
        if row is None:
            return item
        context = dict(item.context)
        context["source"] = {
            "run_name": row[0],
            "run_class": row[1],
            "episode_date_key": row[2],
            "targets": json.loads(str(row[3])),
            "real_sample_count": int(row[4]),
        }
        return ReviewItem(**{**asdict(item), "context": context})

    def _enrich_episode_item(self, item: ReviewItem) -> ReviewItem:
        """Attach member runs and plausible episode partners."""

        run_ids = [int(value) for value in item.context.get("run_ids", [])]
        subject_samples = self._run_sample_sets(item.derivation_id, run_ids)
        subject_union = (
            set().union(*subject_samples.values()) if subject_samples else set()
        )
        with self.engine.connect() as conn:
            member_rows = conn.execute(
                text(
                    """
                    SELECT analysis.pcr_run_id, run.run_name, analysis.run_class,
                           analysis.episode_date_key,
                           analysis.canonical_targets_json,
                           COUNT(DISTINCT classification.pcr_sample_id)
                    FROM pcr_run_analysis AS analysis
                    LEFT JOIN pcr_run AS run
                      ON run.pcr_run_id=analysis.pcr_run_id
                    LEFT JOIN pcr_well AS well
                      ON well.pcr_run_id=analysis.pcr_run_id
                    LEFT JOIN pcr_well_classification AS classification
                      ON classification.derivation_id=analysis.derivation_id
                     AND classification.pcr_well_id=well.pcr_well_id
                     AND classification.pcr_sample_id IS NOT NULL
                    WHERE analysis.derivation_id=:derivation_id
                      AND analysis.pcr_run_id IN :run_ids
                    GROUP BY analysis.pcr_run_id, run.run_name, analysis.run_class,
                             analysis.episode_date_key,
                             analysis.canonical_targets_json
                    """
                ).bindparams(bindparam("run_ids", expanding=True)),
                {"derivation_id": item.derivation_id, "run_ids": run_ids or [-1]},
            ).all()
            candidate_rows = conn.execute(
                text(
                    """
                    SELECT analysis.pcr_run_id, run.run_name, analysis.run_class,
                           analysis.episode_date_key,
                           analysis.canonical_targets_json,
                           COUNT(DISTINCT classification.pcr_sample_id)
                    FROM pcr_run_analysis AS analysis
                    LEFT JOIN pcr_run AS run
                      ON run.pcr_run_id=analysis.pcr_run_id
                    LEFT JOIN pcr_well AS well
                      ON well.pcr_run_id=analysis.pcr_run_id
                    LEFT JOIN pcr_well_classification AS classification
                      ON classification.derivation_id=analysis.derivation_id
                     AND classification.pcr_well_id=well.pcr_well_id
                     AND classification.pcr_sample_id IS NOT NULL
                    WHERE analysis.derivation_id=:derivation_id
                    GROUP BY analysis.pcr_run_id, run.run_name, analysis.run_class,
                             analysis.episode_date_key,
                             analysis.canonical_targets_json
                    """
                ),
                {"derivation_id": item.derivation_id},
            ).all()
        all_sample_sets = self._run_sample_sets(
            item.derivation_id, [int(row[0]) for row in candidate_rows]
        )
        member_id_set = set(run_ids)
        candidate_runs = []
        for row in candidate_rows:
            candidate_id = int(row[0])
            if candidate_id in member_id_set:
                continue
            candidate_samples = all_sample_sets.get(candidate_id, set())
            overlap_score = _overlap(subject_union, candidate_samples)
            same_date = bool(
                row[3] and any(member[3] == row[3] for member in member_rows)
            )
            if overlap_score <= 0 and not same_date:
                continue
            candidate_runs.append(
                {
                    "run_id": candidate_id,
                    "run_name": row[1],
                    "run_class": row[2],
                    "episode_date_key": row[3],
                    "targets": json.loads(str(row[4])),
                    "real_sample_count": int(row[5]),
                    "overlap_with_subject": overlap_score,
                    "shares_episode_date": same_date,
                }
            )
        candidate_runs.sort(
            key=lambda value: (
                not value["shares_episode_date"],
                -float(value["overlap_with_subject"]),
                int(value["run_id"]),
            )
        )
        context = dict(item.context)
        context["member_runs"] = [
            {
                "run_id": int(row[0]),
                "run_name": row[1],
                "run_class": row[2],
                "episode_date_key": row[3],
                "targets": json.loads(str(row[4])),
                "real_sample_count": int(row[5]),
            }
            for row in member_rows
        ]
        context["candidate_partner_runs"] = candidate_runs[:20]
        context["candidate_note"] = (
            "Candidates are runs from the current derivation that share the "
            "episode date or at least one real sample with the reviewed run set."
        )
        return ReviewItem(**{**asdict(item), "context": context})

    def _enrich_qc_item(self, item: ReviewItem) -> ReviewItem:
        """Parse QC details and attach the source run name."""

        run_id = int(item.context["run_id"])
        target = str(item.context["target"])
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT run.run_name, qc.positive_controls,
                           qc.negative_controls, qc.warnings_json,
                           qc.details_json
                    FROM pcr_target_qc AS qc
                    LEFT JOIN pcr_run AS run
                      ON run.pcr_run_id=qc.pcr_run_id
                    WHERE qc.derivation_id=:derivation_id
                      AND qc.pcr_run_id=:run_id
                      AND qc.canonical_target=:target
                    """
                ),
                {
                    "derivation_id": item.derivation_id,
                    "run_id": run_id,
                    "target": target,
                },
            ).one_or_none()
        if row is None:
            return item
        context = dict(item.context)
        context["source"] = {
            "run_name": row[0],
            "positive_controls": int(row[1]),
            "negative_controls": int(row[2]),
            "warnings": _loads_json(row[3]),
            "details": _loads_json(row[4]),
        }
        return ReviewItem(**{**asdict(item), "context": context})

    def _enrich_result_item(self, item: ReviewItem) -> ReviewItem:
        """Attach candidate interpretations for a result review item."""

        sample_key = str(item.context["sample_key"])
        run_type = str(item.context["run_type"])
        target = str(item.context["target"])
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT interpretation.interpretation_id,
                           interpretation.pcr_cq_result_id,
                           interpretation.call, interpretation.cq,
                           interpretation.pcr_run_id, qc.is_valid
                    FROM pcr_interpretation AS interpretation
                    JOIN pcr_well_classification AS classification
                      ON classification.derivation_id=interpretation.derivation_id
                     AND classification.pcr_well_id=interpretation.pcr_well_id
                    JOIN pcr_sample AS sample
                      ON sample.pcr_sample_id=classification.pcr_sample_id
                    JOIN pcr_run_analysis AS analysis
                      ON analysis.derivation_id=interpretation.derivation_id
                     AND analysis.pcr_run_id=interpretation.pcr_run_id
                    LEFT JOIN pcr_target_qc AS qc
                      ON qc.derivation_id=interpretation.derivation_id
                     AND qc.pcr_run_id=interpretation.pcr_run_id
                     AND qc.canonical_target=interpretation.canonical_target
                    WHERE interpretation.derivation_id=:id
                      AND sample.sample_key=:sample_key
                      AND analysis.run_type=:run_type
                      AND interpretation.canonical_target=:target
                    ORDER BY interpretation.pcr_run_id
                    """
                ),
                {
                    "id": item.derivation_id,
                    "sample_key": sample_key,
                    "run_type": run_type,
                    "target": target,
                },
            ).all()
        context = dict(item.context)
        context["candidates"] = [
            {
                "interpretation_id": int(row[0]),
                "result_id": int(row[1]),
                "call": str(row[2]),
                "cq": float(row[3]) if row[3] is not None else None,
                "run_id": int(row[4]),
                "valid": bool(row[5]),
            }
            for row in rows
        ]
        return ReviewItem(**{**asdict(item), "context": context})

    def _run_sample_sets(
        self, derivation_id: int, run_ids: list[int]
    ) -> dict[int, set[int]]:
        """Return real sample identifiers observed in selected runs."""

        if not run_ids:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT well.pcr_run_id, classification.pcr_sample_id
                    FROM pcr_well AS well
                    JOIN pcr_well_classification AS classification
                      ON classification.derivation_id=:derivation_id
                     AND classification.pcr_well_id=well.pcr_well_id
                    WHERE classification.pcr_sample_id IS NOT NULL
                      AND well.pcr_run_id IN :run_ids
                    GROUP BY well.pcr_run_id, classification.pcr_sample_id
                    """
                ).bindparams(bindparam("run_ids", expanding=True)),
                {"derivation_id": derivation_id, "run_ids": run_ids},
            ).all()
        samples: dict[int, set[int]] = {}
        for run_id, sample_id in rows:
            samples.setdefault(int(run_id), set()).add(int(sample_id))
        return samples

    def append_action(self, action: NewReviewAction) -> ReviewAction:
        """Append and return one immutable review event."""

        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO pcr_review_action (
                        item_type, subject_key, issue_fingerprint,
                        source_derivation_id, action, resolution, override_kind,
                        payload_json, reason, reviewer
                    ) VALUES (
                        :item_type, :subject_key, :fingerprint,
                        :derivation_id, :action, :resolution, :override_kind,
                        :payload, :reason, :reviewer
                    )
                    """
                ),
                {
                    "item_type": action.item_type,
                    "subject_key": action.subject_key,
                    "fingerprint": action.fingerprint,
                    "derivation_id": action.derivation_id,
                    "action": action.action,
                    "resolution": action.resolution,
                    "override_kind": action.override_kind,
                    "payload": _json(action.payload) if action.payload else None,
                    "reason": action.reason,
                    "reviewer": action.reviewer,
                },
            )
            action_id = int(result.lastrowid)
        return self.get_action(action_id)

    def get_action(self, action_id: int) -> ReviewAction:
        """Load one action by identifier."""

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT review_action_id, item_type, subject_key,
                           issue_fingerprint, source_derivation_id, action,
                           resolution, override_kind, payload_json, reason,
                           reviewer, created_at
                    FROM pcr_review_action WHERE review_action_id=:id
                    """
                ),
                {"id": action_id},
            ).one()
        return _action(row)

    def history(self, item_type: str, subject_key: str) -> list[ReviewAction]:
        """Return all events for a subject in append order."""

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT review_action_id, item_type, subject_key,
                           issue_fingerprint, source_derivation_id, action,
                           resolution, override_kind, payload_json, reason,
                           reviewer, created_at
                    FROM pcr_review_action
                    WHERE item_type=:item_type AND subject_key=:subject_key
                    ORDER BY review_action_id
                    """
                ),
                {"item_type": item_type, "subject_key": subject_key},
            ).all()
        return [_action(row) for row in rows]

    def all_actions(self) -> list[ReviewAction]:
        """Load all review events for state folding."""

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT review_action_id, item_type, subject_key,
                           issue_fingerprint, source_derivation_id, action,
                           resolution, override_kind, payload_json, reason,
                           reviewer, created_at
                    FROM pcr_review_action ORDER BY review_action_id
                    """
                )
            ).all()
        return [_action(row) for row in rows]

    def active_overrides(self) -> list[ActiveOverride]:
        """Fold override and revoke events into current override state."""

        active: dict[tuple[str, str], ActiveOverride] = {}
        for action in self.all_actions():
            if action.action == ReviewActionType.override and action.override_kind:
                active[(action.item_type, action.subject_key)] = ActiveOverride(
                    action_id=action.action_id,
                    item_type=action.item_type,
                    subject_key=action.subject_key,
                    kind=action.override_kind,
                    payload=action.payload or {},
                )
            elif action.action == ReviewActionType.revoke:
                active.pop((action.item_type, action.subject_key), None)
        return list(active.values())

    def existing_run_ids(self) -> set[int]:
        """Return run identifiers present in the current derivation."""

        derivation_id = self.current_derivation_id()
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT pcr_run_id FROM pcr_run_analysis
                    WHERE derivation_id=:id
                    """
                ),
                {"id": derivation_id},
            ).scalars()
            return {int(value) for value in rows}


def _item(
    item_type: str,
    subject_key: str,
    derivation_id: int,
    reason: str,
    context: dict[str, Any],
    fingerprint_context: dict[str, Any] | None = None,
) -> ReviewItem:
    canonical = _json(
        {
            "type": item_type,
            "subject": subject_key,
            "reason": reason,
            "context": fingerprint_context or context,
        }
    )
    return ReviewItem(
        item_type=item_type,
        subject_key=subject_key,
        derivation_id=derivation_id,
        fingerprint=hashlib.sha256(canonical.encode()).hexdigest(),
        reason=reason,
        context=context,
    )


def _action(row: Any) -> ReviewAction:
    return ReviewAction(
        action_id=int(row[0]),
        item_type=str(row[1]),
        subject_key=str(row[2]),
        fingerprint=str(row[3]),
        derivation_id=int(row[4]),
        action=str(row[5]),
        resolution=str(row[6]) if row[6] is not None else None,
        override_kind=str(row[7]) if row[7] is not None else None,
        payload=json.loads(str(row[8])) if row[8] else None,
        reason=str(row[9]),
        reviewer=str(row[10]),
        created_at=str(row[11]),
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads_json(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return value


def _overlap(left: set[int], right: set[int]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))
