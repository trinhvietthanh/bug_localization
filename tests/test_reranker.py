"""Unit tests for the E3 listwise reranker (pure-Python, no LLM calls)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from evaluation.reranker import (
    ListwiseReranker,
    build_evidence_cards,
    deterministic_shuffle,
    render_card,
    validate_permutation,
)


# ── deterministic_shuffle ────────────────────────────────────────────────────

def test_shuffle_is_deterministic_for_same_seed():
    items = [f"C{i}" for i in range(10)]
    assert deterministic_shuffle(items, "django-11099:0") == deterministic_shuffle(
        items, "django-11099:0"
    )


def test_shuffle_differs_across_seeds():
    items = [f"C{i}" for i in range(10)]
    a = deterministic_shuffle(items, "django-11099:0")
    b = deterministic_shuffle(items, "django-11099:1")
    assert a != b  # 10! permutations — collision would be astronomical


def test_shuffle_does_not_mutate_input():
    items = ["C1", "C2", "C3"]
    original = list(items)
    deterministic_shuffle(items, "seed")
    assert items == original


# ── validate_permutation ─────────────────────────────────────────────────────

VALID = ["C1", "C2", "C3", "C4"]


def test_valid_permutation_passes_through():
    out = validate_permutation(["C3", "C1", "C4", "C2"], VALID, VALID)
    assert out == ["C3", "C1", "C4", "C2"]


def test_missing_ids_appended_in_fallback_order():
    out = validate_permutation(["C3"], VALID, VALID)
    assert out == ["C3", "C1", "C2", "C4"]


def test_duplicates_dropped():
    out = validate_permutation(["C2", "C2", "C1"], VALID, VALID)
    assert out == ["C2", "C1", "C3", "C4"]


def test_unknown_ids_dropped():
    out = validate_permutation(["C9", "C2", "garbage"], VALID, VALID)
    assert out == ["C2", "C1", "C3", "C4"]


def test_empty_ranking_falls_back_entirely():
    assert validate_permutation([], VALID, VALID) == VALID
    assert validate_permutation(None, VALID, VALID) == VALID


# ── evidence cards ───────────────────────────────────────────────────────────

UNIFIED_SCORES = [
    {
        "file_path": "a.py",
        "total_score": 3.5,
        "llm_confidence": 0.9,
        "stack_trace_score": 2.5,
        "error_match_score": 0.0,
        "mentioned_score": 0.0,
        "graph_score": 0.42,
        "semantic_score": 0.0,
        "git_recency_score": 0.3,
        "penalty": 0.0,
        "rank": 1,
    },
    {
        "file_path": "b.py",
        "total_score": 0.1,
        "llm_confidence": 0.0,
        "stack_trace_score": 0.0,
        "error_match_score": 0.0,
        "mentioned_score": 0.0,
        "graph_score": 0.0,
        "semantic_score": 0.0,
        "git_recency_score": 0.0,
        "penalty": 0.0,
        "rank": 2,
    },
]

LOCATIONS = [
    {
        "file_path": "a.py",
        "function_name": "f",
        "confidence": 0.9,
        "explanation": "f() lacks the isinstance check",
    }
]


def test_cards_carry_signals_and_verdicts():
    cards = build_evidence_cards(["a.py", "b.py"], UNIFIED_SCORES, LOCATIONS)
    assert cards[0]["file_path"] == "a.py"
    assert "STACK_TRACE" in cards[0]["signals"]
    assert "isinstance" in cards[0]["agent_verdict"]
    # padded file gets the explicit no-verdict marker
    assert "none" in cards[1]["agent_verdict"]
    assert cards[1]["signals"] == []


def test_cards_never_leak_total_score_or_rank():
    cards = build_evidence_cards(["a.py", "b.py"], UNIFIED_SCORES, LOCATIONS)
    for cid, card in zip(["C1", "C2"], cards):
        rendered = render_card(cid, card)
        assert "total_score" not in rendered
        assert "3.5" not in rendered  # the unified total
        assert "rank" not in rendered.lower()


def test_cards_for_files_missing_from_scores_degrade():
    cards = build_evidence_cards(["zzz.py"], UNIFIED_SCORES, LOCATIONS)
    assert cards[0]["signals"] == []
    assert "none" in cards[0]["agent_verdict"]


# ── rerank_cards with a mocked LLM ──────────────────────────────────────────

def _mk_llm(payload: str):
    """Mock OpenAI-compatible client returning a fixed completion string."""
    llm = MagicMock()
    llm.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=payload))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    return llm


def _cards(files):
    return [
        {"file_path": fp, "signals": [], "llm_confidence": 0.0,
         "agent_verdict": "none", "agent_confidence": None, "snippet": ""}
        for fp in files
    ]


def test_rerank_cards_applies_llm_order():
    files = ["a.py", "b.py", "c.py"]
    payload = json.dumps({"ranking": ["C3", "C1", "C2"], "confidence": {"C3": 0.8}})
    rr = ListwiseReranker(llm=_mk_llm(payload))
    order, conf = rr.rerank_cards(_cards(files), "inst-1", files)
    assert order == ["c.py", "a.py", "b.py"]
    assert conf == {"c.py": 0.8}


def test_rerank_cards_is_noop_on_garbage_output():
    files = ["a.py", "b.py", "c.py"]
    rr = ListwiseReranker(llm=_mk_llm("I cannot rank these files, sorry!"))
    order, conf = rr.rerank_cards(_cards(files), "inst-1", files)
    assert order == files
    assert conf == {}


def test_rerank_cards_is_noop_on_llm_exception():
    files = ["a.py", "b.py"]
    llm = MagicMock()
    llm.chat.completions.create.side_effect = RuntimeError("API down")
    rr = ListwiseReranker(llm=llm)
    order, conf = rr.rerank_cards(_cards(files), "inst-1", files)
    assert order == files


def test_rerank_cards_repairs_partial_permutation():
    files = ["a.py", "b.py", "c.py", "d.py"]
    payload = json.dumps({"ranking": ["C2"]})  # missing C1, C3, C4
    rr = ListwiseReranker(llm=_mk_llm(payload))
    order, _ = rr.rerank_cards(_cards(files), "inst-1", files)
    assert order == ["b.py", "a.py", "c.py", "d.py"]
    assert set(order) == set(files)


def test_rerank_cards_single_candidate_short_circuits():
    files = ["a.py"]
    llm = MagicMock()
    rr = ListwiseReranker(llm=llm)
    order, conf = rr.rerank_cards(_cards(files), "inst-1", files)
    assert order == files
    llm.chat.completions.create.assert_not_called()


# ── full rerank() permutation invariant ──────────────────────────────────────

def _mk_result(files):
    return SimpleNamespace(
        ranked_files=list(files),
        ranked_methods=[],
        ranked_locations=[],
        agent_results={"unified_scores": []},
        total_llm_calls=0,
        total_prompt_tokens=0,
        total_completion_tokens=0,
        total_tokens=0,
    )


def _mk_context():
    return SimpleNamespace(
        instance_id="inst-1",
        repo_path="/nonexistent",
        language="python",
        problem_statement="Bug: crash in foo",
        structured_bug_info={},
    )


def test_rerank_preserves_topk_set_and_tail(monkeypatch):
    from config import config as cfg

    monkeypatch.setattr(cfg, "enable_listwise_rerank", True)
    monkeypatch.setattr(cfg, "enable_hierarchical_narrowing", False)
    monkeypatch.setattr(cfg, "listwise_rerank_top_k", 3)
    monkeypatch.setattr(cfg, "listwise_rerank_passes", 1)

    files = ["a.py", "b.py", "c.py", "tail1.py", "tail2.py"]
    payload = json.dumps({"ranking": ["C2", "C3", "C1"], "confidence": {}})
    rr = ListwiseReranker(llm=_mk_llm(payload))
    result = _mk_result(files)
    rr.rerank(result, _mk_context())

    assert result.ranked_files[:3] == ["b.py", "c.py", "a.py"]
    # tail untouched, in original order
    assert result.ranked_files[3:] == ["tail1.py", "tail2.py"]
    # instrumentation stashed for FE-3 analysis
    assert result.agent_results["listwise_rerank"]["pre_order"] == ["a.py", "b.py", "c.py"]


def test_rerank_flags_off_is_total_noop(monkeypatch):
    from config import config as cfg

    monkeypatch.setattr(cfg, "enable_listwise_rerank", False)
    monkeypatch.setattr(cfg, "enable_hierarchical_narrowing", False)

    llm = MagicMock()
    rr = ListwiseReranker(llm=llm)
    files = ["a.py", "b.py", "c.py"]
    result = _mk_result(files)
    rr.rerank(result, _mk_context())
    assert result.ranked_files == files
    llm.chat.completions.create.assert_not_called()


def test_rerank_tracks_token_usage(monkeypatch):
    from config import config as cfg

    monkeypatch.setattr(cfg, "enable_listwise_rerank", True)
    monkeypatch.setattr(cfg, "enable_hierarchical_narrowing", False)
    monkeypatch.setattr(cfg, "listwise_rerank_top_k", 3)
    monkeypatch.setattr(cfg, "listwise_rerank_passes", 1)

    payload = json.dumps({"ranking": ["C1", "C2", "C3"]})
    rr = ListwiseReranker(llm=_mk_llm(payload))
    result = _mk_result(["a.py", "b.py", "c.py"])
    rr.rerank(result, _mk_context())
    assert result.total_llm_calls == 1
    assert result.total_tokens == 15
