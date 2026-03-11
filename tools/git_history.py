"""
Git history tool for agents.
Provides git log and git blame functionality.
"""

import logging
import subprocess
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GitLogEntry:
    """A single git log entry."""
    commit_hash: str
    author: str
    date: str
    message: str
    files_changed: list[str] = None

    def __post_init__(self):
        self.files_changed = self.files_changed or []


def git_log(
    repo_path: str,
    file_path: str = None,
    max_entries: int = 10,
) -> list[GitLogEntry]:
    """
    Get git log for a file or the entire repo.

    Args:
        repo_path: Path to the repository
        file_path: Optional relative file path to get history for
        max_entries: Maximum log entries to return

    Returns:
        List of GitLogEntry objects
    """
    cmd = [
        "git", "log",
        f"-n{max_entries}",
        "--format=%H%n%an%n%ai%n%s%n---END---",
        "--name-only",
    ]

    if file_path:
        cmd.append("--")
        cmd.append(file_path)

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error(f"git log failed: {result.stderr}")
            return []
    except subprocess.TimeoutExpired:
        logger.error("git log timed out")
        return []
    except FileNotFoundError:
        logger.error("git not found")
        return []

    entries = []
    raw_entries = result.stdout.strip().split("---END---")

    for raw in raw_entries:
        raw = raw.strip()
        if not raw:
            continue

        lines = raw.split("\n")
        if len(lines) < 4:
            continue

        # Parse files changed (after the commit info)
        files = [l.strip() for l in lines[4:] if l.strip()]

        entries.append(GitLogEntry(
            commit_hash=lines[0].strip(),
            author=lines[1].strip(),
            date=lines[2].strip(),
            message=lines[3].strip(),
            files_changed=files,
        ))

    return entries


def git_log_formatted(
    repo_path: str,
    file_path: str = None,
    max_entries: int = 10,
) -> str:
    """Get formatted git log as a string."""
    entries = git_log(repo_path, file_path, max_entries)

    if not entries:
        target = file_path or "repository"
        return f"No git history found for {target}"

    lines = []
    for entry in entries:
        lines.append(f"[{entry.commit_hash[:8]}] {entry.date[:10]} - {entry.author}")
        lines.append(f"  {entry.message}")
        if entry.files_changed:
            for f in entry.files_changed[:5]:
                lines.append(f"    M {f}")
            if len(entry.files_changed) > 5:
                lines.append(f"    ... and {len(entry.files_changed) - 5} more files")
        lines.append("")

    return "\n".join(lines)


def git_blame(
    repo_path: str,
    file_path: str,
    start_line: int = None,
    end_line: int = None,
) -> str:
    """
    Get git blame for a file or line range.

    Args:
        repo_path: Path to the repository
        file_path: Relative path to the file
        start_line: Optional start line
        end_line: Optional end line

    Returns:
        Formatted blame output
    """
    cmd = ["git", "blame", "--line-porcelain"]

    if start_line and end_line:
        cmd.extend([f"-L{start_line},{end_line}"])
    elif start_line:
        cmd.extend([f"-L{start_line},+20"])

    cmd.append(file_path)

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return f"git blame failed: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "git blame timed out"
    except FileNotFoundError:
        return "git not found"

    # Parse porcelain output into readable format
    lines = []
    current = {}
    for line in result.stdout.split("\n"):
        if line.startswith("\t"):
            # This is the actual code line
            code = line[1:]
            commit = current.get("hash", "?")[:8]
            author = current.get("author", "?")
            lines.append(f"{commit} ({author:>15}) | {code}")
            current = {}
        elif " " in line:
            parts = line.split(" ", 1)
            key = parts[0]
            value = parts[1] if len(parts) > 1 else ""
            if len(key) == 40:  # commit hash
                current["hash"] = key
            elif key == "author":
                current["author"] = value

    return "\n".join(lines) if lines else "No blame data available"


# Tool description for LLM agents
TOOL_DESCRIPTION = {
    "name": "git_log",
    "description": (
        "Get the git commit history for a specific file or the entire repository. "
        "Shows recent commits with their messages and changed files. "
        "Useful for understanding what changed recently in suspicious files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Relative path to file (optional, omit for full repo history)"
            },
            "max_entries": {
                "type": "integer",
                "description": "Maximum number of log entries",
                "default": 10
            }
        },
        "required": []
    }
}
