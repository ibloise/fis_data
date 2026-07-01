"""Versioned PCR melting-curve interpretation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from scipy.signal import find_peaks


CANONICAL_TARGET_TEMPERATURES = {
    "OXA48": 79.5,
    "VIM": 82.0,
    "NDM": 86.0,
    "KPC": 88.0,
}


@dataclass(frozen=True)
class MeltingParameters:
    prominence: float = 0.15
    peak_temp_range: float = 0.5
    minimal_temperature: float = 70.0
    cq_lower_limit: float = 5.0
    cq_upper_limit: float = 40.0
    drfu_threshold: float = 100.0
    targets: dict[str, float] = field(
        default_factory=lambda: dict(CANONICAL_TARGET_TEMPERATURES)
    )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Peak:
    temperature: float
    deriv_rfu: float
    normalized_prominence: float


@dataclass(frozen=True)
class MeltingCall:
    target: str
    call: str
    peak_temperature: float | None = None
    peak_deriv_rfu: float | None = None
    error: str | None = None


def canonical_target(target: str) -> str:
    normalized = target.strip().upper()
    if normalized == "OXA":
        return "OXA48"
    if normalized == "SCREENING":
        return "Screening"
    return normalized


def calculate_peaks(
    *,
    temperatures: list[float],
    derivatives: list[float],
    params: MeltingParameters,
) -> list[Peak]:
    """Find peaks on a normalized curve while retaining raw dRFU values."""

    if len(temperatures) != len(derivatives):
        raise ValueError("Temperature and derivative arrays have different lengths.")
    filtered = [
        (float(temp), float(deriv))
        for temp, deriv in zip(temperatures, derivatives, strict=True)
        if float(temp) > params.minimal_temperature
        and np.isfinite(temp)
        and np.isfinite(deriv)
    ]
    if not filtered:
        return []
    temps = np.asarray([item[0] for item in filtered], dtype=float)
    derivs = np.asarray([item[1] for item in filtered], dtype=float)
    maximum = float(np.max(derivs))
    if not np.isfinite(maximum) or maximum <= 0:
        return []
    normalized = derivs / maximum
    indexes, properties = find_peaks(normalized, prominence=params.prominence)
    prominences = properties.get("prominences", np.zeros(len(indexes)))
    return [
        Peak(
            temperature=float(temps[index]),
            deriv_rfu=float(derivs[index]),
            normalized_prominence=float(prominence),
        )
        for index, prominence in zip(indexes, prominences, strict=True)
    ]


def interpret_curve(
    *,
    source_target: str,
    cq: float | None,
    temperatures: list[float],
    derivatives: list[float],
    params: MeltingParameters,
) -> tuple[list[MeltingCall], list[Peak]]:
    """Interpret one specific (unimodal) or Screening (multimodal) curve."""

    target = canonical_target(source_target)
    if target == "16S":
        call = (
            "POSITIVE"
            if cq is not None and params.cq_lower_limit < cq < params.cq_upper_limit
            else "NEGATIVE"
        )
        return [MeltingCall(target="16S", call=call)], []
    if target in {"FAIL", "FAIL-2"} or (
        target != "Screening" and target not in params.targets
    ):
        return [
            MeltingCall(
                target=target,
                call="INDETERMINATE",
                error=f"Unsupported target: {source_target}",
            )
        ], []

    peaks = calculate_peaks(
        temperatures=temperatures,
        derivatives=derivatives,
        params=params,
    )
    targets = (
        params.targets if target == "Screening" else {target: params.targets[target]}
    )
    cq_valid = cq is not None and cq > params.cq_lower_limit
    if target != "Screening":
        cq_valid = cq_valid and cq < params.cq_upper_limit

    calls: list[MeltingCall] = []
    for output_target, expected_temperature in targets.items():
        candidates = [
            peak
            for peak in peaks
            if peak.deriv_rfu >= params.drfu_threshold
            and abs(peak.temperature - expected_temperature) <= params.peak_temp_range
        ]
        best = max(candidates, key=lambda peak: peak.deriv_rfu, default=None)
        calls.append(
            MeltingCall(
                target=output_target,
                call="POSITIVE" if cq_valid and best is not None else "NEGATIVE",
                peak_temperature=best.temperature if best else None,
                peak_deriv_rfu=best.deriv_rfu if best else None,
            )
        )
    return calls, peaks
