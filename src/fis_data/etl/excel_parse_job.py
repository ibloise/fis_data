"""Batch parser job for raw Excel rows."""

from __future__ import annotations

import logging
from dataclasses import asdict

from sqlalchemy.engine import Engine

from .contracts import JobContext, JobResult
from .excel_profiles import EXCEL_ENTITY_PARSERS, ExcelEntityParser, SheetProfile
from .repos import RawExcelRowsRepository
from .types import (
    ExcelParseStats,
    ExcelParseStatus,
    ExcelSheetParseStats,
    ParsedExcelRowUpdate,
    RawExcelRow,
)

log = logging.getLogger(__name__)


class ExcelParseJob:
    """Coordinate parsing of raw Excel rows into payload JSON."""

    job_name = "excel-parse"

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.repo = RawExcelRowsRepository(engine)

    def list_candidate_file_ids(
        self,
        *,
        entity_name: str,
        file_id: int | None,
        sheet_name: str | None,
        reprocess: bool,
    ) -> list[int]:
        """Return workbook IDs that have rows eligible for Excel parsing."""

        return self.repo.list_candidate_file_ids(
            entity_name=entity_name,
            only_pending=not reprocess,
            file_id=file_id,
            sheet_name=sheet_name,
        )

    def run(
        self,
        *,
        ctx: JobContext,
        run_id: int,
        sheet_name: str | None = None,
        batch_size: int = 1000,
        reprocess: bool = False,
    ) -> JobResult:
        """Parse or classify raw Excel rows for one workbook."""

        del batch_size
        try:
            stats = self._run_workbook(
                ctx=ctx,
                run_id=run_id,
                sheet_name=sheet_name,
                reprocess=reprocess,
            )
            return JobResult.success(job=self.job_name, ctx=ctx, payload=stats)
        except Exception as exc:
            log.exception("Excel parse failed for file_id=%s", ctx.file_id)
            return JobResult.failure(job=self.job_name, ctx=ctx, error=str(exc))

    def _run_workbook(
        self,
        *,
        ctx: JobContext,
        run_id: int,
        sheet_name: str | None,
        reprocess: bool,
    ) -> ExcelParseStats:
        metadata = self.repo.get_file_metadata(file_id=ctx.file_id)
        parser = EXCEL_ENTITY_PARSERS.get(ctx.entity_name)

        if parser is None:
            return self._mark_unsupported_entity(
                ctx=ctx,
                run_id=run_id,
                storage_path=metadata.storage_path,
                sheet_name=sheet_name,
                reprocess=reprocess,
            )

        file_profile = parser.match_file(metadata.storage_path)
        if file_profile is None:
            return self._mark_file_profile_skipped(
                ctx=ctx,
                run_id=run_id,
                parser=parser,
                storage_path=metadata.storage_path,
                sheet_name=sheet_name,
                reprocess=reprocess,
            )

        file_audit_id = self.repo.start_file_audit(
            run_id=run_id,
            file_id=ctx.file_id,
            entity_name=ctx.entity_name,
            file_kind=file_profile.kind,
            parser_name=parser.parser_name,
            parser_version=parser.parser_version,
            status=ExcelParseStatus.RAW_ONLY,
            details={"storage_path": metadata.storage_path},
        )
        sheet_names = self.repo.list_sheet_names(
            entity_name=ctx.entity_name,
            file_id=ctx.file_id,
            sheet_name=sheet_name,
            only_pending=not reprocess,
        )

        sheet_stats: list[ExcelSheetParseStats] = []
        for candidate_sheet in sheet_names:
            profile = file_profile.match_sheet(candidate_sheet)
            if profile is None:
                sheet_stats.append(
                    self._mark_sheet_skipped(
                        ctx=ctx,
                        run_id=run_id,
                        excel_file_parse_id=file_audit_id,
                        sheet_name=candidate_sheet,
                        sheet_kind=None,
                        status=ExcelParseStatus.SKIPPED_SHEET_PROFILE,
                        reprocess=reprocess,
                        error="No sheet profile matched.",
                    )
                )
                continue

            sheet_stats.append(
                self._mark_sheet_profile_stub(
                    ctx=ctx,
                    run_id=run_id,
                    excel_file_parse_id=file_audit_id,
                    sheet_name=candidate_sheet,
                    profile=profile,
                    reprocess=reprocess,
                )
            )

        stats = _file_stats(
            file_id=ctx.file_id,
            entity_name=ctx.entity_name,
            file_kind=file_profile.kind,
            parser_name=parser.parser_name,
            parser_version=parser.parser_version,
            status=_aggregate_sheet_status(sheet_stats),
            sheet_stats=sheet_stats,
        )
        self.repo.finish_file_audit(
            excel_file_parse_id=file_audit_id,
            status=stats.status,
            sheets_seen=stats.sheets_seen,
            sheets_parsed=stats.sheets_parsed,
            rows_seen=stats.rows_seen,
            rows_parsed=stats.rows_parsed,
            rows_error=stats.rows_error,
            details={"sheets": [asdict(sheet) for sheet in sheet_stats]},
        )
        return stats

    def _mark_unsupported_entity(
        self,
        *,
        ctx: JobContext,
        run_id: int,
        storage_path: str,
        sheet_name: str | None,
        reprocess: bool,
    ) -> ExcelParseStats:
        file_audit_id = self.repo.start_file_audit(
            run_id=run_id,
            file_id=ctx.file_id,
            entity_name=ctx.entity_name,
            file_kind=None,
            parser_name="unsupported-excel",
            parser_version="0.1",
            status=ExcelParseStatus.UNSUPPORTED_ENTITY,
            details={"storage_path": storage_path},
        )
        sheet_names = self.repo.list_sheet_names(
            entity_name=ctx.entity_name,
            file_id=ctx.file_id,
            sheet_name=sheet_name,
            only_pending=not reprocess,
        )
        sheet_stats = [
            self._mark_sheet_skipped(
                ctx=ctx,
                run_id=run_id,
                excel_file_parse_id=file_audit_id,
                sheet_name=candidate_sheet,
                sheet_kind=None,
                status=ExcelParseStatus.UNSUPPORTED_ENTITY,
                reprocess=reprocess,
                error=f"No Excel parser registered for entity '{ctx.entity_name}'.",
            )
            for candidate_sheet in sheet_names
        ]
        stats = _file_stats(
            file_id=ctx.file_id,
            entity_name=ctx.entity_name,
            file_kind=None,
            parser_name="unsupported-excel",
            parser_version="0.1",
            status=ExcelParseStatus.UNSUPPORTED_ENTITY,
            sheet_stats=sheet_stats,
            error=f"No Excel parser registered for entity '{ctx.entity_name}'.",
        )
        self.repo.finish_file_audit(
            excel_file_parse_id=file_audit_id,
            status=stats.status,
            sheets_seen=stats.sheets_seen,
            sheets_parsed=stats.sheets_parsed,
            rows_seen=stats.rows_seen,
            rows_parsed=stats.rows_parsed,
            rows_error=stats.rows_error,
            error=stats.error,
            details={"sheets": [asdict(sheet) for sheet in sheet_stats]},
        )
        return stats

    def _mark_file_profile_skipped(
        self,
        *,
        ctx: JobContext,
        run_id: int,
        parser: ExcelEntityParser,
        storage_path: str,
        sheet_name: str | None,
        reprocess: bool,
    ) -> ExcelParseStats:
        file_audit_id = self.repo.start_file_audit(
            run_id=run_id,
            file_id=ctx.file_id,
            entity_name=ctx.entity_name,
            file_kind=None,
            parser_name=parser.parser_name,
            parser_version=parser.parser_version,
            status=ExcelParseStatus.SKIPPED_FILE_PROFILE,
            details={"storage_path": storage_path},
        )
        sheet_names = self.repo.list_sheet_names(
            entity_name=ctx.entity_name,
            file_id=ctx.file_id,
            sheet_name=sheet_name,
            only_pending=not reprocess,
        )
        sheet_stats = [
            self._mark_sheet_skipped(
                ctx=ctx,
                run_id=run_id,
                excel_file_parse_id=file_audit_id,
                sheet_name=candidate_sheet,
                sheet_kind=None,
                status=ExcelParseStatus.SKIPPED_FILE_PROFILE,
                reprocess=reprocess,
                error="No file profile matched.",
            )
            for candidate_sheet in sheet_names
        ]
        stats = _file_stats(
            file_id=ctx.file_id,
            entity_name=ctx.entity_name,
            file_kind=None,
            parser_name=parser.parser_name,
            parser_version=parser.parser_version,
            status=ExcelParseStatus.SKIPPED_FILE_PROFILE,
            sheet_stats=sheet_stats,
            error="No file profile matched.",
        )
        self.repo.finish_file_audit(
            excel_file_parse_id=file_audit_id,
            status=stats.status,
            sheets_seen=stats.sheets_seen,
            sheets_parsed=stats.sheets_parsed,
            rows_seen=stats.rows_seen,
            rows_parsed=stats.rows_parsed,
            rows_error=stats.rows_error,
            error=stats.error,
            details={"sheets": [asdict(sheet) for sheet in sheet_stats]},
        )
        return stats

    def _mark_sheet_profile_stub(
        self,
        *,
        ctx: JobContext,
        run_id: int,
        excel_file_parse_id: int,
        sheet_name: str,
        profile: SheetProfile,
        reprocess: bool,
    ) -> ExcelSheetParseStats:
        rows = self.repo.fetch_sheet_rows(
            entity_name=ctx.entity_name,
            file_id=ctx.file_id,
            sheet_name=sheet_name,
            only_pending=not reprocess,
        )
        status = ExcelParseStatus(profile.stub_status)
        header_row_no = _detect_header_row_no(rows, profile.header_scan_rows)
        self.repo.apply_excel_updates(
            ParsedExcelRowUpdate(
                row_id=row.row_id,
                payload_json=None,
                status=status,
                error="Sheet profile is registered but no row parser is implemented.",
            )
            for row in rows
        )
        stat = ExcelSheetParseStats(
            sheet_name=sheet_name,
            sheet_kind=profile.kind,
            header_row_no=header_row_no,
            status=status,
            rows_seen=len(rows),
            rows_parsed=0,
            rows_skipped=len(rows),
            rows_error=0,
            error=None,
        )
        self.repo.insert_sheet_audit(
            excel_file_parse_id=excel_file_parse_id,
            run_id=run_id,
            file_id=ctx.file_id,
            entity_name=ctx.entity_name,
            sheet_name=sheet_name,
            sheet_kind=profile.kind,
            header_row_no=header_row_no,
            status=status,
            rows_seen=stat.rows_seen,
            rows_parsed=stat.rows_parsed,
            rows_skipped=stat.rows_skipped,
            rows_error=stat.rows_error,
            details={"stub": True},
        )
        return stat

    def _mark_sheet_skipped(
        self,
        *,
        ctx: JobContext,
        run_id: int,
        excel_file_parse_id: int,
        sheet_name: str,
        sheet_kind: str | None,
        status: ExcelParseStatus,
        reprocess: bool,
        error: str,
    ) -> ExcelSheetParseStats:
        rows = self.repo.fetch_sheet_rows(
            entity_name=ctx.entity_name,
            file_id=ctx.file_id,
            sheet_name=sheet_name,
            only_pending=not reprocess,
        )
        self.repo.apply_excel_updates(
            ParsedExcelRowUpdate(
                row_id=row.row_id,
                payload_json=None,
                status=status,
                error=error,
            )
            for row in rows
        )
        stat = ExcelSheetParseStats(
            sheet_name=sheet_name,
            sheet_kind=sheet_kind,
            header_row_no=_detect_header_row_no(rows),
            status=status,
            rows_seen=len(rows),
            rows_parsed=0,
            rows_skipped=len(rows),
            rows_error=0,
            error=error,
        )
        self.repo.insert_sheet_audit(
            excel_file_parse_id=excel_file_parse_id,
            run_id=run_id,
            file_id=ctx.file_id,
            entity_name=ctx.entity_name,
            sheet_name=sheet_name,
            sheet_kind=sheet_kind,
            header_row_no=stat.header_row_no,
            status=status,
            rows_seen=stat.rows_seen,
            rows_parsed=stat.rows_parsed,
            rows_skipped=stat.rows_skipped,
            rows_error=stat.rows_error,
            error=error,
        )
        return stat


def _detect_header_row_no(
    rows: list[RawExcelRow],
    header_scan_rows: int = 20,
) -> int | None:
    for row in rows[:header_scan_rows]:
        if any(value is not None and str(value).strip() for value in row.values):
            return row.row_no
    return None


def _file_stats(
    *,
    file_id: int,
    entity_name: str,
    file_kind: str | None,
    parser_name: str,
    parser_version: str,
    status: ExcelParseStatus,
    sheet_stats: list[ExcelSheetParseStats],
    error: str | None = None,
) -> ExcelParseStats:
    return ExcelParseStats(
        file_id=file_id,
        entity_name=entity_name,
        file_kind=file_kind,
        parser_name=parser_name,
        parser_version=parser_version,
        status=status,
        sheets_seen=len(sheet_stats),
        sheets_parsed=sum(
            1 for sheet in sheet_stats if sheet.status == ExcelParseStatus.PARSED_OK
        ),
        rows_seen=sum(sheet.rows_seen for sheet in sheet_stats),
        rows_parsed=sum(sheet.rows_parsed for sheet in sheet_stats),
        rows_error=sum(sheet.rows_error for sheet in sheet_stats),
        error=error,
    )


def _aggregate_sheet_status(
    sheet_stats: list[ExcelSheetParseStats],
) -> ExcelParseStatus:
    if not sheet_stats:
        return ExcelParseStatus.SKIPPED_SHEET_PROFILE
    statuses = {sheet.status for sheet in sheet_stats}
    if len(statuses) == 1:
        return sheet_stats[0].status
    if ExcelParseStatus.PARSE_ERROR in statuses:
        return ExcelParseStatus.PARSE_ERROR
    if ExcelParseStatus.PARSED_OK in statuses:
        return ExcelParseStatus.PARSED_OK
    return sheet_stats[0].status
