"""
Shared path normalization utilities used by both agents and evaluation.

LLM outputs often carry file paths as plain strings, dicts with a "file_path"
key, or strings with trailing ": description" annotations.  The helpers here
canonicalize those into clean, repo-relative path strings.
"""

from typing import Any, Optional

# Keys tried in order when coercing a dict entry to a file path string.
_PATH_DICT_KEYS = ("file_path", "path", "filepath", "file")


def coerce_path_string(path: Any) -> Optional[str]:
    """
    Turn a raw LLM output value into a plain file path string, or None.

    Handles:
    - None                     → None
    - plain string             → stripped string (None if empty)
    - dict with a path key     → value of the first matching key
    - anything else            → None
    """
    if path is None:
        return None
    if isinstance(path, str):
        s = path.strip()
        return s if s else None
    if isinstance(path, dict):
        for key in _PATH_DICT_KEYS:
            v = path.get(key)
            if isinstance(v, str):
                s = v.strip()
                if s:
                    return s
        return None
    return None
