"""
Bug Localization System — Agentic AI
Main CLI entry point.

All command implementations live in the `commands/` package.
This file is a thin argparse dispatcher.

Usage:
    python main.py localize --bug-report "description" --repo-path ./repo
    python main.py evaluate --limit 10 --output results/eval.json
    python main.py index --repo-path ./repo
"""

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from config import config

console = Console()


def setup_logging(level: str = "INFO"):
    """Configure logging with Rich handler."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(message)s",
        handlers=[
            RichHandler(
                console=console,
                show_time=True,
                show_path=False,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Command imports — each command lives in its own module under commands/
# ---------------------------------------------------------------------------
from commands.localize import cmd_localize
from commands.evaluate import cmd_evaluate
from commands.index import cmd_index
from commands.graph import cmd_graph
from commands.defects4j import cmd_defects4j
from commands.swebench import cmd_swebench
from commands.sweexplore import cmd_sweexplore


def main():
    parser = argparse.ArgumentParser(
        description="🐛 Bug Localization System — Agentic AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--log-level",
        default=config.log_level,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # localize command
    loc_parser = subparsers.add_parser("localize", help="Localize a bug")
    loc_parser.add_argument("--bug-report", type=str, help="Bug description text")
    loc_parser.add_argument("--repo-path", type=str, help="Path to repository")
    loc_parser.add_argument("--instance-id", type=str, help="SWE-bench instance ID")
    loc_parser.add_argument("--dataset", type=str, help="Dataset name override")
    loc_parser.add_argument("--no-graph-rag", action="store_true", help="Disable Graph RAG")
    loc_parser.add_argument(
        "--multi-pass", type=int, default=1,
        help="Run Best-of-N localization (2-3 recommended)",
    )
    loc_parser.add_argument(
        "--reflection-rounds", type=int, default=None,
        help="Max extra Navigation+Confirmation rounds after low confidence",
    )
    loc_parser.add_argument(
        "--reflection-threshold", type=float, default=None,
        help="Minimum top-1 confidence to skip reflection",
    )

    # evaluate command
    eval_parser = subparsers.add_parser("evaluate", help="Run benchmark evaluation")
    eval_parser.add_argument("--limit", type=int, help="Max instances to evaluate")
    eval_parser.add_argument("--output", type=str, help="Output JSON path")
    eval_parser.add_argument("--verbose", action="store_true", help="Verbose output")
    eval_parser.add_argument("--no-graph-rag", action="store_true", help="Disable Graph RAG")

    # index command
    idx_parser = subparsers.add_parser("index", help="Index codebase for RAG")
    idx_group = idx_parser.add_mutually_exclusive_group()
    idx_group.add_argument("--repo-path", type=str, help="Path to a single repo to index")
    idx_group.add_argument("--project", type=str, help="Defects4J project name (e.g. Chart, Lang) — indexes one checkout per project")
    idx_parser.add_argument("--repo-id", type=str, help="Override repo_id tag stored in index (default: project name extracted from path)")
    idx_parser.add_argument("--clear", action="store_true", help="Clear existing index before indexing")

    # graph command
    graph_parser = subparsers.add_parser(
        "graph", help="Build & query Code Property Graph (Graph RAG)"
    )
    graph_parser.add_argument("--repo-path", type=str, required=True, help="Path to repository")
    graph_parser.add_argument(
        "--language", type=str, default="auto", choices=["auto", "python", "java"],
        help="Language hint",
    )
    graph_parser.add_argument("--query", type=str, help="Search query for Graph RAG")
    graph_parser.add_argument("--callers", type=str, help="Find callers of a function")
    graph_parser.add_argument("--callees", type=str, help="Find callees of a function")
    graph_parser.add_argument("--stats", action="store_true", help="Show graph statistics")
    graph_parser.add_argument(
        "--visualize", action="store_true",
        help="Generate interactive HTML + static PNG graph visualization",
    )
    graph_parser.add_argument(
        "--focus-file", type=str,
        help="Focus visualization on a specific file path (substring match)",
    )
    graph_parser.add_argument("--top-k", type=int, default=10, help="Number of results")
    graph_parser.add_argument(
        "--backend", type=str, default="memory", choices=["memory", "neo4j"],
        help="Graph storage backend (default: memory)",
    )
    graph_parser.add_argument(
        "--neo4j-uri", type=str, default="bolt://localhost:7687", help="Neo4j Bolt URI"
    )
    graph_parser.add_argument(
        "--neo4j-password", type=str, default="password", help="Neo4j password"
    )

    # defects4j command
    d4j_parser = subparsers.add_parser("defects4j", help="Evaluate on Defects4J benchmark")
    d4j_parser.add_argument(
        "--project", type=str, help="Project name (Lang, Math, Closure, Mockito, Time)"
    )
    d4j_parser.add_argument("--instance-id", type=str, help="Single instance ID (e.g. Lang_1)")
    d4j_parser.add_argument("--repo-path", type=str, help="Path to checked-out buggy repo")
    d4j_parser.add_argument("--limit", type=int, help="Max instances to evaluate")
    d4j_parser.add_argument(
        "--workers", type=int, default=1,
        help="Number of concurrent bug evaluations (default 1)",
    )
    d4j_parser.add_argument("--output", type=str, help="Output JSON path")
    d4j_parser.add_argument("--verbose", action="store_true", help="Verbose output")
    d4j_parser.add_argument("--list-bugs", action="store_true", help="Just list available bugs")
    d4j_parser.add_argument("--no-graph-rag", action="store_true", help="Disable Graph RAG")
    d4j_parser.add_argument(
        "--multi-pass", type=int, default=1,
        help="Run Best-of-N localization (2-3 recommended)",
    )
    d4j_parser.add_argument(
        "--reflection-rounds", type=int, default=None,
        help="Max extra Navigation+Confirmation rounds after low confidence",
    )
    d4j_parser.add_argument(
        "--reflection-threshold", type=float, default=None,
        help="Minimum top-1 confidence to skip reflection",
    )

    # swebench command
    swe_parser = subparsers.add_parser("swebench", help="Evaluate on SWE-bench benchmark")
    swe_parser.add_argument(
        "--dataset", type=str, default=config.evaluation.dataset_name,
        help="SWE-bench dataset to use",
    )
    swe_parser.add_argument("--split", type=str, default="test", help="Dataset split (default: test)")
    swe_parser.add_argument("--instance-id", type=str, help="Single instance ID")
    swe_parser.add_argument("--limit", type=int, help="Max instances to evaluate")
    swe_parser.add_argument(
        "--workers", type=int, default=1, help="Number of concurrent bug evaluations"
    )
    swe_parser.add_argument("--output", type=str, help="Output result path base")
    swe_parser.add_argument("--verbose", action="store_true", help="Verbose output")
    swe_parser.add_argument("--dry-run", action="store_true", help="Preview bugs without running")
    swe_parser.add_argument("--no-graph-rag", action="store_true", help="Disable Graph RAG")

    # sweexplore command
    explore_parser = subparsers.add_parser("sweexplore", help="Evaluate on SWE-Explore benchmark")
    explore_parser.add_argument(
        "--dataset", type=str, default="ByteDance-Seed/SWE-explore",
        help="SWE-Explore dataset name (default: ByteDance-Seed/SWE-explore)",
    )
    explore_parser.add_argument("--split", type=str, default="train", help="Dataset split (default: train)")
    explore_parser.add_argument("--instance-id", type=str, help="Single instance ID")
    explore_parser.add_argument("--limit", type=int, help="Max instances to evaluate")
    explore_parser.add_argument(
        "--workers", type=int, default=1, help="Number of concurrent bug evaluations"
    )
    explore_parser.add_argument("--output", type=str, help="Output result path base")
    explore_parser.add_argument("--verbose", action="store_true", help="Verbose output")
    explore_parser.add_argument("--dry-run", action="store_true", help="Preview bugs without running")
    explore_parser.add_argument("--no-graph-rag", action="store_true", help="Disable Graph RAG")

    args = parser.parse_args()
    setup_logging(args.log_level)

    if args.command is None:
        parser.print_help()
        return

    command_map = {
        "localize": cmd_localize,
        "evaluate": cmd_evaluate,
        "index": cmd_index,
        "graph": cmd_graph,
        "defects4j": cmd_defects4j,
        "swebench": cmd_swebench,
        "sweexplore": cmd_sweexplore,
    }

    command_map[args.command](args)


if __name__ == "__main__":
    main()
