"""
Agent Orchestrator.
Coordinates the multi-agent pipeline: Comprehension → Navigation → Confirmation.
"""

import logging
import subprocess
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import config
from agents.base_agent import AgentContext, AgentResult
from agents.comprehension import ComprehensionAgent
from agents.navigation import NavigationAgent
from agents.confirmation import ConfirmationAgent
from data.loader import BugInstance
from data.preprocessor import BugReportPreprocessor
from tools.repo_skeleton import generate_repo_skeleton

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

    _graph_cache: dict[str, object] = {}

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
        reflection_max_rounds: int | None = None,
        reflection_conf_threshold: float | None = None,
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

        rmr = (
            config.reflection_max_rounds
            if reflection_max_rounds is None
            else reflection_max_rounds
        )
        rct = (
            config.reflection_conf_threshold
            if reflection_conf_threshold is None
            else reflection_conf_threshold
        )
        rmr = max(0, min(int(rmr), 2))

        # Caches cleared externally or handled by LRU in a thread-safe manner

        # Step 0: Preprocess bug report
        if verbose:
            console.print(
                Panel(
                    f"[bold]Bug: {bug_instance.instance_id}[/bold]\n"
                    f"{bug_instance.problem_statement[:200]}...",
                    title="🐛 Bug Report",
                )
            )

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

        # Extract project from instance_id (e.g., "Lang_1" -> "Lang")
        project = (
            bug_instance.instance_id.split("_")[0]
            if "_" in bug_instance.instance_id
            else ""
        )
        is_java = project in [
            "Lang",
            "Math",
            "Chart",
            "Closure",
            "Mockito",
            "Time",
        ]

        # Build shared context
        hint_sources = list(processed.mentioned_files) + list(processed.keywords[:15])
        repo_skeleton = ""
        if config.enable_repo_skeleton:
            repo_skeleton = generate_repo_skeleton(
                repo_path=repo_path,
                language="java" if is_java else "python",
                max_files=config.repo_skeleton_max_files,
                priority_hints=hint_sources,
            )
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
            file_extension="*.java" if is_java else "*.py",
            repo_skeleton=repo_skeleton,
        )

        # Build Code Property Graph in background (ready by Phase 2)
        graph_future: Future | None = None
        graph_cache_key = self._repo_cache_key(
            repo_path, "java" if is_java else "python"
        )
        if config.enable_graph_rag and graph_cache_key in self._graph_cache:
            context.graph_retriever = self._graph_cache[graph_cache_key]
        elif config.enable_graph_rag:
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
                console.print(f"  ✅ Hypothesis: {context.fault_hypothesis[:200]}")
                console.print(f"  📂 Candidate files: {context.candidate_files}")
        else:
            logger.warning("Comprehension agent failed, continuing with defaults")
            if verbose:
                console.print(f"  ❌ Failed: {comp_result.error}")

        # Collect graph from background build (blocks only if not yet done)
        if graph_future is not None:
            try:
                graph_retriever = graph_future.result(timeout=300)
                context.graph_retriever = graph_retriever
                self._graph_cache[graph_cache_key] = graph_retriever
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
                    console.print(
                        f"  ⚠️  Graph RAG unavailable: {type(e).__name__}: {e}"
                    )

        conf_result = None
        rounds = max(0, rmr) + 1
        for round_idx in range(rounds):
            context.reflection_round = round_idx
            if verbose:
                phase_label = "Phase 2: Codebase Navigation"
                if round_idx > 0:
                    phase_label = f"Phase 2 (Reflection Round {round_idx + 1}): Codebase Navigation"
                console.print(f"\n[bold cyan]{phase_label}[/bold cyan]")

            nav_result = self.navigation_agent.run(context)
            result.agent_results[f"navigation_round_{round_idx + 1}"] = {
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

            self._rerank_candidates(context, verbose=verbose)

            if verbose:
                console.print(
                    f"  Candidate Files before validation: {context.candidate_files}"
                )

            conf_result = self.confirmation_agent.run(context)
            result.agent_results[f"confirmation_round_{round_idx + 1}"] = {
                "success": conf_result.success,
                "llm_calls": conf_result.num_llm_calls,
                "tool_calls": conf_result.num_tool_calls,
            }
            result.total_llm_calls += conf_result.num_llm_calls
            result.total_tool_calls += conf_result.num_tool_calls

            if not conf_result.success:
                continue

            self.confirmation_agent.process_result(conf_result, context)
            top_conf = self._top_confidence(conf_result)
            if top_conf >= rct or round_idx == rounds - 1:
                break
            context.reflection_feedback = (
                self.confirmation_agent.get_reflection_message(
                    conf_result,
                    confidence_threshold=rct,
                )
            )
            if verbose:
                console.print(
                    "  🔁 Low-confidence confirmation; retrying with reflection guidance "
                    f"(top={top_conf:.2f}, threshold={rct:.2f})"
                )

        if conf_result and conf_result.success:
            output = conf_result.output
            result.ranked_locations = output.get("ranked_locations", [])
            result.ranked_files = context.candidate_files
            result.explanation = conf_result.explanation
            result.root_cause = output.get("root_cause", "")
            result.success = True
            if verbose:
                self._print_results(result)
        else:
            result.ranked_files = context.candidate_files
            result.success = bool(context.candidate_files)
            if verbose and conf_result is not None:
                console.print(f"  ❌ Confirmation failed: {conf_result.error}")
                console.print(
                    f"  ⚠️  Falling back to navigation results: {context.candidate_files}"
                )

        result.total_time = time.time() - start_time

        if verbose:
            console.print(
                f"\n⏱️  Total time: {result.total_time:.1f}s | "
                f"LLM calls: {result.total_llm_calls} | "
                f"Tool calls: {result.total_tool_calls}"
            )

        return result

    def multi_pass_localize(
        self,
        bug_instance: BugInstance,
        repo_path: str = None,
        verbose: bool = False,
        passes: int = 3,
        reflection_max_rounds: int | None = None,
        reflection_conf_threshold: float | None = None,
    ) -> LocalizationResult:
        """
        Run localize() multiple times and combine predictions via weighted rank fusion.
        """
        passes = max(1, min(passes, 3))
        original_temp = config.llm.temperature
        temp_used = original_temp
        if passes > 1 and original_temp <= 0:
            temp_used = config.multi_pass_temperature
            config.llm.temperature = temp_used

        run_results: list[LocalizationResult] = []
        score_map: dict[str, float] = defaultdict(float)
        try:
            for idx in range(passes):
                if verbose:
                    console.print(
                        f"\n[bold cyan]Multi-pass run {idx + 1}/{passes}[/bold cyan]"
                    )
                run_result = self.localize(
                    bug_instance=bug_instance,
                    repo_path=repo_path,
                    verbose=verbose,
                    reflection_max_rounds=reflection_max_rounds,
                    reflection_conf_threshold=reflection_conf_threshold,
                )
                run_results.append(run_result)
                for rank, file_path in enumerate(run_result.ranked_files, 1):
                    if file_path:
                        score_map[file_path] += 1.0 / rank
        finally:
            config.llm.temperature = original_temp

        if not run_results:
            return LocalizationResult(
                instance_id=bug_instance.instance_id, success=False
            )

        final_result = run_results[-1]
        fused_files = [
            fp
            for fp, _ in sorted(score_map.items(), key=lambda kv: kv[1], reverse=True)
        ]
        final_result.ranked_files = fused_files
        final_result.success = bool(final_result.ranked_files)
        final_result.total_time = sum(r.total_time for r in run_results)
        final_result.total_llm_calls = sum(r.total_llm_calls for r in run_results)
        final_result.total_tool_calls = sum(r.total_tool_calls for r in run_results)
        final_result.explanation = (
            f"Best-of-{passes} weighted rank fusion applied. "
            f"Aggregated {len(score_map)} unique files."
        )
        # Align ranked_locations with fused file order when possible
        by_path = {
            loc.get("file_path"): loc
            for loc in (final_result.ranked_locations or [])
            if loc.get("file_path")
        }
        reordered = []
        for rank, fp in enumerate(fused_files, 1):
            loc = by_path.get(fp)
            if loc:
                loc = {**loc, "rank": rank}
                reordered.append(loc)
        if reordered:
            final_result.ranked_locations = reordered

        final_result.agent_results["multi_pass"] = {
            "passes": passes,
            "temperature_used": temp_used if passes > 1 else original_temp,
            "fusion": "sum(1/rank)",
            "per_pass": [r.to_dict() for r in run_results],
        }
        return final_result

    @staticmethod
    def _top_confidence(conf_result: AgentResult) -> float:
        ranked = (
            conf_result.output.get("ranked_locations", []) if conf_result.output else []
        )
        if not ranked:
            return 0.0
        try:
            return float(ranked[0].get("confidence", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _rerank_candidates(self, context: AgentContext, verbose: bool = False) -> None:
        """Run optional Jina reranker over candidate files."""
        if not (config.reranker.enabled and context.candidate_files):
            return
        try:
            from rag.reranker import JinaReranker
            import os

            if verbose:
                console.print("\n[bold cyan]Phase 2.5: Jina Reranking[/bold cyan]")

            reranker = JinaReranker()
            docs_to_rerank, valid_files = [], []
            candidate_count = len(context.candidate_files)
            # Keep full-file fidelity when candidate list is already small.
            # Only truncate aggressively when reranking many files.
            truncate_chars = (
                config.rerank_max_chars_per_file
                if candidate_count > 12 and config.rerank_max_chars_per_file > 0
                else 0
            )
            for fp in context.candidate_files:
                abs_path = os.path.join(context.repo_path, fp)
                if os.path.exists(abs_path) and os.path.isfile(abs_path):
                    try:
                        with open(abs_path, "r", encoding="utf-8") as f:
                            content = (
                                f.read()
                                if truncate_chars == 0
                                else f.read(truncate_chars)
                            )
                            docs_to_rerank.append(f"File: {fp}\n\n{content}")
                            valid_files.append(fp)
                    except Exception as e:
                        logger.warning(f"Could not read {fp} for reranking: {e}")
            if not docs_to_rerank:
                return
            reranked_results = reranker.rerank(
                query=context.problem_statement,
                documents=docs_to_rerank,
            )
            reranked_files = []
            for res in reranked_results:
                idx = res.get("index")
                if idx is not None and idx < len(valid_files):
                    reranked_files.append(valid_files[idx])
            if reranked_files:
                context.candidate_files = reranked_files
                if verbose:
                    console.print(
                        f"  ✅ Reranked {len(valid_files)} candidates down to {len(reranked_files)}"
                    )
        except Exception as e:
            logger.error(f"Reranking failed: {e}")
            if verbose:
                console.print(
                    f"  ⚠️ Reranking failed, continuing with original order: {e}"
                )

    @staticmethod
    def _repo_cache_key(repo_path: str, language: str) -> str:
        """
        Build graph cache key tied to repo HEAD so cache does not go stale
        when the same path moves to another commit.
        """
        head = ""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            head = result.stdout.strip()
        except Exception:
            head = ""
        return f"{repo_path}|{language}|{head}"

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
            console.print(
                Panel(
                    result.root_cause,
                    title="🔍 Root Cause Analysis",
                )
            )
