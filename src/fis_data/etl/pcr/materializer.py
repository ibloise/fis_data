"""Materialize parsed PCR Excel payloads into normalized domain tables."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy.engine import Connection, Engine

from ..contracts import JobContext
from ..excel_profiles import normalize_profile_token
from ..materialization import MaterializationRow, MaterializationStats
from .enums import (
    MaterializationStatuses,
    PCRAxisKinds,
    PCRMeasurementKinds,
    PCRSheetKinds,
)
from .repository import PCRMaterializationRepository
from .scope_policy import experimental_before_from_environment


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
                        status=MaterializationStatuses.load_error,
                        error=error,
                    )
                if reprocess:
                    for affected_run_id in affected_run_ids:
                        _delete_orphan_wells(conn, pcr_run_id=affected_run_id)
                    _delete_empty_runs(conn, pcr_run_ids=affected_run_ids)
                return MaterializationStats(
                    file_id=ctx.file_id,
                    entity_name=ctx.entity_name,
                    status=MaterializationStatuses.load_error,
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
                                status=MaterializationStatuses.loaded,
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
                            status=MaterializationStatuses.load_error,
                            error=str(exc),
                        )
                        errors += 1

            conflicts = _refresh_attribute_flags(conn, pcr_run_id=pcr_run_id)
            _refresh_run_metadata(conn, pcr_run_id=pcr_run_id)
            cutoff = experimental_before_from_environment()
            if cutoff is not None:
                PCRMaterializationRepository(conn).assign_scope_from_policy(
                    pcr_run_id=pcr_run_id, before=cutoff
                )
            _refresh_well_metadata(conn, pcr_run_id=pcr_run_id)
            if reprocess:
                for affected_run_id in affected_run_ids | {pcr_run_id}:
                    _delete_orphan_wells(conn, pcr_run_id=affected_run_id)
                _delete_empty_runs(conn, pcr_run_ids=affected_run_ids)

        return MaterializationStats(
            file_id=ctx.file_id,
            entity_name=ctx.entity_name,
            status=(
                MaterializationStatuses.loaded_with_errors
                if errors
                else MaterializationStatuses.loaded
            ),
            rows_seen=len(rows),
            rows_loaded=loaded,
            rows_error=errors,
            targets_written=targets,
            warnings=conflicts,
        )


def _run_name(rows: Iterable[MaterializationRow]) -> str | None:
    for row in rows:
        if row.payload.get("sheet_kind") != PCRSheetKinds.run_information:
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
    return PCRMaterializationRepository(conn).ensure_run(
        source_id=source_id, run_name=run_name, normalized=normalized
    )


def _link_run_file(
    conn: Connection, *, pcr_run_id: int, file_id: int, load_run_id: int
) -> None:
    PCRMaterializationRepository(conn).link_run_file(
        pcr_run_id=pcr_run_id, file_id=file_id, run_id=load_run_id
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

    if sheet_kind == PCRSheetKinds.run_information:
        return _materialize_attribute(conn, ctx, row, pcr_run_id, fields)
    if sheet_kind == PCRSheetKinds.cq_results:
        return _materialize_cq(conn, ctx, row, pcr_run_id, fields, well_ids=well_ids)
    if sheet_kind in {
        PCRSheetKinds.amplification_sybr,
        PCRSheetKinds.melt_curve_sybr,
        PCRSheetKinds.melt_curve_derivative_sybr,
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
    attribute_id = PCRMaterializationRepository(conn).upsert_attribute(
        {
            "pcr_run_id": pcr_run_id,
            "file_id": row.file_id,
            "raw_excel_row_id": row.row_id,
            "run_id": ctx.run_id,
            "attribute_key": key,
            "attribute_key_normalized": normalized,
            "value_json": json.dumps(fields.get("value"), ensure_ascii=False),
        }
    )
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
    well_id = PCRMaterializationRepository(conn).ensure_well(
        {
            "pcr_run_id": pcr_run_id, "well": normalized,
            "well_normalized": normalized, "sample_name": _optional_text(sample_name),
            "content": _optional_text(content), "file_id": row.file_id,
            "raw_excel_row_id": row.row_id, "run_id": ctx.run_id,
        }
    )
    well_ids[normalized] = well_id
    return well_id


def _load_well_ids(conn: Connection, *, pcr_run_id: int) -> dict[str, int]:
    return PCRMaterializationRepository(conn).well_ids(pcr_run_id)


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

    PCRMaterializationRepository(conn).ensure_rfu_wells(
        [
            {"pcr_run_id": pcr_run_id, "well": well, "well_normalized": normalized,
             "file_id": row.file_id, "raw_excel_row_id": row.row_id,
             "run_id": ctx.run_id}
            for normalized, well in missing.items()
        ]
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
    result_id = PCRMaterializationRepository(conn).upsert_cq(
        {"pcr_run_id": pcr_run_id, "pcr_well_id": well_id, "file_id": row.file_id,
         "raw_excel_row_id": row.row_id, "run_id": ctx.run_id, "target": target,
         "fluor": _optional_text(fields.get("fluor")),
         "sample_name": _optional_text(fields.get("sample")),
         "content": _optional_text(fields.get("content")), "cq": fields.get("cq"),
         "cq_mean": fields.get("cq_mean"), "cq_std_dev": fields.get("cq_std_dev")}
    )
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
    if sheet_kind == PCRSheetKinds.amplification_sybr:
        axis_kind, axis_value = PCRAxisKinds.cycle, fields[PCRAxisKinds.cycle]
        measurement_kind = PCRMeasurementKinds.amplification_rfu
    elif sheet_kind == PCRSheetKinds.melt_curve_sybr:
        axis_kind, axis_value = (
            PCRAxisKinds.temperature,
            fields[PCRAxisKinds.temperature],
        )
        measurement_kind = PCRMeasurementKinds.melt_curve_rfu
    else:
        axis_kind, axis_value = (
            PCRAxisKinds.temperature,
            fields[PCRAxisKinds.temperature],
        )
        measurement_kind = PCRMeasurementKinds.melt_curve_derivative

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
                "pcr_well_id": well_ids[normalized],
                "file_id": row.file_id,
                "raw_excel_row_id": row.row_id,
                "run_id": ctx.run_id,
                "measurement_kind": measurement_kind,
                "axis_kind": axis_kind,
                "axis_value": float(axis_value),
                "rfu": None if rfu is None else float(rfu),
            }
        )
    PCRMaterializationRepository(conn).upsert_measurements(measurements)
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
    PCRMaterializationRepository(conn).record_item(
        {"run_id": ctx.run_id, "source_table": "raw_excel_rows",
         "source_row_id": row.row_id, "entity_name": ctx.entity_name,
         "file_id": row.file_id, "materializer_name": materializer.materializer_name,
         "materializer_version": materializer.materializer_version, "status": status,
         "error": error, "target_json": json.dumps(target, ensure_ascii=False,
         separators=(",", ":")) if target is not None else None}
    )


def _clear_file_materialization(
    conn: Connection, *, file_id: int, materializer_name: str
) -> set[int]:
    return PCRMaterializationRepository(conn).clear_file(
        file_id=file_id, materializer_name=materializer_name
    )


def _refresh_attribute_flags(conn: Connection, *, pcr_run_id: int) -> int:
    return PCRMaterializationRepository(conn).refresh_attribute_flags(pcr_run_id)


def _refresh_run_metadata(conn: Connection, *, pcr_run_id: int) -> None:
    """Promote canonical run attributes into the denormalized run record."""
    PCRMaterializationRepository(conn).refresh_run_metadata(pcr_run_id)
def _refresh_well_metadata(conn: Connection, *, pcr_run_id: int) -> None:
    PCRMaterializationRepository(conn).refresh_well_metadata(pcr_run_id)


def _delete_orphan_wells(conn: Connection, *, pcr_run_id: int) -> None:
    PCRMaterializationRepository(conn).delete_orphan_wells(pcr_run_id)


def _delete_empty_runs(conn: Connection, *, pcr_run_ids: set[int]) -> None:
    if not pcr_run_ids:
        return
    PCRMaterializationRepository(conn).delete_empty_runs(pcr_run_ids)


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
