"""Control-table helpers for sources and ETL runs."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

from ..schema import ctl_etl_run, ctl_source


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
            select(ctl_source.c.source_id).where(ctl_source.c.source_name == source_name)
        ).scalar_one_or_none()
        if existing is not None:
            conn.execute(
                update(ctl_source)
                .where(ctl_source.c.source_id == existing)
                .values(source_type=source_type)
            )
            return int(existing)

        result = conn.execute(
            insert(ctl_source).values(source_name=source_name, source_type=source_type).returning(ctl_source.c.source_id)
        )
        return int(result.scalar_one())


def start_run(engine: Engine, *, pipeline_name: str) -> int:
    """Start an ETL run and return its run identifier."""

    with engine.begin() as conn:
        result = conn.execute(
            insert(ctl_etl_run).values(
                pipeline_name=pipeline_name, status=RunStatus.STARTED.value
            ).returning(ctl_etl_run.c.run_id)
        )
        return int(result.scalar_one())


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
            update(ctl_etl_run)
            .where(ctl_etl_run.c.run_id == run_id)
            .values(
                finished_at=func.current_timestamp(),
                status=status.value,
                details_json=details_json,
            )
        )
