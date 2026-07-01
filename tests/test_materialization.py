from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from sqlalchemy import create_engine, insert, text, update

from fis_data.cli import cli
from fis_data.etl.materialization import MaterializationRepository
from fis_data.schema import (
    create_schema,
    ctl_etl_run,
    ctl_file_registry,
    ctl_source,
    raw_excel_rows,
)


def _payload(sheet_kind: str, fields: dict[str, object]) -> str:
    return json.dumps(
        {
            "entity": "pcr",
            "file_kind": "test",
            "sheet_kind": sheet_kind,
            "fields": fields,
            "source": {},
        }
    )


def _seed_file(
    engine,
    *,
    source_id: int,
    index: int,
    rows: list[tuple[str, str | None, str]],
) -> tuple[int, list[int]]:
    with engine.begin() as conn:
        run_id = int(
            conn.execute(
                insert(ctl_etl_run).values(
                    pipeline_name="test-seed", status="SUCCESS"
                )
            ).inserted_primary_key[0]
        )
        file_id = int(
            conn.execute(
                insert(ctl_file_registry).values(
                    source_id=source_id,
                    source_name="PCR",
                    storage_path=f"run-{index}.xlsx",
                    file_format="excel",
                    sha256=f"{index:064x}",
                )
            ).inserted_primary_key[0]
        )
        row_ids = []
        for row_no, (sheet_name, payload_json, status) in enumerate(rows, start=1):
            row_ids.append(
                int(
                    conn.execute(
                        insert(raw_excel_rows).values(
                            source_id=source_id,
                            run_id=run_id,
                            file_id=file_id,
                            entity_name="PCR",
                            sheet_name=sheet_name,
                            row_no=row_no,
                            raw_values_json="[]",
                            row_sha256=f"{index:032x}{row_no:032x}",
                            parse_status=status,
                            parse_error=(
                                "bad source row" if status == "PARSE_ERROR" else None
                            ),
                            payload_json=payload_json,
                        )
                    ).inserted_primary_key[0]
                )
            )
    return file_id, row_ids


def _database(tmp_path: Path):
    db_path = tmp_path / "materialization.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    create_schema(engine)
    with engine.begin() as conn:
        source_id = int(
            conn.execute(
                insert(ctl_source).values(source_name="PCR", source_type="file")
            ).inserted_primary_key[0]
        )
    return db_path, engine, source_id


def test_materialize_pcr_groups_profiles_and_keeps_lineage(tmp_path) -> None:
    db_path, engine, source_id = _database(tmp_path)
    cq_file, _ = _seed_file(
        engine,
        source_id=source_id,
        index=1,
        rows=[
            (
                "Run Information",
                _payload(
                    "run_information",
                    {"key": "File Name", "value": "1ªPCR_03042023.pcrd"},
                ),
                "PARSED_OK",
            ),
            (
                "Run Information",
                _payload("run_information", {"key": "Instrument", "value": "QS7"}),
                "PARSED_OK",
            ),
            (
                "Cq",
                _payload(
                    "cq_results",
                    {
                        "well": "A01",
                        "fluor": "FAM",
                        "target": "N1",
                        "content": "Unkn",
                        "sample": "S1",
                        "cq": 23.4,
                        "cq_mean": 23.5,
                        "cq_std_dev": 0.1,
                    },
                ),
                "PARSED_OK",
            ),
            ("Cq", None, "PARSE_ERROR"),
        ],
    )
    amplification_file, _ = _seed_file(
        engine,
        source_id=source_id,
        index=2,
        rows=[
            (
                "Run Information",
                _payload(
                    "run_information",
                    {"key": "File Name", "value": "1ªPCR_03042023.pcrd"},
                ),
                "PARSED_OK",
            ),
            (
                "Run Information",
                _payload("run_information", {"key": "Instrument", "value": "QS8"}),
                "PARSED_OK",
            ),
            (
                "SYBR",
                _payload(
                    "amplification_sybr",
                    {"cycle": 1, "rfu_by_well": {"A1": 10.5, "A2": 20}},
                ),
                "PARSED_OK",
            ),
        ],
    )
    melt_file, _ = _seed_file(
        engine,
        source_id=source_id,
        index=3,
        rows=[
            (
                "Run Information",
                _payload(
                    "run_information",
                    {"key": "File Name", "value": "1ªPCR_03042023.pcrd"},
                ),
                "PARSED_OK",
            ),
            (
                "Run Information",
                _payload("run_information", {"key": "Instrument", "value": "QS7"}),
                "PARSED_OK",
            ),
            (
                "SYBR",
                _payload(
                    "melt_curve_sybr",
                    {"temperature": 65.0, "rfu_by_well": {"A1": 100, "A2": 200}},
                ),
                "PARSED_OK",
            ),
        ],
    )

    result = CliRunner().invoke(
        cli, ["materialize", "--db-path", str(db_path), "--entity", "pcr"]
    )

    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert [item["file_id"] for item in output] == [
        cq_file,
        amplification_file,
        melt_file,
    ]
    assert output[0]["status"] == "LOADED_WITH_ERRORS"
    assert output[0]["source_errors"] == 1
    assert "Materialization complete" in result.stderr

    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM pcr_run")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM pcr_run_file")).scalar_one() == 3
        assert (
            conn.execute(text("SELECT COUNT(*) FROM pcr_cq_result")).scalar_one()
            == 1
        )
        assert (
            conn.execute(text("SELECT COUNT(*) FROM pcr_rfu_measurement")).scalar_one()
            == 4
        )
        wells = conn.execute(
            text("SELECT well, sample_name FROM pcr_well ORDER BY well")
        ).all()
        instruments = conn.execute(
            text(
                """
                SELECT file_id, is_canonical, has_conflict
                FROM pcr_run_attribute
                WHERE attribute_key_normalized = 'instrument'
                ORDER BY file_id
                """
            )
        ).all()
        loaded_items = conn.execute(
            text("SELECT COUNT(*) FROM ctl_materialization_item WHERE status='LOADED'")
        ).scalar_one()

    assert wells == [("A01", "S1"), ("A02", None)]
    assert instruments == [
        (cq_file, 1, 1),
        (amplification_file, 0, 1),
        (melt_file, 0, 1),
    ]
    assert loaded_items == 9

    repeated = CliRunner().invoke(
        cli, ["materialize", "--db-path", str(db_path), "--entity", "PCR"]
    )
    assert repeated.exit_code == 0
    assert json.loads(repeated.stdout) == []


def test_materialize_pcr_rejects_file_without_file_name(tmp_path) -> None:
    db_path, engine, source_id = _database(tmp_path)
    file_id, row_ids = _seed_file(
        engine,
        source_id=source_id,
        index=1,
        rows=[
            (
                "Cq",
                _payload(
                    "cq_results",
                    {
                        "well": "A1",
                        "target": "N1",
                        "sample": "S1",
                        "cq": 20,
                    },
                ),
                "PARSED_OK",
            )
        ],
    )

    result = CliRunner().invoke(
        cli,
        [
            "materialize",
            "--db-path",
            str(db_path),
            "--entity",
            "pcr",
            "--file-id",
            str(file_id),
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)[0]["status"] == "LOAD_ERROR"
    with engine.begin() as conn:
        item = conn.execute(
            text(
                """
                SELECT status, error FROM ctl_materialization_item
                WHERE source_row_id = :row_id
                """
            ),
            {"row_id": row_ids[0]},
        ).one()
        assert item[0] == "LOAD_ERROR"
        assert "File Name" in item[1]
        assert conn.execute(text("SELECT COUNT(*) FROM pcr_run")).scalar_one() == 0
        conn.execute(
            text(
                """
                UPDATE ctl_materialization_item
                SET materializer_version = '0.1'
                WHERE source_row_id = :row_id
                """
            ),
            {"row_id": row_ids[0]},
        )

    repo = MaterializationRepository(engine)
    assert not repo.has_outdated_items(
        file_id=file_id,
        materializer_name="pcr-domain",
        materializer_version="0.2",
    )


def test_materialize_pcr_reprocess_replaces_stale_measurements(tmp_path) -> None:
    db_path, engine, source_id = _database(tmp_path)
    file_id, row_ids = _seed_file(
        engine,
        source_id=source_id,
        index=1,
        rows=[
            (
                "Run Information",
                _payload(
                    "run_information",
                    {"key": "File Name", "value": "1ªPCR_03042023.pcrd"},
                ),
                "PARSED_OK",
            ),
            (
                "SYBR",
                _payload(
                    "amplification_sybr",
                    {"cycle": 1, "rfu_by_well": {"A1": 10, "A2": 20}},
                ),
                "PARSED_OK",
            ),
        ],
    )
    args = [
        "materialize",
        "--db-path",
        str(db_path),
        "--entity",
        "pcr",
        "--file-id",
        str(file_id),
    ]
    first = CliRunner().invoke(cli, args)
    assert first.exit_code == 0, first.output

    with engine.begin() as conn:
        conn.execute(
            update(raw_excel_rows)
            .where(raw_excel_rows.c.row_id == row_ids[1])
            .values(
                payload_json=_payload(
                    "amplification_sybr",
                    {"cycle": 2, "rfu_by_well": {"A1": 99}},
                )
            )
        )

    reprocessed = CliRunner().invoke(
        cli, [*args, "--reprocess-all", "--batch-size", "1"]
    )
    assert reprocessed.exit_code == 0, reprocessed.output
    with engine.begin() as conn:
        measurements = conn.execute(
            text(
                """
                SELECT well.well, measurement.axis_value, measurement.rfu
                FROM pcr_rfu_measurement AS measurement
                JOIN pcr_well AS well ON well.pcr_well_id = measurement.pcr_well_id
                """
            )
        ).all()
        well_count = conn.execute(text("SELECT COUNT(*) FROM pcr_well")).scalar_one()

    assert measurements == [("A01", 2.0, 99.0)]
    assert well_count == 1
