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
POLICY_VERSION = "pcr-sil-linking-v4"


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


def direct_characterization_exists_sql(alias: str) -> str:
    """Check positive characterization across all relevant current isolates."""
    return f"""EXISTS (
        SELECT 1 FROM pcr_sil_organism_marker_summary AS direct
        JOIN microb_sample_analysis AS da ON da.sample_id=direct.microb_sample_id
        JOIN microb_analysis_result AS dr ON dr.sample_analysis_id=da.sample_analysis_id
        JOIN microb_isolate AS di ON di.analysis_result_id=dr.analysis_result_id
          AND di.microorganism_code=direct.microorganism_code
        JOIN microb_susceptibility_result AS ds ON ds.isolate_id=di.isolate_id
        WHERE direct.derivation_id={alias}.derivation_id
          AND direct.pcr_sample_id={alias}.pcr_sample_id
          AND ds.interpretation_code='P'
          AND ds.antibiotic_code IN ('BLE', 'OXA', 'KPC', 'NDM', 'VIM')
    )"""
