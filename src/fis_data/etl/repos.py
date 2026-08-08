"""Repository helpers for raw ETL tables."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import bindparam, func, insert, select, update
from sqlalchemy.engine import Engine

from fis_data.schema import (
    ctl_excel_parse_file,
    ctl_excel_parse_sheet,
    ctl_file_registry,
    raw_excel_rows,
    raw_text_lines,
)

from .types import (
    ExcelFileMetadata,
    ExcelParseStatus,
    HeaderSet,
    ParsedExcelRowUpdate,
    ParsedRowUpdate,
    RawExcelRow,
    RawTextLineRow,
)


class RawTextLinesRepository:
    """Database access layer for ``raw_text_lines`` operations."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_file_headers(self, *, file_id: int) -> HeaderSet:
        """Fetch header rows for a file."""

        query = (
            select(raw_text_lines.c.raw_line)
            .where(
                raw_text_lines.c.file_id == file_id,
                raw_text_lines.c.is_header.is_(True),
            )
            .order_by(raw_text_lines.c.line_no.asc())
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query).scalars().all()

        if not rows:
            raise ValueError(f"No headers found for file_id={file_id}")

        return HeaderSet(lines=tuple(str(row) for row in rows[:2]))

    def list_pending_file_ids(self, *, entity_name: str) -> list[int]:
        """Return file IDs with pending raw text rows for an entity."""

        query = (
            select(raw_text_lines.c.file_id)
            .where(
                raw_text_lines.c.entity_name == entity_name,
                raw_text_lines.c.is_header.is_(False),
                raw_text_lines.c.parse_status == "RAW_ONLY",
            )
            .distinct()
            .order_by(raw_text_lines.c.file_id.asc())
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query).scalars().all()

        return [int(row) for row in rows]

    def list_file_ids(self, *, entity_name: str) -> list[int]:
        """Return all file IDs with non-header raw text rows for an entity."""

        query = (
            select(raw_text_lines.c.file_id)
            .where(
                raw_text_lines.c.entity_name == entity_name,
                raw_text_lines.c.is_header.is_(False),
            )
            .distinct()
            .order_by(raw_text_lines.c.file_id.asc())
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query).scalars().all()

        return [int(row) for row in rows]

    def count_parse_candidates(self, *, entity_name: str, file_id: int) -> int:
        """Count pending non-header rows for a file."""

        query = select(func.count()).select_from(raw_text_lines).where(
            raw_text_lines.c.entity_name == entity_name,
            raw_text_lines.c.file_id == file_id,
            raw_text_lines.c.is_header.is_(False),
            raw_text_lines.c.parse_status == "RAW_ONLY",
        )
        with self.engine.begin() as conn:
            return int(conn.execute(query).scalar_one())

    def fetch_batch_after_line_no(
        self,
        *,
        entity_name: str,
        file_id: int,
        limit: int,
        last_line_no: int,
        only_pending: bool = True,
    ) -> list[RawTextLineRow]:
        """Fetch a keyset-paginated batch of pending non-header rows."""

        conditions = [
            raw_text_lines.c.entity_name == entity_name,
            raw_text_lines.c.file_id == file_id,
            raw_text_lines.c.is_header.is_(False),
            raw_text_lines.c.line_no > last_line_no,
        ]
        if only_pending:
            conditions.append(raw_text_lines.c.parse_status == "RAW_ONLY")
        query = (
            select(
                raw_text_lines.c.line_id,
                raw_text_lines.c.line_no,
                raw_text_lines.c.raw_line,
            )
            .where(*conditions)
            .order_by(raw_text_lines.c.line_no.asc())
            .limit(limit)
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query).all()

        return [
            RawTextLineRow(
                line_id=int(row[0]),
                line_no=int(row[1]),
                raw_line=str(row[2]),
            )
            for row in rows
        ]

    def apply_updates(self, updates: Iterable[ParsedRowUpdate]) -> None:
        """Persist parse results back to ``raw_text_lines``."""

        updates_list = list(updates)
        if not updates_list:
            return

        query = (
            update(raw_text_lines)
            .where(raw_text_lines.c.line_id == bindparam("b_line_id"))
            .values(
                payload_json=bindparam("payload_json"),
                parse_status=bindparam("parse_status"),
                parse_error=bindparam("parse_error"),
            )
        )
        payload = [
            {
                "b_line_id": update.line_id,
                "payload_json": update.payload_json,
                "parse_status": update.status.value,
                "parse_error": update.error,
            }
            for update in updates_list
        ]

        with self.engine.begin() as conn:
            conn.execute(query, payload)


class RawExcelRowsRepository:
    """Database access layer for ``raw_excel_rows`` parsing operations."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list_candidate_file_ids(
        self,
        *,
        entity_name: str,
        only_pending: bool,
        file_id: int | None = None,
        sheet_name: str | None = None,
    ) -> list[int]:
        """Return Excel file IDs with candidate rows for an entity."""

        conditions = [raw_excel_rows.c.entity_name == entity_name]
        if only_pending:
            conditions.append(raw_excel_rows.c.parse_status == "RAW_ONLY")
        if file_id is not None:
            conditions.append(raw_excel_rows.c.file_id == file_id)
        if sheet_name is not None:
            conditions.append(raw_excel_rows.c.sheet_name == sheet_name)
        query = (
            select(raw_excel_rows.c.file_id)
            .where(*conditions)
            .distinct()
            .order_by(raw_excel_rows.c.file_id.asc())
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query).scalars().all()

        return [int(row) for row in rows]

    def get_file_metadata(self, *, file_id: int) -> ExcelFileMetadata:
        """Fetch registered file metadata for an Excel workbook."""

        query = select(
            ctl_file_registry.c.file_id,
            ctl_file_registry.c.source_name,
            ctl_file_registry.c.storage_path,
            ctl_file_registry.c.file_format,
            ctl_file_registry.c.sha256,
        ).where(ctl_file_registry.c.file_id == file_id)
        with self.engine.begin() as conn:
            row = conn.execute(query).one_or_none()

        if row is None:
            raise ValueError(f"No registered file found for file_id={file_id}")

        return ExcelFileMetadata(
            file_id=int(row[0]),
            source_name=str(row[1]),
            storage_path=str(row[2]),
            file_format=str(row[3]),
            sha256=str(row[4]),
        )

    def list_sheet_names(
        self,
        *,
        entity_name: str,
        file_id: int,
        only_pending: bool,
        sheet_name: str | None = None,
    ) -> list[str]:
        """Return sheet names with candidate rows for a file."""

        conditions = [
            raw_excel_rows.c.entity_name == entity_name,
            raw_excel_rows.c.file_id == file_id,
        ]
        if only_pending:
            conditions.append(raw_excel_rows.c.parse_status == "RAW_ONLY")
        if sheet_name is not None:
            conditions.append(raw_excel_rows.c.sheet_name == sheet_name)
        query = (
            select(raw_excel_rows.c.sheet_name)
            .where(*conditions)
            .distinct()
            .order_by(raw_excel_rows.c.sheet_name.asc())
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query).scalars().all()

        return [str(row) for row in rows]

    def fetch_sheet_rows(
        self,
        *,
        entity_name: str,
        file_id: int,
        sheet_name: str,
        only_pending: bool,
    ) -> list[RawExcelRow]:
        """Fetch raw Excel rows for one sheet."""

        conditions = [
            raw_excel_rows.c.entity_name == entity_name,
            raw_excel_rows.c.file_id == file_id,
            raw_excel_rows.c.sheet_name == sheet_name,
        ]
        if only_pending:
            conditions.append(raw_excel_rows.c.parse_status == "RAW_ONLY")
        query = (
            select(
                raw_excel_rows.c.row_id,
                raw_excel_rows.c.sheet_name,
                raw_excel_rows.c.row_no,
                raw_excel_rows.c.raw_values_json,
            )
            .where(*conditions)
            .order_by(raw_excel_rows.c.row_no.asc())
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query).all()

        return [
            RawExcelRow(
                row_id=int(row[0]),
                sheet_name=str(row[1]),
                row_no=int(row[2]),
                values=list(json.loads(str(row[3]))),
            )
            for row in rows
        ]

    def apply_excel_updates(self, updates: Iterable[ParsedExcelRowUpdate]) -> None:
        """Persist parse results back to ``raw_excel_rows``."""

        updates_list = list(updates)
        if not updates_list:
            return

        query = (
            update(raw_excel_rows)
            .where(raw_excel_rows.c.row_id == bindparam("b_row_id"))
            .values(
                payload_json=bindparam("payload_json"),
                parse_status=bindparam("parse_status"),
                parse_error=bindparam("parse_error"),
            )
        )
        payload = [
            {
                "b_row_id": update.row_id,
                "payload_json": update.payload_json,
                "parse_status": update.status.value,
                "parse_error": update.error,
            }
            for update in updates_list
        ]

        with self.engine.begin() as conn:
            conn.execute(query, payload)

    def start_file_audit(
        self,
        *,
        run_id: int,
        file_id: int,
        entity_name: str,
        file_kind: str | None,
        parser_name: str,
        parser_version: str,
        status: ExcelParseStatus,
        details: dict[str, Any] | None = None,
    ) -> int:
        """Create an Excel file parse audit record."""

        query = insert(ctl_excel_parse_file).values(
            run_id=run_id,
            file_id=file_id,
            entity_name=entity_name,
            file_kind=file_kind,
            parser_name=parser_name,
            parser_version=parser_version,
            status=status.value,
            details_json=_json_dumps(details),
        ).returning(ctl_excel_parse_file.c.excel_file_parse_id)
        with self.engine.begin() as conn:
            result = conn.execute(query)
            return int(result.scalar_one())

    def finish_file_audit(
        self,
        *,
        excel_file_parse_id: int,
        status: ExcelParseStatus,
        sheets_seen: int,
        sheets_parsed: int,
        rows_seen: int,
        rows_parsed: int,
        rows_error: int,
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Finalize an Excel file parse audit record."""

        query = (
            update(ctl_excel_parse_file)
            .where(
                ctl_excel_parse_file.c.excel_file_parse_id == excel_file_parse_id
            )
            .values(
                status=status.value,
                sheets_seen=sheets_seen,
                sheets_parsed=sheets_parsed,
                rows_seen=rows_seen,
                rows_parsed=rows_parsed,
                rows_error=rows_error,
                error=error,
                details_json=_json_dumps(details),
                finished_at=func.current_timestamp(),
            )
        )
        with self.engine.begin() as conn:
            conn.execute(query)

    def insert_sheet_audit(
        self,
        *,
        excel_file_parse_id: int,
        run_id: int,
        file_id: int,
        entity_name: str,
        sheet_name: str,
        sheet_kind: str | None,
        header_row_no: int | None,
        status: ExcelParseStatus,
        rows_seen: int,
        rows_parsed: int,
        rows_skipped: int,
        rows_error: int,
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        """Create a completed Excel sheet parse audit record."""

        query = insert(ctl_excel_parse_sheet).values(
            excel_file_parse_id=excel_file_parse_id,
            run_id=run_id,
            file_id=file_id,
            entity_name=entity_name,
            sheet_name=sheet_name,
            sheet_kind=sheet_kind,
            header_row_no=header_row_no,
            status=status.value,
            rows_seen=rows_seen,
            rows_parsed=rows_parsed,
            rows_skipped=rows_skipped,
            rows_error=rows_error,
            error=error,
            details_json=_json_dumps(details),
            finished_at=func.current_timestamp(),
        ).returning(ctl_excel_parse_sheet.c.excel_sheet_parse_id)
        with self.engine.begin() as conn:
            result = conn.execute(query)
            return int(result.scalar_one())


def _json_dumps(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
