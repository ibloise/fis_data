"""Click adapter for the PCR review application service."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click

from ....db import DBSettings, get_engine, sqlite_url_for_path
from ....schema import create_schema
from ...control import RunStatus, finish_run, start_run
from ..analytics import PCRAnalyticsJob
from .repository import PCRReviewRepository
from .service import PCRReviewService


@click.group("pcr-review")
@click.option(
    "--db-path", type=click.Path(dir_okay=False), help="SQLite database path."
)
@click.pass_context
def pcr_review(ctx: click.Context, db_path: str | None) -> None:
    """Inspect and correct PCR analytical review items."""

    engine = (
        get_engine()
        if db_path is None
        else get_engine(DBSettings(url=sqlite_url_for_path(Path(db_path).expanduser())))
    )
    create_schema(engine)
    ctx.obj = {
        "engine": engine,
        "service": PCRReviewService(PCRReviewRepository(engine)),
    }


@pcr_review.command("list")
@click.option("--type", "item_type")
@click.option("--status", default="OPEN", show_default=True)
@click.option("--limit", default=100, type=int, show_default=True)
@click.option("--offset", default=0, type=int, show_default=True)
@click.option(
    "--format", "output_format", type=click.Choice(["table", "json"]), default="table"
)
@click.pass_obj
def list_items(
    obj: dict[str, Any],
    item_type: str | None,
    status: str,
    limit: int,
    offset: int,
    output_format: str,
) -> None:
    """List current review items with filters and pagination."""

    items = _call(
        obj["service"].list_items,
        item_type=item_type,
        status=status,
        limit=limit,
        offset=offset,
    )
    if output_format == "json":
        _json_output([asdict(item) for item in items])
        return
    click.echo("TYPE\tSUBJECT\tSTATUS\tREASON\tFINGERPRINT\tNEXT_STEP")
    for item in items:
        click.echo(
            f"{item.item_type}\t{item.subject_key}\t{item.status}\t"
            f"{item.reason}\t{item.fingerprint}\t"
            f"show --type {item.item_type} --subject {item.subject_key}"
        )


@pcr_review.command("show")
@click.option("--type", "item_type", required=True)
@click.option("--subject", "subject_key", required=True)
@click.option(
    "--format", "output_format", type=click.Choice(["table", "json"]), default="table"
)
@click.pass_obj
def show_item(
    obj: dict[str, Any], item_type: str, subject_key: str, output_format: str
) -> None:
    """Show current context, history and active override."""

    payload = _call(obj["service"].show, item_type=item_type, subject_key=subject_key)
    if output_format == "json":
        _json_output(payload)
        return
    _show_table(payload)


def _review_options(function):
    options = [
        click.option("--reviewer", required=True),
        click.option("--reason", required=True),
        click.option("--fingerprint", required=True),
        click.option("--derivation-id", type=int, required=True),
        click.option("--subject", "subject_key", required=True),
        click.option("--type", "item_type", required=True),
    ]
    for option in options:
        function = option(function)
    return function


@pcr_review.command("resolve")
@_review_options
@click.option("--resolution", required=True)
@click.pass_obj
def resolve_item(obj: dict[str, Any], **kwargs: Any) -> None:
    """Resolve the current fingerprint without changing analytics."""

    _json_output(asdict(_call(obj["service"].resolve, **kwargs)))


@pcr_review.command("reopen")
@_review_options
@click.pass_obj
def reopen_item(obj: dict[str, Any], **kwargs: Any) -> None:
    """Reopen a previously resolved current fingerprint."""

    _json_output(asdict(_call(obj["service"].reopen, **kwargs)))


@pcr_review.command("revoke")
@click.option("--type", "item_type", required=True)
@click.option("--subject", "subject_key", required=True)
@click.option("--reviewer", required=True)
@click.option("--reason", required=True)
@click.pass_obj
def revoke_override(obj: dict[str, Any], **kwargs: Any) -> None:
    """Revoke the latest active override for a subject."""

    _json_output(asdict(_call(obj["service"].revoke, **kwargs)))


@pcr_review.command("history")
@click.option("--type", "item_type", required=True)
@click.option("--subject", "subject_key", required=True)
@click.pass_obj
def action_history(obj: dict[str, Any], **kwargs: Any) -> None:
    """Show immutable review history for one subject."""

    _json_output([asdict(action) for action in _call(obj["service"].history, **kwargs)])


@pcr_review.group("override")
def override_group() -> None:
    """Create validated analytical overrides."""


def _override_options(function):
    options = [
        click.option("--reviewer", required=True),
        click.option("--reason", required=True),
        click.option("--fingerprint", required=True),
        click.option("--derivation-id", type=int, required=True),
        click.option("--subject", "subject_key", required=True),
    ]
    for option in options:
        function = option(function)
    return function


@override_group.command("well")
@_override_options
@click.option("--role", required=True)
@click.option("--sample-key")
@click.pass_obj
def override_well(obj: dict[str, Any], **kwargs: Any) -> None:
    """Override well classification."""

    _json_output(asdict(_call(obj["service"].override_well, **kwargs)))


@override_group.command("run")
@_override_options
@click.option("--run-class", required=True)
@click.option("--episode-date")
@click.pass_obj
def override_run(obj: dict[str, Any], **kwargs: Any) -> None:
    """Override run classification."""

    _json_output(asdict(_call(obj["service"].override_run, **kwargs)))


@override_group.command("episode")
@_override_options
@click.option("--run-id", "run_ids", multiple=True, type=int, required=True)
@click.pass_obj
def override_episode(obj: dict[str, Any], **kwargs: Any) -> None:
    """Override episode membership."""

    _json_output(asdict(_call(obj["service"].override_episode, **kwargs)))


@override_group.command("qc")
@_override_options
@click.option("--status", required=True)
@click.option("--valid/--invalid", required=True)
@click.pass_obj
def override_qc(obj: dict[str, Any], **kwargs: Any) -> None:
    """Override target QC status."""

    _json_output(asdict(_call(obj["service"].override_qc, **kwargs)))


@override_group.command("result")
@_override_options
@click.option("--preferred-result-id", type=int)
@click.option("--call")
@click.pass_obj
def override_result(obj: dict[str, Any], **kwargs: Any) -> None:
    """Override preferred result or effective call."""

    _json_output(asdict(_call(obj["service"].override_result, **kwargs)))


@pcr_review.command("apply")
@click.option("--algorithm-version", required=True)
@click.option("--reviewer", required=True)
@click.option("--rebuild/--no-rebuild", default=True, show_default=True)
@click.pass_obj
def apply_reviews(
    obj: dict[str, Any], algorithm_version: str, reviewer: str, rebuild: bool
) -> None:
    """Run a new analytical derivation consuming active overrides."""

    engine = obj["engine"]
    run_id = start_run(engine, pipeline_name="review:pcr:apply")
    try:
        stats = PCRAnalyticsJob(engine).run(
            algorithm_version=algorithm_version,
            rebuild=rebuild,
        )
    except Exception as exc:
        finish_run(
            engine,
            run_id=run_id,
            status=RunStatus.FAILED,
            details_json=json.dumps({"error": str(exc), "reviewer": reviewer}),
        )
        raise click.ClickException(str(exc)) from exc
    details = {**asdict(stats), "reviewer": reviewer}
    finish_run(
        engine,
        run_id=run_id,
        status=RunStatus.SUCCESS,
        details_json=json.dumps(details),
    )
    _json_output(details)


def _call(function, **kwargs: Any):
    try:
        return function(**kwargs)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def _json_output(value: Any) -> None:
    click.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _show_table(payload: dict[str, Any]) -> None:
    item = payload["item"]
    diagnosis = payload["diagnosis"]
    click.echo(f"TYPE: {item['item_type']}")
    click.echo(f"SUBJECT: {item['subject_key']}")
    click.echo(f"STATUS: {item['status']}")
    click.echo(f"DERIVATION_ID: {item['derivation_id']}")
    click.echo(f"FINGERPRINT: {item['fingerprint']}")
    click.echo(f"REASON: {item['reason']}")
    click.echo("")
    click.echo("DIAGNOSIS")
    click.echo(f"  {diagnosis['summary']}")
    click.echo(f"  Recommended: {diagnosis['recommended_decision']}")
    click.echo("")
    click.echo("EVIDENCE TO CHECK")
    for value in diagnosis["evidence_to_check"]:
        click.echo(f"  - {value}")
    click.echo("")
    click.echo("CONTEXT")
    _echo_context(item["context"])
    click.echo("")
    click.echo("CANDIDATE ACTIONS")
    for action in payload["candidate_actions"]:
        click.echo(f"  - {action['action']}: {action['when']}")
    click.echo("")
    click.echo("COMMAND HINTS")
    for label, command in payload["commands"].items():
        click.echo(f"  {label}:")
        click.echo(f"    {command}")
    if payload["active_override"]:
        click.echo("")
        click.echo("ACTIVE OVERRIDE")
        click.echo(json.dumps(payload["active_override"], ensure_ascii=False))
    if payload["history"]:
        click.echo("")
        click.echo(f"HISTORY: {len(payload['history'])} action(s)")


def _echo_context(context: dict[str, Any]) -> None:
    for key, value in context.items():
        if isinstance(value, list):
            click.echo(f"  {key}:")
            if not value:
                click.echo("    []")
            for entry in value:
                click.echo(f"    - {json.dumps(entry, ensure_ascii=False)}")
        elif isinstance(value, dict):
            click.echo(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
        else:
            click.echo(f"  {key}: {value}")
