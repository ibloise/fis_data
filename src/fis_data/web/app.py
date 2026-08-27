"""Read-only HTTP API and static frontend for PCR analytical review."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..db import get_engine, get_db_settings
from ..json_utils import loads_json

STATIC_DIR = Path(__file__).parent / "static"


def create_app(*, database_url: str | None = None) -> FastAPI:
    """Create the browser application with an injected or configured database."""

    # TODO(frontend): move these read queries into a dedicated PCR review repository.
    engine = get_engine(get_db_settings(database_url))
    app = FastAPI(title="FIS PCR review", version="0.1.0")
    app.state.engine = engine
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/pcr/runs")
    def runs(search: str = "", limit: int = Query(default=200, le=500)) -> list[dict[str, Any]]:
        pattern = f"%{search.lower().strip()}%"
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT run.pcr_run_id, run.run_name, run.run_started,
                           analysis.run_class, analysis.run_type
                    FROM pcr_run AS run
                    LEFT JOIN pcr_run_analysis AS analysis
                      ON analysis.pcr_run_id=run.pcr_run_id
                     AND analysis.derivation_id=(
                        SELECT derivation_id FROM pcr_derivation_run
                        WHERE status='SUCCESS' AND is_current=TRUE
                     )
                    WHERE lower(run.run_name) LIKE :pattern
                    ORDER BY run.run_started DESC, run.pcr_run_id DESC
                    LIMIT :limit
                    """
                ),
                {"pattern": pattern, "limit": limit},
            ).mappings()
            return [_row(row) for row in rows]

    @app.get("/api/pcr/samples/{sample_number}/runs")
    def sample_runs(sample_number: str) -> list[dict[str, Any]]:
        """Return current-derivation runs in which one exact sample was present."""

        normalized = sample_number.strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="sample_number is required")
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT DISTINCT sample.sample_key AS sample_number,
                           run.pcr_run_id, run.run_name, run.run_started,
                           analysis.run_class, analysis.run_type
                    FROM pcr_sample AS sample
                    JOIN pcr_well_classification AS classification
                      ON classification.pcr_sample_id=sample.pcr_sample_id
                    JOIN pcr_well AS well
                      ON well.pcr_well_id=classification.pcr_well_id
                    JOIN pcr_run AS run ON run.pcr_run_id=well.pcr_run_id
                    LEFT JOIN pcr_run_analysis AS analysis
                      ON analysis.pcr_run_id=run.pcr_run_id
                     AND analysis.derivation_id=(
                        SELECT derivation_id FROM pcr_derivation_run
                        WHERE status='SUCCESS' AND is_current=TRUE
                     )
                    WHERE sample.sample_key=:sample_number
                      AND classification.derivation_id=(
                        SELECT derivation_id FROM pcr_derivation_run
                        WHERE status='SUCCESS' AND is_current=TRUE
                      )
                    ORDER BY run.run_started, run.pcr_run_id
                    """
                ),
                {"sample_number": normalized},
            ).mappings()
            return [_row(row) for row in rows]

    @app.get("/api/pcr/samples/{sample_number}/results")
    def sample_results(sample_number: str) -> list[dict[str, Any]]:
        """Return Cq and melting-call evidence for one current PCR sample."""

        normalized = sample_number.strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="sample_number is required")
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT run.pcr_run_id, run.run_name, run.run_started, well.well,
                           interpretation.source_target, interpretation.canonical_target,
                           interpretation.call, interpretation.cq,
                           interpretation.peak_temperature, interpretation.peak_deriv_rfu,
                           interpretation.peaks_json, interpretation.error,
                           qc.is_valid AS target_qc_valid,
                           selection.run_type AS promoted_run_type,
                           (selection.preferred_interpretation_id IS NOT NULL) AS is_promoted
                    FROM pcr_sample AS sample
                    JOIN pcr_well_classification AS classification
                      ON classification.pcr_sample_id=sample.pcr_sample_id
                    JOIN pcr_interpretation AS interpretation
                      ON interpretation.derivation_id=classification.derivation_id
                     AND interpretation.pcr_well_id=classification.pcr_well_id
                    JOIN pcr_well AS well ON well.pcr_well_id=interpretation.pcr_well_id
                    JOIN pcr_run AS run ON run.pcr_run_id=interpretation.pcr_run_id
                    LEFT JOIN pcr_target_qc AS qc
                      ON qc.derivation_id=interpretation.derivation_id
                     AND qc.pcr_run_id=interpretation.pcr_run_id
                     AND qc.canonical_target=interpretation.canonical_target
                    LEFT JOIN pcr_result_selection AS selection
                      ON selection.derivation_id=interpretation.derivation_id
                     AND selection.pcr_sample_id=classification.pcr_sample_id
                     AND selection.canonical_target=interpretation.canonical_target
                     AND selection.preferred_interpretation_id=interpretation.interpretation_id
                    WHERE sample.sample_key=:sample_number
                      AND classification.derivation_id=(
                        SELECT derivation_id FROM pcr_derivation_run
                        WHERE status='SUCCESS' AND is_current=TRUE
                      )
                    ORDER BY run.run_started, run.pcr_run_id, well.well,
                             interpretation.canonical_target
                    """
                ),
                {"sample_number": normalized},
            ).mappings()
            return [
                {**_row(row), "peaks": loads_json(row["peaks_json"])}
                for row in rows
            ]

    @app.get("/api/pcr/runs/{run_id}/filters")
    def curve_filters(run_id: int) -> dict[str, list[str]]:
        _ensure_run(engine, run_id)
        with engine.connect() as conn:
            samples = conn.execute(
                text(
                    """
                    SELECT DISTINCT sample_name FROM pcr_well
                    WHERE pcr_run_id=:run_id AND sample_name IS NOT NULL
                    ORDER BY sample_name
                    """
                ),
                {"run_id": run_id},
            ).scalars()
            targets = conn.execute(
                text(
                    """
                    SELECT DISTINCT target FROM pcr_cq_result
                    WHERE pcr_run_id=:run_id ORDER BY target
                    """
                ),
                {"run_id": run_id},
            ).scalars()
        return {"samples": [str(value) for value in samples], "targets": [str(value) for value in targets]}

    @app.get("/api/pcr/runs/{run_id}/curves")
    def curves(
        run_id: int,
        kind: str = Query(pattern="^(amplification|melting)$"),
        sample: str | None = None,
        target: str | None = None,
    ) -> dict[str, Any]:
        _ensure_run(engine, run_id)
        measurement_kind = (
            "amplification_rfu" if kind == "amplification" else "melt_curve_derivative"
        )
        where = ["measurement.pcr_run_id=:run_id", "measurement.measurement_kind=:kind"]
        params: dict[str, Any] = {"run_id": run_id, "kind": measurement_kind}
        if sample:
            where.append("well.sample_name=:sample")
            params["sample"] = sample
        if target:
            where.append("result.target=:target")
            params["target"] = target
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT well.pcr_well_id, well.well, well.sample_name,
                           COALESCE(result.target, 'Unassigned') AS target,
                           result.cq, measurement.axis_value, measurement.rfu
                    FROM pcr_rfu_measurement AS measurement
                    JOIN pcr_well AS well ON well.pcr_well_id=measurement.pcr_well_id
                    LEFT JOIN pcr_cq_result AS result
                      ON result.pcr_well_id=well.pcr_well_id
                     AND result.pcr_run_id=measurement.pcr_run_id
                    WHERE {' AND '.join(where)}
                    ORDER BY target, well.well, measurement.axis_value
                    """
                ),
                params,
            ).mappings()
            series = _curve_series(rows, group_by_sample=sample is None)
        return {"run_id": run_id, "kind": kind, "series": series}

    @app.get("/api/pcr/qc")
    def quality_control(run_id: int | None = None) -> list[dict[str, Any]]:
        where = [
            "qc.derivation_id=(SELECT derivation_id FROM pcr_derivation_run WHERE status='SUCCESS' AND is_current=TRUE)"
        ]
        params: dict[str, Any] = {}
        if run_id is not None:
            where.append("qc.pcr_run_id=:run_id")
            params["run_id"] = run_id
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT qc.pcr_run_id, run.run_name, qc.canonical_target,
                           qc.qc_status, qc.is_valid, qc.positive_controls,
                           qc.negative_controls, qc.warnings_json, qc.details_json
                    FROM pcr_target_qc AS qc
                    JOIN pcr_run AS run ON run.pcr_run_id=qc.pcr_run_id
                    WHERE {' AND '.join(where)}
                    ORDER BY run.run_started DESC, run.run_name, qc.canonical_target
                    """
                ),
                params,
            ).mappings()
            return [
                {
                    **_row(row),
                    "warnings": loads_json(row["warnings_json"]),
                    "details": loads_json(row["details_json"]),
                }
                for row in rows
            ]

    @app.get("/api/pcr/qc/{run_id}/{target}/evidence")
    def quality_evidence(run_id: int, target: str) -> list[dict[str, Any]]:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT * FROM v_pcr_target_qc_evidence
                    WHERE pcr_run_id=:run_id AND canonical_target=:target
                    ORDER BY well_role, well
                    """
                ),
                {"run_id": run_id, "target": target},
            ).mappings()
            return [_row(row) for row in rows]

    @app.get("/api/pcr/review-queue")
    def review_queue() -> list[dict[str, Any]]:
        """Return analytical items requiring review or carrying an invalid state."""

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT item_type, item_id, reason, NULL::text AS details_json
                    FROM v_pcr_review_queue
                    UNION ALL
                    SELECT 'SAMPLE_RESULT' AS item_type, sample.sample_key AS item_id,
                           COALESCE(result.warning_code, result.pcr_category) AS reason,
                           result.details_json
                    FROM pcr_sample_result AS result
                    JOIN pcr_sample AS sample ON sample.pcr_sample_id=result.pcr_sample_id
                    WHERE result.derivation_id=(
                        SELECT derivation_id FROM pcr_derivation_run
                        WHERE status='SUCCESS' AND is_current=TRUE
                    )
                      AND (result.warning_code IS NOT NULL OR result.pcr_category='PCR_UNCLASSIFIED')
                    ORDER BY item_type, item_id
                    """
                )
            ).mappings()
            return [_row(row) for row in rows]

    return app


def _ensure_run(engine: Engine, run_id: int) -> None:
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pcr_run WHERE pcr_run_id=:run_id"), {"run_id": run_id}
        ).scalar()
    if exists is None:
        raise HTTPException(status_code=404, detail="PCR run not found")


def _curve_series(
    rows: Any, *, group_by_sample: bool
) -> list[dict[str, Any]]:
    """Return raw points grouped for the active curve review scope.

    The run view has one series per sample-target pair. With a sample selected,
    target identifies the series. This function deliberately does no numeric
    aggregation: every returned point comes directly from a measurement row.
    """

    grouped: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        target = str(row["target"])
        sample = str(row["sample_name"] or "No sample")
        key = (sample, target) if group_by_sample else (target,)
        series = grouped.setdefault(
            key,
            {
                "well_id": int(row["pcr_well_id"]),
                "well": row["well"],
                "sample": row["sample_name"],
                "target": target,
                "cq": row["cq"],
                "points": [],
            },
        )
        series["points"].append([float(row["axis_value"]), float(row["rfu"])])
    return list(grouped.values())


def _row(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}
