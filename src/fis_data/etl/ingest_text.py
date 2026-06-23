"""Raw ingestion for text files."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine


def decode_bytes(data: bytes, encodings: tuple[str, ...] | None = None) -> str:
    """Decode bytes with fallbacks common in healthcare exports."""

    candidates = encodings or ("utf-8-sig", "utf-8", "cp1252", "latin-1")
    for encoding in candidates:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def line_sha256(value: str) -> str:
    """Compute a stable SHA256 digest for a text line."""

    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def is_probable_header(line: str) -> bool:
    """Heuristically detect headers in delimited text exports."""

    stripped = line.strip()
    if not stripped or stripped[0].isdigit():
        return False

    separators = {
        ",": stripped.count(","),
        ";": stripped.count(";"),
        "|": stripped.count("|"),
        "\t": stripped.count("\t"),
    }
    return max(separators.values()) >= 2


def ingest_text_file(
    engine: Engine,
    *,
    source_id: int,
    entity_name: str,
    run_id: int,
    file_id: int,
    path: str | Path,
    batch_size: int = 2000,
) -> dict[str, int]:
    """Ingest a text file into ``raw_text_lines`` preserving line order."""

    input_path = Path(path)
    raw_bytes = input_path.read_bytes()
    if not raw_bytes:
        raise ValueError(f"Input text file is empty: {input_path}")

    content = decode_bytes(raw_bytes)
    lines = content.splitlines()
    if not lines:
        raise ValueError(f"Input text file has no ingestable lines: {input_path}")

    rows: list[dict[str, object]] = []
    inserted = 0

    insert_stmt = text(
        """
        INSERT OR IGNORE INTO raw_text_lines (
            source_id, run_id, file_id, entity_name, line_no,
            raw_line, raw_line_sha256, is_header, parse_status
        )
        VALUES (
            :source_id, :run_id, :file_id, :entity_name, :line_no,
            :raw_line, :raw_line_sha256, :is_header, 'RAW_ONLY'
        )
        """
    )

    with engine.begin() as conn:
        for line_no, raw_line in enumerate(lines, start=1):
            rows.append(
                {
                    "source_id": source_id,
                    "run_id": run_id,
                    "file_id": file_id,
                    "entity_name": entity_name,
                    "line_no": line_no,
                    "raw_line": raw_line,
                    "raw_line_sha256": line_sha256(raw_line),
                    "is_header": is_probable_header(raw_line),
                }
            )

            if len(rows) >= batch_size:
                result = conn.execute(insert_stmt, rows)
                inserted += result.rowcount or 0
                rows.clear()

        if rows:
            result = conn.execute(insert_stmt, rows)
            inserted += result.rowcount or 0

    return {"inserted_lines": inserted, "total_lines": len(lines)}
