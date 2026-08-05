"""Persistence for the explicit analytical scope of PCR runs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine

from ...schema import pcr_run, pcr_run_scope


class PCRRunScopeRepository:
    """Assign auditable operational scope without removing source evidence."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def assign_before(
        self,
        *,
        before: datetime,
        reason: str,
        assigned_by: str = "POLICY",
    ) -> int:
        """Synchronize explicit scope for every dated run from one cutoff policy."""

        with self.engine.begin() as conn:
            runs = conn.execute(
                select(pcr_run.c.pcr_run_id, pcr_run.c.run_started).where(
                    pcr_run.c.run_started.is_not(None)
                )
            ).all()
            if not runs:
                return 0
            conn.execute(
                insert(pcr_run_scope)
                .values(
                    [
                        {
                            "pcr_run_id": run_id,
                            "analytical_scope": (
                                "EXPERIMENTAL" if started < before else "ROUTINE"
                            ),
                            "reason": (
                                f"{reason}:"
                                f"{'EXPERIMENTAL' if started < before else 'ROUTINE'}"
                            ),
                            "assigned_by": assigned_by,
                        }
                        for run_id, started in runs
                    ]
                )
                .on_conflict_do_update(
                    index_elements=["pcr_run_id"],
                    set_={
                        "analytical_scope": insert(pcr_run_scope).excluded.analytical_scope,
                        "reason": insert(pcr_run_scope).excluded.reason,
                        "assigned_by": assigned_by,
                    },
                    where=pcr_run_scope.c.assigned_by != "MANUAL",
                )
            )
        return len(runs)

    def assign_run(
        self,
        *,
        pcr_run_id: int,
        analytical_scope: str,
        reason: str,
        assigned_by: str = "MANUAL",
    ) -> None:
        """Assign or override the scope of a single known PCR run."""

        with self.engine.begin() as conn:
            exists = conn.execute(
                select(pcr_run.c.pcr_run_id).where(pcr_run.c.pcr_run_id == pcr_run_id)
            ).scalar_one_or_none()
            if exists is None:
                raise ValueError(f"PCR run not found: {pcr_run_id}")
            conn.execute(
                insert(pcr_run_scope)
                .values(
                    pcr_run_id=pcr_run_id,
                    analytical_scope=analytical_scope,
                    reason=reason,
                    assigned_by=assigned_by,
                )
                .on_conflict_do_update(
                    index_elements=["pcr_run_id"],
                    set_={
                        "analytical_scope": analytical_scope,
                        "reason": reason,
                        "assigned_by": assigned_by,
                    },
                )
            )
