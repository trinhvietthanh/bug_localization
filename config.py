"""
Configuration module for Bug Localization System.
Loads settings from environment variables and .env file.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = Path(__file__).parent


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
    # BugCerberus-style: lightweight LLM pre-extraction of bug_phenomenon/explanation/traceback
    enable_structured_bug_extraction: bool = os.getenv(
        "ENABLE_STRUCTURED_BUG_EXTRACTION", "true"
    ).lower() not in ("false", "0", "no")


# Singleton config instance
config = Config()
