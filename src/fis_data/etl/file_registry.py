"""File registry helpers for raw ingestion."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine


def sha256_file(path: Path) -> str:
    """Compute the SHA256 digest for a file."""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_file(
    engine: Engine,
    *,
    source_id: int,
    source_name: str,
    storage_path: str,
    file_format: str,
) -> int:
    """Register a source file and return its file identifier."""

    path = Path(storage_path)
    checksum = sha256_file(path)

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT file_id
                FROM ctl_file_registry
                WHERE source_name = :source_name
                  AND sha256 = :sha256
                """
            ),
            {"source_name": source_name, "sha256": checksum},
        ).scalar_one_or_none()

        if existing is not None:
            conn.execute(
                text(
                    """
                    UPDATE ctl_file_registry
                    SET source_id = :source_id,
                        storage_path = :storage_path,
                        file_format = :file_format,
                        status = 'REGISTERED'
                    WHERE file_id = :file_id
                    """
                ),
                {
                    "source_id": source_id,
                    "storage_path": str(path),
                    "file_format": file_format,
                    "file_id": existing,
                },
            )
            return int(existing)

        result = conn.execute(
            text(
                """
                INSERT INTO ctl_file_registry (
                    source_id, source_name, storage_path, file_format, sha256, status
                )
                VALUES (
                    :source_id, :source_name, :storage_path,
                    :file_format, :sha256, 'REGISTERED'
                )
                """
            ),
            {
                "source_id": source_id,
                "source_name": source_name,
                "storage_path": str(path),
                "file_format": file_format,
                "sha256": checksum,
            },
        )
        return int(result.lastrowid)
