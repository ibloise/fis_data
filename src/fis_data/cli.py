"""Command-line interface for FIS data ETL workflows."""

from __future__ import annotations

import glob
import json
import sys
from collections.abc import Iterator
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zipfile import is_zipfile

import click
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.engine import Engine

from .db import get_engine, get_db_settings
from .etl.contracts import JobContext
from .etl.control import RunStatus, ensure_source, finish_run, start_run
from .etl.excel_parse_job import ExcelParseJob
from .etl.exports import export_downstream, format_names
from .etl.file_registry import register_file
from .etl.ingest_excel import ingest_excel_file
from .etl.ingest_text import ingest_text_file
from .etl.materialization import MaterializationJob
from .etl.materialization_registry import get_entity_materializer
from .etl.microb.catalogs import MicrobCatalogRepository
from .etl.microb.normalization import MicrobNormalizer
from .etl.microb.parse_job import MicrobParseJob
from .etl.microb.staging_job import MicrobStagingJob
from .etl.pcr.analytics import PCRAnalyticsJob
from .etl.pcr.analytics.sil_linking import SILLinkingPolicy
from .etl.pcr.replacement import PCRFileReplacement, supersede_pcr_files
from .etl.pcr.review.cli import pcr_review
from .schema import create_schema
from .schema import stg_microb_technique
from .web import create_app


@click.group()
def cli() -> None:
    """FIS data management CLI."""

    load_dotenv()


cli.add_command(pcr_review)


def _engine_for_db_url(db_url: str | None) -> Engine:
    return get_engine(get_db_settings(db_url))


@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
@click.option("--db-url", help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).")
def serve(host: str, port: int, db_url: str | None) -> None:
    """Serve the PCR review frontend and read-only API."""

    import uvicorn

    uvicorn.run(create_app(database_url=db_url), host=host, port=port)


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
    "--db-url", "db_path",
    help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).",
)
def init_db(db_path: str | None) -> None:
    """Create the PostgreSQL schema."""

    engine = _engine_for_db_url(db_path)
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
    engine = _engine_for_db_url(db_path)
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
    "--db-url", "db_path",
    help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).",
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
    "--db-url", "db_path",
    help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).",
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
    "--db-url", "db_path",
    help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).",
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

    engine = _engine_for_db_url(db_path)
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
                f"file_id={pending_file_id}" if pending_file_id is not None else ""
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


@cli.command("materialize")
@click.option(
    "--db-url", "db_path",
    help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).",
)
@click.option("--entity", "entity_name", required=True, help="Entity to materialize.")
@click.option(
    "--file-id",
    type=int,
    required=False,
    help="File ID to materialize. If omitted, processes all pending files.",
)
@click.option(
    "--batch-size",
    type=click.IntRange(min=1),
    default=1000,
    show_default=True,
)
@click.option(
    "--reprocess-all",
    is_flag=True,
    help="Replace domain records for selected files from all parsed payloads.",
)
def materialize(
    db_path: str | None,
    entity_name: str,
    file_id: int | None,
    batch_size: int,
    reprocess_all: bool,
) -> None:
    """Materialize parsed payloads into normalized domain tables."""

    materializer = get_entity_materializer(entity_name)
    if materializer is None:
        raise click.BadParameter(
            f"No materializer registered for entity: {entity_name}",
            param_hint="--entity",
        )

    engine = _engine_for_db_url(db_path)
    create_schema(engine)
    run_id = start_run(engine, pipeline_name=f"materialize:{entity_name.casefold()}")
    job = MaterializationJob(engine, materializer)
    file_ids = job.list_candidate_file_ids(
        entity_name=entity_name,
        file_id=file_id,
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
        label=f"Materializing {entity_name}",
        show_pos=True,
        item_show_func=(
            lambda candidate_file_id: (
                f"file_id={candidate_file_id}" if candidate_file_id is not None else ""
            )
        ),
        file=sys.stderr,
    ) as progress:
        for candidate_file_id in progress:
            result = job.run(
                ctx=JobContext(
                    entity_name=entity_name,
                    file_id=candidate_file_id,
                    run_id=run_id,
                ),
                reprocess=reprocess_all,
                batch_size=batch_size,
            )
            item = {
                "file_id": candidate_file_id,
                "ok": result.ok,
                "error": result.error,
                **result.metrics,
            }
            results.append(item)
            failed = failed or not result.ok or item.get("status") == "LOAD_ERROR"

    rows_seen = sum(int(result.get("rows_seen", 0)) for result in results)
    rows_loaded = sum(int(result.get("rows_loaded", 0)) for result in results)
    rows_error = sum(int(result.get("rows_error", 0)) for result in results)
    source_errors = sum(int(result.get("source_errors", 0)) for result in results)
    warnings = sum(int(result.get("warnings", 0)) for result in results)
    click.echo(
        "Materialization complete: "
        f"{rows_loaded}/{rows_seen} rows loaded, {rows_error} load errors, "
        f"{source_errors} source errors, "
        f"{warnings} metadata conflicts.",
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
                "rows_seen": rows_seen,
                "rows_loaded": rows_loaded,
                "rows_error": rows_error,
                "source_errors": source_errors,
                "warnings": warnings,
            },
            ensure_ascii=False,
        ),
    )
    click.echo(json.dumps(results, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


@cli.command("supersede-pcr-files")
@click.option(
    "--db-url", "db_path",
    help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).",
)
@click.option(
    "--old-file-id", type=int, multiple=True, required=True,
    help="Previously materialized PCR file ID to retire; repeat in input order.",
)
@click.option(
    "--new-file-id", type=int, multiple=True, required=True,
    help="Replacement PCR file ID already parsed and materialized; repeat in input order.",
)
@click.option("--reason", help="Optional audit reason for this file replacement.")
def supersede_pcr_files_command(
    db_path: str | None,
    old_file_id: tuple[int, ...],
    new_file_id: tuple[int, ...],
    reason: str | None,
) -> None:
    """Replace materialized PCR source files while preserving raw audit rows.

    The replacement files must already have completed ingestion, parsing, and
    materialization.  This clears the derived PCR model; run derive-pcr with
    --rebuild after a successful replacement.
    """

    if len(old_file_id) != len(new_file_id):
        raise click.UsageError(
            "Provide exactly one --new-file-id for every --old-file-id."
        )
    engine = _engine_for_db_url(db_path)
    create_schema(engine)
    replacements = tuple(
        PCRFileReplacement(superseded_file_id=old, replacement_file_id=new)
        for old, new in zip(old_file_id, new_file_id, strict=True)
    )
    try:
        completed = supersede_pcr_files(
            engine, replacements=replacements, reason=reason
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        json.dumps(
            {
                "superseded": [
                    {
                        "old_file_id": item.superseded_file_id,
                        "new_file_id": item.replacement_file_id,
                    }
                    for item in completed
                ],
                "next_step": "Run derive-pcr --rebuild before using PCR views or exports.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@cli.command("derive-pcr")
@click.option(
    "--db-url", "db_path",
    help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).",
)
@click.option(
    "--algorithm-version",
    required=True,
    help="Version label for the melting interpretation algorithm.",
)
@click.option(
    "--rebuild",
    is_flag=True,
    help="Delete and rebuild all derived PCR analytical data.",
)
@click.option(
    "--ignore-overrides",
    is_flag=True,
    help="Run the derivation without active PCR review overrides.",
)
@click.option(
    "--experimental-before",
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]),
    help="Mark runs before this timestamp EXPERIMENTAL during this derivation. "
    "Defaults to PCR_EXPERIMENTAL_BEFORE when configured.",
)
@click.option(
    "--previous-sample-max-days", type=click.IntRange(min=0), default=90,
    show_default=True, help="Maximum days for retrospective PCR--SIL evidence.",
)
@click.option(
    "--previous-sample-max-negatives", type=click.IntRange(min=0), default=3,
    show_default=True, help="Maximum intervening negative cultures in PCR--SIL linking.",
)
def derive_pcr(
    db_path: str | None,
    algorithm_version: str,
    rebuild: bool,
    ignore_overrides: bool,
    experimental_before: datetime | None,
    previous_sample_max_days: int,
    previous_sample_max_negatives: int,
) -> None:
    """Build the versioned PCR analytical and QC model."""

    engine = _engine_for_db_url(db_path)
    click.echo("Preparing PostgreSQL schema and indexes...", err=True)
    create_schema(engine)
    run_id = start_run(engine, pipeline_name="derive:pcr")
    try:
        with click.progressbar(
            length=11,
            label="Deriving PCR: preparing",
            show_pos=True,
            show_eta=False,
            file=sys.stderr,
        ) as progress:
            def on_stage(label: str, completed: bool) -> None:
                progress.label = f"Deriving PCR: {label}"
                if completed:
                    progress.update(1)

            stats = PCRAnalyticsJob(engine).run(
                algorithm_version=algorithm_version,
                rebuild=rebuild,
                ignore_overrides=ignore_overrides,
                experimental_before=experimental_before,
                sil_linking_policy=SILLinkingPolicy(
                    previous_sample_max_days=previous_sample_max_days,
                    previous_sample_max_negatives=previous_sample_max_negatives,
                ),
                on_stage=on_stage,
            )
    except Exception as exc:
        finish_run(
            engine,
            run_id=run_id,
            status=RunStatus.FAILED,
            details_json=json.dumps({"error": str(exc)}, ensure_ascii=False),
        )
        raise click.ClickException(str(exc)) from exc

    details = asdict(stats)
    finish_run(
        engine,
        run_id=run_id,
        status=RunStatus.SUCCESS,
        details_json=json.dumps(details, ensure_ascii=False),
    )
    click.echo(
        "PCR derivation complete: "
        f"{stats.interpretations} interpretations, "
        f"{stats.target_qc_rows} target QC rows, "
        f"{stats.episodes} episodes, {stats.sample_results} promoted sample results, "
        f"{stats.legacy_screening_blocks} legacy screening blocks, "
        f"{stats.sil_culture_summaries} SIL culture summaries, "
        f"{stats.review_items} review items.",
        err=True,
    )
    click.echo(json.dumps(details, ensure_ascii=False, indent=2))


@cli.command("export-downstream")
@click.option(
    "--db-url", "db_path",
    help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).",
)
@click.option(
    "--format", "format_name", type=click.Choice(format_names()),
    default="analytical-core-tables", show_default=True,
    help="Registered downstream export frame.",
)
@click.option(
    "--output", "output_path", type=click.Path(dir_okay=False, path_type=Path),
    required=True, help="Destination XLSX file.",
)
def export_downstream_command(
    db_path: str | None, format_name: str, output_path: Path
) -> None:
    """Export one read-only downstream analytical frame to XLSX."""

    engine = _engine_for_db_url(db_path)
    counts = export_downstream(engine, format_name=format_name, output_path=output_path)
    click.echo(json.dumps({"format": format_name, "output": str(output_path), "rows": counts}, ensure_ascii=False, indent=2))


@cli.command("materialize-microb")
@click.option("--db-url", "db_path", help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).")
@click.option("--entity", "entity_name", default="microb", show_default=True)
@click.option("--file-id", type=int, help="Materialize only one parsed Microb file.")
def materialize_microb(
    db_path: str | None, entity_name: str, file_id: int | None
) -> None:
    """Materialize parsed Microb text payloads into staging tables."""

    engine = _engine_for_db_url(db_path)
    create_schema(engine)
    job = MicrobStagingJob(engine)
    file_ids = [file_id] if file_id is not None else job.repository.parsed_file_ids(
        entity_name=entity_name
    )
    if not file_ids:
        click.echo(
            "No parsed Microb files found. Run parse-microb first or provide "
            "the matching --entity.",
            err=True,
        )
        click.echo("[]")
        return
    run_id = start_run(engine, pipeline_name="materialize:microb")
    results = []
    failed = False
    with click.progressbar(
        file_ids,
        label=f"Materializing {entity_name}",
        show_pos=True,
        item_show_func=(
            lambda candidate_file_id: (
                f"file_id={candidate_file_id}" if candidate_file_id is not None else ""
            )
        ),
        file=sys.stderr,
    ) as progress:
        for candidate_file_id in progress:
            result = job.run(
                ctx=JobContext(
                    entity_name=entity_name,
                    file_id=candidate_file_id,
                    run_id=run_id,
                )
            )
            results.append({"file_id": candidate_file_id, "ok": result.ok, **result.metrics})
            failed = failed or not result.ok
    finish_run(
        engine,
        run_id=run_id,
        status=RunStatus.FAILED if failed else RunStatus.SUCCESS,
        details_json=json.dumps({"entity_name": entity_name, "files": results}),
    )
    click.echo(json.dumps(results, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


@cli.command("load-microb-catalogs")
@click.option("--db-url", "db_path", help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).")
@click.option(
    "--catalog-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Directory containing pipe-delimited Microb catalog files.",
)
def load_microb_catalogs(db_path: str | None, catalog_dir: Path) -> None:
    """Load versioned Microb reference catalogs for later normalization."""

    engine = _engine_for_db_url(db_path)
    create_schema(engine)
    stats = MicrobCatalogRepository(engine).load_directory(catalog_dir)
    click.echo(json.dumps([asdict(item) for item in stats], ensure_ascii=False, indent=2))


@cli.command("normalize-microb")
@click.option("--db-url", "db_path", help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).")
@click.option("--file-id", type=int, help="Normalize only one Microb file.")
@click.option("--rebuild", is_flag=True, help="Delete the normalized Microb layer before loading it again.")
def normalize_microb(
    db_path: str | None, file_id: int | None, rebuild: bool
) -> None:
    """Normalize Microb staging rows into the relational domain tables."""

    engine = _engine_for_db_url(db_path)
    create_schema(engine)
    normalizer = MicrobNormalizer(engine)
    if rebuild:
        if file_id is not None:
            raise click.UsageError("--rebuild cannot be combined with --file-id.")
        click.echo("Rebuilding normalized Microb tables...", err=True)
        normalizer.rebuild()
    if file_id is not None:
        file_ids = [file_id]
    else:
        with engine.connect() as conn:
            file_ids = list(
                conn.execute(
                    select(stg_microb_technique.c.file_id)
                    .distinct()
                    .order_by(stg_microb_technique.c.file_id)
                ).scalars()
            )
    totals = {"techniques": 0, "susceptibilities": 0, "issues": 0}
    if not file_ids:
        click.echo(json.dumps(totals, indent=2))
        return
    total_stages = sum(normalizer.batch_count(file_id=int(value)) for value in file_ids) * 8

    with click.progressbar(
        length=total_stages,
        label="Normalizing Microb",
        show_pos=True,
        file=sys.stderr,
    ) as progress:
        for candidate_file_id in file_ids:
            def on_stage(stage: str) -> None:
                progress.label = f"Microb file_id={candidate_file_id}: {stage}"
                progress.update(1)

            stats = normalizer.normalize(file_id=int(candidate_file_id), on_stage=on_stage)
            for key in totals:
                totals[key] += stats[key]
    click.echo(json.dumps(totals, indent=2))


@cli.command("parse-microb")
@click.option(
    "--db-url", "db_path",
    help="PostgreSQL SQLAlchemy URL (overrides FIS_DATA_DB_*).",
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

    engine = _engine_for_db_url(db_path)
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
                f"file_id={pending_file_id}" if pending_file_id is not None else ""
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
