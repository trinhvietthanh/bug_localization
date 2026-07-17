"""Unit tests for H1: patch-grounded top-2 duel."""

import json

import pytest

from agents.base_agent import AgentContext
from agents.confirmation import ConfirmationAgent
from agents.orchestrator import LocalizationResult, Orchestrator


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "alpha.py").write_text("def a():\n    return 1\n")
    (tmp_path / "beta.py").write_text("def b():\n    return 2\n")
    return tmp_path


def _ctx(repo):
    return AgentContext(
        instance_id="t", problem_statement="bug", repo_path=str(repo)
    )


class _Resp:
    def __init__(self, content):
        self.usage = type("U", (), {"prompt_tokens": 10,
                                    "completion_tokens": 5,
                                    "total_tokens": 15})()
        msg = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": msg})()]


def _patch_llm(agent, monkeypatch, winner_label):
    captured = {}

    def fake_call(messages, context=None, use_tools=True):
        captured["user"] = messages[1]["content"]
        return _Resp(json.dumps({
            "patch_a": "-x\n+y", "patch_b": "-p\n+q",
            "winner": winner_label, "reason": "root cause",
        }))

    monkeypatch.setattr(agent, "_call_llm", fake_call)
    return captured


# ── ConfirmationAgent.patch_duel ─────────────────────────────────────────────

def test_duel_labels_alphabetically_and_maps_winner(repo, monkeypatch):
    agent = ConfirmationAgent()
    captured = _patch_llm(agent, monkeypatch, "B")
    # rank #1 = beta.py, #2 = alpha.py — alphabetical: A=alpha, B=beta
    winner, usage = agent.patch_duel(_ctx(repo), "beta.py", "alpha.py")
    assert winner == "beta.py"  # B maps to beta regardless of rank order
    assert usage["llm_calls"] == 1
    a_pos = captured["user"].index("Candidate A: alpha.py")
    b_pos = captured["user"].index("Candidate B: beta.py")
    assert a_pos < b_pos
    # excerpts included
    assert "def a():" in captured["user"]
    assert "def b():" in captured["user"]


def test_duel_unparseable_winner_returns_none(repo, monkeypatch):
    agent = ConfirmationAgent()

    monkeypatch.setattr(
        agent, "_call_llm",
        lambda m, context=None, use_tools=True: _Resp('{"winner": "C"}'),
    )
    winner, usage = agent.patch_duel(_ctx(repo), "alpha.py", "beta.py")
    assert winner is None
    assert usage["llm_calls"] == 1


def test_duel_call_failure_fails_open(repo, monkeypatch):
    agent = ConfirmationAgent()

    def boom(m, context=None, use_tools=True):
        raise RuntimeError("api down")

    monkeypatch.setattr(agent, "_call_llm", boom)
    winner, usage = agent.patch_duel(_ctx(repo), "alpha.py", "beta.py")
    assert winner is None
    assert usage["llm_calls"] == 0


# ── Orchestrator._run_patch_duel ─────────────────────────────────────────────

def _orch_with_fake_duel(monkeypatch, winner):
    orch = Orchestrator.__new__(Orchestrator)  # skip heavy __init__

    class FakeConf:
        def patch_duel(self, context, fp1, fp2, loc_map=None):
            return winner, {"llm_calls": 1, "prompt_tokens": 1,
                            "completion_tokens": 1, "total_tokens": 2}

    orch.confirmation_agent = FakeConf()
    return orch


def _result(files):
    r = LocalizationResult(instance_id="t")
    r.ranked_files = list(files)
    r.ranked_locations = [
        {"rank": i, "file_path": fp} for i, fp in enumerate(files, 1)
    ]
    return r


def test_orchestrator_swaps_when_second_wins(repo, monkeypatch):
    orch = _orch_with_fake_duel(monkeypatch, "beta.py")
    res = _result(["alpha.py", "beta.py", "gamma.py"])
    orch._run_patch_duel(res, _ctx(repo))
    assert res.ranked_files == ["beta.py", "alpha.py", "gamma.py"]
    assert [l["file_path"] for l in res.ranked_locations[:2]] == ["beta.py", "alpha.py"]
    assert [l["rank"] for l in res.ranked_locations] == [1, 2, 3]
    assert res.agent_results["patch_duel"]["swapped"] is True
    assert res.total_llm_calls == 1


def test_orchestrator_keeps_order_when_first_wins(repo, monkeypatch):
    orch = _orch_with_fake_duel(monkeypatch, "alpha.py")
    res = _result(["alpha.py", "beta.py"])
    orch._run_patch_duel(res, _ctx(repo))
    assert res.ranked_files == ["alpha.py", "beta.py"]
    assert res.agent_results["patch_duel"]["swapped"] is False


def test_orchestrator_keeps_order_on_duel_failure(repo, monkeypatch):
    orch = _orch_with_fake_duel(monkeypatch, None)
    res = _result(["alpha.py", "beta.py"])
    orch._run_patch_duel(res, _ctx(repo))
    assert res.ranked_files == ["alpha.py", "beta.py"]
    assert res.agent_results["patch_duel"]["swapped"] is False


def test_orchestrator_skips_single_file(repo, monkeypatch):
    called = {"n": 0}
    orch = Orchestrator.__new__(Orchestrator)

    class FakeConf:
        def patch_duel(self, *a, **k):
            called["n"] += 1
            return None, {}

    orch.confirmation_agent = FakeConf()
    res = _result(["alpha.py"])
    orch._run_patch_duel(res, _ctx(repo))
    assert called["n"] == 0
