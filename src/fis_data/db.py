"""SQLite database configuration for ETL workflows."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class DBSettings:
    """Database settings used by the SQLAlchemy engine factory."""

    url: str
    echo: bool = False


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def sqlite_url_for_path(path: str | Path) -> str:
    """Build a SQLAlchemy SQLite URL for a local database path."""

    return f"sqlite:///{Path(path).expanduser()}"


def get_db_settings() -> DBSettings:
    """Load database settings from environment variables."""

    url = _env("FIS_DB_URL")
    if not url:
        path = _env("FIS_DB_PATH", "var/fis_data.sqlite")
        url = sqlite_url_for_path(path)

    return DBSettings(url=url, echo=_env_bool("FIS_DB_ECHO", False))


def get_engine(settings: DBSettings | None = None) -> Engine:
    """Create a SQLAlchemy engine for the configured SQLite database."""

    current = settings or get_db_settings()
    _ensure_sqlite_parent(current.url)
    connect_args = (
        {"check_same_thread": False} if current.url.startswith("sqlite") else {}
    )
    return create_engine(
        current.url,
        echo=current.echo,
        future=True,
        connect_args=connect_args,
    )


def _ensure_sqlite_parent(url: str) -> None:
    if not url.startswith("sqlite:///") or url == "sqlite:///:memory:":
        return

    raw_path = url.removeprefix("sqlite:///")
    if not raw_path:
        return

    Path(unquote(raw_path)).expanduser().parent.mkdir(parents=True, exist_ok=True)
