"""Versioned Microb reference-catalog ingestion."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from ..ingest_text import decode_bytes
from ...schema import ref_microb_catalog, ref_microb_catalog_entry


CATALOG_NAME_BY_STEM = {
    "procedencias": "procedencias_extra",
    "servicios": "servicios_extra",
    "hospitales": "hospitales",
}
EXCEL_LAYOUTS = {
    "procedencias_extra": (3, 2),
    "servicios_extra": (0, 2),
    "hospitales": (0, 2),
}


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
        catalog_name = _catalog_name(path)
        raw = path.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        entries = _parse_catalog(path, catalog_name, raw)
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
                    ).returning(ref_microb_catalog.c.ref_microb_catalog_id)
                ).scalar_one()
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
        paths = sorted(
            (
                *directory.glob("*.txt"),
                *directory.glob("*.xls"),
            ),
            key=lambda path: path.name.casefold(),
        )
        return [self.load(path=path) for path in paths]


def _catalog_name(path: Path) -> str:
    stem = path.stem.casefold()
    return CATALOG_NAME_BY_STEM.get(stem, stem) if path.suffix.casefold() == ".xls" else stem


def _parse_catalog(
    path: Path, catalog_name: str, raw: bytes
) -> list[tuple[str, str | None, list[str], list[str]]]:
    if path.suffix.casefold() == ".xls":
        return _parse_excel_entries(path, catalog_name)
    return _parse_entries(decode_bytes(raw))


def _parse_excel_entries(
    path: Path, catalog_name: str
) -> list[tuple[str, str | None, list[str], list[str]]]:
    """Read the supplied legacy Excel catalogues without losing empty columns."""

    try:
        import xlrd
    except ImportError as exc:  # pragma: no cover - dependency declared in pyproject
        raise RuntimeError("El soporte de catálogos .xls requiere xlrd") from exc

    code_index, description_index = EXCEL_LAYOUTS.get(catalog_name, (0, 1))
    sheet = xlrd.open_workbook(path).sheet_by_index(0)
    entries = []
    for row_index in range(1, sheet.nrows):
        fields = [_excel_cell(sheet.cell_value(row_index, column)) for column in range(sheet.ncols)]
        code = fields[code_index].strip() if len(fields) > code_index else ""
        if not code:
            continue
        description = fields[description_index].strip() if len(fields) > description_index else ""
        entries.append((code, description or None, fields[1:], fields))
    return entries


def _excel_cell(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip() if value is not None else ""


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
