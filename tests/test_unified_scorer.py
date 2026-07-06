"""Unit tests for evaluation/unified_scorer.py.

Focus: git-recency signal (#2) and scoring breakdown integrity.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from evaluation.unified_scorer import (
    UnifiedScorer,
    ScoringWeights,
    CandidateScore,
)


class TestScoringWeightsDefaults:
    def test_git_recency_default_weight_is_positive(self):
        """The new signal should have a non-zero default weight."""
        w = ScoringWeights()
        assert w.git_recency > 0
        assert w.git_recency_half_life_days > 0


class TestGitRecencySignal:
    def _init_git_repo(self, repo_dir: Path, files_and_ages_days: dict[str, int]):
        """
        Create a git repo and commit each file with a backdated commit date.

        files_and_ages_days: {relative_path: age_in_days}
            age_in_days = how long ago the file was last touched.
        """
        subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.local"], cwd=repo_dir, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=repo_dir, check=True
        )
        subprocess.run(
            ["git", "config", "commit.gpgsign", "false"], cwd=repo_dir, check=True
        )

        for rel_path, age_days in files_and_ages_days.items():
            full = repo_dir / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("# content\n", encoding="utf-8")
            subprocess.run(["git", "add", rel_path], cwd=repo_dir, check=True)
            # Backdate commit using GIT_AUTHOR_DATE and GIT_COMMITTER_DATE
            ts = int(time.time()) - age_days * 86400
            env = {
                **os.environ,
                "GIT_AUTHOR_DATE": f"{ts} +0000",
                "GIT_COMMITTER_DATE": f"{ts} +0000",
            }
            subprocess.run(
                ["git", "commit", "-q", "-m", f"add {rel_path}"],
                cwd=repo_dir,
                env=env,
                check=True,
            )

    def test_fresh_file_scores_higher_than_old_file(self):
        """A file committed today should out-score a file committed a year ago."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_git_repo(
                repo, {"fresh.py": 0, "stale.py": 365}
            )
            scorer = UnifiedScorer(
                weights=ScoringWeights(git_recency=1.0, git_recency_half_life_days=30)
            )
            fresh = scorer._compute_git_recency_score("fresh.py", str(repo))
            stale = scorer._compute_git_recency_score("stale.py", str(repo))
            assert fresh > 0.99, f"fresh should be ~1.0, got {fresh}"
            assert stale < 0.01, f"stale (365d, hl=30) should be ~0, got {stale}"
            assert fresh > stale

    def test_half_life_decay_matches_formula(self):
        """Score at exactly one half-life should equal 0.5."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Half-life = 10 days; commit was 10 days ago
            self._init_git_repo(repo, {"f.py": 10})
            scorer = UnifiedScorer(
                weights=ScoringWeights(git_recency=1.0, git_recency_half_life_days=10)
            )
            score = scorer._compute_git_recency_score("f.py", str(repo))
            # Allow a small slack for sub-day timing differences
            assert 0.45 <= score <= 0.55, f"expected ~0.5 at half-life, got {score}"

    def test_non_git_repo_returns_zero(self):
        """Outside a git repo, recency should be 0 (no penalty, no boost)."""
        with tempfile.TemporaryDirectory() as tmp:
            scorer = UnifiedScorer()
            score = scorer._compute_git_recency_score("anything.py", tmp)
            assert score == 0.0

    def test_results_are_cached_per_instance(self):
        """Second lookup for the same path should not re-invoke git."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_git_repo(repo, {"f.py": 5})
            scorer = UnifiedScorer()
            first = scorer._compute_git_recency_score("f.py", str(repo))
            second = scorer._compute_git_recency_score("f.py", str(repo))
            assert first == second
            assert "f.py" in scorer._git_recency_cache


class TestScoringIntegration:
    def test_recency_can_flip_ranking(self):
        """
        Two candidates with identical LLM confidence: the more-recently-touched
        one should rank first once git-recency is enabled.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Set up: old_file.py touched 200 days ago, fresh_file.py touched today
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "t@t.local"], cwd=repo, check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "T"], cwd=repo, check=True
            )
            subprocess.run(
                ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True
            )

            for rel, age in [("old_file.py", 200), ("fresh_file.py", 0)]:
                (repo / rel).write_text("# x\n")
                subprocess.run(["git", "add", rel], cwd=repo, check=True)
                ts = int(time.time()) - age * 86400
                env = {
                    **os.environ,
                    "GIT_AUTHOR_DATE": f"{ts} +0000",
                    "GIT_COMMITTER_DATE": f"{ts} +0000",
                }
                subprocess.run(
                    ["git", "commit", "-q", "-m", rel],
                    cwd=repo,
                    env=env,
                    check=True,
                )

            scorer = UnifiedScorer(
                weights=ScoringWeights(
                    llm_confidence=1.0,
                    git_recency=2.0,  # large enough to flip the order
                    git_recency_half_life_days=30,
                )
            )
            # Both files report identical LLM confidence
            llm_scores = {"old_file.py": 0.5, "fresh_file.py": 0.5}
            results = scorer.score_candidates(
                candidates=["old_file.py", "fresh_file.py"],
                repo_path=str(repo),
                llm_scores=llm_scores,
            )
            top = results[0]
            assert top.file_path == "fresh_file.py", (
                f"expected fresh_file.py first, got {top.file_path}; "
                f"scores: {[(r.file_path, r.total_score) for r in results]}"
            )
            assert top.git_recency_score > 0

    def test_zero_recency_weight_preserves_old_behaviour(self):
        """If the recency weight is 0, the signal should not affect ranking."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "t@t.local"], cwd=repo, check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "T"], cwd=repo, check=True
            )
            subprocess.run(
                ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True
            )
            for rel, age in [("a.py", 365), ("b.py", 0)]:
                (repo / rel).write_text("# x\n")
                subprocess.run(["git", "add", rel], cwd=repo, check=True)
                ts = int(time.time()) - age * 86400
                env = {
                    **os.environ,
                    "GIT_AUTHOR_DATE": f"{ts} +0000",
                    "GIT_COMMITTER_DATE": f"{ts} +0000",
                }
                subprocess.run(
                    ["git", "commit", "-q", "-m", rel],
                    cwd=repo,
                    env=env,
                    check=True,
                )

            scorer = UnifiedScorer(
                weights=ScoringWeights(llm_confidence=1.0, git_recency=0.0)
            )
            results = scorer.score_candidates(
                candidates=["a.py", "b.py"],
                repo_path=str(repo),
                llm_scores={"a.py": 0.9, "b.py": 0.1},
            )
            # Higher-LLM file should win since recency is disabled
            assert results[0].file_path == "a.py"
            assert results[0].git_recency_score == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
