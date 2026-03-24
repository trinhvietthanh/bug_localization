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
    persist_directory: str = str(BASE_DIR / "data" / "chromadb")
    top_k: int = int(os.getenv("RAG_TOP_K", "20"))


@dataclass
class EvaluationConfig:
    """Evaluation configuration."""

    dataset_name: str = os.getenv("EVAL_DATASET", "princeton-nlp/SWE-bench_Lite")
    top_n_values: list = field(default_factory=lambda: [1, 3, 5])
    output_dir: str = str(BASE_DIR / "results")


@dataclass
class RerankerConfig:
    """Reranker configuration."""

    enabled: bool = os.getenv("RERANKER_ENABLED", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    api_key: str = os.getenv("RERANK_API_KEY", os.getenv("JINA_API_KEY", ""))
    model_name: str = os.getenv(
        "RERANK_MODEL", os.getenv("JINA_RERANKER_MODEL", "jina-reranker-v2-code")
    )
    base_url: str = os.getenv("RERANK_BASE_URL", "https://api.jina.ai/v1/rerank")
    top_n: int = int(os.getenv("RERANKER_TOP_N", "10"))


@dataclass
class Config:
    """Main configuration container."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    reranker: RerankerConfig = field(default_factory=RerankerConfig)
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
    repo_skeleton_max_files: int = int(os.getenv("REPO_SKELETON_MAX_FILES", "180"))
    rerank_max_chars_per_file: int = int(
        os.getenv("RERANK_MAX_CHARS_PER_FILE", "12000")
    )
    # Self-reflection: extra Navigation+Confirmation rounds when top confidence is low (max 2 extra)
    reflection_max_rounds: int = int(os.getenv("REFLECTION_MAX_ROUNDS", "2"))
    reflection_conf_threshold: float = float(
        os.getenv("REFLECTION_CONF_THRESHOLD", "0.3")
    )
    # Best-of-N: sampling temperature when base LLM_TEMPERATURE is 0
    multi_pass_temperature: float = float(os.getenv("MULTI_PASS_TEMPERATURE", "0.3"))


# Singleton config instance
config = Config()
