"""Rebuildable analytical model for normalized PCR data."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .pcr_melting import (
    MeltingCall,
    MeltingParameters,
    Peak,
    canonical_target,
    interpret_curve,
)

RULE_VERSION = "1.0"
POSITIVE_CONTROLS = {"CP", "C+", "CPNEW", "CPOLD"}
NEGATIVE_CONTROLS = {"CN", "C-", "CNEG"}
CARBAPENEMASE_TARGETS = {"OXA48", "VIM", "KPC", "NDM"}


@dataclass(frozen=True)
class DerivationStats:
    derivation_id: int
    algorithm_version: str
    wells_classified: int
    real_samples: int
    interpretations: int
    target_qc_rows: int
    episodes: int
    result_groups: int
    review_items: int


class PCRAnalyticsJob:
    """Build one versioned PCR analytical derivation."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def run(
        self,
        *,
        algorithm_version: str,
        params: MeltingParameters | None = None,
        rebuild: bool = False,
    ) -> DerivationStats:
        current = params or MeltingParameters()
        with self.engine.begin() as conn:
            if rebuild:
                _clear_derived_model(conn)
            derivation_id = _start_derivation(
                conn,
                algorithm_version=algorithm_version,
                params=current,
            )

        try:
            wells_classified, real_samples = self._classify_wells(derivation_id)
            self._classify_runs(derivation_id)
            interpretations = self._interpret(derivation_id, current)
            target_qc_rows = self._evaluate_qc(derivation_id)
            episodes = self._associate_episodes(derivation_id)
            result_groups = self._select_results(derivation_id)
            review_items = self._count_review_items(derivation_id)
            stats = DerivationStats(
                derivation_id=derivation_id,
                algorithm_version=algorithm_version,
                wells_classified=wells_classified,
                real_samples=real_samples,
                interpretations=interpretations,
                target_qc_rows=target_qc_rows,
                episodes=episodes,
                result_groups=result_groups,
                review_items=review_items,
            )
            with self.engine.begin() as conn:
                _finish_derivation(
                    conn,
                    derivation_id,
                    status="SUCCESS",
                    details=asdict(stats),
                )
                _create_views(conn)
            return stats
        except Exception as exc:
            with self.engine.begin() as conn:
                _finish_derivation(
                    conn,
                    derivation_id,
                    status="FAILED",
                    details={"error": str(exc)},
                )
            raise

    def _classify_wells(self, derivation_id: int) -> tuple[int, int]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT pcr_well_id, sample_name, content
                    FROM pcr_well ORDER BY pcr_well_id
                    """
                )
            ).all()
            sample_keys = sorted(
                {_sample_key(str(row[1])) for row in rows if _is_real_sample(row[1])}
            )
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
            sample_ids = {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    text("SELECT sample_key, pcr_sample_id FROM pcr_sample")
                )
            }
            payload = []
            for well_id, sample_name, content in rows:
                classification = _classify_sample_name(sample_name)
                sample_key = (
                    _sample_key(str(sample_name))
                    if classification[0] == "REAL_SAMPLE"
                    else None
                )
                payload.append(
                    {
                        "derivation_id": derivation_id,
                        "well_id": int(well_id),
                        "sample_id": sample_ids.get(sample_key),
                        "raw_name": sample_name,
                        "normalized": classification[1],
                        "role": classification[0],
                        "rule_key": classification[2],
                        "content": content,
                        "review": classification[3],
                    }
                )
            _execute_batches(
                conn,
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
                payload,
            )
        return len(rows), len(sample_keys)

    def _classify_runs(self, derivation_id: int) -> None:
        with self.engine.begin() as conn:
            run_rows = conn.execute(
                text(
                    """
                    SELECT run.pcr_run_id, run.run_name,
                           started.value_json
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
                targets_by_run[int(run_id)].add(canonical_target(str(target)))
            payload = []
            for run_id, run_name, started_json in run_rows:
                targets = targets_by_run[int(run_id)]
                run_class, review = _run_class(targets)
                started = _parse_cfx_datetime(
                    json.loads(str(started_json)) if started_json is not None else None
                )
                payload.append(
                    {
                        "derivation_id": derivation_id,
                        "run_id": int(run_id),
                        "run_class": run_class,
                        "run_started": started,
                        "episode_key": _episode_date_key(str(run_name)),
                        "targets": _json(sorted(targets)),
                        "review": review,
                    }
                )
            _execute_batches(
                conn,
                """
                INSERT INTO pcr_run_analysis (
                    derivation_id, pcr_run_id, run_class, run_started,
                    episode_date_key, canonical_targets_json, review_reason
                ) VALUES (
                    :derivation_id, :run_id, :run_class, :run_started,
                    :episode_key, :targets, :review
                )
                """,
                payload,
            )

    def _interpret(self, derivation_id: int, params: MeltingParameters) -> int:
        with self.engine.connect() as conn:
            cq_rows = conn.execute(
                text(
                    """
                    SELECT pcr_cq_result_id, pcr_run_id, pcr_well_id,
                           target, cq
                    FROM pcr_cq_result
                    ORDER BY pcr_well_id
                    """
                )
            ).all()
        cq_by_well = {
            int(row[2]): {
                "result_id": int(row[0]),
                "run_id": int(row[1]),
                "well_id": int(row[2]),
                "target": str(row[3]),
                "cq": float(row[4]) if row[4] is not None else None,
            }
            for row in cq_rows
        }
        interpreted_wells: set[int] = set()
        inserts: list[dict[str, Any]] = []

        with self.engine.connect() as conn:
            result = conn.execution_options(stream_results=True).execute(
                text(
                    """
                    SELECT pcr_well_id, axis_value, rfu
                    FROM pcr_rfu_measurement
                    WHERE measurement_kind = 'melt_curve_derivative'
                    ORDER BY pcr_well_id, axis_value
                    """
                )
            )
            current_well: int | None = None
            temperatures: list[float] = []
            derivatives: list[float] = []
            for row in result:
                well_id = int(row[0])
                if current_well is not None and well_id != current_well:
                    cq_row = cq_by_well.get(current_well)
                    if cq_row is not None:
                        inserts.extend(
                            _interpretation_payload(
                                derivation_id, cq_row, temperatures, derivatives, params
                            )
                        )
                        interpreted_wells.add(current_well)
                    temperatures, derivatives = [], []
                current_well = well_id
                temperatures.append(float(row[1]))
                derivatives.append(float(row[2]))
            if current_well is not None and (cq_row := cq_by_well.get(current_well)):
                inserts.extend(
                    _interpretation_payload(
                        derivation_id, cq_row, temperatures, derivatives, params
                    )
                )
                interpreted_wells.add(current_well)

        for well_id, cq_row in cq_by_well.items():
            if well_id in interpreted_wells:
                continue
            target = canonical_target(cq_row["target"])
            if target in {"16S", "FAIL", "FAIL-2"}:
                inserts.extend(
                    _interpretation_payload(derivation_id, cq_row, [], [], params)
                )
            else:
                inserts.append(
                    _call_payload(
                        derivation_id,
                        cq_row,
                        MeltingCall(
                            target=target,
                            call="ERROR",
                            error="Missing melt-curve derivative data.",
                        ),
                        [],
                    )
                )
        with self.engine.begin() as conn:
            _execute_batches(
                conn,
                """
                INSERT INTO pcr_interpretation (
                    derivation_id, pcr_run_id, pcr_well_id, pcr_cq_result_id,
                    source_target, canonical_target, call, cq,
                    peak_temperature, peak_deriv_rfu, peaks_json, metrics_json,
                    error
                ) VALUES (
                    :derivation_id, :run_id, :well_id, :result_id,
                    :source_target, :target, :call, :cq,
                    :peak_temp, :peak_rfu, :peaks, :metrics, :error
                )
                """,
                inserts,
            )
        return len(inserts)

    def _evaluate_qc(self, derivation_id: int) -> int:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT interpretation.pcr_run_id,
                           interpretation.canonical_target,
                           classification.well_role,
                           interpretation.call
                    FROM pcr_interpretation AS interpretation
                    JOIN pcr_well_classification AS classification
                      ON classification.derivation_id = interpretation.derivation_id
                     AND classification.pcr_well_id = interpretation.pcr_well_id
                    WHERE interpretation.derivation_id = :derivation_id
                    """
                ),
                {"derivation_id": derivation_id},
            ).all()
            grouped: dict[tuple[int, str], dict[str, list[str]]] = defaultdict(
                lambda: defaultdict(list)
            )
            for run_id, target, role, call in rows:
                grouped[(int(run_id), str(target))][str(role)].append(str(call))
            target_payload = []
            run_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for (run_id, target), roles in grouped.items():
                positive = roles.get("POSITIVE_CONTROL", [])
                negative = roles.get("NEGATIVE_CONTROL", [])
                warnings = []
                failures = []
                if not positive:
                    warnings.append("MISSING_POSITIVE_CONTROL")
                if not negative:
                    warnings.append("MISSING_NEGATIVE_CONTROL")
                if any(call != "POSITIVE" for call in positive):
                    failures.append("POSITIVE_CONTROL_NOT_POSITIVE")
                if any(call == "POSITIVE" for call in negative):
                    failures.append("NEGATIVE_CONTROL_POSITIVE")
                if any(call == "INDETERMINATE" for call in negative):
                    warnings.append("NEGATIVE_CONTROL_INDETERMINATE")
                status = "INVALID" if failures else ("WARNING" if warnings else "VALID")
                item = {
                    "derivation_id": derivation_id,
                    "run_id": run_id,
                    "target": target,
                    "status": status,
                    "valid": not failures,
                    "positive": len(positive),
                    "negative": len(negative),
                    "warnings": _json(warnings),
                    "details": _json({"failures": failures, "roles": roles}),
                }
                target_payload.append(item)
                run_groups[run_id].append(item)
            _execute_batches(
                conn,
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
                target_payload,
            )
            run_payload = []
            for run_id, items in run_groups.items():
                invalid = sum(not item["valid"] for item in items)
                warning_count = sum(
                    bool(json.loads(item["warnings"])) for item in items
                )
                if invalid == len(items):
                    status = "INVALID"
                elif invalid:
                    status = "PARTIALLY_INVALID"
                elif warning_count:
                    status = "VALID_WITH_WARNINGS"
                else:
                    status = "VALID"
                run_payload.append(
                    {
                        "derivation_id": derivation_id,
                        "run_id": run_id,
                        "status": status,
                        "valid": len(items) - invalid,
                        "invalid": invalid,
                        "warnings": warning_count,
                    }
                )
            _execute_batches(
                conn,
                """
                INSERT INTO pcr_run_qc (
                    derivation_id, pcr_run_id, qc_status, valid_targets,
                    invalid_targets, warning_count
                ) VALUES (
                    :derivation_id, :run_id, :status, :valid,
                    :invalid, :warnings
                )
                """,
                run_payload,
            )
        return len(target_payload)

    def _associate_episodes(self, derivation_id: int) -> int:
        with self.engine.begin() as conn:
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
                      AND classification.well_role = 'REAL_SAMPLE'
                    GROUP BY well.pcr_run_id, classification.pcr_sample_id
                    """
                ),
                {"derivation_id": derivation_id},
            ).all()
            samples: dict[int, set[int]] = defaultdict(set)
            for run_id, sample_id in sample_rows:
                samples[int(run_id)].add(int(sample_id))
            by_date: dict[str | None, list[tuple[int, str]]] = defaultdict(list)
            for run_id, run_class, date_key in run_rows:
                by_date[str(date_key) if date_key else None].append(
                    (int(run_id), str(run_class))
                )
            episode_count = 0
            for date_key, candidates in by_date.items():
                components = (
                    _run_components(candidates, samples)
                    if date_key
                    else [[candidate] for candidate in candidates]
                )
                for position, component in enumerate(components, start=1):
                    status = "AUTO" if date_key and len(component) > 1 else "REVIEW"
                    review = None if status == "AUTO" else "No unambiguous run partner."
                    episode_key = f"{date_key or 'UNPARSED'}:{position:02d}"
                    result = conn.execute(
                        text(
                            """
                            INSERT INTO pcr_episode (
                                derivation_id, episode_key, episode_date_key,
                                association_status, review_reason
                            ) VALUES (
                                :derivation_id, :episode_key, :date_key,
                                :status, :review
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
                    for run_id, run_class in component:
                        scores = [
                            _overlap(samples[run_id], samples[other_id])
                            for other_id, _ in component
                            if other_id != run_id
                        ]
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
                            {
                                "episode_id": episode_id,
                                "run_id": run_id,
                                "role": run_class,
                                "score": max(scores) if scores else None,
                                "status": status,
                            },
                        )
                    episode_count += 1
        return episode_count

    def _select_results(self, derivation_id: int) -> int:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT interpretation.interpretation_id,
                           classification.pcr_sample_id,
                           interpretation.canonical_target,
                           interpretation.call,
                           interpretation.pcr_run_id,
                           analysis.run_started,
                           analysis.run_class,
                           qc.is_valid
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
                      AND classification.well_role = 'REAL_SAMPLE'
                    """
                ),
                {"derivation_id": derivation_id},
            ).all()
            grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                grouped[(int(row[1]), str(row[2]))].append(
                    {
                        "id": int(row[0]),
                        "call": str(row[3]),
                        "run_id": int(row[4]),
                        "started": row[5],
                        "run_class": str(row[6]),
                        "valid": bool(row[7]),
                    }
                )
            payload = []
            for (sample_id, target), items in grouped.items():
                summary = _summarize_results(items, target=target)
                payload.append(
                    {
                        "derivation_id": derivation_id,
                        "sample_id": sample_id,
                        "target": target,
                        "preferred": summary["preferred"],
                        "count": len(items),
                        "relation": summary["relation"],
                        "concordance": summary["concordance"],
                        "review": summary["needs_review"],
                        "details": _json(
                            {
                                "interpretation_ids": [item["id"] for item in items],
                                "tie": summary["tie"],
                            }
                        ),
                    }
                )
            _execute_batches(
                conn,
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
                payload,
            )
        return len(payload)

    def _count_review_items(self, derivation_id: int) -> int:
        with self.engine.begin() as conn:
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


def _classify_sample_name(value: Any) -> tuple[str, str | None, str, str | None]:
    if value is None or not str(value).strip():
        return "NON_SAMPLE_REVIEW", None, "EMPTY", "Missing sample name."
    raw = str(value).strip()
    if raw.isdigit():
        return "REAL_SAMPLE", _sample_key(raw), "NUMERIC", None
    normalized = re.sub(r"\s+", "", raw.upper())
    if normalized in POSITIVE_CONTROLS:
        return "POSITIVE_CONTROL", normalized, normalized, None
    if normalized in NEGATIVE_CONTROLS:
        return "NEGATIVE_CONTROL", normalized, normalized, None
    return (
        "NON_SAMPLE_REVIEW",
        normalized,
        "UNKNOWN_TEXT",
        "Non-numeric sample name is not a known control.",
    )


def _is_real_sample(value: Any) -> bool:
    return value is not None and str(value).strip().isdigit()


def _sample_key(value: str) -> str:
    return value.lstrip("0") or "0"


def _run_class(targets: set[str]) -> tuple[str, str | None]:
    if "Screening" in targets:
        return "SCREENING", None
    if {"16S", "OXA48", "VIM"}.issubset(targets):
        return "PANEL_OXA48_VIM", None
    if {"16S", "KPC", "NDM"}.issubset(targets):
        return "PANEL_KPC_NDM", None
    if targets & CARBAPENEMASE_TARGETS:
        return "COMPENSATION_PARTIAL", None
    return "OTHER", f"Unrecognized target set: {sorted(targets)}"


def _parse_cfx_datetime(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value), "%m/%d/%Y %H:%M:%S UTC")
        return parsed.isoformat(sep=" ")
    except ValueError:
        return None


def _episode_date_key(run_name: str) -> str | None:
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


def _run_components(
    candidates: list[tuple[int, str]], samples: dict[int, set[int]]
) -> list[list[tuple[int, str]]]:
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
            linked = {
                other
                for other in remaining
                if _overlap(samples[current], samples[other]) >= 0.8
            }
            remaining -= linked
            pending.extend(linked)
            component_ids |= linked
        components.append(
            [(run_id, classes[run_id]) for run_id in sorted(component_ids)]
        )
    return components


def _overlap(left: set[int], right: set[int]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _summarize_results(
    items: list[dict[str, Any]], *, target: str
) -> dict[str, Any]:
    valid = [
        item
        for item in items
        if item["valid"] and item["call"] in {"POSITIVE", "NEGATIVE"}
    ]
    valid.sort(
        key=lambda item: (str(item["started"] or ""), item["run_id"]),
        reverse=True,
    )
    calls = {item["call"] for item in valid}
    classes = {item["run_class"] for item in items}
    if len(items) == 1:
        relation = "SINGLE"
    elif "COMPENSATION_PARTIAL" in classes:
        relation = "CORRECTION"
    elif target == "16S" and {
        "PANEL_OXA48_VIM",
        "PANEL_KPC_NDM",
    }.issubset(classes):
        relation = "PANEL_COMPLEMENT"
    else:
        relation = "HISTORICAL_REPEAT"
    tie = bool(
        len(valid) > 1
        and valid[0]["started"] is not None
        and valid[0]["started"] == valid[1]["started"]
    )
    return {
        "preferred": valid[0]["id"] if valid else None,
        "relation": relation,
        "concordance": (
            "NO_VALID_RESULT"
            if not valid
            else ("CONCORDANT" if len(calls) <= 1 else "DISCORDANT")
        ),
        "needs_review": not valid or len(calls) > 1 or tie,
        "tie": tie,
    }


def _interpretation_payload(
    derivation_id: int,
    cq_row: dict[str, Any],
    temperatures: list[float],
    derivatives: list[float],
    params: MeltingParameters,
) -> list[dict[str, Any]]:
    calls, peaks = interpret_curve(
        source_target=cq_row["target"],
        cq=cq_row["cq"],
        temperatures=temperatures,
        derivatives=derivatives,
        params=params,
    )
    return [_call_payload(derivation_id, cq_row, call, peaks) for call in calls]


def _call_payload(
    derivation_id: int,
    cq_row: dict[str, Any],
    call: MeltingCall,
    peaks: list[Peak],
) -> dict[str, Any]:
    return {
        "derivation_id": derivation_id,
        "run_id": cq_row["run_id"],
        "well_id": cq_row["well_id"],
        "result_id": cq_row["result_id"],
        "source_target": cq_row["target"],
        "target": call.target,
        "call": call.call,
        "cq": cq_row["cq"],
        "peak_temp": call.peak_temperature,
        "peak_rfu": call.peak_deriv_rfu,
        "peaks": _json([asdict(peak) for peak in peaks]),
        "metrics": _json({"peak_count": len(peaks)}),
        "error": call.error,
    }


def _clear_derived_model(conn: Connection) -> None:
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


def _start_derivation(
    conn: Connection, *, algorithm_version: str, params: MeltingParameters
) -> int:
    result = conn.execute(
        text(
            """
            INSERT INTO pcr_derivation_run (
                algorithm_version, rule_version, parameters_json, status
            ) VALUES (:algorithm_version, :rule_version, :parameters, 'RUNNING')
            """
        ),
        {
            "algorithm_version": algorithm_version,
            "rule_version": RULE_VERSION,
            "parameters": _json(params.as_dict()),
        },
    )
    return int(result.lastrowid)


def _finish_derivation(
    conn: Connection,
    derivation_id: int,
    *,
    status: str,
    details: dict[str, Any],
) -> None:
    conn.execute(
        text(
            """
            UPDATE pcr_derivation_run
            SET status=:status, details_json=:details, finished_at=CURRENT_TIMESTAMP
            WHERE derivation_id=:derivation_id
            """
        ),
        {"status": status, "details": _json(details), "derivation_id": derivation_id},
    )


def _execute_batches(
    conn: Connection, statement: str, payload: list[dict[str, Any]], size: int = 2000
) -> None:
    query = text(statement)
    for offset in range(0, len(payload), size):
        conn.execute(query, payload[offset : offset + size])


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _create_views(conn: Connection) -> None:
    views = {
        "v_pcr_current_result": """
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
            WHERE selection.derivation_id = (
                SELECT MAX(derivation_id) FROM pcr_derivation_run WHERE status='SUCCESS'
            )
        """,
        "v_pcr_result_history": """
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
            WHERE interpretation.derivation_id=(
                SELECT MAX(derivation_id) FROM pcr_derivation_run WHERE status='SUCCESS'
            )
        """,
        "v_pcr_run_qc": """
            SELECT run_qc.*, run.run_name
            FROM pcr_run_qc AS run_qc
            JOIN pcr_run AS run ON run.pcr_run_id=run_qc.pcr_run_id
            WHERE run_qc.derivation_id=(
                SELECT MAX(derivation_id) FROM pcr_derivation_run WHERE status='SUCCESS'
            )
        """,
        "v_pcr_episodes": """
            SELECT episode.episode_key, episode.association_status,
                   episode_run.pcr_run_id, episode_run.run_role,
                   episode_run.overlap_score
            FROM pcr_episode AS episode
            JOIN pcr_episode_run AS episode_run
              ON episode_run.episode_id=episode.episode_id
            WHERE episode.derivation_id=(
                SELECT MAX(derivation_id) FROM pcr_derivation_run WHERE status='SUCCESS'
            )
        """,
        "v_pcr_review_queue": """
            SELECT 'WELL' item_type, CAST(pcr_well_id AS TEXT) item_id,
                   review_reason reason
            FROM pcr_well_classification
            WHERE review_reason IS NOT NULL AND derivation_id=(
                SELECT MAX(derivation_id) FROM pcr_derivation_run WHERE status='SUCCESS'
            )
            UNION ALL
            SELECT 'RUN', CAST(pcr_run_id AS TEXT), review_reason
            FROM pcr_run_analysis
            WHERE review_reason IS NOT NULL AND derivation_id=(
                SELECT MAX(derivation_id) FROM pcr_derivation_run WHERE status='SUCCESS'
            )
            UNION ALL
            SELECT 'EPISODE', episode_key, review_reason FROM pcr_episode
            WHERE association_status='REVIEW' AND derivation_id=(
                SELECT MAX(derivation_id) FROM pcr_derivation_run WHERE status='SUCCESS'
            )
            UNION ALL
            SELECT 'TARGET_QC', CAST(pcr_run_id AS TEXT) || ':' || canonical_target,
                   details_json
            FROM pcr_target_qc
            WHERE qc_status='INVALID' AND derivation_id=(
                SELECT MAX(derivation_id) FROM pcr_derivation_run WHERE status='SUCCESS'
            )
            UNION ALL
            SELECT 'RESULT', CAST(pcr_sample_id AS TEXT) || ':' || canonical_target,
                   concordance
            FROM pcr_result_selection
            WHERE needs_review=1 AND derivation_id=(
                SELECT MAX(derivation_id) FROM pcr_derivation_run WHERE status='SUCCESS'
            )
        """,
    }
    for name, select_sql in views.items():
        conn.execute(text(f"DROP VIEW IF EXISTS {name}"))  # noqa: B608
        conn.execute(text(f"CREATE VIEW {name} AS {select_sql}"))  # noqa: B608
