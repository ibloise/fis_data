"""Materialize parsed ST rows and audit their isolate matches."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from ..contracts import JobContext
from ..materialization import MaterializationRow, MaterializationStats
from ...schema import (
    ctl_materialization_item,
    microb_analysis_result,
    microb_isolate,
    microb_isolate_st_link,
    microb_sample,
    microb_sample_analysis,
    stg_st_record,
)


class STMaterializer:
    """Match sample/code ST records to all exact matching Microb isolates."""

    entity_name = "st"
    materializer_name = "st-domain"
    materializer_version = "0.1"

    def materialize_file(
        self,
        *,
        engine: Engine,
        ctx: JobContext,
        rows: list[MaterializationRow],
        context_rows: list[MaterializationRow],
        reprocess: bool,
        batch_size: int,
    ) -> MaterializationStats:
        del context_rows
        if ctx.run_id is None:
            raise ValueError("ST materialization requires an ETL run_id.")
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero.")

        with engine.begin() as conn:
            if reprocess:
                _clear_file(conn, file_id=ctx.file_id)
            sample_ids, isolate_matches = _find_isolates(conn, rows)
            conflict_keys = _conflicting_input_keys(rows)
            loaded = failed = warnings = links_written = 0
            for offset in range(0, len(rows), batch_size):
                for row in rows[offset : offset + batch_size]:
                    payload = row.payload
                    fields = payload.get("fields", {})
                    sample_number = str(fields["sample_number"]).strip()
                    microorganism_code = str(fields["microorganism_code"]).strip()
                    key = (sample_number, microorganism_code)
                    matched_samples = sample_ids.get(sample_number, [])
                    matched_isolates = (
                        isolate_matches.get((matched_samples[0], microorganism_code), [])
                        if len(matched_samples) == 1 else []
                    )

                    if len(matched_samples) == 0:
                        match_status = "SAMPLE_NOT_FOUND"
                    elif len(matched_samples) > 1:
                        match_status = "AMBIGUOUS_SAMPLE"
                    elif not matched_isolates:
                        match_status = "ISOLATE_NOT_FOUND"
                    elif key in conflict_keys:
                        match_status = "CONFLICTING_ST_VALUES"
                    elif len(matched_isolates) > 1:
                        match_status = "AMBIGUOUS_ISOLATES"
                    else:
                        match_status = "MATCHED"

                    details = {
                        "sample_match_ids": matched_samples,
                        "isolate_match_ids": matched_isolates,
                        "input_st_values_for_key": sorted(conflict_keys.get(key, ())),
                        "rule": "exact sample_number and microorganism_code",
                    }
                    record_values = {
                        "source_id": row.source_id,
                        "run_id": ctx.run_id,
                        "file_id": ctx.file_id,
                        "raw_excel_row_id": row.row_id,
                        "sample_number": sample_number,
                        "microorganism_code": microorganism_code,
                        "microorganism_name": _optional_text(
                            fields.get("microorganism_name")
                        ),
                        "microorganism_name_raw": _optional_raw_text(
                            fields.get("microorganism_name_raw")
                        ),
                        "st_value": str(fields["st_value"]).strip(),
                        "st_raw": _optional_raw_text(fields.get("st_raw")),
                        "match_status": match_status,
                        "matched_sample_count": len(matched_samples),
                        "matched_isolate_count": len(matched_isolates),
                        "details_json": json.dumps(details, ensure_ascii=False),
                    }
                    existing_id = conn.execute(select(
                        stg_st_record.c.st_record_id
                    ).where(
                        stg_st_record.c.raw_excel_row_id == row.row_id
                    )).scalar_one_or_none()
                    if existing_id is None:
                        record_id = int(conn.execute(
                            insert(stg_st_record).values(**record_values)
                            .returning(stg_st_record.c.st_record_id)
                        ).scalar_one())
                    else:
                        record_id = int(existing_id)
                        conn.execute(delete(microb_isolate_st_link).where(
                            microb_isolate_st_link.c.st_record_id == record_id
                        ))
                        conn.execute(update(stg_st_record).where(
                            stg_st_record.c.st_record_id == record_id
                        ).values(**record_values))

                    if match_status in {"MATCHED", "AMBIGUOUS_ISOLATES"}:
                        conn.execute(insert(microb_isolate_st_link), [
                            {"st_record_id": record_id, "isolate_id": isolate_id}
                            for isolate_id in matched_isolates
                        ])
                        links_written += len(matched_isolates)
                        if match_status == "AMBIGUOUS_ISOLATES":
                            warnings += 1
                    else:
                        failed += 1

                    _record_materialization_item(
                        conn,
                        dialect=engine.dialect.name,
                        ctx=ctx,
                        row=row,
                        status="LOADED",
                        target={
                            "st_record_id": record_id,
                            "match_status": match_status,
                            "matched_sample_count": len(matched_samples),
                            "matched_isolate_count": len(matched_isolates),
                        },
                    )
                    loaded += 1

        return MaterializationStats(
            file_id=ctx.file_id,
            entity_name=ctx.entity_name,
            status="LOADED_WITH_ERRORS" if failed else "LOADED",
            rows_seen=len(rows),
            rows_loaded=loaded,
            rows_error=failed,
            targets_written=links_written,
            warnings=warnings,
            error=(
                f"{failed} ST rows were retained for audit but not assigned to isolates."
                if failed else None
            ),
        )


def _find_isolates(
    conn: Connection, rows: list[MaterializationRow]
) -> tuple[dict[str, list[int]], dict[tuple[int, str], list[int]]]:
    sample_numbers = sorted({
        str(row.payload["fields"]["sample_number"]).strip() for row in rows
    })
    sample_ids: dict[str, list[int]] = defaultdict(list)
    if not sample_numbers:
        return sample_ids, {}

    sample_rows = conn.execute(
        select(microb_sample.c.sample_id, microb_sample.c.sample_number)
        .where(microb_sample.c.sample_number.in_(sample_numbers))
        .order_by(microb_sample.c.sample_number, microb_sample.c.sample_id)
    ).all()
    for sample_id, sample_number in sample_rows:
        sample_ids[str(sample_number)].append(int(sample_id))

    matched_sample_ids = sorted({
        sample_id for ids in sample_ids.values() for sample_id in ids
    })
    organism_codes = sorted({
        str(row.payload["fields"]["microorganism_code"]).strip() for row in rows
    })
    if not matched_sample_ids or not organism_codes:
        return sample_ids, {}
    isolate_rows = conn.execute(
        select(
            microb_isolate.c.isolate_id,
            microb_sample_analysis.c.sample_id,
            microb_isolate.c.microorganism_code,
        )
        .select_from(
            microb_isolate
            .join(
                microb_analysis_result,
                microb_analysis_result.c.analysis_result_id
                == microb_isolate.c.analysis_result_id,
            )
            .join(
                microb_sample_analysis,
                microb_sample_analysis.c.sample_analysis_id
                == microb_analysis_result.c.sample_analysis_id,
            )
        )
        .where(
            microb_sample_analysis.c.sample_id.in_(matched_sample_ids),
            microb_isolate.c.microorganism_code.in_(organism_codes),
        )
        .distinct()
        .order_by(
            microb_sample_analysis.c.sample_id,
            microb_isolate.c.microorganism_code,
            microb_isolate.c.isolate_id,
        )
    ).all()
    matches: dict[tuple[int, str], list[int]] = defaultdict(list)
    for isolate_id, sample_id, organism_code in isolate_rows:
        matches[(int(sample_id), str(organism_code))].append(int(isolate_id))
    return sample_ids, matches


def _conflicting_input_keys(
    rows: list[MaterializationRow],
) -> dict[tuple[str, str], set[str]]:
    values: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        fields = row.payload["fields"]
        key = (
            str(fields["sample_number"]).strip(),
            str(fields["microorganism_code"]).strip(),
        )
        values[key].add(str(fields["st_value"]).strip())
    return {key: st_values for key, st_values in values.items() if len(st_values) > 1}


def _clear_file(conn: Connection, *, file_id: int) -> None:
    file_records = select(stg_st_record.c.st_record_id).where(
        stg_st_record.c.file_id == file_id
    )
    conn.execute(delete(microb_isolate_st_link).where(
        microb_isolate_st_link.c.st_record_id.in_(file_records)
    ))
    conn.execute(delete(stg_st_record).where(stg_st_record.c.file_id == file_id))
    conn.execute(delete(ctl_materialization_item).where(
        ctl_materialization_item.c.file_id == file_id,
        ctl_materialization_item.c.materializer_name == STMaterializer.materializer_name,
    ))


def _record_materialization_item(
    conn: Connection,
    *,
    dialect: str,
    ctx: JobContext,
    row: MaterializationRow,
    status: str,
    target: dict[str, Any],
) -> None:
    values = {
        "run_id": ctx.run_id,
        "source_table": "raw_excel_rows",
        "source_row_id": row.row_id,
        "entity_name": ctx.entity_name,
        "file_id": row.file_id,
        "materializer_name": STMaterializer.materializer_name,
        "materializer_version": STMaterializer.materializer_version,
        "status": status,
        "error": None,
        "target_json": json.dumps(target, ensure_ascii=False),
    }
    if dialect == "postgresql":
        insert_function = pg_insert
    elif dialect == "sqlite":
        insert_function = sqlite_insert
    else:
        conn.execute(insert(ctl_materialization_item).values(**values))
        return
    conn.execute(
        insert_function(ctl_materialization_item)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["source_table", "source_row_id", "materializer_name"],
            set_={key: value for key, value in values.items()
                  if key not in {"source_table", "source_row_id", "materializer_name"}},
        )
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _optional_raw_text(value: Any) -> str | None:
    return None if value is None else str(value)


ST_MATERIALIZER = STMaterializer()
