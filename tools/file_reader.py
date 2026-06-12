"""
File reader tool for agents.
Provides file content reading, directory listing, and file metadata.
"""

import logging
from pathlib import Path

from tools.cache import read_file_cached

logger = logging.getLogger(__name__)



def read_file(
    file_path: str,
    repo_path: str,
    start_line: int = None,
    end_line: int = None,
    max_lines: int = 100,
) -> str:
    """
    Read contents of a file within the repository.

    Args:
        file_path: Relative path to the file within the repo
        repo_path: Path to the repository root
        start_line: Optional start line (1-indexed)
        end_line: Optional end line (1-indexed, inclusive)
        max_lines: Maximum lines to return

    Returns:
        File contents as a string with line numbers
    """
    full_path = Path(repo_path) / file_path

    if not full_path.exists():
        return f"Error: File not found: {file_path}"

    if not full_path.is_file():
        return f"Error: Not a file: {file_path}"

    try:
        content = read_file_cached(str(full_path))
    except Exception as e:
        return f"Error reading file: {e}"

    lines = content.split("\n")
    total_lines = len(lines)

    # Apply line range
    if start_line is not None:
        start_idx = max(0, start_line - 1)
    else:
        start_idx = 0

    if end_line is not None:
        end_idx = min(total_lines, end_line)
    else:
        end_idx = min(total_lines, start_idx + max_lines)

    selected_lines = lines[start_idx:end_idx]

    # Format with line numbers
    numbered_lines = []
    for i, line in enumerate(selected_lines, start=start_idx + 1):
        numbered_lines.append(f"{i:4d} | {line}")

    header = f"File: {file_path} ({total_lines} lines total, showing {start_idx+1}-{end_idx})\n"
    return header + "\n".join(numbered_lines)


def list_directory(
    dir_path: str,
    repo_path: str,
    max_depth: int = 2,
    show_hidden: bool = False,
) -> str:
    """
    List directory contents with a tree-like structure.

    Args:
        dir_path: Relative path to directory within repo
        repo_path: Path to the repository root
        max_depth: Maximum depth to traverse
        show_hidden: Whether to show hidden files/dirs

    Returns:
        Directory tree as a formatted string
    """
    full_path = Path(repo_path) / dir_path

    if not full_path.exists():
        return f"Error: Directory not found: {dir_path}"

    lines = []
    _build_tree(full_path, Path(repo_path), lines, "", max_depth, 0, show_hidden)
    return "\n".join(lines)


def _build_tree(
    path: Path,
    repo_root: Path,
    lines: list,
    prefix: str,
    max_depth: int,
    current_depth: int,
    show_hidden: bool,
):
    """Recursively build directory tree."""
    if current_depth > max_depth:
        return

    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return

    skip = {".git", "__pycache__", "node_modules", ".tox", ".eggs"}
    entries = [e for e in entries if e.name not in skip]
    if not show_hidden:
        entries = [e for e in entries if not e.name.startswith(".")]

    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        rel = str(entry.relative_to(repo_root))

        if entry.is_dir():
            lines.append(f"{prefix}{connector}{entry.name}/")
            next_prefix = prefix + ("    " if is_last else "│   ")
            _build_tree(entry, repo_root, lines, next_prefix, max_depth, current_depth + 1, show_hidden)
        else:
            size = entry.stat().st_size
            lines.append(f"{prefix}{connector}{entry.name} ({_format_size(size)})")


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    else:
        return f"{size / (1024 * 1024):.1f}MB"


