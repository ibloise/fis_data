"""File registry helpers for raw ingestion."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from ..schema import ctl_file_registry


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
            select(ctl_file_registry.c.file_id).where(
                ctl_file_registry.c.source_name == source_name,
                ctl_file_registry.c.sha256 == checksum,
            )
        ).scalar_one_or_none()

        if existing is not None:
            conn.execute(
                update(ctl_file_registry)
                .where(ctl_file_registry.c.file_id == existing)
                .values(
                    source_id=source_id,
                    storage_path=str(path),
                    file_format=file_format,
                    status="REGISTERED",
                )
            )
            return int(existing)

        result = conn.execute(
            insert(ctl_file_registry).values(
                source_id=source_id,
                source_name=source_name,
                storage_path=str(path),
                file_format=file_format,
                sha256=checksum,
                status="REGISTERED",
            ).returning(ctl_file_registry.c.file_id)
        )
        return int(result.scalar_one())
