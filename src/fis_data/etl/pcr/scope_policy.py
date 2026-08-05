"""Configurable PCR analytical-scope policy used by normal derivations."""

from __future__ import annotations

import os
from datetime import datetime

EXPERIMENTAL_BEFORE_ENV = "PCR_EXPERIMENTAL_BEFORE"
DEFAULT_EXPERIMENTAL_BEFORE = datetime(2023, 7, 1)


def experimental_before_from_environment() -> datetime:
    """Read the policy cutoff, allowing the documented default to be overridden."""

    value = os.getenv(EXPERIMENTAL_BEFORE_ENV)
    if not value:
        return DEFAULT_EXPERIMENTAL_BEFORE
    try:
        return datetime.fromisoformat(value.removesuffix("Z").strip())
    except ValueError as exc:
        raise ValueError(
            f"{EXPERIMENTAL_BEFORE_ENV} must be an ISO-8601 timestamp."
        ) from exc
