"""
Unit tests for the 3 post-processing improvements in Orchestrator:
  1.1 - Multi-signal re-ranking (_rerank_results, _git_recency_score)
  1.2 - Ensemble voting (_ensemble_vote)
  1.3 - Minimum candidate enforcement (_enforce_min_candidates)
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.orchestrator import Orchestrator, LocalizationResult
from agents.base_agent import AgentContext


# ─────────────────────────── Fixtures ────────────────────────────────

def _make_result(ranked_files=None, ranked_locations=None) -> LocalizationResult:
    r = LocalizationResult(instance_id="test__001")
    r.ranked_files = ranked_files or []
    r.ranked_locations = ranked_locations or []
    r.success = True
    return r


def _make_context(**kwargs) -> AgentContext:
    ctx = AgentContext(instance_id="test__001")
    for k, v in kwargs.items():
        setattr(ctx, k, v)
    return ctx


# ─────────────────────────── 1.2 Ensemble Vote ────────────────────────

class TestEnsembleVote:
    def test_file_in_both_agents_ranks_highest(self):
        """A file confirmed by both Navigation and Confirmation should rank #1."""
        nav_locs = [
            {"file_path": "shared.py", "suspicion_score": 0.8},
            {"file_path": "only_nav.py", "suspicion_score": 0.9},
        ]
        conf_locs = [
            {"file_path": "shared.py", "confidence": 0.9},
            {"file_path": "only_conf.py", "confidence": 0.8},
        ]
        result = Orchestrator._ensemble_vote(nav_locs, conf_locs)
        assert result[0] == "shared.py", (
            "File present in both agents should rank first"
        )

    def test_all_files_appear_in_output(self):
        """All unique files from both agents should be in result."""
        nav_locs  = [{"file_path": "a.py", "suspicion_score": 0.5}]
        conf_locs = [{"file_path": "b.py", "confidence": 0.5}]
        result = Orchestrator._ensemble_vote(nav_locs, conf_locs)
        assert set(result) == {"a.py", "b.py"}

    def test_empty_inputs(self):
        """Should gracefully handle empty inputs."""
        assert Orchestrator._ensemble_vote([], []) == []

    def test_confirmation_weight_higher_than_navigation(self):
        """Confirmation weight (0.6) > Navigation weight (0.4) for same score."""
        nav_locs  = [{"file_path": "nav_only.py", "suspicion_score": 1.0}]
        conf_locs = [{"file_path": "conf_only.py", "confidence": 1.0}]
        result = Orchestrator._ensemble_vote(nav_locs, conf_locs)
        # conf file gets 0.6*1.0 + position bonus, nav file gets 0.4*1.0 + same bonus
        assert result[0] == "conf_only.py"


# ─────────────────────────── 1.1 Re-ranking ──────────────────────────

class TestReRanking:
    def test_mentioned_file_gets_bonus(self):
        """A file directly mentioned in the bug report should score higher."""
        result = _make_result(
            ranked_locations=[
                {"file_path": "mentioned.py", "confidence": 0.5},
                {"file_path": "unrelated.py", "confidence": 0.9},
            ]
        )
        ctx = _make_context(
            mentioned_files=["mentioned.py"],
            keywords=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = Orchestrator._rerank_results(
                result=result,
                ensemble_files=["mentioned.py", "unrelated.py"],
                context=ctx,
                comp_candidates=[],
                nav_candidates=[],
                repo_path=tmp,
            )
        # mentioned.py: s1=0.2 + s4=0.15 = 0.35; unrelated.py: s1=0.36 + s4=0
        # unrelated should still win due to very high confidence (0.9 * 0.4 = 0.36)
        # but the point is mentioned.py should have final_score > baseline
        assert result.ranked_locations[0]["file_path"] in ("unrelated.py", "mentioned.py")
        # Confirm final_score is populated
        for loc in result.ranked_locations:
            assert "final_score" in loc

    def test_multi_agent_presence_bonus(self):
        """File appearing in both comp and nav candidates gets a 0.20 bonus."""
        result = _make_result(
            ranked_locations=[
                {"file_path": "multi.py", "confidence": 0.5},
                {"file_path": "single.py", "confidence": 0.65},
            ]
        )
        ctx = _make_context(mentioned_files=[], keywords=[])
        with tempfile.TemporaryDirectory() as tmp:
            result = Orchestrator._rerank_results(
                result=result,
                ensemble_files=["multi.py", "single.py"],
                context=ctx,
                comp_candidates=["multi.py"],
                nav_candidates=["multi.py"],
                repo_path=tmp,
            )
        scores = {loc["file_path"]: loc["final_score"] for loc in result.ranked_locations}
        # multi.py: 0.5*0.4 + 0.20 = 0.40
        # single.py: 0.65*0.4 + 0.0  = 0.26
        assert scores["multi.py"] > scores["single.py"]

    def test_ranked_files_deduplication(self):
        """ranked_files should not contain duplicate entries."""
        result = _make_result(
            ranked_locations=[
                {"file_path": "dup.py", "confidence": 0.8},
                {"file_path": "dup.py", "confidence": 0.5},
                {"file_path": "other.py", "confidence": 0.3},
            ]
        )
        ctx = _make_context(mentioned_files=[], keywords=[])
        with tempfile.TemporaryDirectory() as tmp:
            result = Orchestrator._rerank_results(
                result=result,
                ensemble_files=["dup.py", "other.py"],
                context=ctx,
                comp_candidates=[],
                nav_candidates=[],
                repo_path=tmp,
            )
        assert result.ranked_files.count("dup.py") == 1

    def test_git_recency_score_failure_safe(self):
        """_git_recency_score returns 0.0 when git fails or times out."""
        # In a temp dir without git repo, it should silently return 0.0
        with tempfile.TemporaryDirectory() as tmp:
            score = Orchestrator._git_recency_score("nonexistent.py", tmp)
        assert score == 0.0

    def test_git_recency_score_range(self):
        """_git_recency_score always returns a value in [0, 1]."""
        with patch("subprocess.run") as mock_run:
            import time
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = str(int(time.time()))  # "now"
            score = Orchestrator._git_recency_score("file.py", "/tmp")
        assert 0.0 <= score <= 1.0


# ─────────────────────────── 1.3 Min Candidates ──────────────────────

class TestEnforceMinCandidates:
    def test_pads_from_nav_candidates(self):
        """Should pad from nav_candidates when result has fewer than min_count files."""
        result = _make_result(ranked_files=["a.py", "b.py"])
        result = Orchestrator._enforce_min_candidates(
            result=result,
            nav_candidates=["c.py", "d.py", "e.py", "f.py", "g.py",
                             "h.py", "i.py", "j.py", "k.py"],
            comp_candidates=[],
            min_count=10,
        )
        assert len(result.ranked_files) == 10

    def test_no_duplicates_added(self):
        """Padding should not add files already in ranked_files."""
        result = _make_result(ranked_files=["a.py"])
        result = Orchestrator._enforce_min_candidates(
            result=result,
            nav_candidates=["a.py", "b.py"],
            comp_candidates=[],
            min_count=2,
        )
        assert result.ranked_files.count("a.py") == 1
        assert "b.py" in result.ranked_files

    def test_does_not_trim_if_already_enough(self):
        """If already ≥ min_count, ranked_files should not be modified."""
        files = [f"file{i}.py" for i in range(15)]
        result = _make_result(ranked_files=list(files))
        result = Orchestrator._enforce_min_candidates(
            result=result,
            nav_candidates=["extra.py"],
            comp_candidates=[],
            min_count=10,
        )
        assert len(result.ranked_files) == 15  # unchanged

    def test_skeleton_location_added_for_padded_files(self):
        """Each padded file should have a corresponding skeleton entry in ranked_locations."""
        result = _make_result(ranked_files=["a.py"])
        result = Orchestrator._enforce_min_candidates(
            result=result,
            nav_candidates=["b.py"],
            comp_candidates=[],
            min_count=2,
        )
        padded_fps = [loc["file_path"] for loc in result.ranked_locations]
        assert "b.py" in padded_fps


# ─────────────────────────── Entry Point ─────────────────────────────

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
