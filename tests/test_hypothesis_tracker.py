"""Unit tests for E1 hypothesis tracking (pure-Python, no LLM calls)."""

import math

import pytest

from agents.hypothesis import (
    FALSIFIED_FILE_SCORE,
    EvidenceItem,
    EvidenceProbe,
    Hypothesis,
    HypothesisTracker,
    parse_hypotheses_from_llm,
)


def _hyp(hid="HYP1", prior=0.5, files=None, component="core", probes=None):
    return Hypothesis(
        hid=hid,
        statement=f"{hid} statement",
        suspected_component=component,
        suspected_files=files or [f"{hid.lower()}.py"],
        probes=probes or [],
        prior=prior,
    )


# ── log-odds arithmetic ──────────────────────────────────────────────────────

def test_prior_maps_to_log_odds_and_posterior():
    h = _hyp(prior=0.5)
    assert h.log_odds == pytest.approx(0.0)
    assert h.posterior == pytest.approx(0.5)


def test_extreme_priors_are_clamped():
    high = _hyp(prior=0.999)
    low = _hyp(prior=0.001)
    assert high.log_odds == pytest.approx(2.0)
    assert low.log_odds == pytest.approx(-2.0)


def test_update_moves_log_odds_by_strength_llr():
    h = _hyp(prior=0.5)
    tracker = HypothesisTracker([h])
    tracker.update("HYP1", EvidenceItem(direction=1, strength="strong"))
    assert h.log_odds == pytest.approx(1.1)
    tracker.update("HYP1", EvidenceItem(direction=-1, strength="weak"))
    assert h.log_odds == pytest.approx(1.1 - 0.25)


def test_unknown_strength_defaults_to_moderate():
    h = _hyp(prior=0.5)
    tracker = HypothesisTracker([h])
    tracker.update("HYP1", EvidenceItem(direction=1, strength="banana"))
    assert h.log_odds == pytest.approx(0.6)


def test_zero_direction_is_ignored():
    h = _hyp(prior=0.5)
    tracker = HypothesisTracker([h])
    tracker.update("HYP1", EvidenceItem(direction=0, strength="strong"))
    assert h.log_odds == pytest.approx(0.0)
    assert h.evidence == []


def test_unknown_hid_is_ignored():
    tracker = HypothesisTracker([_hyp()])
    tracker.update("HYP99", EvidenceItem(direction=1, strength="strong"))  # no raise


# ── status transitions ───────────────────────────────────────────────────────

def test_falsify_threshold():
    h = _hyp(prior=0.5)
    tracker = HypothesisTracker([h])
    # 2 strong contradictions: log_odds = -2.2 → posterior ≈ 0.10 < 0.15
    tracker.update("HYP1", EvidenceItem(direction=-1, strength="strong"))
    tracker.update("HYP1", EvidenceItem(direction=-1, strength="strong"))
    assert h.posterior < 0.15
    assert h.status == "falsified"
    assert h not in tracker.surviving()
    assert h in tracker.falsified()


def test_support_threshold():
    h = _hyp(prior=0.5)
    tracker = HypothesisTracker([h])
    tracker.update("HYP1", EvidenceItem(direction=1, strength="strong"))
    tracker.update("HYP1", EvidenceItem(direction=1, strength="moderate"))
    assert h.posterior > 0.75
    assert h.status == "supported"


def test_custom_falsify_threshold():
    h = _hyp(prior=0.5)
    tracker = HypothesisTracker([h], falsify_threshold=0.4)
    tracker.update("HYP1", EvidenceItem(direction=-1, strength="moderate"))
    assert h.posterior < 0.4
    assert h.status == "falsified"


# ── file_scores ──────────────────────────────────────────────────────────────

def test_file_scores_max_over_surviving():
    h1 = _hyp("HYP1", prior=0.7, files=["shared.py", "a.py"], component="a")
    h2 = _hyp("HYP2", prior=0.4, files=["shared.py", "b.py"], component="b")
    tracker = HypothesisTracker([h1, h2])
    scores = tracker.file_scores()
    assert scores["shared.py"] == pytest.approx(h1.posterior)
    assert scores["a.py"] == pytest.approx(h1.posterior)
    assert scores["b.py"] == pytest.approx(h2.posterior)


def test_falsified_only_files_get_negative_score():
    h1 = _hyp("HYP1", prior=0.5, files=["wrong.py", "shared.py"], component="a")
    h2 = _hyp("HYP2", prior=0.5, files=["shared.py"], component="b")
    tracker = HypothesisTracker([h1])
    tracker.hypotheses.append(h2)
    tracker._by_id["HYP2"] = h2
    tracker.update("HYP1", EvidenceItem(direction=-1, strength="strong"))
    tracker.update("HYP1", EvidenceItem(direction=-1, strength="strong"))
    assert h1.status == "falsified"
    scores = tracker.file_scores()
    # wrong.py only in falsified → negative; shared.py survives via HYP2
    assert scores["wrong.py"] == FALSIFIED_FILE_SCORE
    assert scores["shared.py"] > 0


def test_surviving_files_ordered_by_posterior():
    h1 = _hyp("HYP1", prior=0.3, files=["low.py"], component="a")
    h2 = _hyp("HYP2", prior=0.8, files=["high.py"], component="b")
    tracker = HypothesisTracker([h1, h2])
    assert tracker.surviving_files_ordered() == ["high.py", "low.py"]


# ── reflection summary ───────────────────────────────────────────────────────

def test_reflection_summary_mentions_falsified_and_unchecked_probes():
    probe = EvidenceProbe(description="check isinstance in f()")
    h1 = _hyp("HYP1", prior=0.5, component="a")
    h2 = _hyp("HYP2", prior=0.5, component="b", probes=[probe])
    tracker = HypothesisTracker([h1, h2])
    tracker.update(
        "HYP1",
        EvidenceItem(direction=-1, strength="strong", note="check exists", location="a.py:12"),
    )
    tracker.update("HYP1", EvidenceItem(direction=-1, strength="strong"))
    summary = tracker.reflection_summary()
    assert "HYP1 FALSIFIED" in summary
    assert "check isinstance in f()" in summary  # unchecked probe surfaced
    assert "HYP2" in summary


def test_reflection_summary_empty_without_hypotheses():
    assert HypothesisTracker([]).reflection_summary() == ""


# ── parse_hypotheses_from_llm ────────────────────────────────────────────────

RAW = [
    {
        "hid": "HYP1",
        "statement": "resolve_lookup_value coerces list to tuple",
        "suspected_component": "django.db.models.sql",
        "suspected_files": ["django/db/models/sql/query.py"],
        "suspected_functions": ["Query.resolve_lookup_value"],
        "causal_chain": ["list arrives", "coerced to tuple", "exact lookup fails"],
        "probes": [
            {"description": "read resolve_lookup_value", "check_type": "code_pattern",
             "expected_if_true": "sees tuple(...) coercion", "query_hint": "read_file(...)"},
        ],
        "prior": 0.65,
    },
    {
        "statement": "PickledField comparison bug",
        "suspected_component": "fields",
        "suspected_files": ["fields.py"],
        "prior": 0.35,
    },
]


def test_parse_valid_hypotheses():
    hyps = parse_hypotheses_from_llm(RAW)
    assert len(hyps) == 2
    assert hyps[0].hid == "HYP1"
    assert hyps[0].probes[0].query_hint == "read_file(...)"
    assert hyps[1].hid == "HYP2"  # auto-assigned
    assert hyps[1].suspected_files == ["fields.py"]


def test_parse_dedupes_by_component():
    dup = [dict(RAW[0]), {**RAW[1], "suspected_component": "django.db.models.sql"}]
    hyps = parse_hypotheses_from_llm(dup)
    assert len(hyps) == 1


def test_parse_falls_back_to_single_hypothesis():
    hyps = parse_hypotheses_from_llm(None, fallback_statement="the bug is in foo()")
    assert len(hyps) == 1
    assert hyps[0].statement == "the bug is in foo()"
    assert hyps[0].prior == pytest.approx(0.6)


def test_parse_garbage_entries_skipped():
    hyps = parse_hypotheses_from_llm(
        ["not a dict", {"no_statement": True}, {"statement": "valid one"}],
    )
    assert len(hyps) == 1
    assert hyps[0].statement == "valid one"


def test_parse_empty_without_fallback_returns_empty():
    assert parse_hypotheses_from_llm([], fallback_statement="") == []


def test_parse_respects_max_k():
    many = [
        {"statement": f"s{i}", "suspected_component": f"c{i}"} for i in range(10)
    ]
    assert len(parse_hypotheses_from_llm(many, max_k=4)) == 4
