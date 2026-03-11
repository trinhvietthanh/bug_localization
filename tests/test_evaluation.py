"""Unit tests for evaluation metrics."""

import pytest
from evaluation.metrics import (
    top_n_accuracy,
    reciprocal_rank,
    average_precision,
    compute_metrics,
    _paths_match,
)


class TestPathMatching:
    def test_exact_match(self):
        assert _paths_match("src/utils.py", "src/utils.py")

    def test_suffix_match(self):
        assert _paths_match("project/src/utils.py", "src/utils.py")

    def test_no_match(self):
        assert not _paths_match("src/utils.py", "src/helpers.py")

    def test_normalized_paths(self):
        assert _paths_match("src\\utils.py", "src/utils.py")


class TestTopNAccuracy:
    def test_hit_at_1(self):
        assert top_n_accuracy(
            ["a.py", "b.py", "c.py"],
            ["a.py"],
            n=1,
        )

    def test_miss_at_1(self):
        assert not top_n_accuracy(
            ["a.py", "b.py", "c.py"],
            ["c.py"],
            n=1,
        )

    def test_hit_at_3(self):
        assert top_n_accuracy(
            ["a.py", "b.py", "c.py"],
            ["c.py"],
            n=3,
        )

    def test_empty_predictions(self):
        assert not top_n_accuracy([], ["a.py"], n=1)

    def test_multiple_ground_truths(self):
        assert top_n_accuracy(
            ["a.py", "b.py"],
            ["c.py", "b.py"],
            n=2,
        )


class TestReciprocalRank:
    def test_rank_1(self):
        assert reciprocal_rank(["a.py", "b.py"], ["a.py"]) == 1.0

    def test_rank_2(self):
        assert reciprocal_rank(["a.py", "b.py"], ["b.py"]) == 0.5

    def test_no_match(self):
        assert reciprocal_rank(["a.py", "b.py"], ["c.py"]) == 0.0

    def test_empty(self):
        assert reciprocal_rank([], ["a.py"]) == 0.0


class TestAveragePrecision:
    def test_perfect(self):
        ap = average_precision(["a.py"], ["a.py"])
        assert ap == 1.0

    def test_mixed(self):
        ap = average_precision(
            ["a.py", "b.py", "c.py"],
            ["a.py", "c.py"],
        )
        # Position 1: 1/1 = 1.0, Position 3: 2/3 = 0.667
        # AP = (1.0 + 0.667) / 2 = 0.833
        assert abs(ap - 0.833) < 0.01

    def test_no_match(self):
        assert average_precision(["a.py"], ["b.py"]) == 0.0

    def test_empty_ground_truth(self):
        assert average_precision(["a.py"], []) == 0.0

    def test_duplicate_predictions(self):
        # Case: Same file predicted twice
        preds = ["/path/a.py", "/path/a.py"]
        gts = ["/path/a.py"]
        # i=0: match (new). hits=1. P@1=1/1=1. sum=1. Found={a}
        # i=1: match (old). hits=1. P@2=1/2. Ignored (rel=0).
        # AP = 1 / 1 = 1.0
        assert average_precision(preds, gts) == 1.0

    def test_duplicate_mixed(self):
        # Case: GT=[A, B], Pred=[A, A, B]
        # i=0: A (new). hits=1. P=1/1. sum=1. Found={A}
        # i=1: A (dup). hits=1. P=1/2. Ignored.
        # i=2: B (new). hits=2. P=2/3. sum=1+0.666. Found={A, B}
        preds = ["a.py", "a.py", "b.py"]
        gts = ["a.py", "b.py"]
        ap = average_precision(preds, gts)
        assert abs(ap - 0.8333) < 0.001



class TestComputeMetrics:


    def test_basic(self):
        preds = [["a.py", "b.py"], ["c.py", "d.py"]]
        gts = [["a.py"], ["d.py"]]

        metrics = compute_metrics(preds, gts, top_n_values=[1, 3])

        assert metrics["top_1_accuracy"] == 0.5  # 1 hit out of 2
        assert metrics["top_3_accuracy"] == 1.0  # Both in top 3
        assert metrics["total_instances"] == 2
        assert metrics["mrr"] > 0

    def test_all_miss(self):
        preds = [["a.py"], ["b.py"]]
        gts = [["x.py"], ["y.py"]]

        metrics = compute_metrics(preds, gts)
        assert metrics["top_1_accuracy"] == 0.0
        assert metrics["mrr"] == 0.0
        assert metrics["map"] == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
