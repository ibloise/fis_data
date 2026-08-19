"""Public facade for the rebuildable PCR analytical model."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.engine import Engine

from ..enums import DerivationStatuses
from ..melting import MeltingParameters
from ..review.models import ActiveOverride
from ..review.repository import PCRReviewRepository
from ..scope_policy import experimental_before_from_environment
from ..scope_repository import PCRRunScopeRepository
from .domain import (
    classify_run,
    classify_sample_name,
    episode_date_key,
    is_real_sample,
    run_components,
    sample_key,
    summarize_results,
)
from .models import ResultCandidate
from .pipeline import PCRAnalyticsPipeline, _json
from .repository import PCRAnalyticsRepository
from .sil_linking import SILLinkingPolicy


@dataclass(frozen=True)
class DerivationStats:
    """Counters produced by one complete PCR analytical derivation."""

    derivation_id: int
    algorithm_version: str
    wells_classified: int
    real_samples: int
    legacy_screening_blocks: int
    interpretations: int
    target_qc_rows: int
    episodes: int
    result_groups: int
    sample_results: int
    sil_culture_summaries: int
    sil_links: int
    review_items: int


class PCRAnalyticsJob:
    """Facade that owns derivation lifecycle and delegates analytical stages."""

    def __init__(
        self,
        engine: Engine | None = None,
        *,
        repository: PCRAnalyticsRepository | None = None,
        pipeline: PCRAnalyticsPipeline | None = None,
        review_repository: PCRReviewRepository | None = None,
        scope_repository: PCRRunScopeRepository | None = None,
    ) -> None:
        if repository is None:
            if engine is None:
                raise ValueError(
                    "An engine is required when no repository is supplied."
                )
            repository = PCRAnalyticsRepository(engine)
        self.repository = repository
        self.pipeline = pipeline or PCRAnalyticsPipeline(self.repository)
        self.review_repository = review_repository or (
            PCRReviewRepository(engine) if engine is not None else None
        )
        self.scope_repository = scope_repository or (
            PCRRunScopeRepository(engine) if engine is not None else None
        )

    def run(
        self,
        *,
        algorithm_version: str,
        params: MeltingParameters | None = None,
        rebuild: bool = False,
        ignore_overrides: bool = False,
        experimental_before: datetime | None = None,
        sil_linking_policy: SILLinkingPolicy | None = None,
        on_stage: Callable[[str, bool], None] | None = None,
    ) -> DerivationStats:
        """Run all analytical stages and record a terminal derivation status."""

        current = params or MeltingParameters()
        sil_policy = sil_linking_policy or SILLinkingPolicy()
        cutoff = experimental_before or experimental_before_from_environment()
        if cutoff is not None and self.scope_repository is not None:
            self.scope_repository.assign_before(
                before=cutoff,
                reason=f"RUN_STARTED_POLICY_CUTOFF:{cutoff.isoformat()}",
            )
        overrides: list[ActiveOverride] = []
        if not ignore_overrides and self.review_repository is not None:
            overrides = self.review_repository.active_overrides()
        self.pipeline.set_overrides(overrides)
        derivation_id = self.repository.start_derivation(
            algorithm_version=algorithm_version,
            parameters_json=_json(
                {
                    **current.as_dict(),
                    "experimental_before": cutoff.isoformat() if cutoff else None,
                    "legacy_screening_gate": {
                        "cutoff": cutoff.isoformat(),
                        "policy": "BLOCK_SPECIFIC_UNTIL_ROUTINE_SCREENING_RESET",
                    },
                    "pcr_sil_linking": sil_policy.as_dict(),
                }
            ),
            rebuild=rebuild,
        )

        try:
            wells_classified, real_samples = self._run_stage(
                on_stage,
                "Classifying PCR wells",
                lambda: self.pipeline.classify_wells(
                    derivation_id,
                    on_progress=lambda processed, total: on_stage(
                        f"Classifying PCR wells ({processed}/{total})", False
                    )
                    if on_stage is not None
                    else None,
                ),
            )
            self._run_stage(
                on_stage,
                "Validating PCR–Microb matches",
                lambda: self.repository.validate_microb_samples(derivation_id),
            )
            self._run_stage(
                on_stage,
                "Classifying PCR runs",
                lambda: self.pipeline.classify_runs(derivation_id),
            )
            legacy_screening_blocks = self._run_stage(
                on_stage,
                "Applying legacy screening gate",
                lambda: self.repository.apply_legacy_screening_gate(
                    derivation_id, cutoff=cutoff
                ),
            )
            interpretations = self._run_stage(
                on_stage,
                "Interpreting melting curves",
                lambda: self.pipeline.interpret(derivation_id, current),
            )
            target_qc_rows = self._run_stage(
                on_stage,
                "Evaluating PCR QC",
                lambda: self.pipeline.evaluate_qc(derivation_id),
            )
            episodes = self._run_stage(
                on_stage,
                "Associating PCR episodes",
                lambda: self.pipeline.associate_episodes(derivation_id),
            )
            result_groups = self._run_stage(
                on_stage,
                "Selecting PCR results",
                lambda: self.pipeline.select_results(derivation_id),
            )
            sample_results = self._run_stage(
                on_stage,
                "Classifying promoted PCR samples",
                lambda: self.repository.classify_promoted_sample_results(derivation_id),
            )
            sil_links = self._run_stage(
                on_stage,
                "Linking PCR results to SIL evidence",
                lambda: self.repository.link_sil_results(
                    derivation_id,
                    policy_version=sil_policy.version,
                    max_days=sil_policy.previous_sample_max_days,
                    max_negatives=sil_policy.previous_sample_max_negatives,
                ),
            )
            sil_culture_summaries = self._run_stage(
                on_stage,
                "Aggregating direct SIL culture markers",
                lambda: self.repository.summarize_sil_culture_markers(derivation_id),
            )
            review_items = self.repository.count_review_items(derivation_id)
            stats = DerivationStats(
                derivation_id=derivation_id,
                algorithm_version=algorithm_version,
                wells_classified=wells_classified,
                real_samples=real_samples,
                legacy_screening_blocks=legacy_screening_blocks,
                interpretations=interpretations,
                target_qc_rows=target_qc_rows,
                episodes=episodes,
                result_groups=result_groups,
                sample_results=sample_results,
                sil_culture_summaries=sil_culture_summaries,
                sil_links=sil_links,
                review_items=review_items,
            )
            self.repository.finish_derivation(
                derivation_id,
                status=DerivationStatuses.success,
                details_json=_json(asdict(stats)),
            )
            return stats
        except Exception as exc:
            self.repository.finish_derivation(
                derivation_id,
                status=DerivationStatuses.failed,
                details_json=_json({"error": str(exc)}),
            )
            raise

    @staticmethod
    def _run_stage(
        on_stage: Callable[[str, bool], None] | None,
        label: str,
        operation: Callable[[], Any],
    ) -> Any:
        if on_stage is not None:
            on_stage(label, False)
        result = operation()
        if on_stage is not None:
            on_stage(label, True)
        return result


# Compatibility aliases for existing callers of the former module-private rules.
_classify_sample_name = classify_sample_name
_is_real_sample = is_real_sample
_sample_key = sample_key
_run_class = classify_run
_episode_date_key = episode_date_key
_run_components = run_components


def _summarize_results(items: list[dict[str, Any]], *, target: str) -> dict[str, Any]:
    """Adapt legacy dictionary candidates to the typed selection rule."""

    candidates = [
        ResultCandidate(
            interpretation_id=int(item["id"]),
            result_id=int(item.get("result_id", item["id"])),
            sample_id=int(item.get("sample_id", 0)),
            sample_key=str(item.get("sample_key", "")),
            target=target,
            call=str(item["call"]),
            run_id=int(item["run_id"]),
            started=item["started"],
            run_class=str(item["run_class"]),
            run_type=str(item.get("run_type", "SPECIFIC")),
            valid=bool(item["valid"]),
        )
        for item in items
    ]
    summary = summarize_results(candidates, target=target)
    return asdict(summary)
