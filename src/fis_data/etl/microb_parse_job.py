"""Batch parser job for Microb raw text rows."""

from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

from .contracts import JobContext, JobResult
from .parse.microb_parser import MicrobExportLine, MicrobParser
from .repos import RawTextLinesRepository
from .types import ParsedRowUpdate, ParseStats, ParseStatus

log = logging.getLogger(__name__)


class MicrobParseJob:
    """Coordinate parsing of Microb raw text rows into payload JSON."""

    job_name = "microb-parse"

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.repo = RawTextLinesRepository(engine)

    def _build_parser(self, *, file_id: int) -> tuple[MicrobParser, bool]:
        headers = self.repo.get_file_headers(file_id=file_id)

        fixed_header = MicrobExportLine(headers[0])
        atb_line = headers[1] if len(headers) >= 2 else None
        atb_spec = MicrobExportLine(atb_line) if atb_line else None

        return (
            MicrobParser(fixed_header=fixed_header, atb_spec=atb_spec),
            atb_spec is not None,
        )

    def list_pending_file_ids(self, *, entity_name: str) -> list[int]:
        """Return pending file IDs for the Microb entity."""

        return self.repo.list_pending_file_ids(entity_name=entity_name)

    def list_file_ids(self, *, entity_name: str) -> list[int]:
        """Return all file IDs for the Microb entity."""

        return self.repo.list_file_ids(entity_name=entity_name)

    def _parse_batches(
        self,
        *,
        entity_name: str,
        file_id: int,
        batch_size: int,
        parser: MicrobParser,
        reprocess: bool,
    ) -> tuple[int, int, int]:
        total = ok = err = 0
        last_line_no = 0

        while True:
            batch = self.repo.fetch_batch_after_line_no(
                entity_name=entity_name,
                file_id=file_id,
                limit=batch_size,
                last_line_no=last_line_no,
                only_pending=not reprocess,
            )
            if not batch:
                break

            updates: list[ParsedRowUpdate] = []
            for row in batch:
                result = parser.parse_line(row.raw_line)
                total += 1
                if result.status == ParseStatus.PARSED_OK:
                    ok += 1
                else:
                    err += 1

                updates.append(
                    ParsedRowUpdate(
                        line_id=row.line_id,
                        payload_json=result.payload_json(),
                        status=result.status,
                        error=result.error,
                    )
                )

            self.repo.apply_updates(updates)
            last_line_no = batch[-1].line_no

        return total, ok, err

    def run(
        self,
        *,
        ctx: JobContext,
        batch_size: int = 2000,
        reprocess: bool = False,
    ) -> JobResult:
        """Parse pending raw rows for one Microb file."""

        try:
            parser, has_atb_headers = self._build_parser(file_id=ctx.file_id)
            total, ok, err = self._parse_batches(
                entity_name=ctx.entity_name,
                file_id=ctx.file_id,
                batch_size=batch_size,
                parser=parser,
                reprocess=reprocess,
            )
            stats = ParseStats(
                file_id=ctx.file_id,
                entity_name=ctx.entity_name,
                parsed_total=total,
                parsed_ok=ok,
                parsed_error=err,
                fixed_len=parser.fixed_len,
                atb_len=parser.atb_len,
                has_atb_headers=has_atb_headers,
            )
            return JobResult.success(job=self.job_name, ctx=ctx, payload=stats)
        except Exception as exc:
            log.exception("Microb parse failed for file_id=%s", ctx.file_id)
            return JobResult.failure(job=self.job_name, ctx=ctx, error=str(exc))
