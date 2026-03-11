"""
Evaluation metrics for bug localization.
Implements Top-N Accuracy, MAP, and MRR.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def top_n_accuracy(
    predicted_files: list[str],
    ground_truth_files: list[str],
    n: int,
) -> bool:
    """
    Check if any ground truth file is in the top-N predicted files.

    Args:
        predicted_files: Ranked list of predicted file paths
        ground_truth_files: List of actual buggy file paths
        n: Top-N threshold

    Returns:
        True if at least one ground truth file is in top-N
    """
    top_n = predicted_files[:n]
    for gt in ground_truth_files:
        for pred in top_n:
            if _paths_match(pred, gt):
                return True
    return False


def reciprocal_rank(
    predicted_files: list[str],
    ground_truth_files: list[str],
) -> float:
    """
    Compute the reciprocal rank (1/rank of first correct prediction).

    Args:
        predicted_files: Ranked list of predicted file paths
        ground_truth_files: List of actual buggy file paths

    Returns:
        Reciprocal rank (0.0 if no match found)
    """
    for i, pred in enumerate(predicted_files):
        for gt in ground_truth_files:
            if _paths_match(pred, gt):
                return 1.0 / (i + 1)
    return 0.0


def average_precision(
    predicted_files: list[str],
    ground_truth_files: list[str],
) -> float:
    """
    Compute Average Precision for a single query.

    Args:
        predicted_files: Ranked list of predicted file paths
        ground_truth_files: List of actual buggy file paths

    Returns:
        Average Precision score
    """
    if not ground_truth_files:
        return 0.0

    hits = 0
    sum_precision = 0.0
    found_gt_indices = set()

    for i, pred in enumerate(predicted_files):
        # Check if this prediction matches any UNFOUND ground truth
        match_found = False
        for gt_idx, gt in enumerate(ground_truth_files):
            if gt_idx not in found_gt_indices and _paths_match(pred, gt):
                found_gt_indices.add(gt_idx)
                match_found = True
                break
        
        if match_found:
            hits += 1
            precision_at_i = hits / (i + 1)
            sum_precision += precision_at_i

    if hits == 0:
        return 0.0

    return sum_precision / len(ground_truth_files)


def compute_metrics(
    all_predictions: list[list[str]],
    all_ground_truths: list[list[str]],
    top_n_values: list[int] = None,
) -> dict:
    """
    Compute all metrics across a set of bug instances.

    Args:
        all_predictions: List of ranked predicted file lists (one per bug)
        all_ground_truths: List of ground truth file lists (one per bug)
        top_n_values: List of N values for Top-N accuracy

    Returns:
        Dictionary of metric name -> value
    """
    if top_n_values is None:
        top_n_values = [1, 3, 5]

    n = len(all_predictions)
    assert n == len(all_ground_truths), "Predictions and ground truths must have same length"

    metrics = {}

    # Top-N Accuracy
    for k in top_n_values:
        hits = sum(
            1 for pred, gt in zip(all_predictions, all_ground_truths)
            if top_n_accuracy(pred, gt, k)
        )
        metrics[f"top_{k}_accuracy"] = hits / n if n > 0 else 0.0

    # MRR (Mean Reciprocal Rank)
    rr_scores = [
        reciprocal_rank(pred, gt)
        for pred, gt in zip(all_predictions, all_ground_truths)
    ]
    metrics["mrr"] = sum(rr_scores) / n if n > 0 else 0.0

    # MAP (Mean Average Precision)
    ap_scores = [
        average_precision(pred, gt)
        for pred, gt in zip(all_predictions, all_ground_truths)
    ]
    metrics["map"] = sum(ap_scores) / n if n > 0 else 0.0

    # Additional stats
    metrics["total_instances"] = n
    metrics["instances_with_match"] = sum(
        1 for pred, gt in zip(all_predictions, all_ground_truths)
        if reciprocal_rank(pred, gt) > 0
    )

    return metrics


def _paths_match(path1: str, path2: str) -> bool:
    """
    Check if two file paths refer to the same file.
    Handles cases where one path might be a suffix of the other.
    """
    # Normalize paths
    p1 = path1.strip().replace("\\", "/").strip("/")
    p2 = path2.strip().replace("\\", "/").strip("/")

    # Exact match
    if p1 == p2:
        return True

    # One is a suffix of the other (handles relative vs absolute)
    if p1.endswith(p2) or p2.endswith(p1):
        return True

    # Match by filename only (last component)
    if p1.split("/")[-1] == p2.split("/")[-1]:
        # Check if at least the last 2 components match to avoid false positives
        parts1 = p1.split("/")
        parts2 = p2.split("/")
        if len(parts1) >= 2 and len(parts2) >= 2:
            return parts1[-2:] == parts2[-2:]

    return False
