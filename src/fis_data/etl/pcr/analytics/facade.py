"""Public facade for the rebuildable PCR analytical model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.engine import Engine

from ..enums import DerivationStatuses
from ..melting import MeltingParameters
from ..review.models import ActiveOverride
from ..review.repository import PCRReviewRepository
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


@dataclass(frozen=True)
class DerivationStats:
    """Counters produced by one complete PCR analytical derivation."""

    derivation_id: int
    algorithm_version: str
    wells_classified: int
    real_samples: int
    interpretations: int
    target_qc_rows: int
    episodes: int
    result_groups: int
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

    def run(
        self,
        *,
        algorithm_version: str,
        params: MeltingParameters | None = None,
        rebuild: bool = False,
        ignore_overrides: bool = False,
    ) -> DerivationStats:
        """Run all analytical stages and record a terminal derivation status."""

        current = params or MeltingParameters()
        overrides: list[ActiveOverride] = []
        if not ignore_overrides and self.review_repository is not None:
            overrides = self.review_repository.active_overrides()
        self.pipeline.set_overrides(overrides)
        derivation_id = self.repository.start_derivation(
            algorithm_version=algorithm_version,
            parameters_json=_json(current.as_dict()),
            rebuild=rebuild,
        )

        try:
            wells_classified, real_samples = self.pipeline.classify_wells(derivation_id)
            self.pipeline.classify_runs(derivation_id)
            interpretations = self.pipeline.interpret(derivation_id, current)
            target_qc_rows = self.pipeline.evaluate_qc(derivation_id)
            episodes = self.pipeline.associate_episodes(derivation_id)
            result_groups = self.pipeline.select_results(derivation_id)
            review_items = self.repository.count_review_items(derivation_id)
            stats = DerivationStats(
                derivation_id=derivation_id,
                algorithm_version=algorithm_version,
                wells_classified=wells_classified,
                real_samples=real_samples,
                interpretations=interpretations,
                target_qc_rows=target_qc_rows,
                episodes=episodes,
                result_groups=result_groups,
                review_items=review_items,
            )
            self.repository.finish_derivation(
                derivation_id,
                status=DerivationStatuses.success,
                details_json=_json(asdict(stats)),
            )
            self.repository.create_views()
            return stats
        except Exception as exc:
            self.repository.finish_derivation(
                derivation_id,
                status=DerivationStatuses.failed,
                details_json=_json({"error": str(exc)}),
            )
            raise


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
            valid=bool(item["valid"]),
        )
        for item in items
    ]
    summary = summarize_results(candidates, target=target)
    return asdict(summary)
