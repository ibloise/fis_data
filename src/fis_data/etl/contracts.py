"""Small job contract helpers for ETL tasks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any


@dataclass(frozen=True)
class JobContext:
    """Context passed into an ETL job."""

    entity_name: str
    file_id: int
    run_id: int | None = None
    source_id: int | None = None


@dataclass(frozen=True)
class JobResult:
    """Standard result returned by ETL jobs."""

    job: str
    entity_name: str
    file_id: int
    ok: bool
    payload: Any
    metrics: dict[str, Any]
    error: str | None = None

    @staticmethod
    def _to_metrics(payload: Any) -> dict[str, Any]:
        if payload is None:
            return {}
        if is_dataclass(payload):
            return asdict(payload)
        if isinstance(payload, dict):
            return payload
        return {"value": payload}

    @classmethod
    def success(cls, *, job: str, ctx: JobContext, payload: Any) -> JobResult:
        """Build a successful job result."""

        return cls(
            job=job,
            entity_name=ctx.entity_name,
            file_id=ctx.file_id,
            ok=True,
            payload=payload,
            metrics=cls._to_metrics(payload),
        )

    @classmethod
    def failure(cls, *, job: str, ctx: JobContext, error: str) -> JobResult:
        """Build a failed job result."""

        return cls(
            job=job,
            entity_name=ctx.entity_name,
            file_id=ctx.file_id,
            ok=False,
            payload=None,
            metrics={},
            error=error,
        )
