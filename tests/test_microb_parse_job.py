from __future__ import annotations

import json

from sqlalchemy import text

from fis_data.db import DBSettings, get_engine, sqlite_url_for_path
from fis_data.etl.contracts import JobContext
from fis_data.etl.control import ensure_source, start_run
from fis_data.etl.file_registry import register_file
from fis_data.etl.ingest_text import ingest_text_file
from fis_data.etl.microb_parse_job import MicrobParseJob
from fis_data.schema import create_schema


def test_microb_parse_job_updates_raw_text_payloads(tmp_path) -> None:
    db_path = tmp_path / "fis.sqlite"
    source_path = tmp_path / "microb.txt"
    source_path.write_text("a|b|c|\n1|v2|v3|\n", encoding="utf-8")

    engine = get_engine(DBSettings(url=sqlite_url_for_path(db_path)))
    create_schema(engine)
    source_id = ensure_source(engine, source_name="microb", source_type="file")
    run_id = start_run(engine, pipeline_name="ingest:text:microb")
    file_id = register_file(
        engine,
        source_id=source_id,
        source_name="microb",
        storage_path=str(source_path),
        file_format="text",
    )
    ingest_text_file(
        engine,
        source_id=source_id,
        entity_name="microb",
        run_id=run_id,
        file_id=file_id,
        path=source_path,
    )

    result = MicrobParseJob(engine).run(
        ctx=JobContext(entity_name="microb", file_id=file_id),
    )

    assert result.ok is True
    assert result.metrics["parsed_total"] == 1
    assert result.metrics["parsed_ok"] == 1

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT parse_status, payload_json
                FROM raw_text_lines
                WHERE is_header = 0
                """
            )
        ).one()

    assert row[0] == "PARSED_OK"
    assert json.loads(row[1]) == {"a": "1", "b": "v2", "c": "v3"}
