"""SQLAlchemy Core persistence boundary for PCR materialization."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, exists, func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection

from ...schema import (
    ctl_materialization_item,
    pcr_cq_result,
    pcr_rfu_measurement,
    pcr_run,
    pcr_run_attribute,
    pcr_run_file,
    pcr_well,
)


class PCRMaterializationRepository:
    """Persist PCR materialized facts without domain parsing rules."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def ensure_run(self, *, source_id: int, run_name: str, normalized: str) -> int:
        self.conn.execute(
            insert(pcr_run)
            .values(source_id=source_id, run_name=run_name, run_name_normalized=normalized)
            .on_conflict_do_nothing(index_elements=["source_id", "run_name_normalized"])
        )
        return int(
            self.conn.execute(
                select(pcr_run.c.pcr_run_id).where(
                    pcr_run.c.source_id == source_id,
                    pcr_run.c.run_name_normalized == normalized,
                )
            ).scalar_one()
        )

    def link_run_file(self, *, pcr_run_id: int, file_id: int, run_id: int) -> None:
        self.conn.execute(
            insert(pcr_run_file)
            .values(pcr_run_id=pcr_run_id, file_id=file_id, run_id=run_id)
            .on_conflict_do_update(
                index_elements=["file_id"],
                set_={"pcr_run_id": pcr_run_id, "run_id": run_id},
            )
        )

    def upsert_attribute(self, values: dict[str, Any]) -> int:
        self.conn.execute(
            insert(pcr_run_attribute)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["file_id", "attribute_key_normalized"],
                set_={key: values[key] for key in values if key != "file_id"},
            )
        )
        return int(
            self.conn.execute(
                select(pcr_run_attribute.c.pcr_run_attribute_id).where(
                    pcr_run_attribute.c.file_id == values["file_id"],
                    pcr_run_attribute.c.attribute_key_normalized
                    == values["attribute_key_normalized"],
                )
            ).scalar_one()
        )

    def ensure_well(self, values: dict[str, Any]) -> int:
        update_values = {
            "well": values["well"],
            "sample_name": func.coalesce(
                insert(pcr_well).excluded.sample_name, pcr_well.c.sample_name
            ),
            "content": func.coalesce(insert(pcr_well).excluded.content, pcr_well.c.content),
            "file_id": case(
                (insert(pcr_well).excluded.sample_name.is_not(None), insert(pcr_well).excluded.file_id),
                else_=pcr_well.c.file_id,
            ),
            "raw_excel_row_id": case(
                (insert(pcr_well).excluded.sample_name.is_not(None), insert(pcr_well).excluded.raw_excel_row_id),
                else_=pcr_well.c.raw_excel_row_id,
            ),
            "run_id": values["run_id"],
        }
        self.conn.execute(
            insert(pcr_well)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["pcr_run_id", "well_normalized"], set_=update_values
            )
        )
        return int(
            self.conn.execute(
                select(pcr_well.c.pcr_well_id).where(
                    pcr_well.c.pcr_run_id == values["pcr_run_id"],
                    pcr_well.c.well_normalized == values["well_normalized"],
                )
            ).scalar_one()
        )

    def well_ids(self, pcr_run_id: int) -> dict[str, int]:
        return {
            str(row.well_normalized): int(row.pcr_well_id)
            for row in self.conn.execute(
                select(pcr_well.c.well_normalized, pcr_well.c.pcr_well_id).where(
                    pcr_well.c.pcr_run_id == pcr_run_id
                )
            )
        }

    def ensure_rfu_wells(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            self.conn.execute(
                insert(pcr_well)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["pcr_run_id", "well_normalized"])
            )

    def upsert_cq(self, values: dict[str, Any]) -> int:
        self.conn.execute(
            insert(pcr_cq_result)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["raw_excel_row_id"],
                set_={key: values[key] for key in values if key != "file_id"},
            )
        )
        return int(
            self.conn.execute(
                select(pcr_cq_result.c.pcr_cq_result_id).where(
                    pcr_cq_result.c.raw_excel_row_id == values["raw_excel_row_id"]
                )
            ).scalar_one()
        )

    def upsert_measurements(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            self.conn.execute(
                insert(pcr_rfu_measurement)
                .values(rows)
                .on_conflict_do_update(
                    index_elements=["raw_excel_row_id", "pcr_well_id"],
                    set_={key: getattr(insert(pcr_rfu_measurement).excluded, key) for key in (
                        "pcr_run_id", "run_id", "measurement_kind", "axis_kind", "axis_value", "rfu"
                    )},
                )
            )

    def record_item(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            insert(ctl_materialization_item)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["source_table", "source_row_id", "materializer_name"],
                set_={
                    "run_id": values["run_id"], "materializer_version": values["materializer_version"],
                    "status": values["status"], "error": values["error"],
                    "target_json": values["target_json"], "updated_at": func.current_timestamp(),
                },
            )
        )

    def clear_file(self, *, file_id: int, materializer_name: str) -> set[int]:
        run_ids = set(self.conn.execute(select(pcr_run_file.c.pcr_run_id).where(pcr_run_file.c.file_id == file_id)).scalars())
        for table in (pcr_rfu_measurement, pcr_cq_result, pcr_run_attribute, pcr_run_file):
            self.conn.execute(delete(table).where(table.c.file_id == file_id))
        self.conn.execute(delete(ctl_materialization_item).where(ctl_materialization_item.c.file_id == file_id, ctl_materialization_item.c.materializer_name == materializer_name))
        return {int(run_id) for run_id in run_ids}

    def refresh_attribute_flags(self, pcr_run_id: int) -> int:
        rows = self.conn.execute(select(pcr_run_attribute).where(pcr_run_attribute.c.pcr_run_id == pcr_run_id)).mappings()
        grouped: dict[str, list[Any]] = defaultdict(list)
        for row in rows: grouped[str(row["attribute_key_normalized"])].append(row)
        conflicts = 0
        for candidates in grouped.values():
            canonical = min(candidates, key=lambda item: item["file_id"])["pcr_run_attribute_id"]
            conflict = len({item["value_json"] or "null" for item in candidates}) > 1
            conflicts += int(conflict)
            for item in candidates:
                self.conn.execute(update(pcr_run_attribute).where(pcr_run_attribute.c.pcr_run_attribute_id == item["pcr_run_attribute_id"]).values(is_canonical=item["pcr_run_attribute_id"] == canonical, has_conflict=conflict))
        return conflicts

    def refresh_run_metadata(self, pcr_run_id: int) -> None:
        rows = self.conn.execute(select(pcr_run_attribute.c.attribute_key_normalized, pcr_run_attribute.c.value_json).where(pcr_run_attribute.c.pcr_run_id == pcr_run_id, pcr_run_attribute.c.is_canonical.is_(True))).all()
        attributes = {str(key): json.loads(value) if value is not None else None for key, value in rows}
        values = {key: attributes.get(source) for key, source in {
            "created_by_user":"created_by_user", "base_serial_number":"base_serial_number", "optical_head_serial_number":"optical_head_serial_number", "cfx_maestro_version":"cfx_maestro_version", "protocol_file_name":"protocol_file_name", "plate_setup_file_name":"plate_setup_file_name", "sample_vol":"sample_vol", "lid_temp":"lid_temp", "notes":"notes", "run_metadata_id":"id", "source_file_name":"file_name"}.items()}
        values["run_started"] = _sqlite_datetime(attributes.get("run_started"))
        values["run_ended"] = _sqlite_datetime(attributes.get("run_ended"))
        self.conn.execute(update(pcr_run).where(pcr_run.c.pcr_run_id == pcr_run_id).values(**values))

    def refresh_well_metadata(self, pcr_run_id: int) -> None:
        wells = self.conn.execute(select(pcr_well.c.pcr_well_id).where(pcr_well.c.pcr_run_id == pcr_run_id)).scalars()
        for well_id in wells:
            row = self.conn.execute(select(pcr_cq_result.c.sample_name, pcr_cq_result.c.content).where(pcr_cq_result.c.pcr_well_id == well_id).order_by(pcr_cq_result.c.file_id, pcr_cq_result.c.raw_excel_row_id).limit(1)).first()
            values = (
                {"sample_name": row.sample_name, "content": row.content}
                if row is not None
                else {"sample_name": None, "content": None}
            )
            self.conn.execute(
                update(pcr_well)
                .where(pcr_well.c.pcr_well_id == well_id)
                .values(**values)
            )

    def delete_orphan_wells(self, pcr_run_id: int) -> None:
        self.conn.execute(delete(pcr_well).where(pcr_well.c.pcr_run_id == pcr_run_id, ~exists(select(1).where(pcr_cq_result.c.pcr_well_id == pcr_well.c.pcr_well_id)), ~exists(select(1).where(pcr_rfu_measurement.c.pcr_well_id == pcr_well.c.pcr_well_id))))

    def delete_empty_runs(self, run_ids: set[int]) -> None:
        if run_ids:
            self.conn.execute(delete(pcr_run).where(pcr_run.c.pcr_run_id.in_(run_ids), ~exists(select(1).where(pcr_run_file.c.pcr_run_id == pcr_run.c.pcr_run_id))))


def _parse_cfx_datetime(value: Any) -> datetime | None:
    if not value: return None
    try: return datetime.strptime(str(value), "%m/%d/%Y %H:%M:%S UTC")
    except ValueError: return None


def _sqlite_datetime(value: Any) -> Any:
    parsed = _parse_cfx_datetime(value)
    return func.datetime(parsed.strftime("%Y-%m-%d %H:%M:%S")) if parsed else None
