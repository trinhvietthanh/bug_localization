---
name: bug-localization
description: >
  Multi-agent agentic bug localization skill. Given a natural-language bug
  report and a source-code repository, autonomously explores the codebase using
  code search, AST analysis, RAG semantic search, and graph-based call-graph
  tracing to produce a ranked list of suspicious files, functions, and a
  root-cause explanation.
version: "1.0.0"
language: python
entry_point: skill.py
mcp_server: mcp_server.py
benchmarks:
  - Defects4J (Java): Top-1 ~55–65%, MRR ~0.62 [Lang, Math, Time, Closure, Mockito]
  - SWE-bench Lite (Python): Top-1 ~35–45%, MRR ~0.41
  - BugsInPy (Python): Top-1 ~50–60%, MRR ~0.58
---

# Bug Localization Skill

## Overview

This skill implements a **three-phase multi-agent pipeline** for automated bug
localization in Java and Python projects:

```
Bug Report + Repo
      │
      ▼
┌─────────────────────┐
│  Phase 1: Comprehension │  ← Analyze bug report, extract fault hypothesis
│  (ComprehensionAgent)   │    and candidate class/file names
└────────┬────────────┘
         │ fault_hypothesis + candidate_files
         ▼
┌─────────────────────┐
│  Phase 2: Navigation    │  ← Iteratively explore codebase (code search,
│  (NavigationAgent)      │    file reads, AST, RAG, graph traversal)
└────────┬────────────┘
         │ refined candidate_files
         ▼
┌─────────────────────┐
│  Phase 3: Confirmation  │  ← Validate and rank candidates, produce
│  (ConfirmationAgent)    │    root-cause explanation
└────────┬────────────┘
         │
         ▼
 LocalizationResult
 (ranked_files, ranked_locations, root_cause, explanation)
```

A **Code Property Graph (Graph RAG)** is built in a background thread during
Phase 1 and injected before Phase 2 to enable call-graph analysis.

---

## When to Use This Skill

Invoke this skill when:
- A user provides a bug report / issue / error description and a repo path
- You need to identify which file(s) or function(s) are most likely responsible for a defect
- You are running a benchmark evaluation (Defects4J, SWE-bench, BugsInPy)
- You want automated root-cause analysis before writing a patch

Do **not** invoke this skill when:
- The repo is unavailable or cannot be checked out
- No bug description is available (this is not code search for a feature)
- The task is to *fix* the bug (localization only — pass results to a patch skill)

---

## Inputs

| Parameter | Type | Required | Description |
|---|---|---|---|
| `bug_report` | `str` | ✅ | Natural-language bug description (from issue tracker, stack trace, or user) |
| `repo_path` | `str` | ✅ | Absolute path to the checked-out buggy version of the repository |
| `instance_id` | `str` | ❌ | Benchmark instance ID (e.g., `Lang_1`, `django__django-11099`) |
| `dataset` | `str` | ❌ | Dataset name for loading by instance ID (default: `princeton-nlp/SWE-bench_Lite`) |
| `language` | `str` | ❌ | `"python"`, `"java"`, or `"auto"` (default: auto-detect) |
| `enable_graph_rag` | `bool` | ❌ | Whether to build Code Property Graph (default: `true`) |
| `verbose` | `bool` | ❌ | Print agent traces to stdout (default: `false`) |

---

## Output Schema

```json
{
  "instance_id": "string",
  "success": true,
  "ranked_files": ["path/to/file.py", "..."],
  "ranked_locations": [
    {
      "rank": 1,
      "file_path": "path/to/file.py",
      "function_name": "my_function",
      "class_name": "MyClass",
      "confidence": 0.92,
      "explanation": "The bug is in the divide method…"
    }
  ],
  "root_cause": "The operands in Calculator.divide() are swapped…",
  "explanation": "Full agent reasoning text…",
  "total_time": 12.4,
  "total_llm_calls": 8,
  "total_tool_calls": 23
}
```

---

## Tool Catalog

These are the tools available to the internal agents. Host agents implementing
this skill from scratch should provide equivalent tool implementations.

### `code_search`
```json
{
  "name": "code_search",
  "description": "Search for a pattern or keyword across source files using grep-style matching. Returns file paths, line numbers, and matching lines.",
  "parameters": {
    "pattern": {"type": "string", "description": "Search pattern (substring or regex)"},
    "repo_path": {"type": "string", "description": "Repository root path"},
    "file_pattern": {"type": "string", "description": "Glob filter, e.g. '*.py' or '*.java'", "default": "*"},
    "max_results": {"type": "integer", "default": 50}
  }
}
```

### `read_file`
```json
{
  "name": "read_file",
  "description": "Read the full content of a source file, or a specific line range.",
  "parameters": {
    "file_path": {"type": "string", "description": "Relative path from repo root"},
    "repo_path": {"type": "string"},
    "start_line": {"type": "integer", "description": "First line to read (1-indexed)", "optional": true},
    "end_line": {"type": "integer", "description": "Last line to read (1-indexed)", "optional": true}
  }
}
```

### `list_directory`
```json
{
  "name": "list_directory",
  "description": "List files and subdirectories in a directory. Use to understand project structure.",
  "parameters": {
    "directory": {"type": "string", "description": "Relative path from repo root (use '.' for root)"},
    "repo_path": {"type": "string"}
  }
}
```

### `get_file_outline`
```json
{
  "name": "get_file_outline",
  "description": "Return the high-level outline of a file: classes, methods, and function signatures (no body). Faster than read_file for structure discovery.",
  "parameters": {
    "file_path": {"type": "string"},
    "repo_path": {"type": "string"}
  }
}
```

### `get_function_source`
```json
{
  "name": "get_function_source",
  "description": "Return the full source code of a specific function or method.",
  "parameters": {
    "file_path": {"type": "string"},
    "repo_path": {"type": "string"},
    "function_name": {"type": "string"},
    "class_name": {"type": "string", "description": "Class containing the method", "optional": true}
  }
}
```

### `semantic_search`
```json
{
  "name": "semantic_search",
  "description": "Find code chunks semantically similar to the query using vector embeddings (ChromaDB). Requires a pre-built RAG index.",
  "parameters": {
    "query": {"type": "string", "description": "Natural language or code description"},
    "top_k": {"type": "integer", "default": 10}
  }
}
```

### `graph_search`
```json
{
  "name": "graph_search",
  "description": "Search the Code Property Graph (CPG) for functions, classes, or modules matching a query. Returns graph neighbors via BFS expansion.",
  "parameters": {
    "query": {"type": "string"},
    "repo_path": {"type": "string"},
    "top_k": {"type": "integer", "default": 10},
    "language": {"type": "string", "default": "auto"}
  }
}
```

### `find_callers`
```json
{
  "name": "find_callers",
  "description": "Find all functions/methods that call a given function. Useful for tracing how a buggy function is invoked.",
  "parameters": {
    "function_name": {"type": "string"},
    "repo_path": {"type": "string"},
    "language": {"type": "string", "default": "auto"}
  }
}
```

### `find_callees`
```json
{
  "name": "find_callees",
  "description": "Find all functions/methods called by a given function. Useful for tracing the call chain from a suspected entry point.",
  "parameters": {
    "function_name": {"type": "string"},
    "repo_path": {"type": "string"},
    "language": {"type": "string", "default": "auto"}
  }
}
```

### `git_log`
```json
{
  "name": "git_log",
  "description": "Return recent git commit history for a file, optionally filtered by keyword. Useful for identifying when a bug was introduced.",
  "parameters": {
    "file_path": {"type": "string"},
    "repo_path": {"type": "string"},
    "max_commits": {"type": "integer", "default": 10},
    "keyword": {"type": "string", "description": "Filter commits by message keyword", "optional": true}
  }
}
```

---

## Supported Benchmarks

| Benchmark | Language | Projects | Notes |
|---|---|---|---|
| **Defects4J** | Java | Lang, Math, Time, Closure, Mockito | Pre-checkout required: `defects4j checkout` |
| **SWE-bench Lite** | Python | django, astropy, flask, pandas, sympy, … | Loaded from Hugging Face datasets |
| **BugsInPy** | Python | pandas, scrapy, keras, black, thefuck, … | Pre-checkout via `scripts/checkout_bugsinpy.py` |

---

## Quick-Start Examples

### CLI
```bash
cd thesis/

# Free-form bug report
python main.py localize \
  --bug-report "The divide method returns wrong results when denominator is zero" \
  --repo-path /path/to/repo

# From benchmark instance ID
python main.py localize \
  --instance-id "django__django-11099" \
  --repo-path /path/to/django

# Defects4J
python main.py defects4j \
  --project Lang --instance-id Lang_1 \
  --repo-path data/defects4j_checkouts/Lang/Lang_1

# BugsInPy
python main.py bugsinpy \
  --project pandas --limit 5 \
  --repo-path data/bugsinpy_checkouts
```

### Python API
```python
import sys
sys.path.insert(0, "/path/to/thesis")

from skill import BugLocalizationSkill

skill = BugLocalizationSkill(enable_graph_rag=False)

result = skill.localize_bug(
    bug_report="Calculator.divide() swaps operands. calc.divide(10,2) returns 0.2 instead of 5.0",
    repo_path="/path/to/repo",
)

print(result.ranked_files)     # ['calculator/core.py', ...]
print(result.root_cause)       # 'Operands are swapped in the divide method…'
print(result.success)          # True
```

### MCP (via host agent)
```bash
# Start the server
cd thesis/
./start_mcp_server.sh

# The MCP server exposes:
#   localize_bug          — main localization tool
#   index_repository      — build/update RAG index
#   get_supported_benchmarks — list available datasets
```

---

## Configuration

All settings are controlled via environment variables (`.env` file):

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | LLM backend: `gemini`, `openai`, or custom |
| `LLM_MODEL` | `gemini-2.0-flash` | Model name |
| `GEMINI_API_KEY` | — | Gemini API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `ENABLE_GRAPH_RAG` | `true` | Build Code Property Graph |
| `MAX_AGENT_ITERATIONS` | `10` | Max LLM steps per agent phase |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformer for RAG |
| `RAG_TOP_K` | `20` | Chunks retrieved per semantic query |

---

## Dependencies

```
openai>=1.0          # LLM client (used for Gemini/OpenAI/Ollama via OpenAI-compatible API)
chromadb>=0.4        # Vector store for semantic RAG
sentence-transformers>=2.0  # Embeddings
networkx>=3.0        # Code Property Graph
mcp[cli]>=1.0        # MCP server (for mcp_server.py)
gitpython>=3.0       # Git history queries
rich>=13.0           # CLI output
```
