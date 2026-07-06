"""
Agent Orchestrator.
Coordinates the multi-agent pipeline: Comprehension → Navigation → Confirmation.
"""

import logging
import os
import subprocess
import threading
import time
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
    ranked_methods: list[str] = field(default_factory=list)
    ranked_locations: list[dict] = field(default_factory=list)
    explanation: str = ""
    root_cause: str = ""
    total_time: float = 0.0
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    # Token usage aggregated across all agents
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    agent_results: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "success": self.success,
            "ranked_files": self.ranked_files,
            "ranked_methods": self.ranked_methods,
            "ranked_locations": self.ranked_locations,
            "explanation": self.explanation[:500],
            "root_cause": self.root_cause,
            "total_time": round(self.total_time, 2),
            "total_llm_calls": self.total_llm_calls,
            "total_tool_calls": self.total_tool_calls,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
        }


class Orchestrator:
    """Coordinates the multi-agent bug localization pipeline."""

    _graph_cache: dict[str, object] = {}
    _graph_cache_lock = threading.Lock()

    def _get_cached_graph(self, key: str):
        with self._graph_cache_lock:
            return self._graph_cache.get(key)

    def _set_cached_graph(self, key: str, graph) -> None:
        with self._graph_cache_lock:
            self._graph_cache[key] = graph

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
        temperature_override: float | None = None,
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

        # Detect Java by scanning the checked-out repository for .java files.
        # This avoids a hardcoded project-name list and works for any Java repo.
        is_java = self._detect_java_repo(repo_path)

        # Detect source root (old Ant layout uses "source/", Maven uses "src/main/java/")
        source_root = ""
        if is_java:
            if os.path.isdir(os.path.join(repo_path, "source")):
                source_root = "source"
            elif os.path.isdir(os.path.join(repo_path, "src", "main", "java")):
                source_root = "src/main/java"
            elif os.path.isdir(os.path.join(repo_path, "src")):
                source_root = "src"

        # Extract candidate files from failing test class names
        # Pattern: org.jfree.data.time.junit.WeekTests -> source/org/jfree/data/time/Week.java
        test_derived_candidates: list[str] = []
        if is_java and source_root:
            import re as _re
            # Match fully-qualified test class names
            test_patterns = _re.findall(
                r'((?:[\w]+\.)+)(junit\.|test\.|tests\.)?(\w+Tests?)\b',
                bug_instance.problem_statement,
            )
            for pre, _, cls in test_patterns:
                # Strip trailing "Tests" or "Test" to get source class
                src_cls = _re.sub(r'Tests?$', '', cls)
                if not src_cls or src_cls == cls:
                    continue
                # Build package path; strip trailing "junit." or similar from pre
                pkg = pre.rstrip('.')
                pkg = _re.sub(r'\.(junit|test|tests)$', '', pkg, flags=_re.IGNORECASE)
                pkg_path = pkg.replace('.', '/')
                candidate = f"{source_root}/{pkg_path}/{src_cls}.java"
                if candidate not in test_derived_candidates:
                    test_derived_candidates.append(candidate)

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
        stack_trace_files = self._extract_stack_trace_files(
            processed.stack_traces, repo_path
        )
        # Log analysis: seed candidate_methods with methods seen in stack traces
        seed_methods = list(processed.stack_trace_methods)

        # Merge drain3 search terms into keywords (high priority)
        if processed.log_parse_result and processed.log_parse_result.search_terms:
            drain_terms = processed.log_parse_result.search_terms
            kw_set = set(processed.keywords)
            for t in drain_terms:
                if t not in kw_set:
                    processed.keywords.insert(0, t)
                    kw_set.add(t)

        context = AgentContext(
            instance_id=bug_instance.instance_id,
            repo_id=self._extract_project_name(repo_path),
            problem_statement=bug_instance.problem_statement,
            repo_path=repo_path,
            error_messages=processed.error_messages,
            stack_traces=processed.stack_traces,
            mentioned_files=processed.mentioned_files,
            mentioned_functions=list(processed.mentioned_functions) + seed_methods,
            keywords=processed.keywords,
            retriever=self.retriever,
            language="java" if is_java else "python",
            file_extension="*.java" if is_java else "*.py",
            repo_skeleton=repo_skeleton,
            temperature_override=temperature_override,
            stack_trace_files=stack_trace_files,
            # Pre-seed candidate_methods from log stack traces
            candidate_methods=seed_methods,
            source_root=source_root,
            test_derived_candidates=test_derived_candidates,
            log_parse_result=processed.log_parse_result,
        )

        # Build Code Property Graph in background (ready by Phase 2)
        graph_future: Future | None = None
        language = "java" if is_java else "python"
        graph_cache_key = self._repo_cache_key(repo_path, language)
        cached = self._get_cached_graph(graph_cache_key)
        if config.enable_graph_rag and cached is not None:
            if verbose:
                console.print(f"  ♻️  Reusing cached graph (key={graph_cache_key[:60]}...)")
            context.graph_retriever = cached
        elif config.enable_graph_rag:
            executor = ThreadPoolExecutor(max_workers=1)
            graph_future = executor.submit(
                self._build_graph, repo_path, verbose, graph_cache_key
            )
            executor.shutdown(wait=False)

        # === Phase 1: Fault Comprehension ===
        if verbose:
            console.print("\n[bold cyan]Phase 1: Fault Comprehension[/bold cyan]")

        comp_result = self.comprehension_agent.run(context)
        result.agent_results["comprehension"] = {
            "success": comp_result.success,
            "llm_calls": comp_result.num_llm_calls,
            "tool_calls": comp_result.num_tool_calls,
            "prompt_tokens": comp_result.prompt_tokens,
            "completion_tokens": comp_result.completion_tokens,
            "total_tokens": comp_result.total_tokens,
        }
        result.total_llm_calls += comp_result.num_llm_calls
        result.total_tool_calls += comp_result.num_tool_calls
        result.total_prompt_tokens += comp_result.prompt_tokens
        result.total_completion_tokens += comp_result.completion_tokens
        result.total_tokens += comp_result.total_tokens

        if comp_result.success:
            self.comprehension_agent.process_result(comp_result, context)
            # Stack trace boosting: promote stack-trace-linked files to the front
            if context.stack_trace_files:
                promoted = list(context.stack_trace_files)
                for f in context.candidate_files:
                    if f not in promoted:
                        promoted.append(f)
                context.candidate_files = promoted
            if verbose:
                console.print(f"  ✅ Hypothesis: {context.fault_hypothesis[:200]}")
                console.print(f"  📂 Candidate files: {context.candidate_files}")
                if context.stack_trace_files:
                    console.print(
                        f"  🔺 Stack trace boosted: {context.stack_trace_files}"
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
                # Cache using the key so future instances with same commit reuse it
                self._set_cached_graph(graph_cache_key, graph_retriever)
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
                "prompt_tokens": nav_result.prompt_tokens,
                "completion_tokens": nav_result.completion_tokens,
                "total_tokens": nav_result.total_tokens,
            }
            result.total_llm_calls += nav_result.num_llm_calls
            result.total_tool_calls += nav_result.num_tool_calls
            result.total_prompt_tokens += nav_result.prompt_tokens
            result.total_completion_tokens += nav_result.completion_tokens
            result.total_tokens += nav_result.total_tokens

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

            if verbose:
                console.print(
                    f"  Candidate Files before validation: {context.candidate_files}"
                )

            conf_result = self.confirmation_agent.run(context)
            result.agent_results[f"confirmation_round_{round_idx + 1}"] = {
                "success": conf_result.success,
                "llm_calls": conf_result.num_llm_calls,
                "tool_calls": conf_result.num_tool_calls,
                "prompt_tokens": conf_result.prompt_tokens,
                "completion_tokens": conf_result.completion_tokens,
                "total_tokens": conf_result.total_tokens,
            }
            result.total_llm_calls += conf_result.num_llm_calls
            result.total_tool_calls += conf_result.num_tool_calls
            result.total_prompt_tokens += conf_result.prompt_tokens
            result.total_completion_tokens += conf_result.completion_tokens
            result.total_tokens += conf_result.total_tokens

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
            from evaluation.metrics import extract_methods_from_locations

            output = conf_result.output
            result.ranked_locations = output.get("ranked_locations", [])

            # Fallback: if ConfirmationAgent returned no ranked_locations but we have
            # candidate_methods from log/stack-trace analysis, reconstruct locations.
            if not result.ranked_locations and context.candidate_methods:
                logger.info(
                    "[Orchestrator] ranked_locations empty — reconstructing from "
                    "log-analysis candidate_methods"
                )
                for rank, method_id in enumerate(context.candidate_methods[:10], 1):
                    # method_id format: "ClassName#methodName" or "file::Class.method"
                    file_path = ""
                    class_name = ""
                    function_name = method_id
                    if "::" in method_id:
                        file_path, rest = method_id.split("::", 1)
                        if "." in rest:
                            class_name, function_name = rest.rsplit(".", 1)
                        else:
                            function_name = rest
                    elif "#" in method_id:
                        class_name, function_name = method_id.split("#", 1)
                        # Try to map class to file using candidate_files
                        for cf in context.candidate_files:
                            if class_name and class_name.lower() in cf.lower():
                                file_path = cf
                                break
                    if not file_path and context.candidate_files:
                        file_path = context.candidate_files[0]
                    result.ranked_locations.append({
                        "rank": rank,
                        "file_path": file_path,
                        "function_name": function_name,
                        "class_name": class_name,
                        "start_line": 0,
                        "end_line": 0,
                        "confidence": max(0.3, 0.7 - (rank - 1) * 0.1),
                        "explanation": f"Reconstructed from log/stack-trace analysis",
                        "source": "log_analysis",
                    })

            result.ranked_files = self._build_candidate_pool(result, context)
            result.ranked_methods = extract_methods_from_locations(
                result.ranked_locations
            )
            # Also include log-seeded methods not in ranked_locations
            for m in context.candidate_methods:
                if m not in result.ranked_methods:
                    result.ranked_methods.append(m)

            result.explanation = conf_result.explanation
            result.root_cause = output.get("root_cause", "")
            result.success = True

            if config.scoring.enable_unified_scoring:
                result = self._apply_unified_scoring(
                    result, context, processed, verbose
                )

            if verbose:
                self._print_results(result)
        else:
            result.ranked_files = self._build_candidate_pool(result, context)
            result.success = bool(result.ranked_files)
            if verbose and conf_result is not None:
                console.print(f"  ❌ Confirmation failed: {conf_result.error}")
                console.print(
                    f"  ⚠️  Falling back to candidate pool: {result.ranked_files[:10]}"
                )

        # Filter out file paths that do not exist in the checked-out repo
        result.ranked_files = self._filter_nonexistent_files(
            result.ranked_files, repo_path
        )
        result.total_time = time.time() - start_time

        if verbose:
            console.print(
                f"\n⏱️  Total time: {result.total_time:.1f}s | "
                f"LLM calls: {result.total_llm_calls} | "
                f"Tool calls: {result.total_tool_calls} | "
                f"Tokens: {result.total_prompt_tokens:,} prompt / "
                f"{result.total_completion_tokens:,} completion / "
                f"{result.total_tokens:,} total"
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
        passes = max(1, min(passes, 5))
        # Determine temperature for multi-pass without mutating global config
        base_temp = config.llm.temperature
        temp_used = base_temp
        if passes > 1 and base_temp <= 0:
            temp_used = config.multi_pass_temperature

        run_results: list[LocalizationResult] = []
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
                temperature_override=temp_used if passes > 1 else None,
            )
            run_results.append(run_result)

        if not run_results:
            return LocalizationResult(
                instance_id=bug_instance.instance_id, success=False
            )

        # Pick the last successful run as the base result (carries token/time stats)
        final_result = run_results[-1]

        # ── File-level Reciprocal Rank Fusion ──────────────────────────────────
        from utils.ranking import reciprocal_rank_fusion
        fused_files = [
            fp for fp, _ in reciprocal_rank_fusion(
                [r.ranked_files for r in run_results]
            )
        ]
        final_result.ranked_files = fused_files
        final_result.success = bool(final_result.ranked_files)

        # ── Aggregate cost stats ───────────────────────────────────────────────
        final_result.total_time = sum(r.total_time for r in run_results)
        final_result.total_llm_calls = sum(r.total_llm_calls for r in run_results)
        final_result.total_tool_calls = sum(r.total_tool_calls for r in run_results)
        final_result.total_prompt_tokens = sum(
            r.total_prompt_tokens for r in run_results
        )
        final_result.total_completion_tokens = sum(
            r.total_completion_tokens for r in run_results
        )
        final_result.total_tokens = sum(r.total_tokens for r in run_results)
        final_result.explanation = (
            f"Best-of-{passes} weighted rank fusion applied. "
            f"Aggregated {len(fused_files)} unique files."
        )

        # ── Method-level Reciprocal Rank Fusion ────────────────────────────────
        method_rankings = [r.ranked_methods for r in run_results if r.ranked_methods]
        if method_rankings:
            final_result.ranked_methods = [
                m for m, _ in reciprocal_rank_fusion(method_rankings)
            ]

        # ── Re-align ranked_locations with fused file order ────────────────────
        from evaluation.metrics import extract_methods_from_locations

        # Collect locations from all passes, keyed by file_path
        all_locs: dict[str, dict] = {}
        for run_result in run_results:
            for loc in (run_result.ranked_locations or []):
                fp = loc.get("file_path")
                if fp and fp not in all_locs:
                    all_locs[fp] = loc

        reordered = []
        for rank, fp in enumerate(fused_files, 1):
            loc = all_locs.get(fp)
            if loc:
                reordered.append({**loc, "rank": rank})
        if reordered:
            final_result.ranked_locations = reordered
        # If method fusion didn't produce results, fall back to location extraction
        if not final_result.ranked_methods:
            final_result.ranked_methods = extract_methods_from_locations(
                final_result.ranked_locations
            )

        # Filter out file paths that do not exist in the checked-out repo
        final_result.ranked_files = self._filter_nonexistent_files(
            final_result.ranked_files, repo_path
        )
        final_result.agent_results["multi_pass"] = {
            "passes": passes,
            "fusion": "sum(1/rank)",
            "per_pass": [r.to_dict() for r in run_results],
        }
        return final_result

    # Minimum number of files the final ranked list should contain so that
    # Top-5/Top-10 metrics have room to hit even when the LLM's confirmed
    # list is short. Padded files carry no LLM confidence, so with unified
    # scoring enabled they only outrank confirmed files on strong signals
    # (stack trace, mentioned in report).
    MIN_RANKED_FILES: int = int(os.environ.get("MIN_RANKED_FILES", "10"))

    def _build_candidate_pool(
        self, result: LocalizationResult, context: AgentContext
    ) -> list[str]:
        """
        Merge every candidate source into one ordered, deduplicated pool.

        Priority order: confirmed locations → navigation candidates →
        stack-trace files → files mentioned in the report → test-derived
        candidates → semantic retriever hits → graph retriever hits.
        Later sources are only consulted until the pool reaches
        MIN_RANKED_FILES entries.
        """
        pool: list[str] = []
        seen: set[str] = set()

        def _add(fp: str) -> None:
            fp = (fp or "").strip().lstrip("/")
            if fp and fp not in seen:
                seen.add(fp)
                pool.append(fp)

        for loc in result.ranked_locations or []:
            _add(loc.get("file_path", ""))
        for fp in context.candidate_files or []:
            _add(fp)
        for fp in context.stack_trace_files or []:
            _add(fp)
        for fp in context.mentioned_files or []:
            _add(fp)
        for fp in context.test_derived_candidates or []:
            _add(fp)

        # Expensive sources only when the pool is still thin
        if len(pool) < self.MIN_RANKED_FILES and context.retriever is not None:
            try:
                query = context.problem_statement[:4000]
                try:
                    hits = context.retriever.hybrid_get_similar_files(
                        query,
                        top_k=self.MIN_RANKED_FILES,
                        repo_filter=context.repo_id or None,
                    )
                except AttributeError:
                    hits = context.retriever.get_similar_files(
                        query,
                        top_k=self.MIN_RANKED_FILES,
                        repo_filter=context.repo_id or None,
                    )
                for hit in hits:
                    if isinstance(hit, dict):
                        _add(hit.get("file_path", ""))
                    else:
                        _add(str(hit))
            except Exception as e:
                logger.debug(f"[CandidatePool] semantic retrieval failed: {e}")

        if len(pool) < self.MIN_RANKED_FILES and context.graph_retriever is not None:
            try:
                nodes = context.graph_retriever.search(
                    context.problem_statement[:4000],
                    top_k=self.MIN_RANKED_FILES,
                )
                for node in nodes:
                    _add(node.data.get("file_path", ""))
            except Exception as e:
                logger.debug(f"[CandidatePool] graph retrieval failed: {e}")

        # Last resort needs no index or API: match bug-report keywords against
        # source file paths. Covers eval runs where no retriever is wired up.
        if len(pool) < self.MIN_RANKED_FILES:
            try:
                for fp in self._path_keyword_candidates(
                    context, top_k=self.MIN_RANKED_FILES,
                ):
                    _add(fp)
            except Exception as e:
                logger.debug(f"[CandidatePool] path-keyword matching failed: {e}")

        return pool

    _POOL_SKIP_DIRS = {
        ".git", "__pycache__", "node_modules", "build", "dist", "target",
        "docs", "doc", "examples", "benchmarks", ".tox", "venv", ".venv",
        "tests", "test", "testing",
    }

    @staticmethod
    def _path_keyword_candidates(
        context: AgentContext, top_k: int = 10
    ) -> list[str]:
        """
        Rank source files by how strongly their path matches identifiers from
        the bug report (preprocessor keywords + mentioned functions).

        Pure filesystem walk — no LLM, no vector index — so it always works.
        A keyword like "autodetector" or "sqlmigrate" pointing straight at
        django/db/migrations/autodetector.py is exactly the case this catches.
        """
        repo_path = context.repo_path
        if not repo_path or not os.path.isdir(repo_path):
            return []

        import re as _re

        def _subtokens(identifier: str) -> list[str]:
            parts = _re.split(r"_+|(?<=[a-z0-9])(?=[A-Z])", identifier)
            toks = [p.lower() for p in parts if len(p) >= 4]
            if len(identifier) >= 4:
                toks.append(identifier.lower())
            return toks

        tokens: set[str] = set()
        for kw in (context.keywords or []) + (context.mentioned_functions or []):
            tokens.update(_subtokens(str(kw)))
        if not tokens:
            return []

        ext = (context.file_extension or "*.py").lstrip("*")
        skip_dirs = Orchestrator._POOL_SKIP_DIRS
        scored: list[tuple[float, str]] = []
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
            for fname in files:
                if not fname.endswith(ext):
                    continue
                rel = os.path.relpath(os.path.join(root, fname), repo_path)
                rel_lower = rel.lower()
                base_lower = fname.lower()
                score = 0.0
                for tok in tokens:
                    if tok in base_lower:
                        score += 2.0 * len(tok)
                    elif tok in rel_lower:
                        score += float(len(tok))
                if score >= 8.0:  # require at least one solid (4+ char) basename hit
                    scored.append((score, rel))

        scored.sort(key=lambda t: (-t[0], t[1]))
        return [rel for _, rel in scored[:top_k]]

    @staticmethod
    def _filter_nonexistent_files(
        ranked_files: list[str], repo_path: str
    ) -> list[str]:
        """
        Remove predicted file paths that do not exist in the checked-out repo.

        Also tries common wrong-prefix variants before discarding a path:
          - Strips leading "JodaTime/", "project/", or other top-level dir prefixes
            that the LLM sometimes prepends.
          - Strips source-root prefixes (source/, src/main/java/, src/) to handle
            layout mismatches between prediction and checkout.

        Paths that cannot be resolved to an existing file are dropped silently.
        """
        if not repo_path:
            return ranked_files

        _SOURCE_ROOTS = ("src/main/java/", "src/java/", "source/", "src/")
        # Only accept files with recognised source-code extensions
        _SOURCE_EXTS = {
            ".java", ".py", ".js", ".ts", ".jsx", ".tsx",
            ".rb", ".go", ".kt", ".scala", ".cs", ".cpp", ".c", ".h",
        }

        def _resolve(fp: str) -> str | None:
            """Return the first variant of *fp* that exists under repo_path."""
            candidates = [fp]

            # Strip a spurious top-level directory (e.g. "JodaTime/src/...")
            parts = fp.split("/", 1)
            if len(parts) == 2 and "." not in parts[0]:
                candidates.append(parts[1])

            # Try stripping known source roots from any variant
            extra = []
            for c in candidates:
                for root in _SOURCE_ROOTS:
                    if c.startswith(root):
                        extra.append(c[len(root):])
            candidates.extend(extra)

            # A module predicted as "pkg/mod.py" may actually live at
            # "pkg/mod/__init__.py" (common in django: models/fields.py →
            # models/fields/__init__.py). Try that variant before giving up.
            pkg_variants = [
                c[:-3] + "/__init__.py"
                for c in candidates
                if c.endswith(".py") and not c.endswith("__init__.py")
            ]
            candidates.extend(pkg_variants)

            for c in candidates:
                if os.path.isfile(os.path.join(repo_path, c)):
                    return c
            return None

        valid: list[str] = []
        seen: set[str] = set()
        for fp in ranked_files:
            # Reject non-source-code files regardless of existence
            ext = os.path.splitext(fp)[1].lower()
            if ext and ext not in _SOURCE_EXTS:
                logger.debug(f"[FileValidation] Dropping non-source path: {fp}")
                continue
            resolved = _resolve(fp)
            if resolved and resolved not in seen:
                seen.add(resolved)
                valid.append(resolved)
            elif not resolved:
                logger.debug(f"[FileValidation] Dropping non-existent path: {fp}")

        if len(valid) < len(ranked_files):
            dropped = len(ranked_files) - len(valid)
            logger.info(
                f"[FileValidation] Dropped {dropped} non-existent path(s) from ranked_files"
            )
        return valid

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

    def _apply_unified_scoring(
        self,
        result: LocalizationResult,
        context: AgentContext,
        processed,
        verbose: bool = False,
    ) -> LocalizationResult:
        """
        Apply unified multi-signal scoring to rerank final results.

        Combines:
        - LLM confidence scores
        - Stack trace position-weighted scores
        - Error message matches
        - Mentioned files
        - Graph RAG proximity
        - Method count aggregation
        """
        from evaluation.unified_scorer import (
            UnifiedScorer,
            ScoringWeights,
            extract_llm_scores_from_locations,
            extract_method_counts_from_locations,
        )

        if not result.ranked_files:
            return result

        weights = ScoringWeights(
            llm_confidence=config.scoring.weight_llm_confidence,
            stack_trace=config.scoring.weight_stack_trace,
            stack_trace_position_decay=0.85,
            error_message_match=config.scoring.weight_error_match,
            mentioned_file=config.scoring.weight_mentioned_file,
            graph_proximity=config.scoring.weight_graph_proximity,
            semantic_similarity=config.scoring.weight_semantic,
            method_count_boost=config.scoring.weight_method_count,
            git_recency=config.scoring.weight_git_recency,
            git_recency_half_life_days=config.scoring.git_recency_half_life_days,
            test_file_penalty=config.scoring.test_file_penalty,
        )

        scorer = UnifiedScorer(weights=weights)

        llm_scores = extract_llm_scores_from_locations(result.ranked_locations)
        method_counts = extract_method_counts_from_locations(result.ranked_locations)

        graph_scores = {}
        if context.graph_retriever and result.ranked_files:
            try:
                graph_results = context.graph_retriever.search(
                    context.problem_statement, top_k=len(result.ranked_files)
                )
                for node in graph_results:
                    fp = node.data.get("file_path", "")
                    score = getattr(node, "score", 0.0)
                    if fp and score > 0:
                        current = graph_scores.get(fp, 0.0)
                        graph_scores[fp] = max(current, float(score))
            except Exception as e:
                logger.debug(f"Graph scoring failed: {e}")

        semantic_scores = {}
        if context.retriever is not None and result.ranked_files:
            try:
                hits = context.retriever.get_similar_files(
                    context.problem_statement[:4000],
                    top_k=len(result.ranked_files),
                    repo_filter=context.repo_id or None,
                )
                semantic_scores = {
                    h["file_path"]: float(h.get("score", 0.0)) for h in hits
                }
            except Exception as e:
                logger.debug(f"Semantic scoring failed: {e}")

        candidate_scores = scorer.score_candidates(
            candidates=result.ranked_files,
            repo_path=context.repo_path,
            stack_trace_files=context.stack_trace_files,
            error_messages=processed.error_messages,
            mentioned_files=processed.mentioned_files,
            llm_scores=llm_scores,
            graph_scores=graph_scores,
            semantic_scores=semantic_scores,
            method_counts=method_counts,
        )

        result.ranked_files = [s.file_path for s in candidate_scores]

        score_map = {s.file_path: s for s in candidate_scores}
        for loc in result.ranked_locations:
            fp = loc.get("file_path", "")
            if fp in score_map:
                loc["unified_score"] = round(score_map[fp].total_score, 3)

        result.ranked_locations.sort(
            key=lambda x: x.get("unified_score", x.get("confidence", 0)), reverse=True
        )
        for rank, loc in enumerate(result.ranked_locations, 1):
            loc["rank"] = rank

        if verbose:
            top_score = candidate_scores[0] if candidate_scores else None
            if top_score:
                console.print(
                    f"  🎯 Unified scoring applied: Top-1 = {top_score.file_path} "
                    f"(score={top_score.total_score:.2f})"
                )

        return result

    @staticmethod
    def _extract_project_name(repo_path: str) -> str:
        """
        Extract the project-level repo_id from a checkout path.

        Examples:
            data/defects4j_checkouts/Chart/Chart_3  →  "Chart"
            data/defects4j_checkouts/Lang/Lang_10   →  "Lang"
            /some/custom/my-repo                    →  "my-repo"

        Using the project name (not the instance id) means all bugs in a
        project share a single Qdrant partition — so indexing one checkout
        per project is sufficient.
        """
        import re
        basename = os.path.basename(repo_path.rstrip(os.sep))
        # Strip trailing _<digits> to get the project name
        m = re.match(r'^([A-Za-z][A-Za-z0-9]*?)(?:_\d+)?$', basename)
        return m.group(1) if m else basename

    @staticmethod
    def _detect_java_repo(repo_path: str, threshold: int = 5) -> bool:
        """
        Detect whether a repository is primarily Java by scanning for .java files.

        Replaces the old hardcoded project-name list so that any Java project
        (not just known Defects4J subjects) is handled correctly.
        """
        skip_dirs = {".git", "build", "target", ".gradle", ".idea"}
        count = 0
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for filename in files:
                if filename.endswith(".java"):
                    count += 1
                    if count >= threshold:
                        return True
        return False

    @staticmethod
    def _repo_cache_key(repo_path: str, language: str) -> str:
        """
        Build a graph cache key based on (repo_name, git_commit, language).

        Using the repo name + commit instead of the full checkout path means
        that multiple SWE-bench instances sharing the same base_commit will
        reuse the same cached graph — no redundant rebuilds.

        Example:
            astropy__astropy-12907  (commit abc123)  ─┐
            astropy__astropy-12891  (commit abc123)  ─┴─▶ same cache key
            astropy__astropy-13032  (commit def456)       ─▶ different key
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

        # Extract a stable repo identifier: drop the trailing instance-id suffix.
        # e.g. "data/swebench_checkouts/astropy__astropy/astropy__astropy-12907"
        #   → repo_name = "astropy__astropy"
        # For arbitrary paths (manual use) fall back to the basename.
        path_parts = os.path.normpath(repo_path).split(os.sep)
        # Walk backwards: first segment that does NOT look like an instance-id
        # (instance-ids contain digits after a dash, e.g. "astropy__astropy-12907")
        repo_name = path_parts[-1]  # fallback
        for part in reversed(path_parts):
            if part and not part.split("-")[-1].isdigit():
                repo_name = part
                break

        return f"{repo_name}|{language}|{head}"

    @staticmethod
    def _build_graph(repo_path: str, verbose: bool = False, repo_id: str = ""):
        """Build Code Property Graph for a repository (runs in background thread).

        Args:
            repo_path: Checkout path of the repo.
            verbose: Print progress messages.
            repo_id: Stable repo identifier used as Neo4j partition key
                     (e.g. "astropy__astropy|python|abc123"). When provided,
                     the Neo4j backend only loads/stores nodes belonging to
                     this repo, avoiding cross-repo contamination.
        """
        from rag.graph_retriever import GraphRetriever
        from rag.code_graph import get_or_build_graph

        neo4j_cfg = config.neo4j
        if neo4j_cfg.enabled:
            try:
                graph = get_or_build_graph(
                    repo_path=repo_path,
                    language="auto",
                    use_neo4j=True,
                    neo4j_uri=neo4j_cfg.uri,
                    neo4j_user=neo4j_cfg.user,
                    neo4j_password=neo4j_cfg.password,
                    neo4j_database=neo4j_cfg.database,
                    repo_id=repo_id,
                )
                retriever = GraphRetriever(graph=graph, repo_path=repo_path)
                retriever._prepare_indexes()
                logger.info("Graph RAG using Neo4j backend")
                return retriever
            except ConnectionError as e:
                logger.warning(f"Neo4j unavailable, falling back to in-memory: {e}")

        retriever = GraphRetriever(repo_path=repo_path)
        retriever.build_graph(language="auto")
        return retriever

    @staticmethod
    def _extract_stack_trace_files(stack_traces: list, repo_path: str) -> list[str]:
        """
        Extract file paths from preprocessed stack trace entries.

        Supports both the Java format produced by BugReportPreprocessor
          ("at Foo(Bar.java:42) -> src/main/java/.../Bar.java:42")
        and Python traceback lines
          ("File '/path/to/file.py', line 42").
        """
        import re
        import os

        seen: list[str] = []
        seen_set: set[str] = set()

        # Pattern for Java entries produced by _extract_java_stack_traces:
        # "at org.Foo(Foo.java:10) -> src/main/java/org/Foo.java:10"
        java_rhs = re.compile(r"->\s+(.+?):\d+\s*$")
        # Pattern for raw Python traceback lines
        python_file = re.compile(r'File ["\'](.+?)["\'], line \d+')

        for entry in stack_traces:
            # Try Java pattern first
            m = java_rhs.search(entry)
            if m:
                fp = m.group(1).strip()
                full = os.path.join(repo_path, fp)
                if fp not in seen_set and os.path.exists(full):
                    seen.append(fp)
                    seen_set.add(fp)
                continue

            # Try Python pattern
            m = python_file.search(entry)
            if m:
                abs_path = m.group(1)
                try:
                    rel = os.path.relpath(abs_path, repo_path)
                    if not rel.startswith("..") and rel not in seen_set:
                        if os.path.exists(abs_path):
                            seen.append(rel)
                            seen_set.add(rel)
                except ValueError:
                    pass

        return seen

    def _print_results(self, result: LocalizationResult):
        """Print formatted results (file-level)."""
        table = Table(title="🎯 Localization Results (File-Level)")
        table.add_column("Rank", style="bold", width=5)
        table.add_column("File", style="cyan")
        table.add_column("Confidence", style="yellow", width=12)
        table.add_column("Explanation", max_width=60)

        # Deduplicate by file_path — keep highest confidence entry per file
        seen_files: dict[str, dict] = {}
        for loc in result.ranked_locations:
            fp = loc.get("file_path", "")
            if not fp:
                continue
            if fp not in seen_files or loc.get("confidence", 0) > seen_files[fp].get("confidence", 0):
                seen_files[fp] = loc

        # Re-rank deduplicated file list by confidence descending
        deduped = sorted(seen_files.values(), key=lambda x: x.get("confidence", 0), reverse=True)

        for rank, loc in enumerate(deduped[:10], 1):
            table.add_row(
                str(rank),
                loc.get("file_path", "?"),
                f"{loc.get('confidence', 0):.2f}",
                loc.get("explanation", "")[:60] + "...",
            )

        console.print(table)

        if result.root_cause:
            console.print(
                Panel(
                    result.root_cause,
                    title="🔍 Root Cause Analysis",
                )
            )
