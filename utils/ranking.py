"""
Shared ranking utilities.
"""
from __future__ import annotations


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    weights: list[float] | None = None,
    k: int = 60,
) -> list[tuple[str, float]]:
    """
    Combine multiple ranked lists using Reciprocal Rank Fusion (RRF).

    Formula: score(d) = Σ  weight_i / (k + rank_i(d))

    Args:
        rankings: List of ranked lists (each is items in rank order, best first).
        weights:  Optional per-ranking weights (default: equal weight 1.0 each).
        k:        RRF constant (default 60, per the original paper).

    Returns:
        List of (item, score) sorted by score descending.
    """
    if not rankings:
        return []
    if weights is None:
        weights = [1.0] * len(rankings)

    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights):
        for rank, item in enumerate(ranking, 1):
            if item:
                scores[item] = scores.get(item, 0.0) + weight / (k + rank)

    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
