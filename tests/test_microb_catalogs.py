from __future__ import annotations

from sqlalchemy import create_engine, select

from fis_data.etl.microb.catalogs import MicrobCatalogRepository, _catalog_name
from fis_data.schema import (
    create_schema,
    ref_microb_catalog,
    ref_microb_catalog_entry,
)


def test_microb_catalog_loader_versions_and_preserves_aliases(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'catalogs.sqlite'}", future=True)
    create_schema(engine)
    catalog_dir = tmp_path / "catalogs"
    catalog_dir.mkdir()
    catalog = catalog_dir / "antibiotico.txt"
    catalog.write_text("AMC|Amoxicilina|AMX|\nCIP|Ciprofloxacino|\n", encoding="utf-8")

    repository = MicrobCatalogRepository(engine)
    first = repository.load_directory(catalog_dir)
    second = repository.load_directory(catalog_dir)

    assert first[0].entries_loaded == 2
    assert not first[0].unchanged
    assert second[0].unchanged
    with engine.connect() as conn:
        assert conn.execute(select(ref_microb_catalog.c.is_current)).scalar_one()
        entries = conn.execute(
            select(
                ref_microb_catalog_entry.c.code,
                ref_microb_catalog_entry.c.description,
                ref_microb_catalog_entry.c.aliases_json,
            ).order_by(ref_microb_catalog_entry.c.code)
        ).all()
    assert entries == [
        ("AMC", "Amoxicilina", '["AMX"]'),
        ("CIP", "Ciprofloxacino", "[]"),
    ]


def test_microb_catalog_loader_accepts_cp1252_files(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'catalogs.sqlite'}", future=True)
    create_schema(engine)
    catalog = tmp_path / "microorg.txt"
    catalog.write_bytes("ECO|ESCHERICHIA COLI Ñ|\n".encode("cp1252"))

    MicrobCatalogRepository(engine).load(path=catalog)

    with engine.connect() as conn:
        assert conn.execute(
            select(ref_microb_catalog_entry.c.description)
        ).scalar_one() == "ESCHERICHIA COLI Ñ"


def test_excel_catalogues_keep_a_distinct_fallback_name(tmp_path) -> None:
    assert _catalog_name(tmp_path / "Procedencias.xls") == "procedencias_extra"
    assert _catalog_name(tmp_path / "Servicios.xls") == "servicios_extra"
    assert _catalog_name(tmp_path / "Hospitales.xls") == "hospitales"
