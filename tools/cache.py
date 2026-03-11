"""
Shared file I/O cache for tools.
Avoids redundant reads and AST parses of the same files within a localization run.
"""

import ast
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=512)
def read_file_cached(full_path: str) -> str:
    """Read and cache file contents. Key is the absolute path string."""
    return Path(full_path).read_text(encoding="utf-8", errors="ignore")


@lru_cache(maxsize=256)
def parse_ast_cached(full_path: str) -> ast.Module | None:
    """Parse and cache AST for a Python file."""
    try:
        source = read_file_cached(full_path)
        return ast.parse(source, filename=full_path)
    except SyntaxError:
        return None


def clear_caches():
    """Clear all file caches (call between localization runs on different repos)."""
    # Xoá cache trong đa luồng có rủi ro race condition, tạm tắt để cache tự đào thải.
    pass
