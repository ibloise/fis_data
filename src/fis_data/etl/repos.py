"""Repository helpers for raw ETL tables."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

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

        query = text(
            """
            SELECT raw_line
            FROM raw_text_lines
            WHERE file_id = :file_id
              AND is_header = 1
            ORDER BY line_no ASC
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query, {"file_id": file_id}).fetchall()

        if not rows:
            raise ValueError(f"No headers found for file_id={file_id}")

        return HeaderSet(lines=tuple(str(row[0]) for row in rows[:2]))

    def list_pending_file_ids(self, *, entity_name: str) -> list[int]:
        """Return file IDs with pending raw text rows for an entity."""

        query = text(
            """
            SELECT DISTINCT file_id
            FROM raw_text_lines
            WHERE entity_name = :entity_name
              AND is_header = 0
              AND parse_status = 'RAW_ONLY'
            ORDER BY file_id ASC
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query, {"entity_name": entity_name}).fetchall()

        return [int(row[0]) for row in rows]

    def list_file_ids(self, *, entity_name: str) -> list[int]:
        """Return all file IDs with non-header raw text rows for an entity."""

        query = text(
            """
            SELECT DISTINCT file_id
            FROM raw_text_lines
            WHERE entity_name = :entity_name
              AND is_header = 0
            ORDER BY file_id ASC
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query, {"entity_name": entity_name}).fetchall()

        return [int(row[0]) for row in rows]

    def count_parse_candidates(self, *, entity_name: str, file_id: int) -> int:
        """Count pending non-header rows for a file."""

        query = text(
            """
            SELECT COUNT(*)
            FROM raw_text_lines
            WHERE entity_name = :entity_name
              AND file_id = :file_id
              AND is_header = 0
              AND parse_status = 'RAW_ONLY'
            """
        )
        with self.engine.begin() as conn:
            return int(
                conn.execute(
                    query,
                    {"entity_name": entity_name, "file_id": file_id},
                ).scalar_one()
            )

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

        query = text(
            """
            SELECT line_id, line_no, raw_line
            FROM raw_text_lines
            WHERE entity_name = :entity_name
              AND file_id = :file_id
              AND is_header = 0
              AND (:only_pending = 0 OR parse_status = 'RAW_ONLY')
              AND line_no > :last_line_no
            ORDER BY line_no ASC
            LIMIT :limit
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(
                query,
                {
                    "entity_name": entity_name,
                    "file_id": file_id,
                    "limit": limit,
                    "last_line_no": last_line_no,
                    "only_pending": int(only_pending),
                },
            ).fetchall()

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

        query = text(
            """
            UPDATE raw_text_lines
            SET payload_json = :payload_json,
                parse_status = :parse_status,
                parse_error = :parse_error
            WHERE line_id = :line_id
            """
        )
        payload = [
            {
                "line_id": update.line_id,
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

        query = text(
            """
            SELECT DISTINCT file_id
            FROM raw_excel_rows
            WHERE entity_name = :entity_name
              AND (:only_pending = 0 OR parse_status = 'RAW_ONLY')
              AND (:file_id IS NULL OR file_id = :file_id)
              AND (:sheet_name IS NULL OR sheet_name = :sheet_name)
            ORDER BY file_id ASC
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(
                query,
                {
                    "entity_name": entity_name,
                    "only_pending": int(only_pending),
                    "file_id": file_id,
                    "sheet_name": sheet_name,
                },
            ).fetchall()

        return [int(row[0]) for row in rows]

    def get_file_metadata(self, *, file_id: int) -> ExcelFileMetadata:
        """Fetch registered file metadata for an Excel workbook."""

        query = text(
            """
            SELECT file_id, source_name, storage_path, file_format, sha256
            FROM ctl_file_registry
            WHERE file_id = :file_id
            """
        )
        with self.engine.begin() as conn:
            row = conn.execute(query, {"file_id": file_id}).one_or_none()

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

        query = text(
            """
            SELECT DISTINCT sheet_name
            FROM raw_excel_rows
            WHERE entity_name = :entity_name
              AND file_id = :file_id
              AND (:only_pending = 0 OR parse_status = 'RAW_ONLY')
              AND (:sheet_name IS NULL OR sheet_name = :sheet_name)
            ORDER BY sheet_name ASC
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(
                query,
                {
                    "entity_name": entity_name,
                    "file_id": file_id,
                    "only_pending": int(only_pending),
                    "sheet_name": sheet_name,
                },
            ).fetchall()

        return [str(row[0]) for row in rows]

    def fetch_sheet_rows(
        self,
        *,
        entity_name: str,
        file_id: int,
        sheet_name: str,
        only_pending: bool,
    ) -> list[RawExcelRow]:
        """Fetch raw Excel rows for one sheet."""

        query = text(
            """
            SELECT row_id, sheet_name, row_no, raw_values_json
            FROM raw_excel_rows
            WHERE entity_name = :entity_name
              AND file_id = :file_id
              AND sheet_name = :sheet_name
              AND (:only_pending = 0 OR parse_status = 'RAW_ONLY')
            ORDER BY row_no ASC
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(
                query,
                {
                    "entity_name": entity_name,
                    "file_id": file_id,
                    "sheet_name": sheet_name,
                    "only_pending": int(only_pending),
                },
            ).fetchall()

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

        query = text(
            """
            UPDATE raw_excel_rows
            SET payload_json = :payload_json,
                parse_status = :parse_status,
                parse_error = :parse_error
            WHERE row_id = :row_id
            """
        )
        payload = [
            {
                "row_id": update.row_id,
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

        query = text(
            """
            INSERT INTO ctl_excel_parse_file (
                run_id, file_id, entity_name, file_kind, parser_name,
                parser_version, status, details_json
            )
            VALUES (
                :run_id, :file_id, :entity_name, :file_kind, :parser_name,
                :parser_version, :status, :details_json
            )
            """
        )
        with self.engine.begin() as conn:
            result = conn.execute(
                query,
                {
                    "run_id": run_id,
                    "file_id": file_id,
                    "entity_name": entity_name,
                    "file_kind": file_kind,
                    "parser_name": parser_name,
                    "parser_version": parser_version,
                    "status": status.value,
                    "details_json": _json_dumps(details),
                },
            )
            return int(result.lastrowid)

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

        query = text(
            """
            UPDATE ctl_excel_parse_file
            SET status = :status,
                sheets_seen = :sheets_seen,
                sheets_parsed = :sheets_parsed,
                rows_seen = :rows_seen,
                rows_parsed = :rows_parsed,
                rows_error = :rows_error,
                error = :error,
                details_json = :details_json,
                finished_at = CURRENT_TIMESTAMP
            WHERE excel_file_parse_id = :excel_file_parse_id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                query,
                {
                    "excel_file_parse_id": excel_file_parse_id,
                    "status": status.value,
                    "sheets_seen": sheets_seen,
                    "sheets_parsed": sheets_parsed,
                    "rows_seen": rows_seen,
                    "rows_parsed": rows_parsed,
                    "rows_error": rows_error,
                    "error": error,
                    "details_json": _json_dumps(details),
                },
            )

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

        query = text(
            """
            INSERT INTO ctl_excel_parse_sheet (
                excel_file_parse_id, run_id, file_id, entity_name, sheet_name,
                sheet_kind, header_row_no, status, rows_seen, rows_parsed,
                rows_skipped, rows_error, error, details_json, finished_at
            )
            VALUES (
                :excel_file_parse_id, :run_id, :file_id, :entity_name, :sheet_name,
                :sheet_kind, :header_row_no, :status, :rows_seen, :rows_parsed,
                :rows_skipped, :rows_error, :error, :details_json, CURRENT_TIMESTAMP
            )
            """
        )
        with self.engine.begin() as conn:
            result = conn.execute(
                query,
                {
                    "excel_file_parse_id": excel_file_parse_id,
                    "run_id": run_id,
                    "file_id": file_id,
                    "entity_name": entity_name,
                    "sheet_name": sheet_name,
                    "sheet_kind": sheet_kind,
                    "header_row_no": header_row_no,
                    "status": status.value,
                    "rows_seen": rows_seen,
                    "rows_parsed": rows_parsed,
                    "rows_skipped": rows_skipped,
                    "rows_error": rows_error,
                    "error": error,
                    "details_json": _json_dumps(details),
                },
            )
            return int(result.lastrowid)


def _json_dumps(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
