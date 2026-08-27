"""Safe source-file supersession for already materialized PCR Excel files."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, text, update
from sqlalchemy.engine import Engine

from ...schema import (
    ctl_file_registry,
    ctl_file_supersession,
    pcr_run_file,
    raw_excel_rows,
)
from .analytics.repository import _clear_derived_model
from .materializer import PCR_MATERIALIZER
from .repository import PCRMaterializationRepository


@dataclass(frozen=True)
class PCRFileReplacement:
    """One audited old-to-new PCR source-file substitution."""

    superseded_file_id: int
    replacement_file_id: int


def supersede_pcr_files(
    engine: Engine, *, replacements: tuple[PCRFileReplacement, ...], reason: str | None
) -> tuple[PCRFileReplacement, ...]:
    """Retire old PCR file facts after replacement files are materialized.

    Raw rows are retained for audit but marked ``SUPERSEDED``.  The complete
    PCR derived model is cleared in the same transaction because it references
    the old Cq/RFU facts; callers must run ``derive-pcr --rebuild`` afterwards.
    """

    if not replacements:
        raise ValueError("Provide at least one old-to-new file replacement.")
    old_ids = [item.superseded_file_id for item in replacements]
    new_ids = [item.replacement_file_id for item in replacements]
    if len(set(old_ids)) != len(old_ids) or len(set(new_ids)) != len(new_ids):
        raise ValueError("Each old and replacement file ID must be unique.")
    if set(old_ids) & set(new_ids):
        raise ValueError("A replacement file cannot also be superseded.")

    with engine.begin() as conn:
        known_ids = set(
            int(value)
            for value in conn.execute(
                select(ctl_file_registry.c.file_id).where(
                    ctl_file_registry.c.file_id.in_(old_ids + new_ids)
                )
            ).scalars()
        )
        missing = set(old_ids + new_ids) - known_ids
        if missing:
            raise ValueError(f"Unknown file IDs: {sorted(missing)}")
        materialized_new_ids = set(
            int(value)
            for value in conn.execute(
                select(pcr_run_file.c.file_id).where(pcr_run_file.c.file_id.in_(new_ids))
            ).scalars()
        )
        pending = set(new_ids) - materialized_new_ids
        if pending:
            raise ValueError(
                "Replacement files must be parsed and materialized first: "
                f"{sorted(pending)}"
            )

        _clear_derived_model(conn)
        repository = PCRMaterializationRepository(conn)
        affected_run_ids: set[int] = set()
        for old_file_id in old_ids:
            affected_run_ids.update(
                repository.clear_file(
                    file_id=old_file_id,
                    materializer_name=PCR_MATERIALIZER.materializer_name,
                )
            )
        for run_id in affected_run_ids:
            repository.delete_orphan_wells(run_id)
            if conn.execute(
                select(pcr_run_file.c.pcr_run_file_id).where(
                    pcr_run_file.c.pcr_run_id == run_id
                ).limit(1)
            ).scalar_one_or_none() is not None:
                repository.refresh_attribute_flags(run_id)
                repository.refresh_run_metadata(run_id)
                repository.refresh_well_metadata(run_id)
        repository.delete_empty_runs(affected_run_ids)

        for item in replacements:
            conn.execute(
                update(raw_excel_rows)
                .where(raw_excel_rows.c.file_id == item.superseded_file_id)
                .values(parse_status="SUPERSEDED")
            )
            conn.execute(
                update(ctl_file_registry)
                .where(ctl_file_registry.c.file_id == item.superseded_file_id)
                .values(status="SUPERSEDED")
            )
            conn.execute(
                text(
                    """
                    INSERT INTO ctl_file_supersession (
                        superseded_file_id, replacement_file_id, reason
                    ) VALUES (:old_file_id, :new_file_id, :reason)
                    ON CONFLICT (superseded_file_id) DO UPDATE
                    SET replacement_file_id=EXCLUDED.replacement_file_id,
                        reason=EXCLUDED.reason,
                        superseded_at=CURRENT_TIMESTAMP
                    """
                ),
                {
                    "old_file_id": item.superseded_file_id,
                    "new_file_id": item.replacement_file_id,
                    "reason": reason,
                },
            )
    return replacements
