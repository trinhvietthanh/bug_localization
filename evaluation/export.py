"""
Result export utilities.
Supports exporting evaluation results to CSV and JSON formats.
"""

import csv
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def export_per_instance_csv(
    per_instance: list[dict],
    output_path: str,
    extra_columns: dict[str, Any] = None,
) -> str:
    """
    Export per-instance results to a CSV file.

    Args:
        per_instance: List of dicts, each with keys like:
            instance_id, predicted, ground_truth, rr, time, ...
        output_path: Path for the output CSV file.
        extra_columns: Optional dict of column_name -> value to add
                       to every row (e.g. dataset name, model name).

    Returns:
        The absolute path of the written CSV file.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not per_instance:
        logger.warning("No results to export")
        return str(out)

    # Build fieldnames from the first record + extra_columns
    fieldnames = [
        "instance_id",
        "project",
        "success",
        "top1_hit",
        "top3_hit",
        "top5_hit",
        "reciprocal_rank",
        "method_top1_hit",
        "method_top3_hit",
        "method_top5_hit",
        "method_reciprocal_rank",
        "predicted_file_1",
        "predicted_file_2",
        "predicted_file_3",
        "predicted_file_4",
        "predicted_file_5",
        "predicted_method_1",
        "predicted_method_2",
        "predicted_method_3",
        "ground_truth_files",
        "ground_truth_methods",
        "num_predicted",
        "time_seconds",
        "llm_calls",
        "tool_calls",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "root_cause",
        "explanation",
    ]

    if extra_columns:
        for col in extra_columns:
            if col not in fieldnames:
                fieldnames.insert(1, col)

    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for row_data in per_instance:
            predicted = row_data.get("predicted", [])
            predicted_methods = row_data.get("predicted_methods", [])
            ground_truth = row_data.get("ground_truth", [])
            ground_truth_methods = row_data.get("ground_truth_methods", [])
            rr = row_data.get("rr", 0.0) or 0.0
            # method_rr is explicitly None for instances with no GT method,
            # so a dict default won't apply — coerce None to 0.0 before round().
            m_rr = row_data.get("method_rr", 0.0) or 0.0

            # Compute file-level top-n hits
            gt_basenames = {Path(g).name for g in ground_truth}
            gt_set = set(ground_truth)

            def _matches(pred_file):
                if pred_file in gt_set:
                    return True
                return Path(pred_file).name in gt_basenames

            top1 = any(_matches(p) for p in predicted[:1])
            top3 = any(_matches(p) for p in predicted[:3])
            top5 = any(_matches(p) for p in predicted[:5])

            # Compute method-level top-n hits (simple substring match)
            def _method_in_gt(pred_m, gt_list, n):
                for pm in pred_m[:n]:
                    pm_lower = pm.lower()
                    for gm in gt_list:
                        if pm_lower in gm.lower() or gm.lower() in pm_lower:
                            return True
                return False

            m_top1 = _method_in_gt(predicted_methods, ground_truth_methods, 1) if ground_truth_methods else False
            m_top3 = _method_in_gt(predicted_methods, ground_truth_methods, 3) if ground_truth_methods else False
            m_top5 = _method_in_gt(predicted_methods, ground_truth_methods, 5) if ground_truth_methods else False

            inst_id = row_data.get("instance_id", "")
            project = inst_id.split("_")[0] if "_" in inst_id else ""

            row = {
                "instance_id": inst_id,
                "project": project,
                "success": row_data.get("success", top1),
                "top1_hit": int(top1),
                "top3_hit": int(top3),
                "top5_hit": int(top5),
                "reciprocal_rank": round(rr, 4),
                "method_top1_hit": int(m_top1),
                "method_top3_hit": int(m_top3),
                "method_top5_hit": int(m_top5),
                "method_reciprocal_rank": round(m_rr, 4),
                "predicted_file_1": predicted[0] if len(predicted) > 0 else "",
                "predicted_file_2": predicted[1] if len(predicted) > 1 else "",
                "predicted_file_3": predicted[2] if len(predicted) > 2 else "",
                "predicted_file_4": predicted[3] if len(predicted) > 3 else "",
                "predicted_file_5": predicted[4] if len(predicted) > 4 else "",
                "predicted_method_1": predicted_methods[0] if len(predicted_methods) > 0 else "",
                "predicted_method_2": predicted_methods[1] if len(predicted_methods) > 1 else "",
                "predicted_method_3": predicted_methods[2] if len(predicted_methods) > 2 else "",
                "ground_truth_files": "; ".join(ground_truth),
                "ground_truth_methods": "; ".join(ground_truth_methods),
                "num_predicted": len(predicted),
                "time_seconds": round(row_data.get("time", 0), 2),
                "llm_calls": row_data.get("llm_calls", ""),
                "tool_calls": row_data.get("tool_calls", ""),
                "prompt_tokens": row_data.get("prompt_tokens", ""),
                "completion_tokens": row_data.get("completion_tokens", ""),
                "total_tokens": row_data.get("total_tokens", ""),
                "root_cause": _clean_text(row_data.get("root_cause", "")),
                "explanation": _clean_text(row_data.get("explanation", "")),
            }

            # Add extra columns
            if extra_columns:
                row.update(extra_columns)

            writer.writerow(row)

    logger.info(f"Exported {len(per_instance)} results to {out}")
    return str(out.resolve())


def export_summary_csv(
    metrics: dict,
    output_path: str,
    metadata: dict = None,
) -> str:
    """
    Export aggregate metrics to a summary CSV file.

    Args:
        metrics: Dict of metric_name -> value.
        output_path: Path for the output CSV.
        metadata: Optional metadata (dataset, model, timestamp, etc.)

    Returns:
        The absolute path of the written CSV.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["metric", "value"]
    if metadata:
        fieldnames = list(metadata.keys()) + fieldnames

    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for metric_name, metric_value in metrics.items():
            row = {"metric": metric_name, "value": metric_value}
            if metadata:
                row.update(metadata)
            writer.writerow(row)

    logger.info(f"Exported {len(metrics)} metrics to {out}")
    return str(out.resolve())


def export_results(
    per_instance: list[dict],
    metrics: dict,
    output_base: str,
    metadata: dict = None,
) -> dict[str, str]:
    """
    Export both per-instance results and summary metrics.

    Writes two files:
      - {output_base}_results.csv  — per-instance details
      - {output_base}_summary.csv  — aggregate metrics

    If output_base ends with .csv, strips the extension first.

    Args:
        per_instance: Per-instance result dicts.
        metrics: Aggregate metric dict.
        output_base: Base path (e.g. 'results/d4j_lang').
        metadata: Optional metadata dict.

    Returns:
        Dict with keys 'results_csv' and 'summary_csv' pointing to files.
    """
    base = output_base
    if base.endswith(".csv"):
        base = base[:-4]
    if base.endswith(".json"):
        base = base[:-5]

    results_path = export_per_instance_csv(
        per_instance, f"{base}_results.csv", extra_columns=metadata
    )
    summary_path = export_summary_csv(
        metrics, f"{base}_summary.csv", metadata=metadata
    )

    return {
        "results_csv": results_path,
        "summary_csv": summary_path,
    }


def _clean_text(text: str, max_len: int = 300) -> str:
    """Clean text for CSV output: remove newlines, truncate."""
    if not text:
        return ""
    cleaned = text.replace("\n", " ").replace("\r", " ").strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + "..."
    return cleaned
