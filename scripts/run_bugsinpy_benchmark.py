#!/usr/bin/env python3
"""
BugsInPy Benchmark Runner.

Comprehensive benchmark evaluation script for the Bug Localization system
on the BugsInPy (Python) dataset. Generates detailed reports in CSV, JSON,
and Markdown format.

Usage:
    python scripts/run_bugsinpy_benchmark.py                          # Run all checked-out bugs
    python scripts/run_bugsinpy_benchmark.py --project thefuck        # Run only thefuck bugs
    python scripts/run_bugsinpy_benchmark.py --limit 5                # Run first 5 bugs
    python scripts/run_bugsinpy_benchmark.py --instance-id thefuck_1  # Run single bug
    python scripts/run_bugsinpy_benchmark.py --dry-run                # Preview without running

Output:
    results/bugsinpy_benchmark_results.csv     — Per-instance detailed results
    results/bugsinpy_benchmark_summary.csv     — Aggregate metrics
    results/bugsinpy_benchmark.json            — Full JSON dump
    results/bugsinpy_benchmark_report.md       — Human-readable Markdown report
"""

import sys
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.logging import RichHandler

from config import config
from data.bugsinpy_loader import BugsInPyLoader, to_bug_instance, BUGSINPY_PROJECTS
from agents.orchestrator import Orchestrator
from evaluation.metrics import (
    compute_metrics, top_n_accuracy, reciprocal_rank, average_precision
)
from evaluation.export import export_results

console = Console()
logger = logging.getLogger(__name__)


# ─────────────────────────── Helpers ────────────────────────────────

CHECKOUT_DIR = PROJECT_ROOT / "data" / "bugsinpy_checkouts"


def find_checked_out_bugs() -> list[dict]:
    """Discover all checked-out BugsInPy bug directories."""
    bugs = []
    if not CHECKOUT_DIR.exists():
        return bugs

    for project_dir in sorted(CHECKOUT_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        project_name = project_dir.name
        if project_name.endswith("-repo"):
            continue  # Skip base repos

        for bug_dir in sorted(project_dir.iterdir()):
            if not bug_dir.is_dir():
                continue
            instance_id = bug_dir.name
            # Verify it has python source files
            py_files = list(bug_dir.rglob("*.py"))
            if py_files:
                bugs.append({
                    "project": project_name,
                    "instance_id": instance_id,
                    "repo_path": str(bug_dir),
                    "num_files": len(py_files),
                    "language": "python",
                })
    return bugs


def generate_markdown_report(
    per_instance: list[dict],
    metrics: dict,
    metadata: dict,
    output_path: str,
):
    """Generate a human-readable Markdown report."""
    report = []
    report.append("# BugsInPy Benchmark Evaluation Report\n")
    report.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ")
    report.append(f"**Model:** {metadata.get('model', 'N/A')}  ")
    report.append(f"**Provider:** {metadata.get('provider', 'N/A')}  ")
    report.append(f"**Instances:** {len(per_instance)}  \n")

    # Summary metrics table
    report.append("## Aggregate Metrics\n")
    report.append("| Metric | Value |")
    report.append("|:-------|------:|")
    metric_labels = {
        "top_1_accuracy": "Top-1 Accuracy",
        "top_3_accuracy": "Top-3 Accuracy",
        "top_5_accuracy": "Top-5 Accuracy",
        "mrr": "Mean Reciprocal Rank (MRR)",
        "map": "Mean Average Precision (MAP)",
        "total_instances": "Total Instances",
        "instances_with_match": "Instances with Match",
    }
    for key, label in metric_labels.items():
        val = metrics.get(key, "N/A")
        if isinstance(val, float):
            report.append(f"| {label} | {val:.4f} |")
        else:
            report.append(f"| {label} | {val} |")

    # Per-project breakdown
    project_results = defaultdict(list)
    for inst in per_instance:
        proj = inst["instance_id"].rsplit("_", 1)[0]
        project_results[proj].append(inst)

    if len(project_results) > 1:
        report.append("\n## Per-Project Breakdown\n")
        report.append("| Project | Instances | Top-1 | Top-3 | Top-5 | MRR |")
        report.append("|:--------|----------:|------:|------:|------:|----:|")

        for proj, instances in sorted(project_results.items()):
            n = len(instances)
            t1 = sum(1 for i in instances if i.get("top1_hit", False)) / n
            t3 = sum(1 for i in instances if i.get("top3_hit", False)) / n
            t5 = sum(1 for i in instances if i.get("top5_hit", False)) / n
            mrr = sum(i.get("rr", 0) for i in instances) / n
            report.append(
                f"| {proj} | {n} | {t1:.2%} | {t3:.2%} | {t5:.2%} | {mrr:.4f} |"
            )

    # Per-instance details
    report.append("\n## Per-Instance Results\n")
    report.append("| Instance | Result | RR | Predicted File | Ground Truth | Time (s) |")
    report.append("|:---------|:------:|---:|:---------------|:-------------|:--------:|")

    for inst in per_instance:
        iid = inst["instance_id"]
        rr = inst.get("rr", 0.0)
        status = "✅" if rr > 0 else "❌"
        pred = inst.get("predicted", [])
        pred_str = pred[0].split("/")[-1] if pred else "—"
        gt = inst.get("ground_truth", [])
        gt_str = ", ".join(g.split("/")[-1] for g in gt[:2]) if gt else "—"
        t = inst.get("time", 0)
        report.append(f"| {iid} | {status} | {rr:.3f} | {pred_str} | {gt_str} | {t:.1f} |")

    # Timing analysis
    times = [i.get("time", 0) for i in per_instance if i.get("time", 0) > 0]
    if times:
        report.append("\n## Performance Analysis\n")
        report.append(f"- **Average time per instance:** {sum(times)/len(times):.1f}s")
        report.append(f"- **Fastest:** {min(times):.1f}s")
        report.append(f"- **Slowest:** {max(times):.1f}s")
        report.append(f"- **Total benchmark time:** {sum(times):.1f}s ({sum(times)/60:.1f} min)")

    # Error analysis
    errors = [i for i in per_instance if i.get("error")]
    if errors:
        report.append("\n## Errors\n")
        for e in errors:
            report.append(f"- **{e['instance_id']}:** {e['error']}")

    report.append("\n---\n*Generated by Bug Localization System — BugsInPy Benchmark*\n")

    Path(output_path).write_text("\n".join(report), encoding="utf-8")
    return output_path


# ─────────────────────────── Main ───────────────────────────────────

def run_benchmark(args):
    """Run the BugsInPy benchmark evaluation."""

    # Setup logging
    log_level = args.log_level or "INFO"
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )

    console.print(Panel(
        "[bold cyan]🐍 BugsInPy Benchmark Evaluation[/bold cyan]\n"
        f"Model: {config.llm.model} | Provider: {config.llm.provider}",
        title="Bug Localization System",
        border_style="bright_blue",
    ))

    # ─── Step 1: Discover available bugs ───

    available_bugs = find_checked_out_bugs()
    if not available_bugs:
        console.print("[red]❌ No checked-out bugs found![/red]")
        console.print(f"   Expected location: {CHECKOUT_DIR}")
        console.print("   Run: python scripts/checkout_bugsinpy.py --project thefuck --limit 5")
        return

    console.print(f"\n📂 Found [bold]{len(available_bugs)}[/bold] checked-out bugs")

    # Filter by project
    if args.project:
        available_bugs = [b for b in available_bugs if b["project"] == args.project]
        console.print(f"   Filtered to project: {args.project} → {len(available_bugs)} bugs")

    # Filter by instance
    if args.instance_id:
        available_bugs = [b for b in available_bugs if b["instance_id"] == args.instance_id]
        console.print(f"   Filtered to instance: {args.instance_id}")

    # Apply limit
    if args.limit:
        available_bugs = available_bugs[:args.limit]
        console.print(f"   Limited to: {args.limit} bugs")

    if not available_bugs:
        console.print("[red]No bugs match the filter criteria.[/red]")
        return

    # ─── Step 2: Load bug metadata ───

    console.print(f"\n📥 Loading BugsInPy bug metadata...")
    loader = BugsInPyLoader()

    bugs_to_evaluate = []
    for checkout in available_bugs:
        try:
            bug = loader.load_instance(checkout["instance_id"])
            if bug:
                bug.repo_path = checkout["repo_path"]
                bugs_to_evaluate.append({
                    "bug": bug,
                    "repo_path": checkout["repo_path"],
                    "language": checkout["language"],
                    "num_files": checkout["num_files"],
                })
        except Exception as e:
            logger.warning(f"Skipping {checkout['instance_id']}: {e}")

    console.print(f"   Loaded metadata for {len(bugs_to_evaluate)} bugs")

    if not bugs_to_evaluate:
        console.print("[red]No bugs with valid metadata found.[/red]")
        return

    # ─── Step 3: Preview ───

    preview_table = Table(title=f"🐍 Bugs to Evaluate ({len(bugs_to_evaluate)})")
    preview_table.add_column("#", style="dim", width=4)
    preview_table.add_column("Instance", style="bold")
    preview_table.add_column("Files", width=6, justify="right")
    preview_table.add_column("Ground Truth", max_width=40, style="yellow")
    preview_table.add_column("Bug Report (preview)", max_width=50)

    for i, entry in enumerate(bugs_to_evaluate):
        bug = entry["bug"]
        gt_files = ", ".join(f.split("/")[-1] for f in bug.buggy_files[:2])
        report_preview = bug.bug_report[:50] + "..." if len(bug.bug_report) > 50 else bug.bug_report
        preview_table.add_row(
            str(i + 1),
            bug.instance_id,
            str(entry["num_files"]),
            gt_files,
            report_preview,
        )

    console.print(preview_table)

    if args.dry_run:
        console.print("\n[yellow]🔍 Dry run — no evaluation performed.[/yellow]")
        return

    # ─── Step 3.5: Define worker function ───
    def process_single_bug(entry, orchestrator_obj, verbose_flag):
        bug = entry["bug"]
        repo_path = entry["repo_path"]
        
        start_t = time.time()
        try:
            bug_inst = to_bug_instance(bug)
            res = orchestrator_obj.localize(
                bug_inst,
                repo_path=repo_path,
                verbose=verbose_flag,
            )
            
            p_files = res.ranked_files
            g_files = bug.buggy_files
            
            h1 = top_n_accuracy(p_files, g_files, 1)
            h3 = top_n_accuracy(p_files, g_files, 3)
            h5 = top_n_accuracy(p_files, g_files, 5)
            r_r = reciprocal_rank(p_files, g_files)
            a_p = average_precision(p_files, g_files)
            
            e_time = time.time() - start_t
            
            inst_res = {
                "instance_id": bug.instance_id,
                "predicted": p_files[:5],
                "ground_truth": g_files,
                "top1_hit": h1, "top3_hit": h3, "top5_hit": h5,
                "rr": r_r, "ap": a_p, "time": e_time,
                "success": res.success,
                "llm_calls": res.total_llm_calls,
                "tool_calls": res.total_tool_calls,
                "root_cause": res.root_cause,
                "explanation": res.explanation,
                "error": None,
            }
            return (bug.instance_id, True, inst_res, p_files, g_files, e_time, None, h1, h3, h5, r_r)
            
        except Exception as ex:
            e_time = time.time() - start_t
            logger.error(f"Error evaluating {bug.instance_id}: {ex}", exc_info=True)
            err_res = {
                "instance_id": bug.instance_id,
                "predicted": [], "ground_truth": bug.buggy_files,
                "top1_hit": False, "top3_hit": False, "top5_hit": False,
                "rr": 0.0, "ap": 0.0, "time": e_time,
                "success": False, "error": str(ex),
            }
            return (bug.instance_id, False, err_res, [], bug.buggy_files, e_time, str(ex), False, False, False, 0.0)

    # ─── Step 4: Run evaluation ───

    console.print(f"\n[bold cyan]🚀 Starting benchmark evaluation...[/bold cyan]")
    console.print(f"   Model: {config.llm.model}")
    console.print(f"   Provider: {config.llm.provider}")
    console.print(f"   Instances: {len(bugs_to_evaluate)}\n")

    orchestrator = Orchestrator()
    all_preds = []
    all_gts = []
    per_instance = []
    total_start = time.time()
    
    workers = getattr(args, "workers", 1)
    console.print(f"   Workers: {workers}")
    
    # Thread Lock for console printing to avoid mixed output
    print_lock = threading.Lock()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Evaluating bugs ({workers} workers)", total=len(bugs_to_evaluate))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            # Submit all bugs to executor
            future_to_bug = {
                executor.submit(process_single_bug, entry, orchestrator, args.verbose): entry 
                for entry in bugs_to_evaluate
            }
            
            completed_count = 0
            for future in as_completed(future_to_bug):
                entry = future_to_bug[future]
                bug_id = entry["bug"].instance_id
                
                try:
                    (iid, success, inst_res, p_files, g_files, e_time, err_msg, h1, h3, h5, rr) = future.result()
                    
                    all_preds.append(p_files)
                    all_gts.append(g_files)
                    per_instance.append(inst_res)
                    
                    with print_lock:
                        if success:
                            status = "✅" if h1 else ("🔶" if h3 else "❌")
                            console.print(
                                f"  {status} {iid:20s} | RR={rr:.3f} | "
                                f"Top1={'Y' if h1 else 'N'} Top3={'Y' if h3 else 'N'} "
                                f"Top5={'Y' if h5 else 'N'} | "
                                f"{e_time:.1f}s | {p_files[0].split('/')[-1] if p_files else '—'}"
                            )
                        else:
                            console.print(f"  ❌ {iid:20s} | ERROR: {err_msg}")
                except Exception as exc:
                    with print_lock:
                        console.print(f"  ❌ {bug_id:20s} | UNEXPECTED ERROR: {exc}")
                
                completed_count += 1
                progress.update(task, advance=1, description=f"[{completed_count}/{len(bugs_to_evaluate)}] Completed")

    total_time = time.time() - total_start

    # ─── Step 5: Compute metrics ───

    metrics = compute_metrics(all_preds, all_gts, [1, 3, 5])
    metrics["total_time_seconds"] = round(total_time, 2)
    metrics["avg_time_per_instance"] = round(total_time / len(per_instance), 2) if per_instance else 0

    # ─── Step 6: Display results ───

    console.print("\n")
    results_table = Table(title="📊 BugsInPy Benchmark Results", border_style="bright_blue")
    results_table.add_column("Metric", style="bold", min_width=25)
    results_table.add_column("Value", style="cyan", justify="right", min_width=10)

    display_metrics = [
        ("Top-1 Accuracy", metrics.get("top_1_accuracy", 0), True),
        ("Top-3 Accuracy", metrics.get("top_3_accuracy", 0), True),
        ("Top-5 Accuracy", metrics.get("top_5_accuracy", 0), True),
        ("MRR (Mean Reciprocal Rank)", metrics.get("mrr", 0), True),
        ("MAP (Mean Average Precision)", metrics.get("map", 0), True),
        ("Total Instances", metrics.get("total_instances", 0), False),
        ("Instances with Match", metrics.get("instances_with_match", 0), False),
        ("Total Time", f"{total_time:.1f}s ({total_time/60:.1f}min)", False),
        ("Avg Time/Instance", f"{metrics['avg_time_per_instance']:.1f}s", False),
    ]

    for label, value, is_pct in display_metrics:
        if is_pct and isinstance(value, (int, float)):
            results_table.add_row(label, f"{value:.4f} ({value*100:.1f}%)")
        elif isinstance(value, float):
            results_table.add_row(label, f"{value:.4f}")
        else:
            results_table.add_row(label, str(value))

    console.print(results_table)

    # ─── Step 7: Export results ───

    output_base = args.output or "results/bugsinpy_benchmark"
    out_base = Path(output_base)
    if out_base.suffix in (".csv", ".json"):
        out_base = out_base.with_suffix("")
    out_base = str(out_base)

    metadata = {
        "dataset": "bugsinpy",
        "model": config.llm.model,
        "provider": config.llm.provider,
        "timestamp": datetime.now().isoformat(),
        "total_instances": len(per_instance),
    }

    # CSV exports
    csv_paths = export_results(per_instance, metrics, out_base, metadata=metadata)

    # JSON export
    json_path = f"{out_base}.json"
    with open(json_path, "w") as f:
        json.dump({
            "metadata": metadata,
            "metrics": metrics,
            "per_instance": per_instance,
        }, f, indent=2, default=str)

    # Markdown report
    md_path = f"{out_base}_report.md"
    generate_markdown_report(per_instance, metrics, metadata, md_path)

    console.print(f"\n💾 [bold green]Results exported:[/bold green]")
    console.print(f"   📄 Per-instance CSV:  {csv_paths['results_csv']}")
    console.print(f"   📄 Summary CSV:       {csv_paths['summary_csv']}")
    console.print(f"   📄 Full JSON:         {json_path}")
    console.print(f"   📝 Markdown report:   {md_path}")

    # ─── Step 8: Summary highlight ───

    t1 = metrics.get("top_1_accuracy", 0)
    mrr = metrics.get("mrr", 0)
    console.print(Panel(
        f"[bold green]Top-1 Accuracy: {t1*100:.1f}%[/bold green]  |  "
        f"[bold blue]MRR: {mrr:.4f}[/bold blue]  |  "
        f"[bold yellow]Time: {total_time:.0f}s[/bold yellow]",
        title="🏁 Benchmark Complete",
        border_style="green",
    ))


def parse_args():
    parser = argparse.ArgumentParser(
        description="BugsInPy Benchmark Evaluation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_bugsinpy_benchmark.py                           # All bugs
  python scripts/run_bugsinpy_benchmark.py --project thefuck         # thefuck only
  python scripts/run_bugsinpy_benchmark.py --limit 5                 # First 5 bugs
  python scripts/run_bugsinpy_benchmark.py --instance-id thefuck_1   # Single bug
  python scripts/run_bugsinpy_benchmark.py --dry-run                 # Preview only
  python scripts/run_bugsinpy_benchmark.py --verbose                 # Show agent details
""",
    )
    parser.add_argument("--project", type=str, help="Filter by project (thefuck, pandas, etc.)")
    parser.add_argument("--instance-id", type=str, help="Run single instance")
    parser.add_argument("--limit", type=int, help="Max number of bugs to evaluate")
    parser.add_argument("--workers", type=int, default=1, help="Number of concurrent bug evaluations (default 1)")
    parser.add_argument("--output", type=str, default="results/bugsinpy_benchmark", help="Output base path")
    parser.add_argument("--verbose", action="store_true", help="Show detailed agent output")
    parser.add_argument("--dry-run", action="store_true", help="Preview bugs without running")
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_benchmark(args)
