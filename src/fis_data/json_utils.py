"""Compatibility helpers for JSON columns across SQLite and PostgreSQL."""

from __future__ import annotations

import json
from typing import Any


def loads_json(value: Any) -> Any:
    """Decode legacy JSON text, while preserving native PostgreSQL JSON values."""

    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, bytes):
        value = value.decode()
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        # psycopg returns JSONB scalar strings as native Python strings rather
        # than JSON-encoded text.  Legacy SQLite values still decode above.
        return value
