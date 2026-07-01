"""Materialize parsed PCR Excel payloads into normalized domain tables."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .contracts import JobContext
from .excel_profiles import normalize_profile_token
from .materialization import MaterializationRow, MaterializationStats


class PCRMaterializer:
    """PCR domain adapter for parsed Excel payloads."""

    entity_name = "pcr"
    materializer_name = "pcr-domain"
    materializer_version = "0.3"

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
        if ctx.run_id is None:
            raise ValueError("Materialization requires an ETL run_id.")

        run_name = _run_name(context_rows)
        with engine.begin() as conn:
            affected_run_ids: set[int] = set()
            if reprocess:
                affected_run_ids = _clear_file_materialization(
                    conn,
                    file_id=ctx.file_id,
                    materializer_name=self.materializer_name,
                )

            if run_name is None:
                error = "Missing required Run Information value: File Name."
                for row in rows:
                    _record_item(
                        conn,
                        ctx=ctx,
                        row=row,
                        materializer=self,
                        status="LOAD_ERROR",
                        error=error,
                    )
                if reprocess:
                    for affected_run_id in affected_run_ids:
                        _delete_orphan_wells(conn, pcr_run_id=affected_run_id)
                    _delete_empty_runs(conn, pcr_run_ids=affected_run_ids)
                return MaterializationStats(
                    file_id=ctx.file_id,
                    entity_name=ctx.entity_name,
                    status="LOAD_ERROR",
                    rows_seen=len(rows),
                    rows_loaded=0,
                    rows_error=len(rows),
                    targets_written=0,
                    error=error,
                )

            source_id = context_rows[0].source_id
            pcr_run_id = _ensure_pcr_run(conn, source_id=source_id, run_name=run_name)
            _link_run_file(
                conn,
                pcr_run_id=pcr_run_id,
                file_id=ctx.file_id,
                load_run_id=ctx.run_id,
            )
            well_ids = _load_well_ids(conn, pcr_run_id=pcr_run_id)

            loaded = errors = targets = 0
            if batch_size <= 0:
                raise ValueError("batch_size must be greater than zero.")
            for offset in range(0, len(rows), batch_size):
                for row in rows[offset : offset + batch_size]:
                    try:
                        with conn.begin_nested():
                            target = _materialize_row(
                                conn,
                                ctx=ctx,
                                row=row,
                                pcr_run_id=pcr_run_id,
                                well_ids=well_ids,
                            )
                            _record_item(
                                conn,
                                ctx=ctx,
                                row=row,
                                materializer=self,
                                status="LOADED",
                                target=target,
                            )
                        loaded += 1
                        targets += int(target.get("targets_written", 1))
                    except (KeyError, TypeError, ValueError) as exc:
                        _record_item(
                            conn,
                            ctx=ctx,
                            row=row,
                            materializer=self,
                            status="LOAD_ERROR",
                            error=str(exc),
                        )
                        errors += 1

            conflicts = _refresh_attribute_flags(conn, pcr_run_id=pcr_run_id)
            _refresh_well_metadata(conn, pcr_run_id=pcr_run_id)
            if reprocess:
                for affected_run_id in affected_run_ids | {pcr_run_id}:
                    _delete_orphan_wells(conn, pcr_run_id=affected_run_id)
                _delete_empty_runs(conn, pcr_run_ids=affected_run_ids)

        return MaterializationStats(
            file_id=ctx.file_id,
            entity_name=ctx.entity_name,
            status="LOADED_WITH_ERRORS" if errors else "LOADED",
            rows_seen=len(rows),
            rows_loaded=loaded,
            rows_error=errors,
            targets_written=targets,
            warnings=conflicts,
        )


def _run_name(rows: Iterable[MaterializationRow]) -> str | None:
    for row in rows:
        if row.payload.get("sheet_kind") != "run_information":
            continue
        fields = row.payload.get("fields", {})
        if normalize_profile_token(str(fields.get("key", ""))) != "file_name":
            continue
        value = fields.get("value")
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _ensure_pcr_run(conn: Connection, *, source_id: int, run_name: str) -> int:
    normalized = normalize_profile_token(run_name)
    if not normalized:
        raise ValueError("File Name is empty after normalization.")
    conn.execute(
        text(
            """
            INSERT INTO pcr_run (source_id, run_name, run_name_normalized)
            VALUES (:source_id, :run_name, :normalized)
            ON CONFLICT(source_id, run_name_normalized) DO NOTHING
            """
        ),
        {"source_id": source_id, "run_name": run_name, "normalized": normalized},
    )
    return int(
        conn.execute(
            text(
                """
                SELECT pcr_run_id FROM pcr_run
                WHERE source_id = :source_id AND run_name_normalized = :normalized
                """
            ),
            {"source_id": source_id, "normalized": normalized},
        ).scalar_one()
    )


def _link_run_file(
    conn: Connection, *, pcr_run_id: int, file_id: int, load_run_id: int
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO pcr_run_file (pcr_run_id, file_id, run_id)
            VALUES (:pcr_run_id, :file_id, :run_id)
            ON CONFLICT(file_id) DO UPDATE SET
                pcr_run_id = excluded.pcr_run_id,
                run_id = excluded.run_id
            """
        ),
        {"pcr_run_id": pcr_run_id, "file_id": file_id, "run_id": load_run_id},
    )


def _materialize_row(
    conn: Connection,
    *,
    ctx: JobContext,
    row: MaterializationRow,
    pcr_run_id: int,
    well_ids: dict[str, int],
) -> dict[str, Any]:
    sheet_kind = str(row.payload.get("sheet_kind", ""))
    fields = row.payload.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("Payload fields must be an object.")

    if sheet_kind == "run_information":
        return _materialize_attribute(conn, ctx, row, pcr_run_id, fields)
    if sheet_kind == "cq_results":
        return _materialize_cq(
            conn, ctx, row, pcr_run_id, fields, well_ids=well_ids
        )
    if sheet_kind in {
        "amplification_sybr",
        "melt_curve_sybr",
        "melt_curve_derivative_sybr",
    }:
        return _materialize_rfu(
            conn,
            ctx,
            row,
            pcr_run_id,
            sheet_kind=sheet_kind,
            fields=fields,
            well_ids=well_ids,
        )
    raise ValueError(f"Unsupported parsed PCR sheet kind: {sheet_kind!r}.")


def _materialize_attribute(
    conn: Connection,
    ctx: JobContext,
    row: MaterializationRow,
    pcr_run_id: int,
    fields: dict[str, Any],
) -> dict[str, Any]:
    key = str(fields["key"]).strip()
    normalized = normalize_profile_token(key)
    if not normalized:
        raise ValueError("Run Information key is empty.")
    conn.execute(
        text(
            """
            INSERT INTO pcr_run_attribute (
                pcr_run_id, file_id, raw_excel_row_id, run_id, attribute_key,
                attribute_key_normalized, value_json
            ) VALUES (
                :pcr_run_id, :file_id, :row_id, :run_id, :key, :normalized,
                :value_json
            )
            ON CONFLICT(file_id, attribute_key_normalized) DO UPDATE SET
                pcr_run_id = excluded.pcr_run_id,
                raw_excel_row_id = excluded.raw_excel_row_id,
                run_id = excluded.run_id,
                attribute_key = excluded.attribute_key,
                value_json = excluded.value_json
            """
        ),
        {
            "pcr_run_id": pcr_run_id,
            "file_id": row.file_id,
            "row_id": row.row_id,
            "run_id": ctx.run_id,
            "key": key,
            "normalized": normalized,
            "value_json": json.dumps(fields.get("value"), ensure_ascii=False),
        },
    )
    attribute_id = conn.execute(
        text(
            """
            SELECT pcr_run_attribute_id FROM pcr_run_attribute
            WHERE file_id = :file_id AND attribute_key_normalized = :normalized
            """
        ),
        {"file_id": row.file_id, "normalized": normalized},
    ).scalar_one()
    return {"table": "pcr_run_attribute", "ids": [int(attribute_id)]}


def _ensure_well(
    conn: Connection,
    *,
    ctx: JobContext,
    row: MaterializationRow,
    pcr_run_id: int,
    well: str,
    sample_name: Any = None,
    content: Any = None,
    well_ids: dict[str, int],
) -> int:
    normalized = _canonical_well(well)
    existing_id = well_ids.get(normalized)
    if existing_id is not None:
        return existing_id
    conn.execute(
        text(
            """
            INSERT INTO pcr_well (
                pcr_run_id, well, well_normalized, sample_name, content,
                file_id, raw_excel_row_id, run_id
            ) VALUES (
                :pcr_run_id, :well, :normalized, :sample_name, :content,
                :file_id, :row_id, :run_id
            )
            ON CONFLICT(pcr_run_id, well_normalized) DO UPDATE SET
                well = excluded.well,
                sample_name = COALESCE(excluded.sample_name, pcr_well.sample_name),
                content = COALESCE(excluded.content, pcr_well.content),
                file_id = CASE WHEN excluded.sample_name IS NOT NULL
                               THEN excluded.file_id ELSE pcr_well.file_id END,
                raw_excel_row_id = CASE WHEN excluded.sample_name IS NOT NULL
                                        THEN excluded.raw_excel_row_id
                                        ELSE pcr_well.raw_excel_row_id END,
                run_id = excluded.run_id
            """
        ),
        {
            "pcr_run_id": pcr_run_id,
            "well": normalized,
            "normalized": normalized,
            "sample_name": _optional_text(sample_name),
            "content": _optional_text(content),
            "file_id": row.file_id,
            "row_id": row.row_id,
            "run_id": ctx.run_id,
        },
    )
    well_id = int(
        conn.execute(
            text(
                """
                SELECT pcr_well_id FROM pcr_well
                WHERE pcr_run_id = :pcr_run_id AND well_normalized = :normalized
                """
            ),
            {"pcr_run_id": pcr_run_id, "normalized": normalized},
        ).scalar_one()
    )
    well_ids[normalized] = well_id
    return well_id


def _load_well_ids(conn: Connection, *, pcr_run_id: int) -> dict[str, int]:
    rows = conn.execute(
        text(
            """
            SELECT well_normalized, pcr_well_id
            FROM pcr_well
            WHERE pcr_run_id = :pcr_run_id
            """
        ),
        {"pcr_run_id": pcr_run_id},
    ).all()
    return {str(row[0]): int(row[1]) for row in rows}


def _ensure_rfu_wells(
    conn: Connection,
    *,
    ctx: JobContext,
    row: MaterializationRow,
    pcr_run_id: int,
    wells: Iterable[str],
    well_ids: dict[str, int],
) -> None:
    missing: dict[str, str] = {}
    for well in wells:
        normalized = _canonical_well(well)
        if normalized not in well_ids:
            missing[normalized] = normalized
    if not missing:
        return

    conn.execute(
        text(
            """
            INSERT INTO pcr_well (
                pcr_run_id, well, well_normalized, file_id,
                raw_excel_row_id, run_id
            ) VALUES (
                :pcr_run_id, :well, :normalized, :file_id, :row_id, :run_id
            )
            ON CONFLICT(pcr_run_id, well_normalized) DO NOTHING
            """
        ),
        [
            {
                "pcr_run_id": pcr_run_id,
                "well": well,
                "normalized": normalized,
                "file_id": row.file_id,
                "row_id": row.row_id,
                "run_id": ctx.run_id,
            }
            for normalized, well in missing.items()
        ],
    )
    well_ids.update(_load_well_ids(conn, pcr_run_id=pcr_run_id))


def _materialize_cq(
    conn: Connection,
    ctx: JobContext,
    row: MaterializationRow,
    pcr_run_id: int,
    fields: dict[str, Any],
    well_ids: dict[str, int],
) -> dict[str, Any]:
    well = str(fields["well"])
    well_id = _ensure_well(
        conn,
        ctx=ctx,
        row=row,
        pcr_run_id=pcr_run_id,
        well=well,
        sample_name=fields.get("sample"),
        content=fields.get("content"),
        well_ids=well_ids,
    )
    target = str(fields["target"]).strip()
    if not target:
        raise ValueError("PCR target is empty.")
    conn.execute(
        text(
            """
            INSERT INTO pcr_cq_result (
                pcr_run_id, pcr_well_id, file_id, raw_excel_row_id, run_id,
                target, fluor, sample_name, content, cq, cq_mean, cq_std_dev
            ) VALUES (
                :pcr_run_id, :well_id, :file_id, :row_id, :run_id,
                :target, :fluor, :sample_name, :content, :cq, :cq_mean, :cq_std_dev
            )
            ON CONFLICT(raw_excel_row_id) DO UPDATE SET
                pcr_run_id = excluded.pcr_run_id,
                pcr_well_id = excluded.pcr_well_id,
                run_id = excluded.run_id,
                target = excluded.target,
                fluor = excluded.fluor,
                sample_name = excluded.sample_name,
                content = excluded.content,
                cq = excluded.cq,
                cq_mean = excluded.cq_mean,
                cq_std_dev = excluded.cq_std_dev
            """
        ),
        {
            "pcr_run_id": pcr_run_id,
            "well_id": well_id,
            "file_id": row.file_id,
            "row_id": row.row_id,
            "run_id": ctx.run_id,
            "target": target,
            "fluor": _optional_text(fields.get("fluor")),
            "sample_name": _optional_text(fields.get("sample")),
            "content": _optional_text(fields.get("content")),
            "cq": fields.get("cq"),
            "cq_mean": fields.get("cq_mean"),
            "cq_std_dev": fields.get("cq_std_dev"),
        },
    )
    result_id = conn.execute(
        text(
            "SELECT pcr_cq_result_id FROM pcr_cq_result WHERE raw_excel_row_id=:row_id"
        ),
        {"row_id": row.row_id},
    ).scalar_one()
    return {"table": "pcr_cq_result", "ids": [int(result_id)]}


def _materialize_rfu(
    conn: Connection,
    ctx: JobContext,
    row: MaterializationRow,
    pcr_run_id: int,
    *,
    sheet_kind: str,
    fields: dict[str, Any],
    well_ids: dict[str, int],
) -> dict[str, Any]:
    if sheet_kind == "amplification_sybr":
        axis_kind, axis_value = "cycle", fields["cycle"]
        measurement_kind = "amplification_rfu"
    elif sheet_kind == "melt_curve_sybr":
        axis_kind, axis_value = "temperature", fields["temperature"]
        measurement_kind = "melt_curve_rfu"
    else:
        axis_kind, axis_value = "temperature", fields["temperature"]
        measurement_kind = "melt_curve_derivative"

    rfu_by_well = fields.get("rfu_by_well")
    if not isinstance(rfu_by_well, dict) or not rfu_by_well:
        raise ValueError("PCR RFU payload has no well measurements.")

    wells = [str(well) for well in rfu_by_well]
    _ensure_rfu_wells(
        conn,
        ctx=ctx,
        row=row,
        pcr_run_id=pcr_run_id,
        wells=wells,
        well_ids=well_ids,
    )
    measurements = []
    for well, rfu in rfu_by_well.items():
        normalized = _canonical_well(str(well))
        measurements.append(
            {
                "pcr_run_id": pcr_run_id,
                "well_id": well_ids[normalized],
                "file_id": row.file_id,
                "row_id": row.row_id,
                "run_id": ctx.run_id,
                "measurement_kind": measurement_kind,
                "axis_kind": axis_kind,
                "axis_value": float(axis_value),
                "rfu": None if rfu is None else float(rfu),
            }
        )
    conn.execute(
        text(
            """
            INSERT INTO pcr_rfu_measurement (
                pcr_run_id, pcr_well_id, file_id, raw_excel_row_id, run_id,
                measurement_kind, axis_kind, axis_value, rfu
            ) VALUES (
                :pcr_run_id, :well_id, :file_id, :row_id, :run_id,
                :measurement_kind, :axis_kind, :axis_value, :rfu
            )
            ON CONFLICT(raw_excel_row_id, pcr_well_id) DO UPDATE SET
                pcr_run_id = excluded.pcr_run_id,
                run_id = excluded.run_id,
                measurement_kind = excluded.measurement_kind,
                axis_kind = excluded.axis_kind,
                axis_value = excluded.axis_value,
                rfu = excluded.rfu
            """
        ),
        measurements,
    )
    return {
        "table": "pcr_rfu_measurement",
        "source_row_id": row.row_id,
        "wells": wells,
        "targets_written": len(measurements),
    }


def _record_item(
    conn: Connection,
    *,
    ctx: JobContext,
    row: MaterializationRow,
    materializer: PCRMaterializer,
    status: str,
    error: str | None = None,
    target: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO ctl_materialization_item (
                run_id, source_table, source_row_id, entity_name, file_id,
                materializer_name, materializer_version, status, error, target_json
            ) VALUES (
                :run_id, 'raw_excel_rows', :row_id, :entity_name, :file_id,
                :name, :version, :status, :error, :target_json
            )
            ON CONFLICT(source_table, source_row_id, materializer_name) DO UPDATE SET
                run_id = excluded.run_id,
                materializer_version = excluded.materializer_version,
                status = excluded.status,
                error = excluded.error,
                target_json = excluded.target_json,
                updated_at = CURRENT_TIMESTAMP
            """
        ),
        {
            "run_id": ctx.run_id,
            "row_id": row.row_id,
            "entity_name": ctx.entity_name,
            "file_id": row.file_id,
            "name": materializer.materializer_name,
            "version": materializer.materializer_version,
            "status": status,
            "error": error,
            "target_json": (
                json.dumps(target, ensure_ascii=False, separators=(",", ":"))
                if target is not None
                else None
            ),
        },
    )


def _clear_file_materialization(
    conn: Connection, *, file_id: int, materializer_name: str
) -> set[int]:
    affected_run_ids = {
        int(row[0])
        for row in conn.execute(
            text("SELECT pcr_run_id FROM pcr_run_file WHERE file_id = :file_id"),
            {"file_id": file_id},
        )
    }
    conn.execute(
        text("DELETE FROM pcr_rfu_measurement WHERE file_id = :file_id"),
        {"file_id": file_id},
    )
    conn.execute(
        text("DELETE FROM pcr_cq_result WHERE file_id = :file_id"),
        {"file_id": file_id},
    )
    conn.execute(
        text("DELETE FROM pcr_run_attribute WHERE file_id = :file_id"),
        {"file_id": file_id},
    )
    conn.execute(
        text("DELETE FROM pcr_run_file WHERE file_id = :file_id"),
        {"file_id": file_id},
    )
    conn.execute(
        text(
            """
            DELETE FROM ctl_materialization_item
            WHERE file_id = :file_id AND materializer_name = :materializer_name
            """
        ),
        {"file_id": file_id, "materializer_name": materializer_name},
    )
    return affected_run_ids


def _refresh_attribute_flags(conn: Connection, *, pcr_run_id: int) -> int:
    conn.execute(
        text(
            """
            UPDATE pcr_run_attribute AS current
            SET is_canonical = CASE WHEN current.file_id = (
                    SELECT MIN(candidate.file_id) FROM pcr_run_attribute AS candidate
                    WHERE candidate.pcr_run_id = current.pcr_run_id
                      AND candidate.attribute_key_normalized =
                          current.attribute_key_normalized
                ) THEN 1 ELSE 0 END,
                has_conflict = CASE WHEN 1 < (
                    SELECT COUNT(DISTINCT COALESCE(candidate.value_json, 'null'))
                    FROM pcr_run_attribute AS candidate
                    WHERE candidate.pcr_run_id = current.pcr_run_id
                      AND candidate.attribute_key_normalized =
                          current.attribute_key_normalized
                ) THEN 1 ELSE 0 END
            WHERE current.pcr_run_id = :pcr_run_id
            """
        ),
        {"pcr_run_id": pcr_run_id},
    )
    return int(
        conn.execute(
            text(
                """
                SELECT COUNT(DISTINCT attribute_key_normalized)
                FROM pcr_run_attribute
                WHERE pcr_run_id = :pcr_run_id AND has_conflict = 1
                """
            ),
            {"pcr_run_id": pcr_run_id},
        ).scalar_one()
    )


def _refresh_well_metadata(conn: Connection, *, pcr_run_id: int) -> None:
    conn.execute(
        text(
            """
            UPDATE pcr_well AS well
            SET sample_name = (
                    SELECT result.sample_name FROM pcr_cq_result AS result
                    WHERE result.pcr_well_id = well.pcr_well_id
                    ORDER BY result.file_id, result.raw_excel_row_id LIMIT 1
                ),
                content = (
                    SELECT result.content FROM pcr_cq_result AS result
                    WHERE result.pcr_well_id = well.pcr_well_id
                    ORDER BY result.file_id, result.raw_excel_row_id LIMIT 1
                )
            WHERE well.pcr_run_id = :pcr_run_id
            """
        ),
        {"pcr_run_id": pcr_run_id},
    )


def _delete_orphan_wells(conn: Connection, *, pcr_run_id: int) -> None:
    conn.execute(
        text(
            """
            DELETE FROM pcr_well
            WHERE pcr_run_id = :pcr_run_id
              AND NOT EXISTS (
                    SELECT 1 FROM pcr_cq_result
                    WHERE pcr_cq_result.pcr_well_id = pcr_well.pcr_well_id
                  )
              AND NOT EXISTS (
                    SELECT 1 FROM pcr_rfu_measurement
                    WHERE pcr_rfu_measurement.pcr_well_id = pcr_well.pcr_well_id
                  )
            """
        ),
        {"pcr_run_id": pcr_run_id},
    )


def _delete_empty_runs(conn: Connection, *, pcr_run_ids: set[int]) -> None:
    if not pcr_run_ids:
        return
    conn.execute(
        text(
            """
            DELETE FROM pcr_run
            WHERE pcr_run_id = :pcr_run_id
              AND NOT EXISTS (
                    SELECT 1 FROM pcr_run_file
                    WHERE pcr_run_file.pcr_run_id = pcr_run.pcr_run_id
                  )
            """
        ),
        [{"pcr_run_id": pcr_run_id} for pcr_run_id in pcr_run_ids],
    )


def _optional_text(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def _canonical_well(value: str) -> str:
    match = re.fullmatch(r"([A-Ha-h])0*([1-9]|1[0-2])", value.strip())
    if match is None:
        raise ValueError(f"Invalid PCR well: {value!r}.")
    return f"{match.group(1).upper()}{int(match.group(2)):02d}"


PCR_MATERIALIZER = PCRMaterializer()
