"""Versioned Microb reference-catalog ingestion."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine

from .ingest_text import decode_bytes
from ..schema import ref_microb_catalog, ref_microb_catalog_entry


@dataclass(frozen=True)
class CatalogLoadStats:
    catalog_name: str
    entries_loaded: int
    unchanged: bool


class MicrobCatalogRepository:
    """Persist immutable catalog versions and mark one version current."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def load(self, *, path: Path) -> CatalogLoadStats:
        catalog_name = path.stem.casefold()
        raw = path.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        entries = _parse_entries(decode_bytes(raw))
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(ref_microb_catalog.c.ref_microb_catalog_id).where(
                    ref_microb_catalog.c.catalog_name == catalog_name,
                    ref_microb_catalog.c.sha256 == checksum,
                )
            ).scalar_one_or_none()
            if existing is not None:
                conn.execute(
                    update(ref_microb_catalog)
                    .where(ref_microb_catalog.c.catalog_name == catalog_name)
                    .values(is_current=False)
                )
                conn.execute(
                    update(ref_microb_catalog)
                    .where(ref_microb_catalog.c.ref_microb_catalog_id == existing)
                    .values(is_current=True)
                )
                return CatalogLoadStats(catalog_name, len(entries), True)
            conn.execute(
                update(ref_microb_catalog)
                .where(ref_microb_catalog.c.catalog_name == catalog_name)
                .values(is_current=False)
            )
            catalog_id = int(
                conn.execute(
                    insert(ref_microb_catalog).values(
                        catalog_name=catalog_name,
                        source_path=str(path),
                        sha256=checksum,
                        entry_count=len(entries),
                        is_current=True,
                    )
                ).lastrowid
            )
            if entries:
                conn.execute(
                    insert(ref_microb_catalog_entry),
                    [
                        {
                            "ref_microb_catalog_id": catalog_id,
                            "code": entry[0],
                            "description": entry[1],
                            "aliases_json": json.dumps(entry[2], ensure_ascii=False),
                            "raw_fields_json": json.dumps(entry[3], ensure_ascii=False),
                        }
                        for entry in entries
                    ],
                )
        return CatalogLoadStats(catalog_name, len(entries), False)

    def load_directory(self, directory: Path) -> list[CatalogLoadStats]:
        return [self.load(path=path) for path in sorted(directory.glob("*.txt"))]


def _parse_entries(content: str) -> list[tuple[str, str | None, list[str], list[str]]]:
    entries = []
    for line in content.splitlines():
        fields = [value.strip() for value in line.split("|")]
        while fields and not fields[-1]:
            fields.pop()
        if not fields or not fields[0]:
            continue
        entries.append((fields[0], fields[1] if len(fields) > 1 else None, fields[2:], fields))
    return entries
