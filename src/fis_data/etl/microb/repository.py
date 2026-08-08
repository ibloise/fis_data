"""SQLAlchemy Core persistence boundary for Microb staging."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from ...schema import raw_text_lines, stg_microb_antibiogram, stg_microb_technique


class MicrobStagingRepository:
    """Read parsed Microb payloads and replace their file-level staging rows."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def parsed_rows(self, *, entity_name: str, file_id: int) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    select(
                        raw_text_lines.c.line_id,
                        raw_text_lines.c.source_id,
                        raw_text_lines.c.run_id,
                        raw_text_lines.c.file_id,
                        raw_text_lines.c.line_no,
                        raw_text_lines.c.payload_json,
                    )
                    .where(
                        func.lower(raw_text_lines.c.entity_name)
                        == func.lower(entity_name),
                        raw_text_lines.c.file_id == file_id,
                        raw_text_lines.c.parse_status == "PARSED_OK",
                        raw_text_lines.c.payload_json.is_not(None),
                    )
                    .order_by(raw_text_lines.c.line_no)
                ).mappings()
            ]

    def parsed_file_ids(self, *, entity_name: str) -> list[int]:
        with self.engine.connect() as conn:
            return [
                int(file_id)
                for file_id in conn.execute(
                    select(raw_text_lines.c.file_id)
                    .distinct()
                    .where(
                        func.lower(raw_text_lines.c.entity_name)
                        == func.lower(entity_name),
                        raw_text_lines.c.parse_status == "PARSED_OK",
                    )
                    .order_by(raw_text_lines.c.file_id)
                ).scalars()
            ]

    def replace_file(
        self,
        *,
        file_id: int,
        techniques: Iterable[dict[str, Any]],
        antibiograms_by_line: dict[int, list[dict[str, Any]]],
    ) -> tuple[int, int]:
        """Atomically replace staging evidence for a file and its antibiograms."""

        technique_rows = list(techniques)
        with self.engine.begin() as conn:
            conn.execute(delete(stg_microb_antibiogram).where(stg_microb_antibiogram.c.file_id == file_id))
            conn.execute(delete(stg_microb_technique).where(stg_microb_technique.c.file_id == file_id))
            if not technique_rows:
                return 0, 0
            conn.execute(insert(stg_microb_technique), technique_rows)
            ids = {
                int(line_id): int(technique_id)
                for line_id, technique_id in conn.execute(
                    select(
                        stg_microb_technique.c.line_id,
                        stg_microb_technique.c.stg_microb_technique_id,
                    ).where(stg_microb_technique.c.file_id == file_id)
                )
            }
            antibiogram_rows = [
                {
                    **row,
                    "stg_microb_technique_id": ids[line_id],
                }
                for line_id, rows in antibiograms_by_line.items()
                for row in rows
                if line_id in ids
            ]
            if antibiogram_rows:
                conn.execute(insert(stg_microb_antibiogram), antibiogram_rows)
        return len(technique_rows), len(antibiogram_rows)
