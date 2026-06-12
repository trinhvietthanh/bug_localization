"""
evaluate command — run benchmark evaluation on SWE-bench.
"""

from pathlib import Path
from rich.console import Console

from config import config

console = Console()


def cmd_evaluate(args):
    """Run evaluation benchmark."""
    from agents.orchestrator import Orchestrator
    from evaluation.evaluator import BenchmarkEvaluator

    if getattr(args, "no_graph_rag", False):
        config.enable_graph_rag = False

    try:
        from commands.localize import _make_retriever
        retriever = _make_retriever()
    except Exception:
        retriever = None

    orchestrator = Orchestrator(retriever=retriever)
    evaluator = BenchmarkEvaluator(orchestrator=orchestrator)

    output_path = args.output or str(
        Path(config.evaluation.output_dir) / "evaluation_results.json"
    )

    evaluator.evaluate(
        limit=args.limit,
        output_path=output_path,
        verbose=args.verbose,
    )
