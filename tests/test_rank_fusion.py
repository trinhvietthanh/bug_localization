"""Unit tests for RRF stage-ranking fusion."""

from agents.base_agent import AgentContext
from evaluation.rank_fusion import (
    reciprocal_rank_fusion,
    stage_rankings_from_context,
)


def test_consensus_file_beats_single_stage_leader():
    # b.py is #2 in three rankings; a/c/d each lead only one
    fused = reciprocal_rank_fusion([
        ["a.py", "b.py", "x.py"],
        ["c.py", "b.py", "y.py"],
        ["d.py", "b.py", "z.py"],
    ])
    top = max(fused, key=fused.get)
    assert top == "b.py"
    assert fused["b.py"] == 1.0  # normalized peak


def test_single_ranking_preserves_order():
    fused = reciprocal_rank_fusion([["a.py", "b.py", "c.py"]])
    assert fused["a.py"] > fused["b.py"] > fused["c.py"]


def test_duplicates_and_empties_ignored():
    fused = reciprocal_rank_fusion([
        ["a.py", "a.py", "", "b.py"],
        [],
    ])
    # duplicate keeps best position: a rank1, b rank2
    assert fused["a.py"] > fused["b.py"]
    assert "" not in fused
    assert reciprocal_rank_fusion([]) == {}
    assert reciprocal_rank_fusion([[], []]) == {}


def test_stage_rankings_assembled_from_context():
    ctx = AgentContext(instance_id="t")
    ctx.suspicious_locations = [
        {"file_path": "low.py", "suspicion_score": 0.2},
        {"file_path": "high.py", "suspicion_score": 0.9},
    ]
    ctx.comprehension_candidates = ["comp.py"]
    ctx.stack_trace_files = ["stack.py"]
    ranked_locations = [{"file_path": "conf.py"}]
    rankings = stage_rankings_from_context(ctx, ranked_locations)
    assert ["conf.py"] in rankings
    assert ["high.py", "low.py"] in rankings  # explorer sorted by score desc
    assert ["comp.py"] in rankings
    assert ["stack.py"] in rankings
    # empty stages omitted
    ctx2 = AgentContext(instance_id="t2")
    assert stage_rankings_from_context(ctx2, []) == []
