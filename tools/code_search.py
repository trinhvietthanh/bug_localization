"""
Code search tool for agents.
Provides text/regex search across a codebase directory.
"""

import os
import re
import logging
from dataclasses import dataclass
from pathlib import Path

from tools.cache import read_file_cached

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result."""
    file_path: str
    line_number: int
    line_content: str
    context_before: list = None
    context_after: list = None

    def __post_init__(self):
        self.context_before = self.context_before or []
        self.context_after = self.context_after or []

    def to_str(self) -> str:
        lines = []
        for l in self.context_before:
            lines.append(f"  {l}")
        lines.append(f"► {self.file_path}:{self.line_number}: {self.line_content}")
        for l in self.context_after:
            lines.append(f"  {l}")
        return "\n".join(lines)


# File extensions to search
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".hpp",
    ".go", ".rs", ".rb", ".php", ".scala", ".kt",
}

# Directories to skip
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".tox", ".eggs",
    "*.egg-info", "build", "dist", ".venv", "venv",
    "test", "tests", "testing"
}

def _is_test_file(filepath: Path) -> bool:
    """Check if file is a test file based on path."""
    parts = filepath.parts
    # Check if any directory is 'test' or 'tests'
    if "test" in parts or "tests" in parts or "testing" in parts:
        return True
    
    name = filepath.name.lower()
    return name.startswith("test_") or name.endswith("_test.py") or name.endswith("test.java") or name.endswith("tests.java")


def code_search(
    query: str,
    repo_path: str,
    is_regex: bool = False,
    case_sensitive: bool = False,
    max_results: int = 50,
    context_lines: int = 2,
    file_pattern: str = None,
) -> list[SearchResult]:
    """
    Search for a query string in all code files within a repository.

    Args:
        query: Search term or regex pattern
        repo_path: Path to the repository root
        is_regex: Whether query is a regex
        case_sensitive: Case-sensitive search
        max_results: Maximum number of results to return
        context_lines: Number of context lines before/after match
        file_pattern: Optional glob pattern to filter files (e.g., "*.py")

    Returns:
        List of SearchResult objects
    """
    results = []
    repo = Path(repo_path)

    if not repo.exists():
        logger.error(f"Repository path does not exist: {repo_path}")
        return results

    # Compile search pattern
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        if is_regex:
            pattern = re.compile(query, flags)
        else:
            pattern = re.compile(re.escape(query), flags)
    except re.error as e:
        logger.error(f"Invalid regex pattern: {e}")
        return results

    # Walk the directory
    for root, dirs, files in os.walk(repo):
        # Skip unwanted directories
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and "test" not in d.lower()]

        for filename in files:
            filepath = Path(root) / filename
            
            if _is_test_file(filepath):
                continue

            # Filter by extension or pattern
            if file_pattern:
                if not filepath.match(file_pattern):
                    continue
            elif filepath.suffix not in CODE_EXTENSIONS:
                continue

            # Search the file
            try:
                file_results = _search_file(
                    filepath, pattern, repo, context_lines
                )
                results.extend(file_results)

                if len(results) >= max_results:
                    return results[:max_results]

            except (UnicodeDecodeError, PermissionError, OSError):
                continue

    return results


def _search_file(
    filepath: Path,
    pattern: re.Pattern,
    repo_root: Path,
    context_lines: int,
) -> list[SearchResult]:
    """Search a single file for the pattern."""
    results = []

    try:
        lines = read_file_cached(str(filepath)).split("\n")
    except Exception:
        return results

    rel_path = str(filepath.relative_to(repo_root))

    for i, line in enumerate(lines):
        if pattern.search(line):
            ctx_before = lines[max(0, i - context_lines):i]
            ctx_after = lines[i + 1:i + 1 + context_lines]

            results.append(SearchResult(
                file_path=rel_path,
                line_number=i + 1,
                line_content=line.strip(),
                context_before=[l.strip() for l in ctx_before],
                context_after=[l.strip() for l in ctx_after],
            ))

    return results


def find_files(
    repo_path: str,
    pattern: str = None,
    extensions: list[str] = None,
    max_results: int = 100,
) -> list[str]:
    """
    Find files in a repository matching a pattern.

    Args:
        repo_path: Path to the repository root
        pattern: Glob or substring pattern for filenames
        extensions: List of file extensions to include
        max_results: Maximum results

    Returns:
        List of relative file paths
    """
    results = []
    repo = Path(repo_path)

    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and "test" not in d.lower()]

        for filename in files:
            filepath = Path(root) / filename
            
            if _is_test_file(filepath):
                continue
                
            rel_path = str(filepath.relative_to(repo))

            # Filter
            if extensions and filepath.suffix not in extensions:
                continue
            if pattern and pattern.lower() not in filename.lower():
                continue

            results.append(rel_path)
            if len(results) >= max_results:
                return results

    return results


# Tool description for LLM agents
TOOL_DESCRIPTION = {
    "name": "code_search",
    "description": (
        "Search for text or regex patterns across all code files in the repository. "
        "Returns matching lines with file paths, line numbers, and surrounding context. "
        "Use this to find specific code patterns, function usages, error messages, etc."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search term or regex pattern to look for"
            },
            "is_regex": {
                "type": "boolean",
                "description": "Whether the query is a regex pattern",
                "default": False
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Whether search is case-sensitive",
                "default": False
            },
            "file_pattern": {
                "type": "string",
                "description": "Optional glob to filter files, e.g. '*.py'",
                "default": None
            },
            "max_results": {
                "type": "integer",
                "description": "Max number of results",
                "default": 30
            }
        },
        "required": ["query"]
    }
}
