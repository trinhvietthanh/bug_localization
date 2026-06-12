"""
index command — index a codebase for RAG-based semantic search.

Per-project indexing (recommended for Defects4J evaluation):
    python main.py index --project Chart
    python main.py index --project Lang
    python main.py index --project Math ...

This indexes one representative checkout per project and tags all chunks
with repo_id=<project_name> (e.g. "Chart"), so every Chart_N bug query
hits the same index partition.

Single-repo indexing (for custom repos):
    python main.py index --repo-path /path/to/repo [--repo-id my_project]
"""

import re
from pathlib import Path
from rich.console import Console

from config import config

console = Console()


def _project_name_from_path(repo_path: Path) -> str:
    """Extract project name: 'Chart_3' → 'Chart', 'my-repo' → 'my-repo'."""
    basename = repo_path.name
    m = re.match(r'^([A-Za-z][A-Za-z0-9]*?)(?:_\d+)?$', basename)
    return m.group(1) if m else basename


def cmd_index(args):
    """Index a codebase for RAG-based semantic search."""
    from rag.indexer import CodebaseIndexer

    # ── Per-project mode ─────────────────────────────────────────────
    if getattr(args, "project", None):
        _index_project(args)
        return

    # ── Single-repo mode ─────────────────────────────────────────────
    if not args.repo_path:
        console.print("[red]Provide --repo-path <path> or --project <name>[/red]")
        return

    repo_path = Path(args.repo_path).resolve()
    if not repo_path.exists():
        console.print(f"[red]Path not found: {repo_path}[/red]")
        return

    repo_id = getattr(args, "repo_id", None) or _project_name_from_path(repo_path)
    console.print(f"[bold]Indexing:[/bold] {repo_path}  [dim](repo_id='{repo_id}')[/dim]")

    indexer = _make_indexer()

    if args.clear:
        console.print("Clearing existing index...")
        indexer.clear_index()

    num_chunks = indexer.index_repository(str(repo_path), repo_id=repo_id)
    console.print(f"\n✅ Indexed {num_chunks} chunks  (repo_id='{repo_id}')")
    console.print(f"📊 {indexer.get_stats()}")


def _index_project(args):
    """Index one representative checkout for a Defects4J project."""
    project = args.project
    checkouts_base = Path(config.rag.persist_directory).parent / "defects4j_checkouts" / project

    if not checkouts_base.exists():
        # Try common alternative locations
        alt = Path("data/defects4j_checkouts") / project
        if alt.exists():
            checkouts_base = alt
        else:
            console.print(f"[red]No checkout directory found for project '{project}'[/red]")
            console.print(f"  Looked at: {checkouts_base} and {alt}")
            return

    # Pick the first available checkout as representative
    checkouts = sorted(
        [d for d in checkouts_base.iterdir() if d.is_dir()],
        key=lambda d: int(re.search(r'\d+', d.name).group() or 0),
    )
    if not checkouts:
        console.print(f"[red]No checkout subdirectories in {checkouts_base}[/red]")
        return

    repo_path = checkouts[0]
    repo_id = project
    console.print(f"[bold]Indexing project '{project}':[/bold] {repo_path}  [dim](repo_id='{repo_id}')[/dim]")

    indexer = _make_indexer()

    if getattr(args, "clear", False):
        console.print(f"Deleting existing chunks for repo_id='{repo_id}'...")
        indexer.delete_repo(repo_id)

    num_chunks = indexer.index_repository(str(repo_path), repo_id=repo_id)
    console.print(f"\n✅ Indexed {num_chunks} chunks for '{project}'")
    console.print(f"📊 {indexer.get_stats()}")


def _make_indexer():
    from rag.indexer import CodebaseIndexer
    return CodebaseIndexer(
        persist_directory=config.rag.persist_directory,
        collection_name=config.rag.collection_name,
        embedding_model=config.embedding.model_name,
        embedding_api_base=config.embedding.api_base,
        embedding_api_key=config.embedding.api_key,
    )
