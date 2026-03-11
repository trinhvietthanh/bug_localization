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
    model_name: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
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
class Config:
    """Main configuration container."""
    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    max_agent_iterations: int = int(os.getenv("MAX_AGENT_ITERATIONS", "10"))
    max_parallel_tools: int = int(os.getenv("MAX_PARALLEL_TOOLS", "8"))
    max_parallel_evals: int = int(os.getenv("MAX_PARALLEL_EVALS", "4"))
    enable_graph_rag: bool = os.getenv("ENABLE_GRAPH_RAG", "true").lower() in ("true", "1", "yes")


# Singleton config instance
config = Config()
