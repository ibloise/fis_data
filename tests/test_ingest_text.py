from __future__ import annotations

import pytest
from sqlalchemy import text

from fis_data.db import DBSettings, get_engine, sqlite_url_for_path
from fis_data.etl.control import RunStatus, ensure_source, finish_run, start_run
from fis_data.etl.file_registry import register_file
from fis_data.etl.ingest_text import ingest_text_file
from fis_data.schema import create_schema


def test_ingest_text_file_preserves_raw_lines(tmp_path) -> None:
    db_path = tmp_path / "fis.sqlite"
    source_path = tmp_path / "source.txt"
    source_path.write_text("nhc|name|value\n1|Ana|ok\n", encoding="utf-8")

    engine = get_engine(DBSettings(url=sqlite_url_for_path(db_path)))
    create_schema(engine)

    source_id = ensure_source(engine, source_name="TEST", source_type="file")
    run_id = start_run(engine, pipeline_name="ingest:text:TEST")
    file_id = register_file(
        engine,
        source_id=source_id,
        source_name="TEST",
        storage_path=str(source_path),
        file_format="text",
    )

    stats = ingest_text_file(
        engine,
        source_id=source_id,
        entity_name="TEST_ENTITY",
        run_id=run_id,
        file_id=file_id,
        path=source_path,
    )
    finish_run(engine, run_id=run_id, status=RunStatus.SUCCESS)

    assert stats == {"inserted_lines": 2, "total_lines": 2}

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT line_no, raw_line, is_header, parse_status
                FROM raw_text_lines
                ORDER BY line_no
                """
            )
        ).fetchall()

    assert rows == [
        (1, "nhc|name|value", True, "RAW_ONLY"),
        (2, "1|Ana|ok", False, "RAW_ONLY"),
    ]


def test_ingest_text_file_rejects_empty_files(tmp_path) -> None:
    db_path = tmp_path / "fis.sqlite"
    source_path = tmp_path / "empty.txt"
    source_path.write_bytes(b"")

    engine = get_engine(DBSettings(url=sqlite_url_for_path(db_path)))
    create_schema(engine)

    with pytest.raises(ValueError, match="empty"):
        ingest_text_file(
            engine,
            source_id=1,
            entity_name="TEST_ENTITY",
            run_id=1,
            file_id=1,
            path=source_path,
        )
