"""
Unified candidate scoring module.
Combines multiple signals to produce a final score for each candidate file.
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class ScoringWeights:
    """Configurable weights for different scoring signals."""

    llm_confidence: float = 1.0
    stack_trace: float = 2.5
    stack_trace_position_decay: float = 0.85
    error_message_match: float = 1.5
    mentioned_file: float = 1.2
    graph_proximity: float = 0.8
    semantic_similarity: float = 0.6
    method_count_boost: float = 0.3
    git_recency: float = 0.5
    git_recency_half_life_days: int = 90
    test_file_penalty: float = 0.5


@dataclass
class CandidateScore:
    """Score breakdown for a candidate file."""

    file_path: str
    total_score: float = 0.0
    llm_confidence: float = 0.0
    stack_trace_score: float = 0.0
    error_match_score: float = 0.0
    mentioned_score: float = 0.0
    graph_score: float = 0.0
    semantic_score: float = 0.0
    method_boost: float = 0.0
    git_recency_score: float = 0.0
    penalty: float = 0.0
    rank: int = 0


class UnifiedScorer:
    """
    Combines multiple signals to score and rank candidate files.

    Signals considered:
    1. LLM confidence scores (from Navigation/Confirmation agents)
    2. Stack trace matches (position-weighted)
    3. Error message matches in file content
    4. Explicitly mentioned files in bug report
    5. Graph RAG proximity scores
    6. Semantic similarity scores
    7. Method count aggregation
    8. Git-recency (recently-changed files are more likely buggy)
    9. Test file penalty
    """

    def __init__(self, weights: Optional[ScoringWeights] = None):
        self.weights = weights or ScoringWeights()
        # Cache last-commit timestamps (seconds since epoch) per file path
        # within the current repo to avoid repeated git subprocess calls.
        self._git_recency_cache: dict[str, float] = {}

    def score_candidates(
        self,
        candidates: list[str],
        repo_path: str,
        stack_trace_files: list[str] = None,
        error_messages: list[str] = None,
        mentioned_files: list[str] = None,
        llm_scores: dict[str, float] = None,
        graph_scores: dict[str, float] = None,
        semantic_scores: dict[str, float] = None,
        method_counts: dict[str, int] = None,
        git_recency_scores: dict[str, float] = None,
    ) -> list[CandidateScore]:
        """
        Score all candidates and return sorted by total score.

        Args:
            candidates: List of candidate file paths
            repo_path: Path to the repository
            stack_trace_files: Files extracted from stack traces (ordered by position)
            error_messages: Error/exception messages from bug report
            mentioned_files: Files explicitly mentioned in bug report
            llm_scores: Confidence scores from LLM (file -> score)
            graph_scores: Graph RAG proximity scores (file -> score)
            semantic_scores: Semantic similarity scores (file -> score)
            method_counts: Number of suspicious methods per file
            git_recency_scores: Pre-computed recency scores [0,1] per file
                (1 = touched in the most recent commit, decays with age).
                If None, scores are computed lazily via git log.

        Returns:
            List of CandidateScore objects sorted by total_score (descending)
        """
        stack_trace_files = stack_trace_files or []
        error_messages = error_messages or []
        mentioned_files = mentioned_files or []
        llm_scores = llm_scores or {}
        graph_scores = graph_scores or {}
        semantic_scores = semantic_scores or {}
        method_counts = method_counts or {}
        git_recency_scores = dict(git_recency_scores) if git_recency_scores else None

        scores = []

        for file_path in candidates:
            if git_recency_scores is None:
                recency = self._compute_git_recency_score(file_path, repo_path)
            else:
                recency = git_recency_scores.get(file_path, 0.0)
            score = self._score_single(
                file_path=file_path,
                repo_path=repo_path,
                stack_trace_files=stack_trace_files,
                error_messages=error_messages,
                mentioned_files=mentioned_files,
                llm_score=llm_scores.get(file_path, 0.0),
                graph_score=graph_scores.get(file_path, 0.0),
                semantic_score=semantic_scores.get(file_path, 0.0),
                method_count=method_counts.get(file_path, 0),
                git_recency=recency,
            )
            scores.append(score)

        scores.sort(key=lambda s: s.total_score, reverse=True)
        for rank, score in enumerate(scores, 1):
            score.rank = rank

        return scores

    def _score_single(
        self,
        file_path: str,
        repo_path: str,
        stack_trace_files: list[str],
        error_messages: list[str],
        mentioned_files: list[str],
        llm_score: float,
        graph_score: float,
        semantic_score: float,
        method_count: int,
        git_recency: float = 0.0,
    ) -> CandidateScore:
        """Score a single candidate file."""
        score = CandidateScore(file_path=file_path)
        w = self.weights

        score.llm_confidence = llm_score * w.llm_confidence

        if file_path in stack_trace_files:
            position = stack_trace_files.index(file_path)
            decay = w.stack_trace_position_decay**position
            score.stack_trace_score = w.stack_trace * decay

        if error_messages:
            score.error_match_score = (
                self._compute_error_match_score(file_path, repo_path, error_messages)
                * w.error_message_match
            )

        if file_path in mentioned_files:
            score.mentioned_score = w.mentioned_file

        if graph_score > 0:
            score.graph_score = graph_score * w.graph_proximity

        if semantic_score > 0:
            score.semantic_score = semantic_score * w.semantic_similarity

        if method_count > 1:
            score.method_boost = (method_count - 1) * w.method_count_boost

        if git_recency > 0:
            score.git_recency_score = git_recency * w.git_recency

        if self._is_test_file(file_path):
            score.penalty = w.test_file_penalty

        score.total_score = (
            score.llm_confidence
            + score.stack_trace_score
            + score.error_match_score
            + score.mentioned_score
            + score.graph_score
            + score.semantic_score
            + score.method_boost
            + score.git_recency_score
            - score.penalty
        )

        return score

    def _compute_error_match_score(
        self, file_path: str, repo_path: str, error_messages: list[str]
    ) -> float:
        """
        Compute score based on error message matches in file content.
        Returns normalized score [0, 1].
        """
        if not error_messages:
            return 0.0

        abs_path = os.path.join(repo_path, file_path)
        if not os.path.exists(abs_path):
            return 0.0

        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            return 0.0

        matches = 0
        for error_msg in error_messages:
            error_type = (
                error_msg.split(":")[0].strip() if ":" in error_msg else error_msg
            )
            if error_type and error_type in content:
                matches += 1
            elif error_msg[:50] in content:
                matches += 0.5

        return min(1.0, matches / max(1, len(error_messages)))

    def _is_test_file(self, file_path: str) -> bool:
        """Check if a file is likely a test file."""
        path_lower = file_path.lower()
        indicators = [
            "/test/",
            "/tests/",
            "/__tests__/",
            "test_",
            "_test.",
            "_tests.",
            "/spec/",
            "_spec.",
        ]
        return any(ind in path_lower for ind in indicators)

    def _compute_git_recency_score(
        self, file_path: str, repo_path: str
    ) -> float:
        """
        Score [0, 1] based on how recently the file was last touched by a commit.

        Uses exponential decay with a configurable half-life (default 90 days):
        ``score = 0.5 ** (age_days / half_life_days)``.

        - File touched today:     score = 1.0
        - Half-life days ago:     score = 0.5
        - 2x half-life days ago:  score = 0.25
        - Never touched / not in git / git unavailable: score = 0.0

        Results are cached on the scorer instance for the lifetime of one
        localization run (one repo), keyed by file path.
        """
        if file_path in self._git_recency_cache:
            return self._git_recency_cache[file_path]

        import subprocess
        import time

        half_life = max(1, self.weights.git_recency_half_life_days)
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%ct", "--", file_path],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            self._git_recency_cache[file_path] = 0.0
            return 0.0

        if result.returncode != 0 or not result.stdout.strip():
            self._git_recency_cache[file_path] = 0.0
            return 0.0

        try:
            last_commit_ts = int(result.stdout.strip())
        except ValueError:
            self._git_recency_cache[file_path] = 0.0
            return 0.0

        age_seconds = max(0.0, time.time() - last_commit_ts)
        age_days = age_seconds / 86400.0
        score = 0.5 ** (age_days / half_life)
        # Clamp to [0, 1]
        score = max(0.0, min(1.0, score))
        self._git_recency_cache[file_path] = score
        return score

def extract_llm_scores_from_locations(
    ranked_locations: list[dict],
) -> dict[str, float]:
    """Extract file -> confidence mapping from ranked locations."""
    scores = {}
    for loc in ranked_locations:
        file_path = loc.get("file_path", "")
        confidence = float(loc.get("confidence", 0.0))
        if file_path:
            current = scores.get(file_path, 0.0)
            scores[file_path] = max(current, confidence)
    return scores


def extract_method_counts_from_locations(
    ranked_locations: list[dict],
) -> dict[str, int]:
    """Count suspicious methods per file from ranked locations."""
    counts = defaultdict(int)
    for loc in ranked_locations:
        file_path = loc.get("file_path", "")
        if file_path:
            counts[file_path] += 1
    return dict(counts)


