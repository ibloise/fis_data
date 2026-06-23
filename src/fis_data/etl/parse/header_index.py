"""Header utilities for positional text exports."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class HeaderIndex:
    """Build stable unique keys while preserving empty positional headers."""

    headers: tuple[str, ...]

    @classmethod
    def from_iterable(cls, headers: Iterable[str]) -> HeaderIndex:
        """Create an index from a header sequence."""

        return cls(headers=tuple(headers))

    @property
    def keys(self) -> tuple[str, ...]:
        """Return unique keys, suffixing duplicated non-empty headers."""

        seen: dict[str, int] = {}
        out: list[str] = []

        for header in self.headers:
            header = header or ""
            if header == "":
                out.append("")
                continue

            count = seen.get(header, 0) + 1
            seen[header] = count
            out.append(header if count == 1 else f"{header}__{count}")

        return tuple(out)
