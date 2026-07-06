"""
_shared.py — shared helpers for batch benchmark evaluation commands.

cmd_defects4j (and other batch commands) use this to avoid code duplication.
"""

import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from config import config

console = Console()


def make_orchestrator(args):
    """Create an Orchestrator, optionally disabling graph RAG."""
    from agents.orchestrator import Orchestrator

    if getattr(args, "no_graph_rag", False):
        config.enable_graph_rag = False

    try:
        from commands.localize import _make_retriever
        retriever = _make_retriever()
    except Exception:
        retriever = None

    return Orchestrator(retriever=retriever)


def process_bug(bug_obj, orchestrator, repo_base_path: str, args, *, has_methods: bool = False):
    """Evaluate a single bug instance. Returns a result tuple."""
    from evaluation.metrics import (
        top_n_accuracy, reciprocal_rank,
        compute_method_metrics, method_top_n_accuracy, method_reciprocal_rank,
        normalize_d4j_ground_truth_methods,
    )
    start_t = time.time()

    # Resolve per-bug repo path
    repo = Path(repo_base_path) / getattr(bug_obj, "project", "") / bug_obj.instance_id
    if not repo.exists():
        repo = Path(repo_base_path)

    # Convert to BugInstance
    from_bug = getattr(bug_obj, "_to_bug_instance", None)
    if from_bug:
        bug_instance = from_bug()
    else:
        # Generic fallback — each dataset module exposes a to_bug_instance()
        raise TypeError(f"bug_obj {type(bug_obj)} must expose _to_bug_instance()")

    try:
        passes = getattr(args, "multi_pass", 1) or 1
        timeout = config.per_bug_timeout

        def _run_localize():
            if passes > 1:
                return orchestrator.multi_pass_localize(
                    bug_instance,
                    repo_path=str(repo),
                    verbose=getattr(args, "verbose", False),
                    passes=passes,
                    reflection_max_rounds=getattr(args, "reflection_rounds", None),
                    reflection_conf_threshold=getattr(args, "reflection_threshold", None),
                )
            return orchestrator.localize(
                bug_instance,
                repo_path=str(repo),
                verbose=getattr(args, "verbose", False),
                reflection_max_rounds=getattr(args, "reflection_rounds", None),
                reflection_conf_threshold=getattr(args, "reflection_threshold", None),
            )

        if timeout > 0:
            import concurrent.futures as _cf
            _tex = _cf.ThreadPoolExecutor(max_workers=1)
            _fut = _tex.submit(_run_localize)
            try:
                res = _fut.result(timeout=timeout)
            except _cf.TimeoutError:
                # shutdown(wait=False) lets execution continue immediately without
                # blocking on the zombie thread.  The background thread will be
                # collected by the OS when the process exits (daemon behaviour).
                _tex.shutdown(wait=False)
                raise TimeoutError(
                    f"Bug {bug_obj.instance_id} exceeded {timeout}s timeout"
                )
            else:
                _tex.shutdown(wait=False)
        else:
            res = _run_localize()

        r_files = res.ranked_files
        rr = reciprocal_rank(r_files, bug_obj.buggy_files)
        hit = top_n_accuracy(r_files, bug_obj.buggy_files, 1)

        # Method-level metrics (Defects4J only)
        r_methods = getattr(res, "ranked_methods", [])
        gt_methods: list[str] = []
        m_hit, m_rr = False, 0.0
        if has_methods and hasattr(bug_obj, "buggy_methods"):
            gt_methods = normalize_d4j_ground_truth_methods(bug_obj.buggy_methods)
            if gt_methods:
                m_hit = method_top_n_accuracy(r_methods, gt_methods, 1)
                m_rr = method_reciprocal_rank(r_methods, gt_methods)

        inst_dict = {
            "instance_id": bug_obj.instance_id,
            "predicted": r_files[:5],
            "predicted_methods": r_methods[:5],
            "ground_truth": bug_obj.buggy_files,
            "ground_truth_methods": gt_methods,
            "rr": rr,
            "method_rr": m_rr,
            "time": res.total_time,
            "llm_calls": res.total_llm_calls,
            "tool_calls": res.total_tool_calls,
            "prompt_tokens": res.total_prompt_tokens,
            "completion_tokens": res.total_completion_tokens,
            "total_tokens": res.total_tokens,
        }
        return (bug_obj.instance_id, True, inst_dict, r_files, bug_obj.buggy_files,
                hit, rr, None, r_methods, gt_methods)

    except Exception as exc:
        err_dict = {
            "instance_id": bug_obj.instance_id,
            "predicted": [],
            "predicted_methods": [],
            "ground_truth": bug_obj.buggy_files,
            "ground_truth_methods": [],
            "rr": 0.0,
            "method_rr": 0.0,
            "time": time.time() - start_t,
            "llm_calls": 0,
            "tool_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        return (bug_obj.instance_id, False, err_dict, [], bug_obj.buggy_files,
                False, 0.0, str(exc), [], [])


def run_batch(bugs, orchestrator, args, *, has_methods: bool = False):
    """
    Run batch evaluation with progress display.
    Returns (all_preds, all_gts, all_pred_methods, all_gt_methods, per_instance).
    """
    workers = getattr(args, "workers", 1) or 1
    all_preds, all_gts = [], []
    all_pred_methods, all_gt_methods = [], []
    per_instance = []
    print_lock = threading.Lock()

    console.print(f"\n[bold cyan]🚀 Starting batch evaluation...[/bold cyan]")
    console.print(f"   Workers: {workers}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Evaluating ({workers} workers)", total=len(bugs))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_bug = {
                executor.submit(
                    process_bug, bug, orchestrator, args.repo_path, args,
                    has_methods=has_methods
                ): bug
                for bug in bugs
            }

            completed = 0
            for future in as_completed(future_to_bug):
                bug = future_to_bug[future]
                try:
                    (iid, success, inst_data, p_files, g_files, hit, rr, err,
                     p_methods, g_methods) = future.result()
                    all_preds.append(p_files)
                    all_gts.append(g_files)
                    all_pred_methods.append(p_methods)
                    all_gt_methods.append(g_methods)
                    per_instance.append(inst_data)
                    with print_lock:
                        if success:
                            status_icon = "✅" if hit else "❌"
                            console.print(
                                f"  {status_icon} {iid:20s} | RR={rr:.3f} | "
                                f"Tokens: {inst_data.get('total_tokens', 0):,} | "
                                f"Predicted: {p_files[:3]}"
                            )
                        else:
                            console.print(f"  [red]❌ ERROR {iid:20s}: {err}[/red]")
                except Exception as exc:
                    with print_lock:
                        console.print(f"  [red]❌ UNEXPECTED ERROR {bug.instance_id}: {exc}[/red]")

                completed += 1
                progress.update(
                    task, advance=1,
                    description=f"[{completed}/{len(bugs)}] {bug.instance_id}",
                )

    return all_preds, all_gts, all_pred_methods, all_gt_methods, per_instance


def export_and_print(
    per_instance: list,
    all_preds: list,
    all_gts: list,
    all_pred_methods: list,
    all_gt_methods: list,
    args,
    dataset_name: str,
    *,
    has_methods: bool = False,
    default_output: str = "results/evaluation",
):
    """Compute metrics, print tables, and export CSV + JSON."""
    from evaluation.metrics import compute_metrics, compute_method_metrics
    from evaluation.export import export_results

    metrics = compute_metrics(all_preds, all_gts, [1, 3, 5])
    method_metrics = compute_method_metrics(all_pred_methods, all_gt_methods, [1, 3, 5]) \
        if has_methods else {}
    all_metrics = {**metrics, **method_metrics}

    # File-level table
    table = Table(title="📊 Evaluation Results (File-Level)")
    table.add_column("Metric", style="bold")
    table.add_column("Value", style="cyan")
    for k, v in metrics.items():
        table.add_row(k, f"{v:.4f}" if isinstance(v, float) else str(v))
    console.print("\n")
    console.print(table)

    # Method-level table
    if has_methods and method_metrics.get("method_total_instances", 0) > 0:
        m_table = Table(title="📊 Evaluation Results (Method-Level)")
        m_table.add_column("Metric", style="bold")
        m_table.add_column("Value", style="magenta")
        for k, v in method_metrics.items():
            m_table.add_row(k, f"{v:.4f}" if isinstance(v, float) else str(v))
        console.print(m_table)

    output_base = args.output or default_output
    out_base = Path(output_base)
    if out_base.suffix in (".csv", ".json"):
        out_base = out_base.with_suffix("")
    out_base.parent.mkdir(parents=True, exist_ok=True)

    metadata = {
        "dataset": dataset_name,
        "model": config.llm.model,
        "provider": config.llm.provider,
    }
    csv_paths = export_results(per_instance, all_metrics, str(out_base), metadata=metadata)
    console.print(f"\n💾 [bold green]Results exported:[/bold green]")
    console.print(f"   📄 Per-instance CSV: {csv_paths['results_csv']}")
    console.print(f"   📄 Summary CSV:      {csv_paths['summary_csv']}")

    json_path = str(out_base) + ".json"
    with open(json_path, "w") as f:
        json.dump(
            {"dataset": dataset_name, "model": config.llm.model,
             "provider": config.llm.provider, "metrics": metrics,
             "method_metrics": method_metrics, "per_instance": per_instance},
            f, indent=2,
        )
    console.print(f"   📄 Full JSON:        {json_path}")
