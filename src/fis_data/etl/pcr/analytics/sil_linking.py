"""Versioned policy for retrospective PCR--SIL reconciliation."""

from __future__ import annotations

import re
from dataclasses import dataclass


POSITIVE_CULTURE_CODES = ("CO1", "AIS")
NEGATIVE_CULTURE_CODES = ("NEG",)
NON_PROCESS_CULTURE_CODES = (".04", ".05", "1", "C06", "NPR", "PEN")
CARBAPENEMASE_MARKERS = ("OXA", "KPC", "NDM", "VIM")
CHARACTERIZATION_MARKERS = ("BLE", *CARBAPENEMASE_MARKERS)
IRRELEVANT_MICROORGANISM_PATTERN = (
    r"Candida|Enterococcus|Stenotrophomonas|Acinetobacter"
)
POLICY_VERSION = "pcr-sil-linking-v2"


def is_sil_relevant_microorganism(description: str | None) -> bool:
    """Return whether a catalogued organism participates in SIL--FIS analysis.

    An unknown catalog description remains eligible: the exclusion policy is
    deliberately limited to the documented name patterns.
    """

    if description is None:
        return True
    return re.search(IRRELEVANT_MICROORGANISM_PATTERN, description, re.IGNORECASE) is None


@dataclass(frozen=True)
class SILLinkingPolicy:
    """Limits and code lists recorded with every PCR derivation."""

    previous_sample_max_days: int = 90
    previous_sample_max_negatives: int = 3
    version: str = POLICY_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "previous_sample_max_days": self.previous_sample_max_days,
            "previous_sample_max_negatives": self.previous_sample_max_negatives,
            "positive_culture_codes": list(POSITIVE_CULTURE_CODES),
            "negative_culture_codes": list(NEGATIVE_CULTURE_CODES),
            "non_process_culture_codes": list(NON_PROCESS_CULTURE_CODES),
            "characterization_markers": list(CHARACTERIZATION_MARKERS),
            "irrelevant_microorganism_pattern": IRRELEVANT_MICROORGANISM_PATTERN,
        }
