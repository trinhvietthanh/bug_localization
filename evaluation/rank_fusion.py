"""
Reciprocal Rank Fusion across pipeline-stage rankings.

The pipeline produces several independent file rankings (comprehension
candidates, explorer relevance order, confirmation ranking, stack-trace
position). LocAgent-style consensus: a file consistently ranked high by
multiple stages is a stronger candidate than one a single stage promoted.
RRF is rank-based, so heterogeneous score scales fuse cleanly, and it costs
zero LLM calls.
"""

from __future__ import annotations

RRF_K = 60  # standard damping constant (Cormack et al.)


def reciprocal_rank_fusion(
    rankings: list[list[str]], k: int = RRF_K
) -> dict[str, float]:
    """Fuse rank lists into {file: score}, normalized to [0, 1].

    score(f) = Σ_r 1/(k + rank_r(f)) over rankings that contain f (1-based).
    Empty rankings are ignored; duplicate entries within one ranking keep
    their first (best) position.
    """
    raw: dict[str, float] = {}
    for ranking in rankings:
        if not ranking:
            continue
        seen: set[str] = set()
        rank = 0
        for fp in ranking:
            if not fp or fp in seen:
                continue
            seen.add(fp)
            rank += 1
            raw[fp] = raw.get(fp, 0.0) + 1.0 / (k + rank)
    if not raw:
        return {}
    peak = max(raw.values())
    return {fp: s / peak for fp, s in raw.items()}


def stage_rankings_from_context(context, ranked_locations: list[dict]) -> list[list[str]]:
    """Assemble the per-stage rank lists available on the shared context.

    Order of the list is irrelevant to RRF; each inner list must be
    best-first. Query-string pollution is already filtered at the sources.
    """
    confirmation = [
        loc.get("file_path", "") for loc in ranked_locations or []
    ]
    explorer = [
        loc.get("file_path", "")
        for loc in sorted(
            context.suspicious_locations or [],
            key=lambda l: l.get("suspicion_score", 0) or 0,
            reverse=True,
        )
    ]
    comprehension = list(getattr(context, "comprehension_candidates", []) or [])
    stack = list(context.stack_trace_files or [])
    return [r for r in (confirmation, explorer, comprehension, stack) if r]
