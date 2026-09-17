# Repository Guidelines

## Project Structure & Module Organization

`main.py` dispatches CLI subcommands implemented in `commands/`. The localization workflow lives in `agents/`; repository inspection utilities are in `tools/`, retrieval and graph indexing in `rag/`, and scoring in `evaluation/`. `api/` provides the FastAPI service. Keep automated checks in `tests/`, benchmark utilities in `scripts/`, and architecture or presentation material in `docs/`. Runtime settings are defined in `config.py` and loaded from environment variables.

## Build, Test, and Development Commands

Create an isolated environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run `python -m pytest` for the complete test suite, or target a module with `python -m pytest tests/test_graph_rag.py -v`. Start the API locally with `uvicorn api.main:app --reload --port 8000`. Exercise the CLI with commands such as:

```bash
python main.py localize --bug-report "Parser fails" --repo-path /path/to/repo
python main.py index --repo-path /path/to/repo
python main.py evaluate --limit 10 --output results/eval_10.json
```

Benchmark runs may download datasets or require checked-out SWE-bench/Defects4J repositories. Keep generated data out of source modules.

## Coding Style & Naming Conventions

Use four-space indentation, PEP 8 layout, `snake_case` for functions and modules, `PascalCase` for classes, and uppercase constants. Add type hints to public APIs and concise docstrings for non-obvious behavior. Keep CLI parsing in `main.py` and command behavior in `commands/`. No formatter or linter is configured, so match nearby code and group standard-library, third-party, and local imports.

## Testing Guidelines

Tests use pytest. Name files `test_<area>.py` and functions `test_<behavior>`. Prefer deterministic tests with fixtures and mocks for LLM, network, dataset, and Neo4j interactions. Skip integration tests when external services or fixture data are unavailable. Run focused tests while developing, then the full suite before submitting.

## Commit & Pull Request Guidelines

Recent history favors short subjects such as `update: logic ranking`; prefer a clear scoped form, for example `rag: fix graph candidate ranking`. Keep commits focused. Pull requests should explain the problem and approach, list verification commands, link issues or experiments, and include screenshots for UI or visualization changes. Call out configuration and benchmark-result changes.

## Security & Configuration

Store API keys, provider endpoints, and Neo4j credentials in `.env`; never commit secrets. Avoid committing generated indexes, benchmark checkouts, logs, or large result artifacts unless they are intentional research outputs.

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
<!-- CODEGRAPH_END -->
