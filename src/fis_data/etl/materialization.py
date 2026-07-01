"""Generic contracts and orchestration for domain materialization."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .contracts import JobContext, JobResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaterializationRow:
    """One parsed raw row available to an entity materializer."""

    row_id: int
    source_id: int
    file_id: int
    sheet_name: str
    row_no: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class MaterializationStats:
    """Summary returned for one materialized file."""

    file_id: int
    entity_name: str
    status: str
    rows_seen: int
    rows_loaded: int
    rows_error: int
    targets_written: int
    warnings: int = 0
    source_errors: int = 0
    error: str | None = None


class EntityMaterializer(Protocol):
    """Adapter contract for materializing one entity into domain tables."""

    entity_name: str
    materializer_name: str
    materializer_version: str

    def materialize_file(
        self,
        *,
        engine: Engine,
        ctx: JobContext,
        rows: list[MaterializationRow],
        context_rows: list[MaterializationRow],
        reprocess: bool,
        batch_size: int,
    ) -> MaterializationStats: ...


class MaterializationRepository:
    """Read parsed payloads and current materialization state."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list_candidate_file_ids(
        self,
        *,
        entity_name: str,
        materializer_name: str,
        materializer_version: str,
        file_id: int | None,
        reprocess: bool,
    ) -> list[int]:
        query = text(
            """
            SELECT DISTINCT raw.file_id
            FROM raw_excel_rows AS raw
            LEFT JOIN ctl_materialization_item AS item
              ON item.source_table = 'raw_excel_rows'
             AND item.source_row_id = raw.row_id
             AND item.materializer_name = :materializer_name
            WHERE lower(raw.entity_name) = lower(:entity_name)
              AND raw.parse_status = 'PARSED_OK'
              AND (:file_id IS NULL OR raw.file_id = :file_id)
              AND (
                    :reprocess = 1
                    OR item.materialization_item_id IS NULL
                    OR item.status != 'LOADED'
                    OR item.materializer_version != :materializer_version
                  )
            ORDER BY raw.file_id
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(
                query,
                {
                    "entity_name": entity_name,
                    "materializer_name": materializer_name,
                    "materializer_version": materializer_version,
                    "file_id": file_id,
                    "reprocess": int(reprocess),
                },
            ).fetchall()
        return [int(row[0]) for row in rows]

    def fetch_file_rows(
        self,
        *,
        entity_name: str,
        file_id: int,
        materializer_name: str,
        materializer_version: str,
        pending_only: bool,
    ) -> list[MaterializationRow]:
        query = text(
            """
            SELECT raw.row_id, raw.source_id, raw.file_id, raw.sheet_name,
                   raw.row_no, raw.payload_json
            FROM raw_excel_rows AS raw
            LEFT JOIN ctl_materialization_item AS item
              ON item.source_table = 'raw_excel_rows'
             AND item.source_row_id = raw.row_id
             AND item.materializer_name = :materializer_name
            WHERE lower(raw.entity_name) = lower(:entity_name)
              AND raw.file_id = :file_id
              AND raw.parse_status = 'PARSED_OK'
              AND (
                    :pending_only = 0
                    OR item.materialization_item_id IS NULL
                    OR item.status != 'LOADED'
                    OR item.materializer_version != :materializer_version
                  )
            ORDER BY raw.sheet_name, raw.row_no
            """
        )
        with self.engine.begin() as conn:
            records = conn.execute(
                query,
                {
                    "entity_name": entity_name,
                    "file_id": file_id,
                    "materializer_name": materializer_name,
                    "materializer_version": materializer_version,
                    "pending_only": int(pending_only),
                },
            ).fetchall()
        return [
            MaterializationRow(
                row_id=int(row[0]),
                source_id=int(row[1]),
                file_id=int(row[2]),
                sheet_name=str(row[3]),
                row_no=int(row[4]),
                payload=json.loads(str(row[5])),
            )
            for row in records
        ]

    def count_source_errors(self, *, entity_name: str, file_id: int) -> int:
        """Count parser errors retained in raw data for one file."""

        with self.engine.begin() as conn:
            return int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM raw_excel_rows
                        WHERE lower(entity_name) = lower(:entity_name)
                          AND file_id = :file_id
                          AND parse_status = 'PARSE_ERROR'
                        """
                    ),
                    {"entity_name": entity_name, "file_id": file_id},
                ).scalar_one()
            )

    def has_outdated_items(
        self,
        *,
        file_id: int,
        materializer_name: str,
        materializer_version: str,
    ) -> bool:
        """Return whether a file has rows loaded by an older adapter version."""

        with self.engine.begin() as conn:
            return bool(
                conn.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM ctl_materialization_item
                            WHERE file_id = :file_id
                              AND materializer_name = :materializer_name
                              AND status = 'LOADED'
                              AND materializer_version != :materializer_version
                        )
                        """
                    ),
                    {
                        "file_id": file_id,
                        "materializer_name": materializer_name,
                        "materializer_version": materializer_version,
                    },
                ).scalar_one()
            )


class MaterializationJob:
    """Coordinate generic selection and one entity-specific materializer."""

    job_name = "domain-materialization"

    def __init__(self, engine: Engine, materializer: EntityMaterializer) -> None:
        self.engine = engine
        self.materializer = materializer
        self.repo = MaterializationRepository(engine)

    def list_candidate_file_ids(
        self, *, entity_name: str, file_id: int | None, reprocess: bool
    ) -> list[int]:
        return self.repo.list_candidate_file_ids(
            entity_name=entity_name,
            materializer_name=self.materializer.materializer_name,
            materializer_version=self.materializer.materializer_version,
            file_id=file_id,
            reprocess=reprocess,
        )

    def run(
        self,
        *,
        ctx: JobContext,
        reprocess: bool = False,
        batch_size: int = 1000,
    ) -> JobResult:
        try:
            effective_reprocess = reprocess or self.repo.has_outdated_items(
                file_id=ctx.file_id,
                materializer_name=self.materializer.materializer_name,
                materializer_version=self.materializer.materializer_version,
            )
            context_rows = self.repo.fetch_file_rows(
                entity_name=ctx.entity_name,
                file_id=ctx.file_id,
                materializer_name=self.materializer.materializer_name,
                materializer_version=self.materializer.materializer_version,
                pending_only=False,
            )
            rows = (
                context_rows
                if effective_reprocess
                else self.repo.fetch_file_rows(
                    entity_name=ctx.entity_name,
                    file_id=ctx.file_id,
                    materializer_name=self.materializer.materializer_name,
                    materializer_version=self.materializer.materializer_version,
                    pending_only=True,
                )
            )
            stats = self.materializer.materialize_file(
                engine=self.engine,
                ctx=ctx,
                rows=rows,
                context_rows=context_rows,
                reprocess=effective_reprocess,
                batch_size=batch_size,
            )
            source_errors = self.repo.count_source_errors(
                entity_name=ctx.entity_name, file_id=ctx.file_id
            )
            if source_errors:
                stats = replace(
                    stats,
                    status=(
                        "LOADED_WITH_ERRORS"
                        if stats.status == "LOADED"
                        else stats.status
                    ),
                    source_errors=source_errors,
                )
            return JobResult.success(job=self.job_name, ctx=ctx, payload=stats)
        except Exception as exc:
            log.exception("Materialization failed for file_id=%s", ctx.file_id)
            return JobResult.failure(job=self.job_name, ctx=ctx, error=str(exc))
