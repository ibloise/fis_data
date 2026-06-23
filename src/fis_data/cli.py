"""Command-line interface for FIS data ETL workflows."""

from __future__ import annotations

import glob
import json
from pathlib import Path
from zipfile import is_zipfile

import click
from dotenv import load_dotenv
from sqlalchemy.engine import Engine

from .db import DBSettings, get_engine, sqlite_url_for_path
from .etl.contracts import JobContext
from .etl.control import RunStatus, ensure_source, finish_run, start_run
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
    for raw_input in inputs:
        pattern = str(Path(raw_input).expanduser())
        matches = [Path(match) for match in sorted(glob.glob(pattern, recursive=True))]
        if not matches:
            path = Path(raw_input).expanduser()
            if path.exists():
                matches = [path]
            else:
                raise click.BadParameter(
                    f"No files matched input: {raw_input}",
                    param_hint="inputs",
                )

        expanded_matches: list[Path] = []
        for path in matches:
            if path.is_dir():
                iterator = path.rglob("*") if recursive_dirs else path.iterdir()
                directory_files = sorted(
                    child for child in iterator if _is_accepted_file(
                        child,
                        allowed_extensions=allowed_extensions,
                    )
                )
                if not directory_files:
                    raise click.BadParameter(
                        f"Input directory has no files: {path}",
                        param_hint="inputs",
                )
                expanded_matches.extend(directory_files)
                continue

            expanded_matches.append(path)

        for path in expanded_matches:
            if not _is_accepted_file(path, allowed_extensions=allowed_extensions):
                raise click.BadParameter(
                    f"Input is not an accepted file: {path}",
                    param_hint="inputs",
                )
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                paths.append(path)

    return paths


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

    results = [
        _ingest_file(
            db_path=db_path,
            input_path=input_path,
            source_name=source_name,
            source_type="file",
            entity_name=entity_name,
            file_format="text",
        )
        for input_path in _expand_input_paths(inputs)
    ]
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

    results = []
    failed = False
    for input_path in _expand_input_paths(
        inputs,
        recursive_dirs=True,
        allowed_extensions={".xlsx", ".xlsm", ".xltx", ".xltm"},
    ):
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
def parse_microb(
    db_path: str | None,
    entity_name: str,
    file_id: int | None,
    batch_size: int,
) -> None:
    """Parse pending Microb raw text rows into payload_json."""

    engine = _engine_for_db_path(db_path)
    create_schema(engine)
    job = MicrobParseJob(engine)
    file_ids = (
        [file_id]
        if file_id is not None
        else job.list_pending_file_ids(entity_name=entity_name)
    )

    if not file_ids:
        click.echo("[]")
        return

    results = []
    failed = False
    for pending_file_id in file_ids:
        result = job.run(
            ctx=JobContext(entity_name=entity_name, file_id=pending_file_id),
            batch_size=batch_size,
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

    click.echo(json.dumps(results, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)
