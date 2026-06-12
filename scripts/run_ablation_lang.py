#!/usr/bin/env python3
"""
Ablation Study — Defects4J Lang

Measures the contribution of each component by running 4 configurations:
  A (Baseline):  Graph RAG=ON,  Reflection=ON   ← full system
  B:             Graph RAG=OFF, Reflection=ON   ← no graph
  C:             Graph RAG=ON,  Reflection=OFF  ← no reflection
  D:             Graph RAG=OFF, Reflection=OFF  ← LLM-only

Each config invokes `main.py defects4j` as a subprocess so global config
state is fully isolated between runs.

Usage:
    python scripts/run_ablation_lang.py --limit 20             # quick test
    python scripts/run_ablation_lang.py                        # all 61 instances
    python scripts/run_ablation_lang.py --configs B C D        # skip A (already done)
    python scripts/run_ablation_lang.py --workers 2            # parallel workers per config
    python scripts/run_ablation_lang.py --resume               # skip completed configs

Output:
    results/ablation/lang_A_baseline.json
    results/ablation/lang_B_no_graphrag.json
    results/ablation/lang_C_no_reflection.json
    results/ablation/lang_D_llm_only.json
    results/ablation/lang_ablation_summary.json
    results/ablation/lang_ablation_report.md
"""

import sys
import json
import time
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    console = Console()
except ImportError:
    class _FallbackConsole:
        def print(self, *a, **kw): print(*a)
    console = _FallbackConsole()
    Table = Panel = None

# ─── Configuration matrix ────────────────────────────────────────────────────

CONFIGS = {
    "A": {
        "label": "Baseline (Full System)",
        "description": "Graph RAG=ON, Reflection=1 round",
        "graph_rag": True,
        "reflection_rounds": 1,
        "output": "lang_A_baseline",
    },
    "B": {
        "label": "No Graph RAG",
        "description": "Graph RAG=OFF, Reflection=1 round",
        "graph_rag": False,
        "reflection_rounds": 1,
        "output": "lang_B_no_graphrag",
    },
    "C": {
        "label": "No Reflection",
        "description": "Graph RAG=ON, Reflection=0 rounds",
        "graph_rag": True,
        "reflection_rounds": 0,
        "output": "lang_C_no_reflection",
    },
    "D": {
        "label": "LLM-Only",
        "description": "Graph RAG=OFF, Reflection=0 rounds",
        "graph_rag": False,
        "reflection_rounds": 0,
        "output": "lang_D_llm_only",
    },
}

# ─── Runner ──────────────────────────────────────────────────────────────────

REPO_PATH = str(PROJECT_ROOT / "data" / "defects4j_checkouts")
PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python")


def run_config(cfg_key: str, cfg: dict, limit: int | None, workers: int) -> dict | None:
    """Invoke main.py defects4j in a subprocess and return the parsed metrics."""
    output_dir = PROJECT_ROOT / "results" / "ablation"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_base = str(output_dir / cfg["output"])

    cmd = [
        PYTHON, str(PROJECT_ROOT / "main.py"),
        "defects4j",
        "--project", "Lang",
        "--repo-path", REPO_PATH,
        "--output", output_base,
        "--workers", str(workers),
        "--reflection-rounds", str(cfg["reflection_rounds"]),
    ]
    if not cfg["graph_rag"]:
        cmd.append("--no-graph-rag")
    if limit:
        cmd += ["--limit", str(limit)]

    console.print(f"\n[bold cyan]Config {cfg_key}: {cfg['label']}[/bold cyan]")
    console.print(f"  {cfg['description']}")
    console.print(f"  Command: {' '.join(cmd[2:])}")

    start = time.time()
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=False)
    elapsed = time.time() - start

    if proc.returncode != 0:
        console.print(f"[red]  Config {cfg_key} FAILED (exit code {proc.returncode})[/red]")
        return None

    # Load the generated JSON to extract metrics
    json_path = Path(output_base + ".json")
    if not json_path.exists():
        console.print(f"[red]  Output file not found: {json_path}[/red]")
        return None

    with open(json_path) as f:
        data = json.load(f)

    metrics = data.get("metrics", {})
    metrics["wall_time"] = round(elapsed, 1)
    console.print(
        f"  Done in {elapsed:.0f}s | "
        f"Top-1: {metrics.get('top_1_accuracy', 0)*100:.1f}% | "
        f"MRR: {metrics.get('mrr', 0):.4f}"
    )
    return metrics


# ─── Reporting ───────────────────────────────────────────────────────────────

def generate_report(results: dict[str, dict], output_dir: Path, limit: int | None) -> None:
    baseline = results.get("A", {})
    baseline_top1 = baseline.get("top_1_accuracy")

    # Rich table
    if Table:
        table = Table(title="Ablation Study — Defects4J Lang", show_lines=True)
        table.add_column("Config", style="bold", min_width=4)
        table.add_column("Description", min_width=35)
        table.add_column("Top-1", justify="right")
        table.add_column("Δ Top-1", justify="right")
        table.add_column("Top-3", justify="right")
        table.add_column("Top-5", justify="right")
        table.add_column("MRR", justify="right")

        for key in ["A", "B", "C", "D"]:
            if key not in results:
                continue
            m = results[key]
            top1 = m.get("top_1_accuracy", 0)
            delta_str = ""
            if baseline_top1 is not None and key != "A":
                diff = (top1 - baseline_top1) * 100
                color = "green" if diff >= 0 else "red"
                delta_str = f"[{color}]{diff:+.1f}%[/{color}]"

            table.add_row(
                key,
                CONFIGS[key]["description"],
                f"{top1*100:.1f}%",
                delta_str,
                f"{m.get('top_3_accuracy', 0)*100:.1f}%",
                f"{m.get('top_5_accuracy', 0)*100:.1f}%",
                f"{m.get('mrr', 0):.4f}",
            )
        console.print("\n")
        console.print(table)

    # Component impact summary
    impacts = {}
    if "A" in results and "B" in results:
        impacts["Graph RAG contribution (A vs B)"] = (
            results["A"].get("top_1_accuracy", 0) - results["B"].get("top_1_accuracy", 0)
        ) * 100
    if "A" in results and "C" in results:
        impacts["Reflection contribution (A vs C)"] = (
            results["A"].get("top_1_accuracy", 0) - results["C"].get("top_1_accuracy", 0)
        ) * 100
    if "A" in results and "D" in results:
        impacts["Combined contribution (A vs D)"] = (
            results["A"].get("top_1_accuracy", 0) - results["D"].get("top_1_accuracy", 0)
        ) * 100

    if impacts and Table:
        impact_table = Table(title="Component Contribution (Top-1 Δ)", show_lines=True)
        impact_table.add_column("Component")
        impact_table.add_column("Impact on Top-1", justify="right")
        for comp, val in impacts.items():
            color = "green" if val > 0 else ("red" if val < 0 else "yellow")
            impact_table.add_row(comp, f"[{color}]{val:+.1f}%[/{color}]")
        console.print(impact_table)

    # JSON summary
    summary = {
        "dataset": "defects4j_lang",
        "model": "gemini-2.5-flash",
        "timestamp": datetime.now().isoformat(),
        "limit": limit,
        "configs": {
            k: {
                "label": CONFIGS[k]["label"],
                "description": CONFIGS[k]["description"],
                "graph_rag": CONFIGS[k]["graph_rag"],
                "reflection_rounds": CONFIGS[k]["reflection_rounds"],
                "metrics": v,
            }
            for k, v in results.items()
        },
        "component_impact": {k: round(v, 2) for k, v in impacts.items()},
    }
    summary_path = output_dir / "lang_ablation_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    # Markdown report
    lines = [
        "# Ablation Study — Defects4J Lang\n",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  \n",
        f"**Dataset:** Defects4J Lang — {limit or 61} instances  \n",
        f"**Model:** gemini-2.5-flash\n\n",
        "## Results\n",
        "| Config | Description | Top-1 | Δ Top-1 | Top-3 | Top-5 | MRR |",
        "|--------|-------------|------:|--------:|------:|------:|----:|",
    ]
    for key in ["A", "B", "C", "D"]:
        if key not in results:
            continue
        m = results[key]
        top1 = m.get("top_1_accuracy", 0)
        delta = ""
        if baseline_top1 is not None and key != "A":
            diff = (top1 - baseline_top1) * 100
            delta = f"{diff:+.1f}%"
        lines.append(
            f"| **{key}** | {CONFIGS[key]['description']} "
            f"| {top1*100:.1f}% | {delta} "
            f"| {m.get('top_3_accuracy',0)*100:.1f}% "
            f"| {m.get('top_5_accuracy',0)*100:.1f}% "
            f"| {m.get('mrr',0):.4f} |"
        )

    lines += [
        "\n## Component Contribution Analysis\n",
        "| Component | Impact on Top-1 Accuracy |",
        "|-----------|:------------------------:|",
    ]
    for comp, val in impacts.items():
        sign = "+" if val > 0 else ""
        lines.append(f"| {comp} | **{sign}{val:.1f}%** |")

    lines += [
        "\n## Configuration Details\n",
        "| Config | Graph RAG | Reflection Rounds | Notes |",
        "|--------|:---------:|:-----------------:|-------|",
        "| A (Baseline) | ✅ ON | 1 | Full system |",
        "| B | ❌ OFF | 1 | Remove graph structure knowledge |",
        "| C | ✅ ON | 0 | Remove iterative self-refinement |",
        "| D | ❌ OFF | 0 | Pure LLM + code search only |",
        "\n## Interpretation\n",
        "- **Graph RAG contribution** = how much the Code Property Graph helps navigation",
        "- **Reflection contribution** = how much iterative re-ranking improves accuracy",
        "- **Combined** = total benefit of both components over the LLM baseline",
    ]
    report_path = output_dir / "lang_ablation_report.md"
    report_path.write_text("\n".join(lines) + "\n")

    console.print(f"\n📄 Summary → {summary_path}")
    console.print(f"📝 Report  → {report_path}")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ablation Study on Defects4J Lang")
    parser.add_argument(
        "--configs", nargs="+", choices=list(CONFIGS.keys()),
        default=list(CONFIGS.keys()),
        help="Configs to run (default: all A B C D)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max instances per config (default: all 61)",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Parallel workers per config (default: 1)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip configs that already have output files",
    )
    args = parser.parse_args()

    output_dir = PROJECT_ROOT / "results" / "ablation"
    output_dir.mkdir(parents=True, exist_ok=True)

    n_instances = args.limit or 61
    est_seconds = n_instances * 60  # ~60s/instance
    est_total = est_seconds * len(args.configs) / max(args.workers, 1)

    if Panel:
        console.print(Panel.fit(
            f"[bold]Ablation Study — Defects4J Lang[/bold]\n\n"
            f"Configs   : {', '.join(args.configs)}\n"
            f"Instances : {n_instances} per config\n"
            f"Workers   : {args.workers}\n"
            f"Est. time : ~{est_total/60:.0f} min\n\n"
            f"[dim]Results → results/ablation/[/dim]",
            title="Bug Localization Ablation",
        ))

    results = {}

    # Load completed configs when resuming
    if args.resume:
        for key in list(CONFIGS.keys()):
            path = output_dir / f"{CONFIGS[key]['output']}.json"
            if path.exists():
                data = json.loads(path.read_text())
                results[key] = data.get("metrics", {})
                console.print(f"[dim]Config {key}: loaded from {path.name}[/dim]")

    for key in args.configs:
        if args.resume and key in results:
            console.print(f"[yellow]Skipping Config {key} (already done)[/yellow]")
            continue
        metrics = run_config(key, CONFIGS[key], args.limit, args.workers)
        if metrics is not None:
            results[key] = metrics

    # Also load any non-selected configs for the comparison table
    for key in CONFIGS:
        if key not in results:
            path = output_dir / f"{CONFIGS[key]['output']}.json"
            if path.exists():
                data = json.loads(path.read_text())
                results[key] = data.get("metrics", {})

    if results:
        generate_report(results, output_dir, args.limit)
    else:
        console.print("[red]No results to report.[/red]")


if __name__ == "__main__":
    main()
