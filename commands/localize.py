"""
localize command — run bug localization on a single instance.
"""

from pathlib import Path
from rich.console import Console

from config import config

console = Console()


def _make_retriever(persist_dir: str = None):
    """Build a CodeRetriever using current config."""
    from rag.retriever import CodeRetriever

    if persist_dir is None:
        persist_dir = str(Path(config.rag.persist_directory))
    retriever = CodeRetriever(
        persist_directory=persist_dir,
        embedding_model=config.embedding.model_name,
        embedding_api_base=config.embedding.api_base,
        embedding_api_key=config.embedding.api_key,
    )
    if retriever._ready:
        return retriever
    return None


def cmd_localize(args):
    """Run bug localization on a single bug instance or from a dataset."""
    from agents.orchestrator import Orchestrator
    from data.loader import SWEBenchLoader, BugInstance

    if getattr(args, "no_graph_rag", False):
        config.enable_graph_rag = False

    retriever = None
    if args.repo_path:
        try:
            retriever = _make_retriever()
            if retriever:
                console.print(
                    f"✅ RAG index loaded ({retriever.collection.count()} chunks)"
                )
            else:
                console.print(
                    "⚠️  No RAG index found. Run 'index' first for semantic search."
                )
        except Exception as exc:
            console.print(f"⚠️  RAG not available ({exc}). Continuing without semantic search.")

    orchestrator = Orchestrator(retriever=retriever)

    def _localize_instance(instance):
        use_multi = args.multi_pass and args.multi_pass > 1
        refl_kw = {
            "reflection_max_rounds": args.reflection_rounds,
            "reflection_conf_threshold": args.reflection_threshold,
        }
        if use_multi:
            return orchestrator.multi_pass_localize(
                instance,
                repo_path=args.repo_path,
                verbose=True,
                passes=args.multi_pass,
                **refl_kw,
            )
        return orchestrator.localize(
            instance,
            repo_path=args.repo_path,
            verbose=True,
            **refl_kw,
        )

    if args.instance_id:
        loader = SWEBenchLoader(args.dataset or config.evaluation.dataset_name)
        instance = loader.load_instance(args.instance_id)
        if instance is None:
            console.print(f"[red]Instance '{args.instance_id}' not found[/red]")
            return
        result = _localize_instance(instance)
    elif args.bug_report:
        instance = BugInstance(
            instance_id="manual",
            repo="local",
            problem_statement=args.bug_report,
            base_commit="",
            patch="",
            test_patch="",
        )
        if not args.repo_path:
            console.print("[red]--repo-path is required for manual bug reports[/red]")
            return
        result = _localize_instance(instance)
    else:
        console.print("[red]Provide --bug-report or --instance-id[/red]")
        return

    if args.instance_id and instance.buggy_files:
        console.print(f"\n[bold]Ground Truth:[/bold] {instance.buggy_files}")
        from evaluation.metrics import top_n_accuracy

        for n in [1, 3, 5]:
            hit = top_n_accuracy(result.ranked_files, instance.buggy_files, n)
            status = "✅" if hit else "❌"
            console.print(f"  {status} Top-{n}: {'HIT' if hit else 'MISS'}")
