"""PostgreSQL configuration for ETL workflows."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class DBSettings:
    """Connection settings used by the SQLAlchemy engine factory."""

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
    return default if value is None else value.lower() in {"1", "true", "yes", "y", "on"}


def postgres_url_from_env() -> str:
    """Build a PostgreSQL URL from FIS_DATA_DB_* environment variables."""

    user = _env("FIS_DATA_DB_USER")
    password = _env("FIS_DATA_DB_PASSWORD")
    host = _env("FIS_DATA_DB_HOST", "localhost")
    port = _env("FIS_DATA_DB_PORT", "5432")
    name = _env("FIS_DATA_DB_NAME", "fis_data")
    if not user:
        raise RuntimeError("FIS_DATA_DB_USER must be configured")
    credentials = quote_plus(user)
    if password is not None:
        credentials += ":" + quote_plus(password)
    query = _env("FIS_DATA_DB_OPTIONS")
    suffix = f"?{query}" if query else ""
    return f"postgresql+psycopg://{credentials}@{host}:{port}/{name}{suffix}"


def get_db_settings(url: str | None = None) -> DBSettings:
    """Load settings, optionally using a CLI-supplied PostgreSQL URL."""

    return DBSettings(url=url or _env("FIS_DATA_DB_URL") or postgres_url_from_env(), echo=_env_bool("FIS_DATA_DB_ECHO"))


def get_engine(settings: DBSettings | None = None) -> Engine:
    """Create a SQLAlchemy engine for PostgreSQL."""

    current = settings or get_db_settings()
    if not current.url.startswith(("postgresql://", "postgresql+")):
        raise ValueError("Only PostgreSQL database URLs are supported")
    return create_engine(current.url, echo=current.echo, future=True, pool_pre_ping=True)
