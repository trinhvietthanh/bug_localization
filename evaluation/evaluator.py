"""
Benchmark evaluator for the bug localization system.
Runs the pipeline on a dataset and computes metrics.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table

from config import config
from data.loader import SWEBenchLoader, BugInstance
from agents.orchestrator import Orchestrator, LocalizationResult
from evaluation.metrics import compute_metrics

logger = logging.getLogger(__name__)
console = Console()


class BenchmarkEvaluator:
    """Runs evaluation benchmarks on the bug localization system."""

    def __init__(self, orchestrator: Orchestrator = None):
        self.orchestrator = orchestrator or Orchestrator()
        self.loader = SWEBenchLoader(config.evaluation.dataset_name)

    def evaluate(
        self,
        limit: int = None,
        output_path: str = None,
        verbose: bool = False,
    ) -> dict:
        """
        Run evaluation on the SWE-bench dataset.

        Args:
            limit: Max instances to evaluate (None = all)
            output_path: Path to save results JSON
            verbose: Verbose per-instance output

        Returns:
            Dictionary with metrics and per-instance results
        """
        # Load dataset
        console.print("[bold]Loading dataset...[/bold]")
        if limit:
            instances = self.loader.load_subset(limit)
        else:
            instances = self.loader.load()

        console.print(f"Evaluating on {len(instances)} instances\n")

        from evaluation.metrics import top_n_accuracy, reciprocal_rank

        # Run pipeline on each instance
        all_predictions = []
        all_ground_truths = []
        per_instance_results = []

        def _evaluate_one(instance):
            """Evaluate a single instance (thread-safe)."""
            try:
                result = self.orchestrator.localize(
                    instance, verbose=verbose
                )
                hit = top_n_accuracy(result.ranked_files, instance.buggy_files, 1)
                rr = reciprocal_rank(result.ranked_files, instance.buggy_files)
                return {
                    "instance_id": instance.instance_id,
                    "success": result.success,
                    "predicted_files": result.ranked_files[:10],
                    "ground_truth_files": instance.buggy_files,
                    "time": result.total_time,
                    "llm_calls": result.total_llm_calls,
                    "tool_calls": result.total_tool_calls,
                    "root_cause": result.root_cause,
                    "_ranked_files": result.ranked_files,
                    "_hit": hit,
                    "_rr": rr,
                }
            except Exception as e:
                logger.error(f"Failed on {instance.instance_id}: {e}")
                return {
                    "instance_id": instance.instance_id,
                    "success": False,
                    "error": str(e),
                    "ground_truth_files": instance.buggy_files,
                    "_ranked_files": [],
                    "_hit": False,
                    "_rr": 0.0,
                }

        max_workers = config.max_parallel_evals

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:
            task = progress.add_task("Evaluating...", total=len(instances))

            if max_workers <= 1 or verbose:
                # Sequential mode (verbose requires ordered output)
                for i, instance in enumerate(instances):
                    progress.update(
                        task,
                        description=f"[{i+1}/{len(instances)}] {instance.instance_id}"
                    )
                    res = _evaluate_one(instance)
                    all_predictions.append(res["_ranked_files"])
                    all_ground_truths.append(instance.buggy_files)
                    status = "✅" if res["_hit"] else "❌"
                    logger.info(
                        f"{status} {instance.instance_id}: "
                        f"Top-1={'HIT' if res['_hit'] else 'MISS'}, RR={res['_rr']:.3f}"
                    )
                    per_instance_results.append({
                        k: v for k, v in res.items() if not k.startswith("_")
                    })
                    progress.advance(task)
            else:
                # Parallel mode — process multiple instances concurrently
                # Use ordered results to maintain deterministic output
                instance_results = [None] * len(instances)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_idx = {
                        executor.submit(_evaluate_one, inst): i
                        for i, inst in enumerate(instances)
                    }
                    for future in as_completed(future_to_idx):
                        idx = future_to_idx[future]
                        instance_results[idx] = future.result()
                        inst = instances[idx]
                        res = instance_results[idx]
                        status = "✅" if res["_hit"] else "❌"
                        logger.info(
                            f"{status} {inst.instance_id}: "
                            f"Top-1={'HIT' if res['_hit'] else 'MISS'}, RR={res['_rr']:.3f}"
                        )
                        progress.advance(task)

                for i, res in enumerate(instance_results):
                    all_predictions.append(res["_ranked_files"])
                    all_ground_truths.append(instances[i].buggy_files)
                    per_instance_results.append({
                        k: v for k, v in res.items() if not k.startswith("_")
                    })

        # Compute metrics
        metrics = compute_metrics(
            all_predictions,
            all_ground_truths,
            config.evaluation.top_n_values,
        )

        # Print results
        self._print_metrics(metrics)

        # Compile full results
        full_results = {
            "timestamp": datetime.now().isoformat(),
            "dataset": config.evaluation.dataset_name,
            "num_instances": len(instances),
            "model": config.llm.model,
            "metrics": metrics,
            "per_instance": per_instance_results,
        }

        # Save to file
        if output_path:
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            with open(output, "w") as f:
                json.dump(full_results, f, indent=2, ensure_ascii=False)
            console.print(f"\n💾 Results saved to {output_path}")

        return full_results

    def _print_metrics(self, metrics: dict):
        """Print metrics in a formatted table."""
        table = Table(title="📊 Evaluation Metrics")
        table.add_column("Metric", style="bold")
        table.add_column("Value", style="cyan")

        for key, value in metrics.items():
            if isinstance(value, float):
                table.add_row(key, f"{value:.4f}")
            else:
                table.add_row(key, str(value))

        console.print("\n")
        console.print(table)
