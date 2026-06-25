"""Repository helpers for raw ETL tables."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .types import HeaderSet, ParsedRowUpdate, RawTextLineRow


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

        status_filter = "AND parse_status = 'RAW_ONLY'" if only_pending else ""
        query = text(
            f"""
            SELECT line_id, line_no, raw_line
            FROM raw_text_lines
            WHERE entity_name = :entity_name
              AND file_id = :file_id
              AND is_header = 0
              {status_filter}
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
