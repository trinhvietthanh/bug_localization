"""
Configuration module for Bug Localization System.
Loads settings from environment variables and .env file.
"""

import os
import socket
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = Path(__file__).parent


def _apply_dns_pin() -> None:
    """Pin API hostnames to fixed IPs to survive flaky/corporate resolvers.

    On some networks the local resolver intermittently returns a bogus address
    (e.g. ``::``) for third-party API hosts such as ``api.siliconflow.com``,
    breaking the OpenAI/httpx client even though the host is reachable via its
    public IP. Set ``LLM_FORCE_IP=host:ip[,host:ip...]`` to override resolution
    at the socket layer. SNI/TLS stay correct: httpx derives the hostname from
    the URL, only the connect target changes. Applied once at import so every
    subprocess (agentic + bare) inherits the pin.
    """
    if getattr(_apply_dns_pin, "_done", False):
        return
    _apply_dns_pin._done = True
    raw = os.getenv("LLM_FORCE_IP", "").strip()
    if not raw:
        return
    pin: dict[str, list[str]] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        host, ip = pair.split(":", 1)
        host, ip = host.strip(), ip.strip()
        if host and ip:
            pin.setdefault(host, []).append(ip)
    if not pin:
        return
    _orig_getaddrinfo = socket.getaddrinfo

    def _patched(host, port, *args, **kwargs):
        if host and host in pin:
            res = []
            for ip in pin[host]:
                try:
                    res.extend(_orig_getaddrinfo(ip, port, *args, **kwargs))
                except OSError:
                    continue
            if res:
                return res
        return _orig_getaddrinfo(host, port, *args, **kwargs)

    socket.getaddrinfo = _patched


_apply_dns_pin()


@dataclass
class LLMConfig:
    """LLM configuration."""

    provider: str = os.getenv("LLM_PROVIDER", "gemini")
    model: str = os.getenv("LLM_MODEL", "gemini-2.0-flash")
    api_key: str = os.getenv("OPENAI_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.0"))
    max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "8192"))
    api_base: str = os.getenv("LLM_API_BASE", "")


@dataclass
class EmbeddingConfig:
    """Embedding model configuration."""

    model_name: str = os.getenv("EMBEDDING_MODEL", "jinaai/jina-embeddings-v3")
    # When set, embeddings are fetched via an OpenAI-compatible API instead of local sentence-transformers
    api_base: str = os.getenv("EMBEDDING_BASE_URL", "")
    api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "512"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "64"))


@dataclass
class RAGConfig:
    """RAG pipeline configuration."""

    collection_name: str = "codebase"
    persist_directory: str = str(BASE_DIR / "data" / "qdrant")
    top_k: int = int(os.getenv("RAG_TOP_K", "20"))


@dataclass
class Neo4jConfig:
    """Neo4j graph database configuration."""

    enabled: bool = os.getenv("NEO4J_ENABLED", "false").lower() in ("true", "1", "yes")
    uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user: str = os.getenv("NEO4J_USER", "neo4j")
    password: str = os.getenv("NEO4J_PASSWORD", "password")
    database: str = os.getenv("NEO4J_DATABASE", "neo4j")


@dataclass
class EvaluationConfig:
    """Evaluation configuration."""

    dataset_name: str = os.getenv("EVAL_DATASET", "princeton-nlp/SWE-bench_Lite")
    top_n_values: list = field(default_factory=lambda: [1, 3, 5])
    output_dir: str = str(BASE_DIR / "results")


@dataclass
class ScoringConfig:
    """Unified scoring configuration."""

    weight_llm_confidence: float = float(os.getenv("SCORE_WEIGHT_LLM", "1.0"))
    weight_stack_trace: float = float(os.getenv("SCORE_WEIGHT_STACK_TRACE", "2.5"))
    weight_error_match: float = float(os.getenv("SCORE_WEIGHT_ERROR", "1.5"))
    weight_mentioned_file: float = float(os.getenv("SCORE_WEIGHT_MENTIONED", "1.2"))
    weight_graph_proximity: float = float(os.getenv("SCORE_WEIGHT_GRAPH", "0.8"))
    weight_semantic: float = float(os.getenv("SCORE_WEIGHT_SEMANTIC", "0.6"))
    weight_method_count: float = float(os.getenv("SCORE_WEIGHT_METHOD", "0.3"))
    weight_git_recency: float = float(os.getenv("SCORE_WEIGHT_GIT_RECENCY", "0.5"))
    # E1: support from surviving hypotheses (posterior), negative for falsified
    weight_hypothesis_support: float = float(os.getenv("SCORE_WEIGHT_HYPOTHESIS", "0.8"))
    # RRF consensus across stage rankings (comprehension/explorer/
    # confirmation/stack-trace) — LocAgent-style; 0 disables
    weight_rank_consensus: float = float(os.getenv("SCORE_WEIGHT_CONSENSUS", "1.0"))
    git_recency_half_life_days: int = int(os.getenv("SCORE_GIT_RECENCY_HALFLIFE_DAYS", "90"))
    test_file_penalty: float = float(os.getenv("SCORE_TEST_PENALTY", "0.5"))
    enable_unified_scoring: bool = os.getenv(
        "ENABLE_UNIFIED_SCORING", "true"
    ).lower() in ("true", "1", "yes")


@dataclass
class Config:
    """Main configuration container."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    max_agent_iterations: int = int(os.getenv("MAX_AGENT_ITERATIONS", "10"))
    max_parallel_tools: int = int(os.getenv("MAX_PARALLEL_TOOLS", "8"))
    max_parallel_evals: int = int(os.getenv("MAX_PARALLEL_EVALS", "4"))
    enable_graph_rag: bool = os.getenv("ENABLE_GRAPH_RAG", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    enable_repo_skeleton: bool = os.getenv("ENABLE_REPO_SKELETON", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    repo_skeleton_max_files: int = int(os.getenv("REPO_SKELETON_MAX_FILES", "80"))
    # Self-reflection: extra Navigation+Confirmation rounds when top confidence is low (max 2 extra)
    reflection_max_rounds: int = int(os.getenv("REFLECTION_MAX_ROUNDS", "2"))
    reflection_conf_threshold: float = float(
        os.getenv("REFLECTION_CONF_THRESHOLD", "0.5")
    )
    # Best-of-N: sampling temperature when base LLM_TEMPERATURE is 0
    multi_pass_temperature: float = float(os.getenv("MULTI_PASS_TEMPERATURE", "0.3"))
    # Hard timeout per bug (seconds). 0 = no limit. Prevents runaway eval jobs.
    per_bug_timeout: int = int(os.getenv("PER_BUG_TIMEOUT", "300"))
    # Per-LLM-call HTTP timeout (seconds). Prevents a single slow API call from
    # blocking a worker thread indefinitely. Should be < per_bug_timeout.
    llm_call_timeout: int = int(os.getenv("LLM_CALL_TIMEOUT", "120"))
    # Network-fault resilience: SDK-level retries on the shared client, plus an
    # application-level exponential-backoff wrapper around every chat call.
    # The 300-run lost ~69 instances to dashscope connection resets before this.
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "6"))
    llm_call_retries: int = int(os.getenv("LLM_CALL_RETRIES", "5"))
    llm_retry_base_delay: float = float(os.getenv("LLM_RETRY_BASE_DELAY", "2.0"))
    # BugCerberus-style: lightweight LLM pre-extraction of bug_phenomenon/explanation/traceback
    enable_structured_bug_extraction: bool = os.getenv(
        "ENABLE_STRUCTURED_BUG_EXTRACTION", "true"
    ).lower() not in ("false", "0", "no")
    # Comprehension v2: one tool-free call; the agentic tool loop runs only as
    # an escalation when the single shot is low-confidence or empty.
    # Default OFF: two 50-instance ablations showed single-shot trades
    # 5-6đ Top-1 for the saved calls (72-74% vs 80% baseline).
    comprehension_single_shot: bool = os.getenv(
        "COMPREHENSION_SINGLE_SHOT", "false"
    ).lower() not in ("false", "0", "no")
    comprehension_escalation_conf: float = float(
        os.getenv("COMPREHENSION_ESCALATION_CONF", "0.5")
    )
    comprehension_max_iterations: int = int(
        os.getenv("COMPREHENSION_MAX_ITERATIONS", "4")
    )
    # v2 verify pass: one extra tool-free call showing outlines of the top
    # suspect files, so the model checks the FIX location (not the symptom
    # location) before its ranking seeds downstream phases. Self-reported
    # confidence is uninformative (qwen answers 0.95 flat), so this replaces
    # confidence-based escalation as the accuracy guard.
    comprehension_verify_shot: bool = os.getenv(
        "COMPREHENSION_VERIFY_SHOT", "true"
    ).lower() not in ("false", "0", "no")
    comprehension_verify_files: int = int(
        os.getenv("COMPREHENSION_VERIFY_FILES", "3")
    )
    # Full repository file listing in the comprehension prompt (the bare-LLM
    # baseline reaches 64% Top-1 from the tree alone — richest cheap signal)
    comprehension_file_tree: bool = os.getenv(
        "COMPREHENSION_FILE_TREE", "true"
    ).lower() not in ("false", "0", "no")
    comprehension_file_tree_max: int = int(
        os.getenv("COMPREHENSION_FILE_TREE_MAX", "2500")
    )
    # Confirmation v2: one tool-free listwise call over an evidence pack built
    # from the explorer's scored observations (source excerpts via line
    # ranges). Falls back to the tool loop on a STRUCTURAL trigger only —
    # thin evidence or free-form navigation — never self-reported confidence.
    confirmation_single_shot: bool = os.getenv(
        "CONFIRMATION_SINGLE_SHOT", "false"
    ).lower() in ("true", "1", "yes")
    # loop = full tool loop | single = listwise only (best Top-3/5, weak
    # Top-1) | hybrid = listwise + short verify loop discriminating #1 among
    # the top-3 (tail preserved). CONFIRMATION_SINGLE_SHOT=true maps to
    # "single" for backward compatibility.
    confirmation_mode: str = (
        os.getenv("CONFIRMATION_MODE")
        or (
            "single"
            if os.getenv("CONFIRMATION_SINGLE_SHOT", "").lower()
            in ("true", "1", "yes")
            else "loop"
        )
    ).lower()
    confirmation_verify_iters: int = int(
        os.getenv("CONFIRMATION_VERIFY_ITERS", "3")
    )
    # H1: patch-grounded discrimination between the final #1 and #2 — one
    # call drafting a concrete minimal patch for each, then picking the file
    # a real fix would edit. Targets adjacent-layer confusions (gold at
    # rank 2 = 7% of the 300-run).
    enable_patch_duel: bool = os.getenv(
        "ENABLE_PATCH_DUEL", "false"
    ).lower() in ("true", "1", "yes")
    patch_duel_max_lines: int = int(os.getenv("PATCH_DUEL_MAX_LINES", "150"))
    confirmation_evidence_top_k: int = int(
        os.getenv("CONFIRMATION_EVIDENCE_TOP_K", "8")
    )
    confirmation_evidence_max_lines: int = int(
        os.getenv("CONFIRMATION_EVIDENCE_MAX_LINES", "100")
    )
    confirmation_min_evidence: int = int(
        os.getenv("CONFIRMATION_MIN_EVIDENCE", "3")
    )
    # E1: competing-hypotheses comprehension + evidence-based verification
    enable_hypothesis_loop: bool = os.getenv(
        "ENABLE_HYPOTHESIS_LOOP", "false"
    ).lower() in ("true", "1", "yes")
    hypothesis_k: int = int(os.getenv("HYPOTHESIS_K", "4"))
    hypothesis_verify_max_iter: int = int(os.getenv("HYPOTHESIS_VERIFY_MAX_ITER", "6"))
    hypothesis_falsify_threshold: float = float(
        os.getenv("HYPOTHESIS_FALSIFY_THRESHOLD", "0.15")
    )
    # Skip the verification stage on stack-trace-rich bugs with a confident
    # top hypothesis (they don't need competing hypotheses; controls tokens)
    hypothesis_fastpath_prior: float = float(
        os.getenv("HYPOTHESIS_FASTPATH_PRIOR", "0.85")
    )
    # E2: priority-guided exploration (OrcaLoca-style) — a deterministic
    # scheduler owns the navigation loop; the LLM only scores observations
    enable_priority_exploration: bool = os.getenv(
        "ENABLE_PRIORITY_EXPLORATION", "false"
    ).lower() in ("true", "1", "yes")
    exploration_max_actions: int = int(os.getenv("EXPLORATION_MAX_ACTIONS", "20"))
    exploration_w_llm: float = float(os.getenv("EXPLORATION_W_LLM", "0.5"))
    exploration_w_graph: float = float(os.getenv("EXPLORATION_W_GRAPH", "0.3"))
    exploration_w_signal: float = float(os.getenv("EXPLORATION_W_SIGNAL", "0.2"))
    # Java CPG is regex-built and noisier — lower graph-distance weight
    exploration_w_graph_java: float = float(os.getenv("EXPLORATION_W_GRAPH_JAVA", "0.15"))
    exploration_min_priority: float = float(os.getenv("EXPLORATION_MIN_PRIORITY", "0.15"))
    exploration_max_depth: int = int(os.getenv("EXPLORATION_MAX_DEPTH", "4"))
    # Run vanilla free-form NavigationAgent when the explorer finds too little
    exploration_fallback_to_freeform: bool = os.getenv(
        "EXPLORATION_FALLBACK_TO_FREEFORM", "true"
    ).lower() not in ("false", "0", "no")
    # E3: hierarchical narrowing (file → function/line via one structured LLM call)
    enable_hierarchical_narrowing: bool = os.getenv(
        "ENABLE_HIERARCHICAL_NARROWING", "false"
    ).lower() in ("true", "1", "yes")
    # E3: listwise rerank of the top-K ranked files (permutation only, never
    # changes pool membership — Top-10/recall are invariant by construction)
    enable_listwise_rerank: bool = os.getenv(
        "ENABLE_LISTWISE_RERANK", "false"
    ).lower() in ("true", "1", "yes")
    listwise_rerank_top_k: int = int(os.getenv("LISTWISE_RERANK_TOP_K", "10"))
    listwise_rerank_passes: int = int(os.getenv("LISTWISE_RERANK_PASSES", "1"))


# Singleton config instance
config = Config()
