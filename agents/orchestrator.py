"""
Agent Orchestrator.
Coordinates the multi-agent pipeline: Comprehension → Navigation → Confirmation.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import config
from agents.base_agent import AgentContext, AgentResult
from agents.comprehension import ComprehensionAgent
from agents.navigation import NavigationAgent
from agents.confirmation import ConfirmationAgent
from data.loader import BugInstance
from data.preprocessor import BugReportPreprocessor, ProcessedBugReport
from tools.cache import clear_caches

logger = logging.getLogger(__name__)
console = Console()


@dataclass
class LocalizationResult:
    """Final result of the bug localization pipeline."""
    instance_id: str
    success: bool = False
    ranked_files: list[str] = field(default_factory=list)
    ranked_locations: list[dict] = field(default_factory=list)
    explanation: str = ""
    root_cause: str = ""
    total_time: float = 0.0
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    agent_results: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "success": self.success,
            "ranked_files": self.ranked_files,
            "ranked_locations": self.ranked_locations,
            "explanation": self.explanation[:500],
            "root_cause": self.root_cause,
            "total_time": round(self.total_time, 2),
            "total_llm_calls": self.total_llm_calls,
            "total_tool_calls": self.total_tool_calls,
        }


class Orchestrator:
    """Coordinates the multi-agent bug localization pipeline."""

    def __init__(self, retriever=None):
        """
        Args:
            retriever: Optional CodeRetriever instance for semantic search.
                       If None, semantic search will be disabled.
        """
        self.retriever = retriever
        self.preprocessor = BugReportPreprocessor()

        # Initialize agents
        self.comprehension_agent = ComprehensionAgent()
        self.navigation_agent = NavigationAgent()
        self.confirmation_agent = ConfirmationAgent()

    def localize(
        self,
        bug_instance: BugInstance,
        repo_path: str = None,
        verbose: bool = False,
    ) -> LocalizationResult:
        """
        Run the full bug localization pipeline.

        Args:
            bug_instance: The bug instance to localize
            repo_path: Path to the repository (if already cloned)
            verbose: Whether to print detailed output

        Returns:
            LocalizationResult with ranked suspicious locations
        """
        start_time = time.time()
        result = LocalizationResult(instance_id=bug_instance.instance_id)

        # Caches cleared externally or handled by LRU in a thread-safe manner

        # Step 0: Preprocess bug report
        if verbose:
            console.print(Panel(
                f"[bold]Bug: {bug_instance.instance_id}[/bold]\n"
                f"{bug_instance.problem_statement[:200]}...",
                title="🐛 Bug Report",
            ))

        processed = self.preprocessor.process(bug_instance)

        # Checkout repo if needed
        if repo_path is None:
            try:
                repo_path = self.preprocessor.checkout_repo(bug_instance)
            except Exception as e:
                logger.error(f"Failed to checkout repo: {e}")
                result.success = False
                result.explanation = f"Failed to checkout repo: {e}"
                return result

        is_java = "defects4j" in bug_instance.repo.lower()

        # Build shared context
        context = AgentContext(
            instance_id=bug_instance.instance_id,
            problem_statement=bug_instance.problem_statement,
            repo_path=repo_path,
            error_messages=processed.error_messages,
            stack_traces=processed.stack_traces,
            mentioned_files=processed.mentioned_files,
            mentioned_functions=processed.mentioned_functions,
            keywords=processed.keywords,
            retriever=self.retriever,
            language="java" if is_java else "python",
            file_extension="*.java" if is_java else "*.py"
        )

        # Build Code Property Graph in background (ready by Phase 2)
        graph_future: Future | None = None
        if config.enable_graph_rag:
            executor = ThreadPoolExecutor(max_workers=1)
            graph_future = executor.submit(self._build_graph, repo_path, verbose)
            executor.shutdown(wait=False)

        # === Phase 1: Fault Comprehension ===
        if verbose:
            console.print("\n[bold cyan]Phase 1: Fault Comprehension[/bold cyan]")

        comp_result = self.comprehension_agent.run(context)
        result.agent_results["comprehension"] = {
            "success": comp_result.success,
            "llm_calls": comp_result.num_llm_calls,
            "tool_calls": comp_result.num_tool_calls,
        }
        result.total_llm_calls += comp_result.num_llm_calls
        result.total_tool_calls += comp_result.num_tool_calls

        if comp_result.success:
            self.comprehension_agent.process_result(comp_result, context)
            if verbose:
                console.print(
                    f"  ✅ Hypothesis: {context.fault_hypothesis[:200]}"
                )
                console.print(
                    f"  📂 Candidate files: {context.candidate_files}"
                )
        else:
            logger.warning("Comprehension agent failed, continuing with defaults")
            if verbose:
                console.print(f"  ❌ Failed: {comp_result.error}")

        # Collect graph from background build (blocks only if not yet done)
        if graph_future is not None:
            try:
                graph_retriever = graph_future.result(timeout=300)
                context.graph_retriever = graph_retriever
                if verbose:
                    stats = graph_retriever.graph.stats()
                    console.print(
                        f"  📊 Graph RAG ready: {stats['total_nodes']} nodes, "
                        f"{stats['total_edges']} edges"
                    )
            except Exception as e:
                import traceback
                logger.warning(
                    f"Graph RAG build failed, continuing without it: "
                    f"{type(e).__name__}: {e}"
                )
                logger.debug(f"Graph RAG traceback:\n{traceback.format_exc()}")
                if verbose:
                    console.print(f"  ⚠️  Graph RAG unavailable: {type(e).__name__}: {e}")

        # === Phase 2: Codebase Navigation ===
        if verbose:
            console.print("\n[bold cyan]Phase 2: Codebase Navigation[/bold cyan]")

        nav_result = self.navigation_agent.run(context)
        result.agent_results["navigation"] = {
            "success": nav_result.success,
            "llm_calls": nav_result.num_llm_calls,
            "tool_calls": nav_result.num_tool_calls,
        }
        result.total_llm_calls += nav_result.num_llm_calls
        result.total_tool_calls += nav_result.num_tool_calls

        if nav_result.success:
            self.navigation_agent.process_result(nav_result, context)
            if verbose:
                console.print(
                    f"  ✅ Found {len(context.candidate_files)} candidate files"
                )
        else:
            logger.warning("Navigation agent failed")
            if verbose:
                console.print(f"  ❌ Failed: {nav_result.error}")

        # === Phase 3: Fault Confirmation ===
        if verbose:
            console.print("\n[bold cyan]Phase 3: Fault Confirmation[/bold cyan]")
            console.print(f"  Candidate Files before validation: {context.candidate_files}")

        conf_result = self.confirmation_agent.run(context)
        result.agent_results["confirmation"] = {
            "success": conf_result.success,
            "llm_calls": conf_result.num_llm_calls,
            "tool_calls": conf_result.num_tool_calls,
        }
        result.total_llm_calls += conf_result.num_llm_calls
        result.total_tool_calls += conf_result.num_tool_calls

        if conf_result.success:
            self.confirmation_agent.process_result(conf_result, context)

            # Extract final results
            output = conf_result.output
            result.ranked_locations = output.get("ranked_locations", [])
            result.ranked_files = context.candidate_files
            result.explanation = conf_result.explanation
            result.root_cause = output.get("root_cause", "")
            result.success = True

            if verbose:
                self._print_results(result)
        else:
            # Fallback: use navigation results
            result.ranked_files = context.candidate_files
            result.success = bool(context.candidate_files)
            if verbose:
                console.print(f"  ❌ Confirmation failed: {conf_result.error}")
                console.print(
                    f"  ⚠️  Falling back to navigation results: "
                    f"{context.candidate_files}"
                )

        result.total_time = time.time() - start_time

        if verbose:
            console.print(
                f"\n⏱️  Total time: {result.total_time:.1f}s | "
                f"LLM calls: {result.total_llm_calls} | "
                f"Tool calls: {result.total_tool_calls}"
            )

        return result

    @staticmethod
    def _build_graph(repo_path: str, verbose: bool = False):
        """Build Code Property Graph for a repository (runs in background thread)."""
        from rag.graph_retriever import GraphRetriever
        retriever = GraphRetriever(repo_path=repo_path)
        retriever.build_graph(language="auto")
        return retriever

    def _print_results(self, result: LocalizationResult):
        """Print formatted results."""
        table = Table(title="🎯 Localization Results")
        table.add_column("Rank", style="bold")
        table.add_column("File", style="cyan")
        table.add_column("Function", style="green")
        table.add_column("Confidence", style="yellow")
        table.add_column("Explanation", max_width=50)

        for loc in result.ranked_locations[:10]:
            func = loc.get("function_name", "")
            if loc.get("class_name"):
                func = f"{loc['class_name']}.{func}"

            table.add_row(
                str(loc.get("rank", "?")),
                loc.get("file_path", "?"),
                func,
                f"{loc.get('confidence', 0):.2f}",
                loc.get("explanation", "")[:50] + "...",
            )

        console.print(table)

        if result.root_cause:
            console.print(Panel(
                result.root_cause,
                title="🔍 Root Cause Analysis",
            ))
