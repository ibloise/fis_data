"""Control-table helpers for sources and ETL runs."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import text
from sqlalchemy.engine import Engine


class RunStatus(StrEnum):
    """Lifecycle states for an ETL run."""

    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    WARNING = "WARNING"


def ensure_source(engine: Engine, *, source_name: str, source_type: str) -> int:
    """Ensure a source exists and return its source identifier."""

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT source_id FROM ctl_source WHERE source_name = :name"),
            {"name": source_name},
        ).scalar_one_or_none()
        if existing is not None:
            conn.execute(
                text(
                    """
                    UPDATE ctl_source
                    SET source_type = :source_type
                    WHERE source_id = :source_id
                    """
                ),
                {"source_type": source_type, "source_id": existing},
            )
            return int(existing)

        result = conn.execute(
            text(
                """
                INSERT INTO ctl_source (source_name, source_type)
                VALUES (:name, :source_type)
                """
            ),
            {"name": source_name, "source_type": source_type},
        )
        return int(result.lastrowid)


def start_run(engine: Engine, *, pipeline_name: str) -> int:
    """Start an ETL run and return its run identifier."""

    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO ctl_etl_run (pipeline_name, status)
                VALUES (:pipeline_name, :status)
                """
            ),
            {"pipeline_name": pipeline_name, "status": RunStatus.STARTED.value},
        )
        return int(result.lastrowid)


def finish_run(
    engine: Engine,
    *,
    run_id: int,
    status: RunStatus,
    details_json: str | None = None,
) -> None:
    """Mark an ETL run as finished."""

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE ctl_etl_run
                SET finished_at = CURRENT_TIMESTAMP,
                    status = :status,
                    details_json = :details_json
                WHERE run_id = :run_id
                """
            ),
            {
                "status": status.value,
                "details_json": details_json,
                "run_id": run_id,
            },
        )
