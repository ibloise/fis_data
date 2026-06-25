"""Command-line interface for FIS data ETL workflows."""

from __future__ import annotations

import glob
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from zipfile import is_zipfile

import click
from dotenv import load_dotenv
from sqlalchemy.engine import Engine

from .db import DBSettings, get_engine, sqlite_url_for_path
from .etl.contracts import JobContext
from .etl.control import RunStatus, ensure_source, finish_run, start_run
from .etl.excel_parse_job import ExcelParseJob
from .etl.file_registry import register_file
from .etl.ingest_excel import ingest_excel_file
from .etl.ingest_text import ingest_text_file
from .etl.microb_parse_job import MicrobParseJob
from .schema import create_schema


@click.group()
def cli() -> None:
    """FIS data management CLI."""

    load_dotenv()


def _engine_for_db_path(db_path: str | None) -> Engine:
    if db_path is None:
        return get_engine()

    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return get_engine(DBSettings(url=sqlite_url_for_path(path)))


def _expand_input_paths(
    inputs: tuple[str, ...],
    *,
    recursive_dirs: bool = False,
    allowed_extensions: set[str] | None = None,
) -> list[Path]:
    if not inputs:
        raise click.UsageError("Provide at least one input path or glob pattern.")

    paths: list[Path] = []
    seen: set[Path] = set()
    for path in _iter_input_files(
        inputs,
        recursive_dirs=recursive_dirs,
        allowed_extensions=allowed_extensions,
    ):
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            paths.append(path)

    return paths


def _iter_input_files(
    inputs: tuple[str, ...],
    *,
    recursive_dirs: bool,
    allowed_extensions: set[str] | None,
) -> Iterator[Path]:
    for raw_input in inputs:
        for path in _iter_input_matches(raw_input):
            yield from _iter_path_files(
                path,
                recursive_dirs=recursive_dirs,
                allowed_extensions=allowed_extensions,
            )


def _iter_input_matches(raw_input: str) -> Iterator[Path]:
    pattern = str(Path(raw_input).expanduser())
    matches = [Path(match) for match in sorted(glob.glob(pattern, recursive=True))]
    if matches:
        yield from matches
        return

    path = Path(raw_input).expanduser()
    if path.exists():
        yield path
        return

    raise click.BadParameter(
        f"No files matched input: {raw_input}",
        param_hint="inputs",
    )


def _iter_path_files(
    path: Path,
    *,
    recursive_dirs: bool,
    allowed_extensions: set[str] | None,
) -> Iterator[Path]:
    if not path.is_dir():
        _validate_input_file(path, allowed_extensions=allowed_extensions)
        yield path
        return

    iterator = path.rglob("*") if recursive_dirs else path.iterdir()
    directory_files = sorted(
        child
        for child in iterator
        if _is_accepted_file(child, allowed_extensions=allowed_extensions)
    )
    if not directory_files:
        raise click.BadParameter(
            f"Input directory has no files: {path}",
            param_hint="inputs",
        )

    yield from directory_files


def _validate_input_file(
    path: Path,
    *,
    allowed_extensions: set[str] | None,
) -> None:
    if not _is_accepted_file(path, allowed_extensions=allowed_extensions):
        raise click.BadParameter(
            f"Input is not an accepted file: {path}",
            param_hint="inputs",
        )


def _is_accepted_file(
    path: Path,
    *,
    allowed_extensions: set[str] | None,
) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith("~$"):
        return False
    if allowed_extensions is None:
        return True
    return path.suffix.lower() in allowed_extensions


def _excel_candidate_error(path: Path) -> str | None:
    if path.stat().st_size == 0:
        return f"Input Excel file is empty: {path}"
    if not is_zipfile(path):
        return f"Input Excel file is not a valid OpenXML workbook: {path}"
    return None


@cli.command("init-db")
@click.option(
    "--db-path",
    type=click.Path(dir_okay=False),
    help="SQLite database path.",
)
def init_db(db_path: str | None) -> None:
    """Create the SQLite schema."""

    engine = _engine_for_db_path(db_path)
    create_schema(engine)
    click.echo("Database schema initialized.")


def _ingest_file(
    *,
    db_path: str | None,
    input_path: Path,
    source_name: str,
    source_type: str,
    entity_name: str,
    file_format: str,
) -> dict[str, object]:
    engine = _engine_for_db_path(db_path)
    create_schema(engine)
    run_id = start_run(engine, pipeline_name=f"ingest:{file_format}:{entity_name}")

    try:
        source_id = ensure_source(
            engine,
            source_name=source_name,
            source_type=source_type,
        )
        file_id = register_file(
            engine,
            source_id=source_id,
            source_name=source_name,
            storage_path=str(input_path),
            file_format=file_format,
        )

        if file_format == "text":
            stats = ingest_text_file(
                engine,
                source_id=source_id,
                entity_name=entity_name,
                run_id=run_id,
                file_id=file_id,
                path=input_path,
            )
        elif file_format == "excel":
            stats = ingest_excel_file(
                engine,
                source_id=source_id,
                entity_name=entity_name,
                run_id=run_id,
                file_id=file_id,
                path=input_path,
            )
        else:
            raise ValueError(f"Unsupported file format: {file_format}")

        details = {"path": str(input_path), "file_id": file_id, **stats}
        finish_run(
            engine,
            run_id=run_id,
            status=RunStatus.SUCCESS,
            details_json=json.dumps(details, ensure_ascii=False),
        )
        return {"ok": True, "run_id": run_id, **details}
    except Exception as exc:
        finish_run(
            engine,
            run_id=run_id,
            status=RunStatus.FAILED,
            details_json=json.dumps(
                {"path": str(input_path), "error": str(exc)},
                ensure_ascii=False,
            ),
        )
        raise


@cli.command("ingest-text")
@click.option(
    "--db-path",
    type=click.Path(dir_okay=False),
    help="SQLite database path.",
)
@click.option("--source-name", required=True, help="Logical source name.")
@click.option("--entity", "entity_name", required=True, help="Logical entity name.")
@click.argument("inputs", nargs=-1)
def ingest_text(
    db_path: str | None,
    source_name: str,
    entity_name: str,
    inputs: tuple[str, ...],
) -> None:
    """Ingest text files into the raw layer."""

    input_paths = _expand_input_paths(inputs)
    results = []
    with click.progressbar(
        input_paths,
        label="Ingesting text files",
        show_pos=True,
        item_show_func=lambda path: str(path) if path is not None else "",
        file=sys.stderr,
    ) as progress:
        for input_path in progress:
            results.append(
                _ingest_file(
                    db_path=db_path,
                    input_path=input_path,
                    source_name=source_name,
                    source_type="file",
                    entity_name=entity_name,
                    file_format="text",
                )
            )

    inserted_lines = sum(int(result.get("inserted_lines", 0)) for result in results)
    total_lines = sum(int(result.get("total_lines", 0)) for result in results)
    click.echo(
        "Text ingest complete: "
        f"{len(results)} succeeded; "
        f"{inserted_lines} inserted lines from {total_lines} lines seen.",
        err=True,
    )
    click.echo(json.dumps(results, ensure_ascii=False, indent=2))


@cli.command("ingest-excel")
@click.option(
    "--db-path",
    type=click.Path(dir_okay=False),
    help="SQLite database path.",
)
@click.option("--source-name", required=True, help="Logical source name.")
@click.option("--entity", "entity_name", required=True, help="Logical entity name.")
@click.option(
    "--strict/--no-strict",
    default=False,
    show_default=True,
    help="Exit with an error when an invalid workbook is skipped.",
)
@click.argument("inputs", nargs=-1)
def ingest_excel(
    db_path: str | None,
    source_name: str,
    entity_name: str,
    strict: bool,
    inputs: tuple[str, ...],
) -> None:
    """Ingest Excel workbooks into the raw layer."""

    input_paths = _expand_input_paths(
        inputs,
        recursive_dirs=True,
        allowed_extensions={".xlsx", ".xlsm", ".xltx", ".xltm"},
    )
    results = []
    failed = False
    with click.progressbar(
        input_paths,
        label="Ingesting Excel workbooks",
        show_pos=True,
        item_show_func=lambda path: str(path) if path is not None else "",
        file=sys.stderr,
    ) as progress:
        for input_path in progress:
            candidate_error = _excel_candidate_error(input_path)
            if candidate_error is not None:
                failed = failed or strict
                results.append(
                    {
                        "ok": False,
                        "skipped": True,
                        "path": str(input_path),
                        "error": candidate_error,
                    }
                )
                continue

            try:
                results.append(
                    _ingest_file(
                        db_path=db_path,
                        input_path=input_path,
                        source_name=source_name,
                        source_type="file",
                        entity_name=entity_name,
                        file_format="excel",
                    )
                )
            except Exception as exc:
                failed = True
                results.append(
                    {
                        "ok": False,
                        "path": str(input_path),
                        "error": str(exc),
                    }
                )

    ok_count = sum(1 for result in results if result["ok"])
    skipped_count = sum(1 for result in results if result.get("skipped"))
    failed_count = sum(
        1 for result in results if not result["ok"] and not result.get("skipped")
    )
    inserted_rows = sum(
        int(result.get("inserted_rows", 0)) for result in results if result["ok"]
    )
    total_rows = sum(
        int(result.get("total_rows", 0)) for result in results if result["ok"]
    )
    click.echo(
        "Excel ingest complete: "
        f"{ok_count} succeeded, {skipped_count} skipped, {failed_count} failed; "
        f"{inserted_rows} inserted rows from {total_rows} non-empty rows seen.",
        err=True,
    )
    click.echo(json.dumps(results, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


@cli.command("parse-excel")
@click.option(
    "--db-path",
    type=click.Path(dir_okay=False),
    help="SQLite database path.",
)
@click.option("--entity", "entity_name", required=True, help="Excel entity name.")
@click.option(
    "--file-id",
    type=int,
    required=False,
    help="File ID to parse. If omitted, parses all candidate Excel files.",
)
@click.option(
    "--sheet-name",
    required=False,
    help="Sheet name to parse. If omitted, parses all candidate sheets.",
)
@click.option("--batch-size", type=int, default=1000, show_default=True)
@click.option(
    "--reprocess-all",
    is_flag=True,
    help="Reparse all Excel rows, including rows already parsed or skipped.",
)
def parse_excel(
    db_path: str | None,
    entity_name: str,
    file_id: int | None,
    sheet_name: str | None,
    batch_size: int,
    reprocess_all: bool,
) -> None:
    """Parse raw Excel rows into payload_json."""

    engine = _engine_for_db_path(db_path)
    create_schema(engine)
    run_id = start_run(engine, pipeline_name=f"parse:excel:{entity_name}")
    job = ExcelParseJob(engine)
    file_ids = job.list_candidate_file_ids(
        entity_name=entity_name,
        file_id=file_id,
        sheet_name=sheet_name,
        reprocess=reprocess_all,
    )

    if not file_ids:
        finish_run(
            engine,
            run_id=run_id,
            status=RunStatus.SUCCESS,
            details_json=json.dumps({"entity_name": entity_name, "files": 0}),
        )
        click.echo("[]")
        return

    results = []
    failed = False
    with click.progressbar(
        file_ids,
        label="Parsing Excel files",
        show_pos=True,
        item_show_func=(
            lambda pending_file_id: (
                f"file_id={pending_file_id}"
                if pending_file_id is not None
                else ""
            )
        ),
        file=sys.stderr,
    ) as progress:
        for pending_file_id in progress:
            result = job.run(
                ctx=JobContext(entity_name=entity_name, file_id=pending_file_id),
                run_id=run_id,
                sheet_name=sheet_name,
                batch_size=batch_size,
                reprocess=reprocess_all,
            )
            results.append(
                {
                    "file_id": pending_file_id,
                    "ok": result.ok,
                    "error": result.error,
                    **result.metrics,
                }
            )
            failed = failed or not result.ok

    ok_count = sum(1 for result in results if result["ok"])
    failed_count = len(results) - ok_count
    rows_seen = sum(int(result.get("rows_seen", 0)) for result in results)
    rows_parsed = sum(int(result.get("rows_parsed", 0)) for result in results)
    rows_error = sum(int(result.get("rows_error", 0)) for result in results)
    click.echo(
        "Excel parse complete: "
        f"{ok_count} succeeded, {failed_count} failed; "
        f"{rows_parsed}/{rows_seen} rows parsed, {rows_error} row errors.",
        err=True,
    )

    finish_run(
        engine,
        run_id=run_id,
        status=RunStatus.FAILED if failed else RunStatus.SUCCESS,
        details_json=json.dumps(
            {
                "entity_name": entity_name,
                "files": len(results),
                "ok": ok_count,
                "failed": failed_count,
                "rows_seen": rows_seen,
                "rows_parsed": rows_parsed,
                "rows_error": rows_error,
            },
            ensure_ascii=False,
        ),
    )
    click.echo(json.dumps(results, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


@cli.command("parse-microb")
@click.option(
    "--db-path",
    type=click.Path(dir_okay=False),
    help="SQLite database path.",
)
@click.option(
    "--entity",
    "entity_name",
    default="microb",
    show_default=True,
    help="Entity name used during raw ingestion.",
)
@click.option(
    "--file-id",
    type=int,
    required=False,
    help="File ID to parse. If omitted, parses all pending Microb files.",
)
@click.option("--batch-size", type=int, default=2000, show_default=True)
@click.option(
    "--reprocess-all",
    is_flag=True,
    help="Reparse all non-header rows, including rows already parsed.",
)
def parse_microb(
    db_path: str | None,
    entity_name: str,
    file_id: int | None,
    batch_size: int,
    reprocess_all: bool,
) -> None:
    """Parse pending Microb raw text rows into payload_json."""

    engine = _engine_for_db_path(db_path)
    create_schema(engine)
    job = MicrobParseJob(engine)
    file_ids = (
        [file_id]
        if file_id is not None
        else (
            job.list_file_ids(entity_name=entity_name)
            if reprocess_all
            else job.list_pending_file_ids(entity_name=entity_name)
        )
    )

    if not file_ids:
        click.echo("[]")
        return

    results = []
    failed = False
    with click.progressbar(
        file_ids,
        label="Parsing Microb files",
        show_pos=True,
        item_show_func=(
            lambda pending_file_id: (
                f"file_id={pending_file_id}"
                if pending_file_id is not None
                else ""
            )
        ),
        file=sys.stderr,
    ) as progress:
        for pending_file_id in progress:
            result = job.run(
                ctx=JobContext(entity_name=entity_name, file_id=pending_file_id),
                batch_size=batch_size,
                reprocess=reprocess_all,
            )
            results.append(
                {
                    "file_id": pending_file_id,
                    "ok": result.ok,
                    "error": result.error,
                    **result.metrics,
                }
            )
            failed = failed or not result.ok

    ok_count = sum(1 for result in results if result["ok"])
    failed_count = len(results) - ok_count
    parsed_total = sum(int(result.get("parsed_total", 0)) for result in results)
    parsed_ok = sum(int(result.get("parsed_ok", 0)) for result in results)
    parsed_error = sum(int(result.get("parsed_error", 0)) for result in results)
    click.echo(
        "Microb parse complete: "
        f"{ok_count} succeeded, {failed_count} failed; "
        f"{parsed_ok}/{parsed_total} rows parsed, {parsed_error} parse errors.",
        err=True,
    )

    click.echo(json.dumps(results, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)
