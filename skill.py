import logging
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from agents.orchestrator import Orchestrator, LocalizationResult  # noqa: E402
from data.loader import BugInstance  # noqa: E402

logger = logging.getLogger(__name__)

__all__ = ["BugLocalizationSkill", "LocalizationResult"]


class BugLocalizationSkill:
    """
    High-level API for the multi-agent bug localization skill.

    Supports two invocation modes:
    1. ``localize_bug``         — free-form bug report + repo path
    2. ``localize_by_instance`` — benchmark instance ID (loads from dataset)

    Parameters
    ----------
    enable_graph_rag : bool
        Build a Code Property Graph during Phase 1 (default: reads from config /
        ENABLE_GRAPH_RAG env var). Set False to skip for faster results when the
        repo is very large.
    verbose : bool
        Print agent traces and rich progress tables to stdout.
    rag_persist_dir : str, optional
        Path to a pre-built ChromaDB index for semantic search. If None, the
        default configured path is used. If no index exists there, semantic
        search is gracefully skipped.
    """

    def __init__(
        self,
        enable_graph_rag: bool = None,
        verbose: bool = False,
        rag_persist_dir: str = None,
    ):
        from config import config

        if enable_graph_rag is not None:
            config.enable_graph_rag = enable_graph_rag

        self._config = config
        self._verbose = verbose
        self._rag_persist_dir = rag_persist_dir or str(
            Path(config.rag.persist_directory)
        )

        # Lazy-initialised orchestrator — created on first localize() call
        self._orchestrator: Orchestrator | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def localize_bug(
        self,
        bug_report: str,
        repo_path: str,
        instance_id: str = "manual",
        language: str = "auto",
        verbose: bool = None,
    ) -> LocalizationResult:
        """
        Localize a bug from a free-form bug report.

        Parameters
        ----------
        bug_report : str
            Natural-language description of the bug (issue text, error message,
            reproduction steps, stack trace — all welcome).
        repo_path : str
            Absolute path to the checked-out repository at the buggy revision.
        instance_id : str
            Identifier for this run (used in result / logging). Default: "manual".
        language : str
            ``"python"``, ``"java"``, or ``"auto"`` (default). Auto-detects
            based on the presence of .py / .java files in the repo.
        verbose : bool
            Override instance-level verbose flag for this call.

        Returns
        -------
        LocalizationResult
            Dataclass with fields: ``instance_id``, ``success``,
            ``ranked_files``, ``ranked_locations``, ``root_cause``,
            ``explanation``, ``total_time``, ``total_llm_calls``,
            ``total_tool_calls``, ``agent_results``.
        """
        bug_instance = BugInstance(
            instance_id=instance_id,
            repo=repo_path,
            problem_statement=bug_report,
            base_commit="",
            patch="",
            test_patch="",
        )

        # Apply language hint to the repo field so orchestrator auto-detects it
        if language == "java":
            bug_instance.repo = "defects4j/auto"  # triggers java detection
        elif language == "python":
            bug_instance.repo = "swebench/auto"

        return self._get_orchestrator().localize(
            bug_instance,
            repo_path=repo_path,
            verbose=verbose if verbose is not None else self._verbose,
        )

    def localize_by_instance(
        self,
        instance_id: str,
        repo_path: str = None,
        dataset: str = None,
        verbose: bool = None,
    ) -> LocalizationResult:
        """
        Localize a bug by loading a benchmark instance from a dataset.

        Parameters
        ----------
        instance_id : str
            Benchmark instance identifier. Supported formats:
            - SWE-bench:  ``"django__django-11099"``
            - Defects4J:  ``"Lang_1"``, ``"Math_5"``
        repo_path : str, optional
            Path to pre-checked-out repo. If omitted, the system will attempt
            to checkout the repo automatically (requires network access /
            Defects4J CLI).
        dataset : str, optional
            Hugging Face dataset name (SWE-bench only). Defaults to the value
            in ``config.evaluation.dataset_name``.
        verbose : bool
            Override instance-level verbose flag for this call.

        Returns
        -------
        LocalizationResult
        """
        bug_instance = self._load_instance(instance_id, dataset)
        return self._get_orchestrator().localize(
            bug_instance,
            repo_path=repo_path,
            verbose=verbose if verbose is not None else self._verbose,
        )

    def index_repository(self, repo_path: str, clear: bool = False) -> dict:
        """
        Build or update the RAG (ChromaDB) index for a repository.

        Parameters
        ----------
        repo_path : str
            Absolute path to the repository to index.
        clear : bool
            If True, wipe the existing index before re-indexing.

        Returns
        -------
        dict
            ``{"num_chunks": int, "collection": str, "persist_dir": str}``
        """
        from rag.indexer import CodebaseIndexer

        indexer = CodebaseIndexer(
            persist_directory=self._rag_persist_dir,
            collection_name=self._config.rag.collection_name,
            embedding_model=self._config.embedding.model_name,
        )
        if clear:
            indexer.clear_index()

        num_chunks = indexer.index_repository(repo_path)
        logger.info(f"[Skill] Indexed {num_chunks} chunks from {repo_path}")

        return {
            "num_chunks": num_chunks,
            "collection": self._config.rag.collection_name,
            "persist_dir": self._rag_persist_dir,
        }

    @staticmethod
    def get_supported_benchmarks() -> dict:
        """
        Return metadata about the supported benchmarks and evaluation metrics.

        Returns
        -------
        dict
            Mapping of benchmark name → metadata dict.
        """
        return {
            "defects4j": {
                "language": "Java",
                "projects": ["Lang", "Math", "Time", "Closure", "Mockito"],
                "instance_id_format": "Lang_1 / Math_5 / Time_3",
                "requires_checkout": True,
                "notes": "Run: defects4j checkout -p [project] -v [id]b -w [dir]",
            },
            "swe-bench": {
                "language": "Python",
                "dataset": "princeton-nlp/SWE-bench_Lite",
                "instance_id_format": "django__django-11099",
                "requires_checkout": True,
                "notes": "Run: scripts/checkout_swebench.py",
            },
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_orchestrator(self) -> Orchestrator:
        """Lazy-initialise the orchestrator (and optional RAG retriever)."""
        if self._orchestrator is None:
            retriever = self._try_load_retriever()
            self._orchestrator = Orchestrator(retriever=retriever)
        return self._orchestrator

    def _try_load_retriever(self):
        """Attempt to load the ChromaDB retriever; return None on failure."""
        try:
            from rag.retriever import CodeRetriever

            retriever = CodeRetriever(persist_directory=self._rag_persist_dir)
            if retriever.collection and retriever.collection.count() > 0:
                logger.info(
                    f"[Skill] RAG index loaded"
                    f" ({retriever.collection.count()} chunks)"
                )
                return retriever
            logger.info("[Skill] No RAG index found — semantic search disabled")
        except Exception as e:
            logger.debug(f"[Skill] RAG init failed (continuing without): {e}")
        return None

    def _load_instance(self, instance_id: str, dataset: str = None) -> BugInstance:
        """Load a bug instance from the appropriate dataset loader."""
        # Defects4J: instance_id matches "Project_N"
        if "_" in instance_id and instance_id.split("_")[0].isalpha() and \
                instance_id.split("_")[1].isdigit():
            try:
                from data.defects4j_loader import Defects4JLoader, to_bug_instance
                loader = Defects4JLoader()
                d4j_bug = loader.load_instance(instance_id)
                if d4j_bug is not None:
                    return to_bug_instance(d4j_bug)
            except Exception as e:
                logger.debug(f"[Skill] Defects4J load failed: {e}")

        # SWE-bench default
        from data.loader import SWEBenchLoader
        dataset = dataset or self._config.evaluation.dataset_name
        loader = SWEBenchLoader(dataset)
        instance = loader.load_instance(instance_id)
        if instance is None:
            raise ValueError(
                f"Instance '{instance_id}' not found in dataset '{dataset}'. "
                f"Also checked the Defects4J loader."
            )
        return instance
