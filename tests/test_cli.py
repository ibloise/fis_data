from __future__ import annotations

import json

from click.testing import CliRunner
from sqlalchemy import create_engine, text

from fis_data.cli import _expand_input_paths, cli


def test_expand_input_paths_accepts_directories(tmp_path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.csv"
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    nested = nested_dir / "nested.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    nested.write_text("c", encoding="utf-8")

    paths = _expand_input_paths((str(tmp_path),))

    assert paths == [first, second]


def test_expand_input_paths_accepts_recursive_excel_directories(tmp_path) -> None:
    flat = tmp_path / "farmacia.xlsx"
    nested_dir = tmp_path / "PCR_RUN"
    nested_dir.mkdir()
    nested = nested_dir / "PCR_RUN - Quantification Cq Results.xlsx"
    temp = nested_dir / "~$PCR_RUN.xlsx"
    ignored = nested_dir / "notes.txt"

    flat.write_text("flat", encoding="utf-8")
    nested.write_text("nested", encoding="utf-8")
    temp.write_text("temp", encoding="utf-8")
    ignored.write_text("ignored", encoding="utf-8")

    paths = _expand_input_paths(
        (str(tmp_path),),
        recursive_dirs=True,
        allowed_extensions={".xlsx", ".xlsm", ".xltx", ".xltm"},
    )

    assert paths == [nested, flat]


def test_cli_ingest_text_creates_schema_and_rows(tmp_path) -> None:
    db_path = tmp_path / "fis.sqlite"
    source_path = tmp_path / "source.txt"
    source_path.write_text("a;b;c\n1;2;3\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "ingest-text",
            "--db-path",
            str(db_path),
            "--source-name",
            "TEST",
            "--entity",
            "LAB",
            str(source_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload[0]["ok"] is True
    assert payload[0]["inserted_lines"] == 2
    assert "Ingesting text files" in result.stderr
    assert "Text ingest complete: 1 succeeded" in result.stderr

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM raw_text_lines")).scalar_one()

    assert count == 2


def test_cli_ingest_excel_skips_empty_workbook(tmp_path) -> None:
    from openpyxl import Workbook

    db_path = tmp_path / "fis.sqlite"
    valid_path = tmp_path / "valid.xlsx"
    empty_path = tmp_path / "empty.xlsx"

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["a", "b"])
    worksheet.append([1, 2])
    workbook.save(valid_path)
    empty_path.write_bytes(b"")

    result = CliRunner().invoke(
        cli,
        [
            "ingest-excel",
            "--db-path",
            str(db_path),
            "--source-name",
            "TEST",
            "--entity",
            "LAB",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [item["ok"] for item in payload] == [False, True]
    assert payload[0]["skipped"] is True
    assert "empty" in payload[0]["error"]
    assert "Ingesting Excel workbooks" in result.stderr
    assert "Excel ingest complete: 1 succeeded, 1 skipped, 0 failed" in result.stderr

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM raw_excel_rows")).scalar_one()

    assert count == 2


def test_cli_ingest_excel_strict_fails_after_empty_workbook(tmp_path) -> None:
    db_path = tmp_path / "fis.sqlite"
    empty_path = tmp_path / "empty.xlsx"
    empty_path.write_bytes(b"")

    result = CliRunner().invoke(
        cli,
        [
            "ingest-excel",
            "--strict",
            "--db-path",
            str(db_path),
            "--source-name",
            "TEST",
            "--entity",
            "LAB",
            str(empty_path),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload[0]["skipped"] is True
    assert "Excel ingest complete: 0 succeeded, 1 skipped, 0 failed" in result.stderr


def test_cli_parse_microb_shows_progress(tmp_path) -> None:
    db_path = tmp_path / "fis.sqlite"
    source_path = tmp_path / "microb.txt"
    source_path.write_text("a|b|c|\n1|v2|v3|\n", encoding="utf-8")

    ingest_result = CliRunner().invoke(
        cli,
        [
            "ingest-text",
            "--db-path",
            str(db_path),
            "--source-name",
            "microb",
            "--entity",
            "microb",
            str(source_path),
        ],
    )

    assert ingest_result.exit_code == 0, ingest_result.output

    result = CliRunner().invoke(
        cli,
        [
            "parse-microb",
            "--db-path",
            str(db_path),
            "--entity",
            "microb",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload[0]["ok"] is True
    assert payload[0]["parsed_total"] == 1
    assert payload[0]["parsed_ok"] == 1
    assert "Parsing Microb files" in result.stderr
    assert "Microb parse complete: 1 succeeded, 0 failed" in result.stderr

    no_pending_result = CliRunner().invoke(
        cli,
        [
            "parse-microb",
            "--db-path",
            str(db_path),
            "--entity",
            "microb",
        ],
    )

    assert no_pending_result.exit_code == 0, no_pending_result.output
    assert json.loads(no_pending_result.stdout) == []

    reprocess_result = CliRunner().invoke(
        cli,
        [
            "parse-microb",
            "--reprocess-all",
            "--db-path",
            str(db_path),
            "--entity",
            "microb",
        ],
    )

    assert reprocess_result.exit_code == 0, reprocess_result.output
    reprocess_payload = json.loads(reprocess_result.stdout)
    assert reprocess_payload[0]["ok"] is True
    assert reprocess_payload[0]["parsed_total"] == 1
    assert reprocess_payload[0]["parsed_ok"] == 1
    assert "Parsing Microb files" in reprocess_result.stderr
    assert "Microb parse complete: 1 succeeded, 0 failed" in reprocess_result.stderr
