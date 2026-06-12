"""
Code search tool for agents.
Provides text/regex search across a codebase directory.
Uses ripgrep (rg) when available for 10-50x speed improvement,
falling back to a pure-Python os.walk implementation.
"""

import os
import re
import json
import shutil
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tools.cache import read_file_cached

logger = logging.getLogger(__name__)

# Detect once at import time whether `rg` is available on PATH
_RG_AVAILABLE: bool = shutil.which("rg") is not None
if _RG_AVAILABLE:
    logger.debug("ripgrep (rg) found — using fast search backend")
else:
    logger.debug("ripgrep not found — using Python search backend")


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
    max_results: int = 20,
    context_lines: int = 1,
    file_pattern: str = None,
) -> list[SearchResult]:
    """
    Search for a query string in all code files within a repository.

    Uses ripgrep when available (fast), otherwise falls back to Python.

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
    if _RG_AVAILABLE:
        try:
            return _code_search_rg(
                query, repo_path,
                is_regex=is_regex,
                case_sensitive=case_sensitive,
                max_results=max_results,
                context_lines=context_lines,
                file_pattern=file_pattern,
            )
        except Exception as exc:
            logger.warning(
                f"ripgrep search failed ({exc}), falling back to Python implementation"
            )
    return _code_search_python(
        query, repo_path,
        is_regex=is_regex,
        case_sensitive=case_sensitive,
        max_results=max_results,
        context_lines=context_lines,
        file_pattern=file_pattern,
    )


def _code_search_rg(
    query: str,
    repo_path: str,
    is_regex: bool = False,
    case_sensitive: bool = False,
    max_results: int = 20,
    context_lines: int = 1,
    file_pattern: str = None,
) -> list[SearchResult]:
    """
    Fast code search using ripgrep (rg) with JSON output.
    """
    cmd = [
        "rg",
        "--json",
        f"--context={context_lines}",
        f"--max-count={max_results}",
    ]

    if not case_sensitive:
        cmd.append("--ignore-case")
    if not is_regex:
        cmd.append("--fixed-strings")

    # Glob filters
    if file_pattern:
        cmd.extend(["--glob", file_pattern])
    else:
        for ext in CODE_EXTENSIONS:
            cmd.extend(["--glob", f"*{ext}"])

    # Skip test directories
    for skip in SKIP_DIRS:
        cmd.extend(["--glob", f"!**/{skip}/**"])
    cmd.extend(["--glob", "!**/test*/**"])

    cmd.append(query)
    cmd.append(repo_path)

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )

    results: list[SearchResult] = []
    repo = Path(repo_path)

    # rg --json emits one JSON object per line
    context_before: list[str] = []
    pending_match: dict | None = None
    pending_context_after: list[str] = []

    def _flush():
        """Emit the pending match, then reset."""
        nonlocal pending_match, pending_context_after, context_before
        if pending_match and len(results) < max_results:
            file_path = pending_match["file_path"]
            if not _is_test_file(Path(repo_path) / file_path):
                results.append(SearchResult(
                    file_path=file_path,
                    line_number=pending_match["line_number"],
                    line_content=pending_match["line_content"],
                    context_before=list(pending_match["ctx_before"]),
                    context_after=list(pending_context_after),
                ))
        pending_match = None
        pending_context_after = []
        context_before = []

    for raw_line in proc.stdout.splitlines():
        if len(results) >= max_results:
            break
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        kind = obj.get("type")
        if kind == "match":
            _flush()
            data = obj["data"]
            file_path = str(Path(data["path"]["text"]).relative_to(repo))
            line_text = data["lines"]["text"].rstrip("\n")
            pending_match = {
                "file_path": file_path,
                "line_number": data["line_number"],
                "line_content": line_text.strip(),
                "ctx_before": list(context_before),
            }
            context_before = []
        elif kind == "context":
            data = obj["data"]
            line_text = data["lines"]["text"].rstrip("\n").strip()
            if pending_match is None:
                # before the match
                context_before.append(line_text)
                if len(context_before) > context_lines:
                    context_before.pop(0)
            else:
                # after the match
                pending_context_after.append(line_text)
        elif kind in ("end", "summary"):
            _flush()

    _flush()  # flush last pending
    return results


def _code_search_python(
    query: str,
    repo_path: str,
    is_regex: bool = False,
    case_sensitive: bool = False,
    max_results: int = 20,
    context_lines: int = 1,
    file_pattern: str = None,
) -> list[SearchResult]:
    """
    Pure-Python fallback code search using os.walk.
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
