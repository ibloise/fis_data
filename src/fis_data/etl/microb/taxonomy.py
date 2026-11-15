"""Descriptive study labels, keyed by the unchanged, case-sensitive SIL code."""

from __future__ import annotations

import csv
from importlib.resources import files

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from ...schema import (
    dim_microb_microorganism,
    ref_microb_catalog,
    ref_microb_catalog_entry,
)

NORMALIZATION_VERSION = "microorganism-normalization-v1"


def read_dictionary() -> list[dict[str, str]]:
    """Read the sole mapping resource; never transform a SIL identifier."""

    resource = files("fis_data").joinpath("resources", f"{NORMALIZATION_VERSION}.csv")
    with resource.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    codes = [row["microorganism_code"] for row in rows]
    if len(rows) != 28 or len(set(codes)) != 28:
        raise ValueError("The study dictionary must contain 28 unique SIL codes.")
    for row in rows:
        for field in (
            "microorganism_code",
            "observed_name",
            "scientific_name",
            "analysis_group_code",
            "analysis_group_name",
        ):
            if not row[field]:
                raise ValueError(f"Missing dictionary field: {field}")
        if len(row["microorganism_code"]) > 32 or len(row["analysis_group_code"]) > 64:
            raise ValueError("Dictionary identifier exceeds schema length.")
    if len({row["scientific_name"] for row in rows}) != 26:
        raise ValueError("The study dictionary must contain 26 scientific names.")
    groups: dict[str, str] = {}
    for row in rows:
        code, name = row["analysis_group_code"], row["analysis_group_name"]
        if code in groups and groups[code] != name:
            raise ValueError(f"Ambiguous analysis group: {code}")
        groups[code] = name
    if len(groups) != 24 or len(set(groups.values())) != 24:
        raise ValueError("The study dictionary must contain 24 analysis groups.")
    return rows


def load_microorganism_dimension(engine: Engine) -> dict[str, object]:
    """Upsert the versioned dictionary without changing any clinical table."""

    rows = read_dictionary()
    insert = sqlite_insert if engine.dialect.name == "sqlite" else pg_insert
    table = dim_microb_microorganism
    values = [
        {column.name: row[column.name] or None for column in table.columns}
        for row in rows
    ]
    statement = insert(table).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.microorganism_code],
        set_={
            column.name: statement.excluded[column.name]
            for column in table.columns
            if not column.primary_key
        },
    )
    with engine.begin() as conn:
        conn.execute(statement)
        current_entries = conn.execute(
            select(
                ref_microb_catalog_entry.c.code, ref_microb_catalog_entry.c.description
            )
            .join(ref_microb_catalog)
            .where(
                ref_microb_catalog.c.catalog_name == "microorg",
                ref_microb_catalog.c.is_current.is_(True),
            )
        ).all()
        observed = {row["microorganism_code"]: row["observed_name"] for row in rows}
        differences = [
            {
                "microorganism_code": code,
                "catalog_name": description,
                "observed_name": observed[code],
            }
            for code, description in current_entries
            if code in observed and description != observed[code]
        ]
    return {
        "version": NORMALIZATION_VERSION,
        "codes_loaded": len(rows),
        "scientific_names": len({row["scientific_name"] for row in rows}),
        "analysis_groups": len({row["analysis_group_code"] for row in rows}),
        "catalog_compared": bool(current_entries),
        "catalog_description_differences": differences,
        "dictionary_codes_absent_from_current_catalog": sorted(
            set(observed) - {code for code, _ in current_entries}
        ),
    }


def validate_microorganism_coverage(conn: Connection) -> dict[str, object]:
    """Fail explicitly on unmapped cohort codes, retaining legitimate NULLs."""

    unknown = (
        conn.execute(
            text("""
        SELECT DISTINCT organism.microorg_code
        FROM v_downstream_sil_pcr_organism AS organism
        LEFT JOIN dim_microb_microorganism AS taxonomy
          ON taxonomy.microorganism_code=organism.microorg_code
        WHERE organism.microorg_code IS NOT NULL
          AND taxonomy.microorganism_code IS NULL
        ORDER BY organism.microorg_code
    """)
        )
        .scalars()
        .all()
    )
    if unknown:
        raise ValueError(
            "Microorganism normalization validation failed; unmapped SIL codes: "
            + ", ".join(repr(code) for code in unknown)
            + ". Load/review the versioned dictionary with "
            "fis-data load-microorganism-dimension before exporting."
        )
    counts = (
        conn.execute(
            text("""
        SELECT COUNT(*) AS microorganism_rows,
               COUNT(DISTINCT organism.microorg_code) AS covered_codes,
               COUNT(DISTINCT taxonomy.scientific_name) AS scientific_names,
               COUNT(DISTINCT taxonomy.analysis_group_code) AS analysis_groups
        FROM v_downstream_sil_pcr_organism AS organism
        JOIN dim_microb_microorganism AS taxonomy
          ON taxonomy.microorganism_code=organism.microorg_code
    """)
        )
        .mappings()
        .one()
    )
    return {
        "version": NORMALIZATION_VERSION,
        "dimension_codes": conn.execute(
            select(func.count()).select_from(dim_microb_microorganism)
        ).scalar_one(),
        **dict(counts),
        "unmapped_codes": [],
    }
