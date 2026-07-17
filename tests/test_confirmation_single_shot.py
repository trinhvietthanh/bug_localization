"""Unit tests for Confirmation v2: evidence pack + structural fallback trigger."""

import pytest

from agents.base_agent import AgentContext, AgentResult
from agents.confirmation import ConfirmationAgent


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "pkg"
    src.mkdir()
    (src / "core.py").write_text(
        "\n".join(
            ["# module head"]
            + [f"x{i} = {i}" for i in range(10)]
            + ["def buggy():", "    return 1 / 0", ""]
            + [f"y{i} = {i}" for i in range(200)]
        )
    )
    (src / "other.py").write_text("def helper():\n    return 42\n")
    (src / "third.py").write_text("VALUE = 3\n")
    return tmp_path


@pytest.fixture
def agent():
    return ConfirmationAgent()


def _ctx(repo, locations, freeform=False):
    ctx = AgentContext(
        instance_id="t-1",
        problem_statement="division by zero in buggy()",
        repo_path=str(repo),
    )
    ctx.suspicious_locations = locations
    ctx.navigation_was_freeform = freeform
    ctx.candidate_files = [l["file_path"] for l in locations]
    return ctx


LOCS = [
    {"file_path": "pkg/core.py", "function_name": "buggy", "class_name": "",
     "start_line": 12, "end_line": 13, "suspicion_score": 0.9, "reason": "div by zero"},
    {"file_path": "pkg/other.py", "function_name": "helper", "class_name": "",
     "start_line": 1, "end_line": 2, "suspicion_score": 0.5, "reason": "called nearby"},
    {"file_path": "pkg/third.py", "function_name": "", "class_name": "",
     "start_line": 0, "end_line": 0, "suspicion_score": 0.3, "reason": "import site"},
]


# ── evidence pack ────────────────────────────────────────────────────────────

def test_evidence_pack_has_excerpts_ordered_by_score(agent, repo):
    ctx = _ctx(repo, LOCS)
    pack = agent._build_evidence_pack(ctx)
    assert [e["file_path"] for e in pack[:3]] == [
        "pkg/core.py", "pkg/other.py", "pkg/third.py"
    ]
    # line-range entry: numbered excerpt containing the buggy line
    assert "1 / 0" in pack[0]["excerpt"]
    assert "12 |" in pack[0]["excerpt"] or " 12 |" in pack[0]["excerpt"]
    # no line range and no function → file head fallback
    assert "VALUE = 3" in pack[2]["excerpt"]


def test_evidence_pack_caps_lines_and_topk(agent, repo, monkeypatch):
    from config import config
    monkeypatch.setattr(config, "confirmation_evidence_max_lines", 10)
    monkeypatch.setattr(config, "confirmation_evidence_top_k", 2)
    big = [dict(LOCS[0], start_line=1, end_line=150)] + LOCS[1:]
    pack = agent._build_evidence_pack(_ctx(repo, big))
    assert len(pack) == 2
    assert len(pack[0]["excerpt"].split("\n")) <= 10


def test_evidence_pack_markers_and_excludes_nonfiles(agent, repo):
    # ghost.py doesn't exist; the last entry mimics a run_search observation
    # whose file_path is actually a query string — neither may take a slot
    ctx = _ctx(repo, LOCS + [
        {"file_path": "pkg/ghost.py", "suspicion_score": 0.95},
        {"file_path": "TimeSeries aggregate downsample", "suspicion_score": 0.99},
    ])
    ctx.stack_trace_files = ["pkg/core.py"]
    ctx.mentioned_files = ["pkg/other.py"]
    pack = agent._build_evidence_pack(ctx)
    by_fp = {e["file_path"]: e for e in pack}
    assert by_fp["pkg/core.py"]["markers"] == "[IN STACK TRACE]"
    assert by_fp["pkg/other.py"]["markers"] == "[MENTIONED IN REPORT]"
    assert "pkg/ghost.py" not in by_fp
    assert "TimeSeries aggregate downsample" not in by_fp
    assert all(e["excerpt"] for e in pack)


# ── structural fallback trigger ──────────────────────────────────────────────

def _install_spies(agent, monkeypatch, shot_result="ok"):
    calls = {"single": 0, "loop": 0}

    def fake_single(context, evidence):
        calls["single"] += 1
        if shot_result is None:
            return None
        r = AgentResult(agent_name=agent.name)
        r.success = True
        r.num_llm_calls = 1
        r.output = {"ranked_locations": [{"rank": 1, "file_path": "pkg/core.py"}]} \
            if shot_result == "ok" else {}
        return r

    def fake_loop(context, max_iterations=None):
        calls["loop"] += 1
        r = AgentResult(agent_name=agent.name)
        r.success = True
        r.num_llm_calls = 5
        r.output = {"ranked_locations": [{"rank": 1, "file_path": "pkg/other.py"}]}
        return r

    monkeypatch.setattr(agent, "_run_single_shot", fake_single)
    monkeypatch.setattr(
        type(agent).__mro__[1], "run", lambda self, c, m=None: fake_loop(c, m)
    )
    return calls


def test_single_shot_used_when_evidence_sufficient(agent, repo, monkeypatch):
    from config import config
    monkeypatch.setattr(config, "confirmation_mode", "single")
    calls = _install_spies(agent, monkeypatch)
    res = agent.run(_ctx(repo, LOCS))
    assert calls == {"single": 1, "loop": 0}
    assert res.output["ranked_locations"][0]["file_path"] == "pkg/core.py"


def test_fallback_on_freeform_navigation(agent, repo, monkeypatch):
    from config import config
    monkeypatch.setattr(config, "confirmation_mode", "single")
    calls = _install_spies(agent, monkeypatch)
    agent.run(_ctx(repo, LOCS, freeform=True))
    assert calls == {"single": 0, "loop": 1}


def test_fallback_on_thin_low_confidence_evidence(agent, repo, monkeypatch):
    from config import config
    monkeypatch.setattr(config, "confirmation_mode", "single")
    calls = _install_spies(agent, monkeypatch)
    # 1 excerpt < min 3 AND score below the convergence bar → loop
    thin = [dict(LOCS[1])]  # other.py, score 0.5
    agent.run(_ctx(repo, thin))
    assert calls == {"single": 0, "loop": 1}


def test_single_shot_on_confident_convergence(agent, repo, monkeypatch):
    from config import config
    monkeypatch.setattr(config, "confirmation_mode", "single")
    calls = _install_spies(agent, monkeypatch)
    # 1 excerpt but explorer scored it 0.9 ≥ 0.8 → concentrated evidence OK
    agent.run(_ctx(repo, LOCS[:1]))
    assert calls == {"single": 1, "loop": 0}


def test_fallback_when_shot_returns_empty(agent, repo, monkeypatch):
    from config import config
    monkeypatch.setattr(config, "confirmation_mode", "single")
    calls = _install_spies(agent, monkeypatch, shot_result="empty")
    res = agent.run(_ctx(repo, LOCS))
    assert calls == {"single": 1, "loop": 1}
    # shot usage merged into loop result
    assert res.num_llm_calls == 6


def test_flag_off_goes_straight_to_loop(agent, repo, monkeypatch):
    from config import config
    monkeypatch.setattr(config, "confirmation_mode", "loop")
    calls = _install_spies(agent, monkeypatch)
    agent.run(_ctx(repo, LOCS))
    assert calls == {"single": 0, "loop": 1}


# ── hybrid verify stage ──────────────────────────────────────────────────────

def _shot_with(agent, files):
    r = AgentResult(agent_name=agent.name)
    r.success = True
    r.num_llm_calls = 1
    r.output = {"ranked_locations": [
        {"rank": i, "file_path": fp, "confidence": 0.9 - i * 0.1,
         "explanation": f"e{i}"}
        for i, fp in enumerate(files, 1)
    ]}
    return r


def _patch_verify_loop(agent, monkeypatch, ranked, success=True, calls=3):
    def fake_loop(self, context, max_iterations=None):
        r = AgentResult(agent_name=agent.name)
        r.success = success
        r.num_llm_calls = calls
        r.output = {"ranked_locations": [
            {"rank": i, "file_path": fp, "confidence": 0.95,
             "explanation": "verified"}
            for i, fp in enumerate(ranked, 1)
        ]} if success else {}
        return r

    monkeypatch.setattr(type(agent).__mro__[1], "run", fake_loop)


def test_hybrid_verify_reorders_head_keeps_tail(agent, repo, monkeypatch):
    shot = _shot_with(agent, ["a.py", "b.py", "c.py", "d.py", "e.py"])
    _patch_verify_loop(agent, monkeypatch, ["b.py", "a.py"])
    agent._verify_top(shot, _ctx(repo, LOCS))
    fps = [l["file_path"] for l in shot.output["ranked_locations"]]
    # head: verify order (b, a) + dropped finalist c; tail d, e untouched
    assert fps == ["b.py", "a.py", "c.py", "d.py", "e.py"]
    assert [l["rank"] for l in shot.output["ranked_locations"]] == [1, 2, 3, 4, 5]
    assert shot.num_llm_calls == 4  # 1 shot + 3 verify


def test_hybrid_verify_ignores_injected_files(agent, repo, monkeypatch):
    shot = _shot_with(agent, ["a.py", "b.py", "c.py", "d.py"])
    _patch_verify_loop(agent, monkeypatch, ["x.py", "c.py"])
    agent._verify_top(shot, _ctx(repo, LOCS))
    fps = [l["file_path"] for l in shot.output["ranked_locations"]]
    assert fps == ["c.py", "a.py", "b.py", "d.py"]
    assert "x.py" not in fps


def test_hybrid_verify_failure_keeps_listwise_order(agent, repo, monkeypatch):
    shot = _shot_with(agent, ["a.py", "b.py", "c.py"])
    _patch_verify_loop(agent, monkeypatch, [], success=False, calls=2)
    agent._verify_top(shot, _ctx(repo, LOCS))
    fps = [l["file_path"] for l in shot.output["ranked_locations"]]
    assert fps == ["a.py", "b.py", "c.py"]
    assert shot.num_llm_calls == 3  # usage still merged


def test_hybrid_verify_skipped_for_single_candidate(agent, repo, monkeypatch):
    shot = _shot_with(agent, ["a.py"])
    called = {"n": 0}

    def fake_loop(self, context, max_iterations=None):
        called["n"] += 1
        return AgentResult(agent_name=agent.name)

    monkeypatch.setattr(type(agent).__mro__[1], "run", fake_loop)
    agent._verify_top(shot, _ctx(repo, LOCS))
    assert called["n"] == 0


def test_hybrid_mode_runs_shot_then_verify(agent, repo, monkeypatch):
    from config import config
    monkeypatch.setattr(config, "confirmation_mode", "hybrid")
    shot = _shot_with(agent, ["a.py", "b.py", "c.py"])
    monkeypatch.setattr(agent, "_run_single_shot", lambda c, e: shot)
    _patch_verify_loop(agent, monkeypatch, ["c.py", "a.py"])
    res = agent.run(_ctx(repo, LOCS))
    fps = [l["file_path"] for l in res.output["ranked_locations"]]
    assert fps == ["c.py", "a.py", "b.py"]
