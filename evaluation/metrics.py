"""
Evaluation metrics for bug localization.
Implements Top-N Accuracy, MAP, and MRR at both file-level and method-level.
"""

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# File-level metrics
# ═══════════════════════════════════════════════════════════════

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
    Compute all file-level metrics across a set of bug instances.

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


# ═══════════════════════════════════════════════════════════════
# Method-level metrics
# ═══════════════════════════════════════════════════════════════

def method_top_n_accuracy(
    predicted_methods: list[str],
    ground_truth_methods: list[str],
    n: int,
) -> bool:
    """Check if any ground truth method appears in the top-N predictions."""
    top_n = predicted_methods[:n]
    for gt in ground_truth_methods:
        for pred in top_n:
            if _methods_match(pred, gt):
                return True
    return False


def method_reciprocal_rank(
    predicted_methods: list[str],
    ground_truth_methods: list[str],
) -> float:
    """Reciprocal rank at method level."""
    for i, pred in enumerate(predicted_methods):
        for gt in ground_truth_methods:
            if _methods_match(pred, gt):
                return 1.0 / (i + 1)
    return 0.0


def method_average_precision(
    predicted_methods: list[str],
    ground_truth_methods: list[str],
) -> float:
    """Average Precision at method level."""
    if not ground_truth_methods:
        return 0.0

    hits = 0
    sum_precision = 0.0
    found_gt_indices = set()

    for i, pred in enumerate(predicted_methods):
        match_found = False
        for gt_idx, gt in enumerate(ground_truth_methods):
            if gt_idx not in found_gt_indices and _methods_match(pred, gt):
                found_gt_indices.add(gt_idx)
                match_found = True
                break

        if match_found:
            hits += 1
            sum_precision += hits / (i + 1)

    if hits == 0:
        return 0.0
    return sum_precision / len(ground_truth_methods)


def compute_method_metrics(
    all_predicted_methods: list[list[str]],
    all_ground_truth_methods: list[list[str]],
    top_n_values: list[int] = None,
) -> dict:
    """
    Compute method-level metrics across bug instances.
    Only instances that have ground-truth methods are counted.

    Returns dict with keys prefixed ``method_``.
    """
    if top_n_values is None:
        top_n_values = [1, 3, 5]

    # Filter to instances that actually have ground truth methods
    pairs = [
        (pred, gt)
        for pred, gt in zip(all_predicted_methods, all_ground_truth_methods)
        if gt
    ]
    n = len(pairs)
    if n == 0:
        return {"method_total_instances": 0}

    metrics: dict = {}

    for k in top_n_values:
        hits = sum(1 for pred, gt in pairs if method_top_n_accuracy(pred, gt, k))
        metrics[f"method_top_{k}_accuracy"] = hits / n

    rr_scores = [method_reciprocal_rank(pred, gt) for pred, gt in pairs]
    metrics["method_mrr"] = sum(rr_scores) / n

    ap_scores = [method_average_precision(pred, gt) for pred, gt in pairs]
    metrics["method_map"] = sum(ap_scores) / n

    metrics["method_total_instances"] = n
    metrics["method_instances_with_match"] = sum(1 for rr in rr_scores if rr > 0)

    return metrics


def extract_methods_from_locations(ranked_locations: list[dict]) -> list[str]:
    """
    Build a ranked list of qualified method identifiers from the agent output.

    Each ``ranked_locations`` entry has keys: file_path, class_name, function_name.
    Produces strings like ``ClassName#methodName`` which is compatible with the
    Defects4J ground truth format after normalisation.
    """
    methods: list[str] = []
    for loc in ranked_locations:
        fp = loc.get("file_path", "")
        cls = loc.get("class_name", "")
        fn = loc.get("function_name", "")
        if not fn:
            continue
        qualified = _build_method_id(fp, cls, fn)
        if qualified and qualified not in methods:
            methods.append(qualified)
    return methods


def _strip_java_return_type_after_hash(method_id: str) -> str:
    """
    Jolemon entries often use ``ClassName#<returnType> <methodName>`` (e.g.
    ``HypergeometricDistribution#double getNumericalMean``). Predictions use
    ``ClassName#methodName``. Keep only the final token after ``#`` as the method name.
    """
    method_id = method_id.strip()
    if "#" not in method_id:
        return method_id
    cls, rhs = method_id.rsplit("#", 1)
    rhs = rhs.strip()
    words = rhs.split()
    if len(words) >= 2:
        rhs = words[-1]
    return f"{cls}#{rhs}"


def normalize_d4j_ground_truth_methods(raw_methods: list[str]) -> list[str]:
    """
    Normalise Defects4J ground-truth method entries to ``ClassName#methodName``.

    Raw format: ``Project/Project_N/src/.../File.java内ClassName#methodSignature外...``
    """
    normalised: list[str] = []
    for entry in raw_methods:
        for sub in entry.split("外"):
            sub = sub.strip()
            if not sub:
                continue
            # Split on '内' to separate file path from method info
            parts = sub.split("内")
            method_part = parts[-1].strip() if len(parts) > 1 else sub
            # method_part looks like "ClassName#methodName(argTypes)"
            # Strip arguments to get "ClassName#methodName"
            method_part = re.sub(r"\(.*?\)", "", method_part).strip()
            method_part = _strip_java_return_type_after_hash(method_part)
            if method_part and method_part not in normalised:
                normalised.append(method_part)
    return normalised


# ═══════════════════════════════════════════════════════════════
# Matching helpers
# ═══════════════════════════════════════════════════════════════

def _coerce_path_string(path: Any) -> Optional[str]:
    """
    Turn a metric input into a file path string.

    Predictions sometimes carry dict-shaped entries (e.g. LLM JSON with
    ``file_path``) while benchmarks use plain strings.
    """
    if path is None:
        return None
    if isinstance(path, str):
        s = path.strip()
        return s if s else None
    if isinstance(path, dict):
        for key in ("file_path", "path", "filepath", "file"):
            v = path.get(key)
            if isinstance(v, str):
                s = v.strip()
                if s:
                    return s
        return None
    return None


_SOURCE_ROOT_PREFIXES = (
    "source/",
    "src/main/java/",
    "src/",
)

_COLON_DESCRIPTION_RE = re.compile(
    r'^([^\s:(]+\.(?:java|py|js|ts|rb|go|kt|scala))\s*[:(].*$', re.IGNORECASE
)


def _strip_source_root(p: str) -> str:
    """Strip common Java source root prefixes to get the package-relative path."""
    for prefix in _SOURCE_ROOT_PREFIXES:
        if p.startswith(prefix):
            return p[len(prefix):]
    return p


def _paths_match(path1: Any, path2: Any) -> bool:
    """
    Check if two file paths refer to the same file.
    Handles cases where one path might be a suffix of the other,
    and normalises Java source root prefixes (source/ vs src/main/java/).
    """
    s1 = _coerce_path_string(path1)
    s2 = _coerce_path_string(path2)
    if not s1 or not s2:
        return False

    p1 = s1.replace("\\", "/").strip("/")
    p2 = s2.replace("\\", "/").strip("/")

    # Strip trailing ": description" from LLM-generated paths like
    # "source/org/jfree/Foo.java: This file contains..."
    m1 = _COLON_DESCRIPTION_RE.match(p1)
    if m1:
        p1 = m1.group(1).strip("/")
    m2 = _COLON_DESCRIPTION_RE.match(p2)
    if m2:
        p2 = m2.group(1).strip("/")

    if p1 == p2:
        return True

    if p1.endswith(p2) or p2.endswith(p1):
        return True

    # Normalise Java source root prefixes so that
    # "source/org/jfree/Foo.java" matches "src/main/java/org/jfree/Foo.java"
    n1 = _strip_source_root(p1)
    n2 = _strip_source_root(p2)
    if n1 and n2 and n1 == n2:
        return True

    if p1.split("/")[-1] == p2.split("/")[-1]:
        parts1 = p1.split("/")
        parts2 = p2.split("/")
        if len(parts1) >= 2 and len(parts2) >= 2:
            return parts1[-2:] == parts2[-2:]

    return False


def _methods_match(pred: str, gt: str) -> bool:
    """
    Fuzzy match between a predicted method identifier and a ground-truth one.

    Both are expected in ``ClassName#methodName`` or ``ClassName.methodName`` form
    (possibly with package prefix on the GT side).
    """
    p = _normalise_method_id(pred)
    g = _normalise_method_id(gt)
    if not p or not g:
        return False
    if p == g:
        return True
    # Allow suffix match (GT may carry full package prefix)
    if g.endswith(p) or p.endswith(g):
        return True
    # Compare just class#method ignoring package
    p_short = p.rsplit(".", 1)[-1] if "." in p else p
    g_short = g.rsplit(".", 1)[-1] if "." in g else g
    return p_short == g_short


def _normalise_method_id(raw: str) -> str:
    """Lowercase, strip whitespace, unify separator to ``#``."""
    s = raw.strip().lower()
    s = re.sub(r"\(.*?\)", "", s)  # strip argument lists
    s = s.replace("::", "#").replace(".", "#", s.count(".") - 1 if s.count(".") > 1 else 0)
    parts = s.rsplit("#", 1)
    if len(parts) == 2:
        cls, rhs = parts[0], parts[1].strip()
        rhs_words = rhs.split()
        if len(rhs_words) >= 2:
            rhs = rhs_words[-1]
        return f"{cls}#{rhs}"
    return s


def _build_method_id(file_path: str, class_name: str, function_name: str) -> str:
    """Build a ``ClassName#methodName`` identifier from location dict fields."""
    if class_name and function_name:
        return f"{class_name}#{function_name}"
    if function_name:
        # Derive class name from Java file name if possible
        fname = file_path.split("/")[-1] if file_path else ""
        if fname.endswith(".java"):
            cls = fname[:-5]
            return f"{cls}#{function_name}"
        return function_name
    return ""
