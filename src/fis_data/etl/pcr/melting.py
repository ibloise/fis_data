"""Versioned PCR melting-curve interpretation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from scipy.signal import find_peaks

from .enums import PCRTargetAliases, PCRTargets, ResultLevels

CANONICAL_TARGET_TEMPERATURES = {
    PCRTargets.oxa48: 79.5,
    PCRTargets.vim: 82.0,
    PCRTargets.ndm: 86.0,
    PCRTargets.kpc: 88.0,
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
    compatible_targets: tuple[str, ...] = ()


def canonical_target(target: str) -> str:
    normalized = target.strip().upper()
    if "FAIL" in normalized:
        return PCRTargets.fail
    if normalized == PCRTargetAliases.oxa:
        return PCRTargets.oxa48
    if normalized == PCRTargetAliases.screening:
        return PCRTargets.screening
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
    if target == PCRTargets.control_16s:
        call = (
            ResultLevels.positive
            if cq is not None and params.cq_lower_limit < cq < params.cq_upper_limit
            else ResultLevels.negative
        )
        return [MeltingCall(target=PCRTargets.control_16s, call=call)], []
    if target in {PCRTargets.fail, PCRTargets.fail_2}:
        return [
            MeltingCall(
                target=target,
                call=ResultLevels.indeterminate,
                error="TARGET FAILED",
            )
        ], []
    if target != PCRTargets.screening and target not in params.targets:
        return [
            MeltingCall(
                target=target,
                call=ResultLevels.indeterminate,
                error=f"Unsupported target: {source_target}",
            )
        ], []

    peaks = calculate_peaks(
        temperatures=temperatures,
        derivatives=derivatives,
        params=params,
    )
    # Screening evaluates all the targets. Specific targets only evaluate themselves.
    targets = (
        params.targets
        if target == PCRTargets.screening
        else {target: params.targets[target]}
    )
    cq_valid = cq is not None and cq > params.cq_lower_limit

    # Algo is more generous in Screening.
    if target != PCRTargets.screening:
        cq_valid = cq_valid and cq < params.cq_upper_limit

    screening_melting_incompatible = (
        target == PCRTargets.screening
        and cq_valid
        and bool(peaks)
        and not any(
            peak.deriv_rfu >= params.drfu_threshold
            and any(
                abs(peak.temperature - expected_temperature)
                <= params.peak_temp_range
                for expected_temperature in params.targets.values()
            )
            for peak in peaks
        )
    )
    calls: list[MeltingCall] = []
    for output_target, expected_temperature in targets.items():
        candidates = [
            peak
            for peak in peaks
            if peak.deriv_rfu >= params.drfu_threshold
            and abs(peak.temperature - expected_temperature) <= params.peak_temp_range
        ]
        best = max(candidates, key=lambda peak: peak.deriv_rfu, default=None)
        observed = max(peaks, key=lambda peak: peak.deriv_rfu, default=None)
        compatible_targets = ()
        if best is None and observed is not None:
            compatible_targets = tuple(
                candidate_target
                for candidate_target, candidate_temperature in params.targets.items()
                if abs(observed.temperature - candidate_temperature)
                <= params.peak_temp_range
            )
        melting_incompatible = (
            target != PCRTargets.screening and cq_valid and best is None
        )
        calls.append(
            MeltingCall(
                target=output_target,
                call=(
                    ResultLevels.positive
                    if cq_valid and best is not None
                    else ResultLevels.negative
                ),
                peak_temperature=(best or observed).temperature if (best or observed) else None,
                peak_deriv_rfu=(best or observed).deriv_rfu if (best or observed) else None,
                error=(
                    "MELTING_INCOMPATIBLE"
                    if melting_incompatible or screening_melting_incompatible
                    else None
                ),
                compatible_targets=compatible_targets,
            )
        )
    return calls, peaks
