"""Unit tests for the E2 priority exploration engine (no LLM calls)."""

import pytest

from core.explorer import (
    PRIOR_DEFAULT,
    ExplorationAction,
    ExplorationFrontier,
    Observation,
    PriorityExplorer,
)


def _action(priority, kind="inspect_file", target="a.py", depth=0):
    return ExplorationAction(priority=priority, kind=kind, target=target, depth=depth)


# ── frontier ordering / dedupe / visited ────────────────────────────────────

def test_frontier_pops_highest_priority_first():
    f = ExplorationFrontier()
    f.push(_action(0.2, target="low.py"))
    f.push(_action(0.9, target="high.py"))
    f.push(_action(0.5, target="mid.py"))
    assert f.pop().target == "high.py"
    assert f.pop().target == "mid.py"
    assert f.pop().target == "low.py"
    assert f.pop() is None


def test_frontier_fifo_tiebreak_on_equal_priority():
    f = ExplorationFrontier()
    f.push(_action(0.5, target="first.py"))
    f.push(_action(0.5, target="second.py"))
    assert f.pop().target == "first.py"
    assert f.pop().target == "second.py"


def test_frontier_dedupes_lower_or_equal_priority():
    f = ExplorationFrontier()
    assert f.push(_action(0.5)) is True
    assert f.push(_action(0.3)) is False  # lower — rejected
    assert f.push(_action(0.5)) is False  # equal — rejected
    assert f.push(_action(0.8)) is True   # upgrade allowed
    assert f.pop().priority == 0.8
    assert f.pop() is None  # stale 0.5 copy skipped


def test_frontier_visited_blocks_repush():
    f = ExplorationFrontier()
    f.push(_action(0.5))
    f.pop()
    assert f.push(_action(0.9)) is False


def test_frontier_visited_preseed_blocks_push():
    """Reflection rounds pre-seed visited so work isn't redone."""
    f = ExplorationFrontier()
    f.visited.add(("inspect_file", "a.py"))
    assert f.push(_action(0.9, target="a.py")) is False
    assert f.push(_action(0.9, target="b.py")) is True


def test_peek_priority_matches_next_pop():
    f = ExplorationFrontier()
    f.push(_action(0.4, target="x.py"))
    f.push(_action(0.7, target="y.py"))
    assert f.peek_priority() == pytest.approx(0.7)
    assert f.pop().target == "y.py"


# ── priority formula ─────────────────────────────────────────────────────────

def _explorer(**kw):
    defaults = dict(
        execute=lambda a: "output",
        observe=lambda a, o: Observation(entity=a.target, kind=a.kind, relevance=0),
        max_actions=20,
    )
    defaults.update(kw)
    return PriorityExplorer(**defaults)


def test_priority_formula_components():
    ex = _explorer(
        graph_distances={"a.py": 1},
        static_priors={"a.py": 1.0},
        w_llm=0.5, w_graph=0.3, w_signal=0.2,
    )
    # llm 8/10 → 0.4; graph 1/(1+1) → 0.15; signal 1.0 → 0.2
    assert ex.priority("a.py", llm_relevance=8) == pytest.approx(0.75)


def test_priority_graph_absent_is_neutral():
    ex = _explorer(graph_distances={}, max_depth=4, w_llm=0.5, w_graph=0.3, w_signal=0.2)
    # unknown file → distance max_depth+1 = 5 → 0.3/6 = 0.05
    assert ex.graph_term("unknown.py") == pytest.approx(1.0 / 6.0)
    assert ex.priority("unknown.py") == pytest.approx(
        0.3 / 6.0 + 0.2 * PRIOR_DEFAULT
    )


def test_priority_uses_file_part_of_entity_target():
    ex = _explorer(graph_distances={"pkg/mod.py": 0}, static_priors={"pkg/mod.py": 1.0})
    assert ex.graph_term("pkg/mod.py::Class.method") == pytest.approx(1.0)
    assert ex.static_prior("pkg/mod.py::Class.method") == pytest.approx(1.0)


# ── termination criteria ─────────────────────────────────────────────────────

def test_stops_at_max_actions():
    calls = []

    def observe(a, o):
        return Observation(entity=a.target, kind=a.kind, relevance=7,
                           new_queries=[f"q{len(calls)}"])

    def execute(a):
        calls.append(a.target)
        return "out"

    ex = _explorer(execute=execute, observe=observe, max_actions=5,
                   static_priors={}, min_priority=0.0)
    for i in range(3):
        ex.seed("inspect_file", f"s{i}.py")
    ex.run()
    assert ex.actions_executed == 5
    assert ex.stop_reason == "max_actions"


def test_stops_when_frontier_empty():
    ex = _explorer(min_priority=0.0)
    ex.seed("inspect_file", "only.py")
    ex.run()
    assert ex.stop_reason == "frontier_empty"
    assert ex.actions_executed == 1


def test_stops_below_min_priority():
    ex = _explorer(min_priority=0.9)
    ex.seed("inspect_file", "weak.py")  # priority ≈ 0.09 < 0.9
    ex.run()
    assert ex.stop_reason == "below_min_priority"
    assert ex.actions_executed == 0


def test_stops_on_stagnation():
    def observe(a, o):
        return Observation(entity=a.target, kind=a.kind, relevance=1)

    ex = _explorer(observe=observe, max_actions=50, min_priority=0.0)
    for i in range(10):
        ex.seed("inspect_file", f"s{i}.py")
    ex.run()
    assert ex.stop_reason == "stagnation"
    assert ex.actions_executed == 5  # STAGNATION_WINDOW


def test_stops_on_early_success():
    def observe(a, o):
        return Observation(entity=a.target, kind=a.kind, relevance=9,
                           new_queries=["more"])

    ex = _explorer(observe=observe, max_actions=50, min_priority=0.0)
    for i in range(12):
        ex.seed("inspect_file", f"s{i}.py")
    findings = ex.run()
    assert ex.stop_reason == "early_success"
    assert len(findings) >= 8


# ── robustness: failures never crash the loop ────────────────────────────────

def test_execute_failure_skips_action():
    def execute(a):
        if a.target == "bad.py":
            raise RuntimeError("tool exploded")
        return "out"

    ex = _explorer(execute=execute, min_priority=0.0)
    ex.seed("inspect_file", "bad.py")
    ex.seed("inspect_file", "good.py")
    ex.run()  # no raise
    assert ex.actions_executed == 2


def test_observe_failure_skips_action():
    def observe(a, o):
        raise ValueError("LLM said no")

    ex = _explorer(observe=observe, min_priority=0.0)
    ex.seed("inspect_file", "a.py")
    ex.run()  # no raise
    assert ex.findings == []


def test_findings_sorted_and_capped():
    scores = iter([6, 9, 7, 8, 6, 6])

    def observe(a, o):
        return Observation(entity=a.target, kind=a.kind, relevance=next(scores))

    ex = _explorer(observe=observe, min_priority=0.0, max_actions=6)
    for i in range(6):
        ex.seed("inspect_file", f"s{i}.py")
    findings = ex.run()
    assert [f.relevance for f in findings[:3]] == [9, 8, 7]


def test_offspring_respects_max_depth():
    def observe(a, o):
        return Observation(entity=a.target, kind=a.kind, relevance=7,
                           new_entities=[{"target": f"{a.target}+", "kind": "inspect_file"}])

    ex = _explorer(observe=observe, max_actions=50, min_priority=0.0, max_depth=2)
    ex.seed("inspect_file", "root.py")
    ex.run()
    # root(d0) → root+(d1) → root++(d2) → offspring of d2 NOT enqueued
    visited_targets = {t for _, t in ex.frontier.visited}
    assert "root.py++" in visited_targets
    assert "root.py+++" not in visited_targets


def test_invalid_offspring_kinds_dropped():
    def observe(a, o):
        return Observation(entity=a.target, kind=a.kind, relevance=7,
                           new_entities=[
                               {"target": "x.py", "kind": "rm_rf"},
                               {"target": "", "kind": "inspect_file"},
                               "not a dict",
                           ])

    ex = _explorer(observe=observe, min_priority=0.0, max_actions=10)
    ex.seed("inspect_file", "root.py")
    ex.run()
    assert ex.actions_executed == 1  # nothing valid was enqueued
