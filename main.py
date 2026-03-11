"""
Bug Localization System — Agentic AI
Main CLI entry point.

Usage:
    python main.py localize --bug-report "description" --repo-path ./repo
    python main.py evaluate --limit 10 --output results/eval.json
    python main.py index --repo-path ./repo
"""

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from config import config

console = Console()


def setup_logging(level: str = "INFO"):
    """Configure logging with Rich handler."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(message)s",
        handlers=[RichHandler(
            console=console,
            show_time=True,
            show_path=False,
        )],
    )


def cmd_localize(args):
    """Run bug localization on a single bug instance or from a dataset."""
    from agents.orchestrator import Orchestrator
    from data.loader import SWEBenchLoader, BugInstance

    # Disable graph RAG if requested
    if getattr(args, "no_graph_rag", False):
        config.enable_graph_rag = False

    # Initialize retriever if index exists
    retriever = None
    if args.repo_path:
        try:
            from rag.retriever import CodeRetriever
            persist_dir = str(Path(config.rag.persist_directory))
            retriever = CodeRetriever(persist_directory=persist_dir)
            if retriever.collection and retriever.collection.count() > 0:
                console.print(f"✅ RAG index loaded ({retriever.collection.count()} chunks)")
            else:
                retriever = None
                console.print("⚠️  No RAG index found. Run 'index' first for semantic search.")
        except Exception:
            console.print("⚠️  RAG not available. Continuing without semantic search.")

    orchestrator = Orchestrator(retriever=retriever)

    if args.instance_id:
        # Load from dataset
        loader = SWEBenchLoader(args.dataset or config.evaluation.dataset_name)
        instance = loader.load_instance(args.instance_id)
        if instance is None:
            console.print(f"[red]Instance '{args.instance_id}' not found[/red]")
            return
        result = orchestrator.localize(
            instance, repo_path=args.repo_path, verbose=True
        )
    elif args.bug_report:
        # Create ad-hoc instance
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
        result = orchestrator.localize(
            instance, repo_path=args.repo_path, verbose=True
        )
    else:
        console.print("[red]Provide --bug-report or --instance-id[/red]")
        return

    # Print ground truth comparison if available
    if args.instance_id and instance.buggy_files:
        console.print(f"\n[bold]Ground Truth:[/bold] {instance.buggy_files}")
        from evaluation.metrics import top_n_accuracy
        for n in [1, 3, 5]:
            hit = top_n_accuracy(result.ranked_files, instance.buggy_files, n)
            status = "✅" if hit else "❌"
            console.print(f"  {status} Top-{n}: {'HIT' if hit else 'MISS'}")


def cmd_evaluate(args):
    """Run evaluation benchmark."""
    from agents.orchestrator import Orchestrator
    from evaluation.evaluator import BenchmarkEvaluator

    if getattr(args, "no_graph_rag", False):
        config.enable_graph_rag = False

    retriever = None
    try:
        from rag.retriever import CodeRetriever
        from config import config as cfg
        persist_dir = str(Path(cfg.rag.persist_directory))
        retriever = CodeRetriever(persist_directory=persist_dir)
        if not (retriever.collection and retriever.collection.count() > 0):
            retriever = None
    except Exception:
        pass

    orchestrator = Orchestrator(retriever=retriever)
    evaluator = BenchmarkEvaluator(orchestrator=orchestrator)

    output_path = args.output or str(
        Path(config.evaluation.output_dir) / "evaluation_results.json"
    )

    evaluator.evaluate(
        limit=args.limit,
        output_path=output_path,
        verbose=args.verbose,
    )


def cmd_index(args):
    """Index a codebase for RAG-based semantic search."""
    from rag.indexer import CodebaseIndexer

    if not args.repo_path:
        console.print("[red]--repo-path is required[/red]")
        return

    repo_path = Path(args.repo_path).resolve()
    if not repo_path.exists():
        console.print(f"[red]Path not found: {repo_path}[/red]")
        return

    console.print(f"[bold]Indexing repository: {repo_path}[/bold]")

    indexer = CodebaseIndexer(
        persist_directory=config.rag.persist_directory,
        collection_name=config.rag.collection_name,
        embedding_model=config.embedding.model_name,
    )

    if args.clear:
        console.print("Clearing existing index...")
        indexer.clear_index()

    num_chunks = indexer.index_repository(str(repo_path))
    console.print(f"\n✅ Indexed {num_chunks} code chunks")

    stats = indexer.get_stats()
    console.print(f"📊 Collection: {stats}")


def cmd_defects4j(args):
    """Evaluate on the Defects4J benchmark."""
    import json
    import time
    from pathlib import Path as P
    from data.defects4j_loader import Defects4JLoader, to_bug_instance, D4J_PROJECTS
    from agents.orchestrator import Orchestrator
    from evaluation.metrics import compute_metrics, top_n_accuracy, reciprocal_rank
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    if getattr(args, "no_graph_rag", False):
        config.enable_graph_rag = False

    projects = [args.project] if args.project else None
    loader = Defects4JLoader(projects=projects)

    # List bugs mode
    if args.list_bugs:
        bugs = loader.load(limit=args.limit)
        table = Table(title=f"🐛 Defects4J Bugs ({len(bugs)} total)")
        table.add_column("ID", style="bold")
        table.add_column("Project", style="cyan")
        table.add_column("Bug Report (preview)", max_width=60)
        table.add_column("Buggy Files", style="yellow")

        for bug in bugs:
            table.add_row(
                bug.instance_id,
                bug.project,
                bug.bug_report[:60] + "..." if len(bug.bug_report) > 60 else bug.bug_report,
                ", ".join(f.split("/")[-1] for f in bug.buggy_files[:3]),
            )
        console.print(table)
        console.print(f"\nProjects: {', '.join(D4J_PROJECTS.keys())}")
        return

    # Single instance mode
    if args.instance_id:
        bug = loader.load_instance(args.instance_id)
        if bug is None:
            console.print(f"[red]Instance '{args.instance_id}' not found[/red]")
            return

        console.print(f"[bold]Bug Report ({bug.instance_id}):[/bold]")
        console.print(bug.bug_report)
        console.print(f"\n[bold]Ground Truth Files:[/bold] {bug.buggy_files}")
        console.print(f"[bold]Ground Truth Methods:[/bold]")
        for m in bug.buggy_methods:
            console.print(f"  • {m}")

        if args.repo_path:
            console.print(f"\n[bold cyan]Running agent pipeline...[/bold cyan]")
            
            retriever = None
            try:
                from rag.retriever import CodeRetriever
                from config import config as cfg
                persist_dir = str(P(cfg.rag.persist_directory))
                retriever = CodeRetriever(persist_directory=persist_dir)
                if not (retriever.collection and retriever.collection.count() > 0):
                    retriever = None
            except Exception:
                pass

            orchestrator = Orchestrator(retriever=retriever)
            bug_instance = to_bug_instance(bug)
            result = orchestrator.localize(
                bug_instance, repo_path=args.repo_path, verbose=True
            )

            console.print(f"\n[bold]Predicted files:[/bold] {result.ranked_files}")
            for n in [1, 3, 5]:
                hit = top_n_accuracy(result.ranked_files, bug.buggy_files, n)
                console.print(f"  Top-{n}: {'✅ HIT' if hit else '❌ MISS'}")
        return

    # Batch evaluation mode
    bugs = loader.load(limit=args.limit)

    if not args.repo_path:
        console.print(
            "[yellow]⚠ No --repo-path provided. Showing dataset info only.[/yellow]"
        )
        console.print(f"Total bugs: {len(bugs)}")
        for p in D4J_PROJECTS:
            cnt = sum(1 for b in bugs if b.project == p)
            if cnt > 0:
                console.print(f"  {p}: {cnt}")
        console.print(
            "\nTo run evaluation, provide --repo-path pointing to "
            "the checked-out Defects4J buggy version."
        )
        return

    # Create a worker function for multi-processing
    def process_d4j_bug(bug_obj, orchestrator_inst, repo_base_path, is_verbose):
        start_t = time.time()
        bug_instance = to_bug_instance(bug_obj)

        repo = P(repo_base_path) / bug_obj.project / f"{bug_obj.instance_id}"
        if not repo.exists():
            repo = P(repo_base_path)

        try:
            res = orchestrator_inst.localize(
                bug_instance,
                repo_path=str(repo),
                verbose=is_verbose,
            )
            r_files = res.ranked_files
            hit = top_n_accuracy(r_files, bug_obj.buggy_files, 1)
            rr = reciprocal_rank(r_files, bug_obj.buggy_files)
            
            inst_dict = {
                "instance_id": bug_obj.instance_id,
                "predicted": r_files[:5],
                "ground_truth": bug_obj.buggy_files,
                "rr": rr,
                "time": res.total_time,
            }
            return (bug_obj.instance_id, True, inst_dict, r_files, bug_obj.buggy_files, hit, rr, None)
            
        except Exception as e:
            err_dict = {
                "instance_id": bug_obj.instance_id,
                "predicted": [],
                "ground_truth": bug_obj.buggy_files,
                "rr": 0.0,
                "time": time.time() - start_t,
            }
            return (bug_obj.instance_id, False, err_dict, [], bug_obj.buggy_files, False, 0.0, str(e))

    # Run pipeline
    retriever = None
    try:
        from rag.retriever import CodeRetriever
        from config import config as cfg
        persist_dir = str(P(cfg.rag.persist_directory))
        retriever = CodeRetriever(persist_directory=persist_dir)
        if not (retriever.collection and retriever.collection.count() > 0):
            retriever = None
    except Exception:
        pass

    orchestrator = Orchestrator(retriever=retriever)
    all_preds = []
    all_gts = []
    per_instance = []

    workers = getattr(args, "workers", 1)
    console.print(f"\n[bold cyan]🚀 Starting Defects4J benchmark evaluation...[/bold cyan]")
    console.print(f"   Workers: {workers}")

    print_lock = threading.Lock()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Evaluating bugs ({workers} workers)", total=len(bugs))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_bug = {
                executor.submit(process_d4j_bug, bug, orchestrator, args.repo_path, args.verbose): bug
                for bug in bugs
            }
            
            completed_count = 0
            for future in as_completed(future_to_bug):
                bug = future_to_bug[future]
                
                try:
                    iid, success, inst_data, p_files, g_files, hit, rr, err = future.result()
                    all_preds.append(p_files)
                    all_gts.append(g_files)
                    per_instance.append(inst_data)

                    with print_lock:
                        if success:
                            status_icon = "✅" if hit else "❌"
                            console.print(
                                f"  {status_icon} {iid:20s} | RR={rr:.3f} | "
                                f"Predicted: {p_files[:3]}"
                            )
                        else:
                            console.print(f"  [red]❌ ERROR {iid:20s}: {err}[/red]")
                            
                except Exception as exc:
                    with print_lock:
                        console.print(f"  [red]❌ UNEXPECTED ERROR {bug.instance_id}: {exc}[/red]")
                        
                completed_count += 1
                progress.update(task, advance=1, description=f"[{completed_count}/{len(bugs)}] {bug.instance_id}")

    # Compute and display metrics
    metrics = compute_metrics(all_preds, all_gts, [1, 3, 5])
    table = Table(title="📊 Defects4J Evaluation Results")
    table.add_column("Metric", style="bold")
    table.add_column("Value", style="cyan")
    for k, v in metrics.items():
        table.add_row(k, f"{v:.4f}" if isinstance(v, float) else str(v))
    console.print("\n")
    console.print(table)

    # Save results
    output_base = args.output or "results/defects4j"
    out_base = P(output_base)
    # Strip extension if user provided one
    if out_base.suffix in (".csv", ".json"):
        out_base = out_base.with_suffix("")
    out_base.parent.mkdir(parents=True, exist_ok=True)

    from evaluation.export import export_results

    metadata = {
        "dataset": "defects4j",
        "model": config.llm.model,
        "provider": config.llm.provider,
    }

    # Export CSV files
    csv_paths = export_results(per_instance, metrics, str(out_base), metadata=metadata)
    console.print(f"\n💾 [bold green]Results exported:[/bold green]")
    console.print(f"   📄 Per-instance CSV: {csv_paths['results_csv']}")
    console.print(f"   📄 Summary CSV:      {csv_paths['summary_csv']}")

    # Also export JSON
    json_path = str(out_base) + ".json"
    with open(json_path, "w") as f:
        json.dump({
            "dataset": "defects4j",
            "model": config.llm.model,
            "provider": config.llm.provider,
            "metrics": metrics,
            "per_instance": per_instance,
        }, f, indent=2)
    console.print(f"   📄 Full JSON:        {json_path}")


def cmd_bugsinpy(args):
    """Evaluate on the BugsInPy (Python) benchmark."""
    import json
    import time
    from pathlib import Path as P
    from data.bugsinpy_loader import BugsInPyLoader, to_bug_instance, BUGSINPY_PROJECTS
    from agents.orchestrator import Orchestrator
    from evaluation.metrics import compute_metrics, top_n_accuracy, reciprocal_rank
    from rich.table import Table

    if getattr(args, "no_graph_rag", False):
        config.enable_graph_rag = False

    projects = [args.project] if args.project else None
    loader = BugsInPyLoader(projects=projects)

    # List bugs mode
    if args.list_bugs:
        bugs = loader.load(limit=args.limit)
        table = Table(title=f"🐍 BugsInPy Bugs ({len(bugs)} total)")
        table.add_column("ID", style="bold")
        table.add_column("Project", style="cyan")
        table.add_column("Bug Report (preview)", max_width=60)
        table.add_column("Buggy Files", style="yellow")

        for bug in bugs:
            table.add_row(
                bug.instance_id,
                bug.project,
                bug.bug_report[:60] + "..." if len(bug.bug_report) > 60 else bug.bug_report,
                ", ".join(f.split("/")[-1] for f in bug.buggy_files[:3]),
            )
        console.print(table)
        console.print(f"\nProjects: {', '.join(BUGSINPY_PROJECTS.keys())}")
        return

    # Single instance mode
    if args.instance_id:
        bug = loader.load_instance(args.instance_id)
        if bug is None:
            console.print(f"[red]Instance '{args.instance_id}' not found[/red]")
            return

        console.print(f"[bold]Bug Report ({bug.instance_id}):[/bold]")
        console.print(bug.bug_report)
        console.print(f"\n[bold]Ground Truth Files:[/bold] {bug.buggy_files}")
        console.print(f"[bold]Buggy Commit:[/bold] {bug.buggy_commit}")
        console.print(f"[bold]Fixed Commit:[/bold] {bug.fixed_commit}")
        console.print(f"[bold]Test File:[/bold] {bug.test_file}")

        if args.repo_path:
            console.print(f"\n[bold cyan]Running agent pipeline...[/bold cyan]")
            orchestrator = Orchestrator()
            bug_instance = to_bug_instance(bug)
            result = orchestrator.localize(
                bug_instance, repo_path=args.repo_path, verbose=True
            )

            console.print(f"\n[bold]Predicted files:[/bold] {result.ranked_files}")
            for n in [1, 3, 5]:
                hit = top_n_accuracy(result.ranked_files, bug.buggy_files, n)
                console.print(f"  Top-{n}: {'✅ HIT' if hit else '❌ MISS'}")
        return

    # Batch evaluation mode
    bugs = loader.load(limit=args.limit)

    if not args.repo_path:
        console.print(
            "[yellow]⚠ No --repo-path provided. Showing dataset info only.[/yellow]"
        )
        console.print(f"Total bugs: {len(bugs)}")
        for p in BUGSINPY_PROJECTS:
            cnt = sum(1 for b in bugs if b.project == p)
            if cnt > 0:
                console.print(f"  {p}: {cnt}")
        console.print(
            "\nTo run evaluation, provide --repo-path pointing to "
            "the checked-out BugsInPy buggy version."
        )
        return

    # Run pipeline
    orchestrator = Orchestrator()
    all_preds, all_gts = [], []
    per_instance = []

    for i, bug in enumerate(bugs):
        console.print(
            f"\n[bold][{i+1}/{len(bugs)}] {bug.instance_id}[/bold]"
        )
        bug_instance = to_bug_instance(bug)

        # Construct per-bug repo path
        repo = P(args.repo_path) / bug.project / f"{bug.instance_id}"
        if not repo.exists():
            repo = P(args.repo_path)

        try:
            result = orchestrator.localize(
                bug_instance,
                repo_path=str(repo),
                verbose=args.verbose,
            )
            all_preds.append(result.ranked_files)
            all_gts.append(bug.buggy_files)
            hit = top_n_accuracy(result.ranked_files, bug.buggy_files, 1)
            rr = reciprocal_rank(result.ranked_files, bug.buggy_files)
            console.print(
                f"  {'✅' if hit else '❌'} RR={rr:.3f} | "
                f"Predicted: {result.ranked_files[:3]}"
            )
            per_instance.append({
                "instance_id": bug.instance_id,
                "predicted": result.ranked_files[:5],
                "ground_truth": bug.buggy_files,
                "rr": rr,
                "time": result.total_time,
            })
        except Exception as e:
            console.print(f"  [red]ERROR: {e}[/red]")
            all_preds.append([])
            all_gts.append(bug.buggy_files)

    # Compute and display metrics
    metrics = compute_metrics(all_preds, all_gts, [1, 3, 5])
    table = Table(title="📊 BugsInPy Evaluation Results")
    table.add_column("Metric", style="bold")
    table.add_column("Value", style="cyan")
    for k, v in metrics.items():
        table.add_row(k, f"{v:.4f}" if isinstance(v, float) else str(v))
    console.print("\n")
    console.print(table)

    # Save results
    output_base = args.output or "results/bugsinpy"
    out_base = P(output_base)
    if out_base.suffix in (".csv", ".json"):
        out_base = out_base.with_suffix("")
    out_base.parent.mkdir(parents=True, exist_ok=True)

    from evaluation.export import export_results
    metadata = {
        "dataset": "bugsinpy",
        "model": config.llm.model,
        "provider": config.llm.provider,
    }
    csv_paths = export_results(per_instance, metrics, str(out_base), metadata=metadata)
    console.print(f"\n💾 [bold green]Results exported:[/bold green]")
    console.print(f"   📄 Per-instance CSV: {csv_paths['results_csv']}")
    console.print(f"   📄 Summary CSV:      {csv_paths['summary_csv']}")

    json_path = str(out_base) + ".json"
    with open(json_path, "w") as f:
        json.dump({
            "dataset": "bugsinpy",
            "model": config.llm.model,
            "provider": config.llm.provider,
            "metrics": metrics,
            "per_instance": per_instance,
        }, f, indent=2)
    console.print(f"   📄 Full JSON:        {json_path}")


def cmd_graph(args):
    """Build and query the Code Property Graph (Graph RAG)."""
    from tools.graph_search import get_graph_retriever, graph_stats, graph_search
    from tools.graph_search import find_callers, find_callees

    console.print(f"[bold cyan]📊 Code Property Graph — Graph RAG[/bold cyan]")
    console.print(f"   Repository: {args.repo_path}")
    console.print(f"   Language:   {args.language}\n")

    # Build graph (cached)
    retriever = get_graph_retriever(args.repo_path, language=args.language)
    stats = retriever.graph.stats()

    console.print(f"[green]✅ Graph built successfully![/green]")
    console.print(f"   Nodes: {stats['total_nodes']}  |  Edges: {stats['total_edges']}")

    if args.stats:
        result = graph_stats(args.repo_path, language=args.language)
        console.print(f"\n{result}")

    if args.query:
        console.print(f"\n[bold]🔍 Graph RAG Search: '{args.query}'[/bold]\n")
        result = graph_search(
            args.query, args.repo_path,
            top_k=args.top_k, language=args.language,
        )
        console.print(result)

    if args.callers:
        console.print(f"\n[bold]← Finding callers of: '{args.callers}'[/bold]\n")
        result = find_callers(args.callers, args.repo_path, language=args.language)
        console.print(result)

    if args.callees:
        console.print(f"\n[bold]→ Finding callees of: '{args.callees}'[/bold]\n")
        result = find_callees(args.callees, args.repo_path, language=args.language)
        console.print(result)

    if not any([args.stats, args.query, args.callers, args.callees, args.visualize, args.backend == "neo4j"]):
        console.print("\n[yellow]💡 Use --stats, --query, --callers, --callees, --visualize, or --backend neo4j to explore the graph.[/yellow]")

    # Neo4j export
    if args.backend == "neo4j":
        from rag.neo4j_backend import Neo4jGraph

        console.print(f"\n[bold cyan]🔗 Exporting to Neo4j...[/bold cyan]")
        console.print(f"   URI: {args.neo4j_uri}")

        try:
            neo4j_graph = Neo4jGraph(
                uri=args.neo4j_uri,
                password=args.neo4j_password,
            )
            neo4j_graph.import_from_in_memory(retriever.graph)
            neo4j_stats = neo4j_graph.stats()

            console.print(f"[green]✅ Neo4j import complete![/green]")
            console.print(f"   Nodes: {neo4j_stats['total_nodes']}  |  Edges: {neo4j_stats['total_edges']}")
            console.print(f"\n[bold]🌐 Open Neo4j Browser:[/bold] http://localhost:7474")
            console.print(f"[dim]   Try: MATCH (n:CodeNode)-[r]->(m) RETURN n, r, m LIMIT 50[/dim]")
            console.print(f"[dim]   Try: MATCH (n {{name:'createNumber'}})-[:CALLS]->(m) RETURN n, m[/dim]")

            neo4j_graph.close()
        except ConnectionError as e:
            console.print(f"[red]❌ Neo4j connection failed:[/red] {e}")
            console.print(f"\n[yellow]💡 Start Neo4j with Docker:[/yellow]")
            console.print(f"   docker run -d --name neo4j -p 7474:7474 -p 7687:7687 \\")
            console.print(f"     -e NEO4J_AUTH=neo4j/password neo4j:latest")

    if args.visualize:
        from rag.graph_visualizer import visualize_interactive, visualize_static

        focus = args.focus_file or None
        out_dir = "results"

        # Interactive HTML
        html_path = f"{out_dir}/code_graph.html"
        visualize_interactive(
            retriever.graph,
            output_path=html_path,
            title=f"Code Graph: {args.repo_path}",
            focus_file=focus,
            max_nodes=300,
        )
        console.print(f"\n[bold green]🌐 Interactive graph:[/bold green] {html_path}")

        # Static PNG
        png_path = f"{out_dir}/code_graph.png"
        visualize_static(
            retriever.graph,
            output_path=png_path,
            title=f"Code Property Graph: {args.repo_path}",
            focus_file=focus,
            max_nodes=150,
        )
        console.print(f"[bold green]📸 Static image:[/bold green]     {png_path}")


def main():
    parser = argparse.ArgumentParser(
        description="🐛 Bug Localization System — Agentic AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--log-level", default=config.log_level,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # localize command
    loc_parser = subparsers.add_parser("localize", help="Localize a bug")
    loc_parser.add_argument("--bug-report", type=str, help="Bug description text")
    loc_parser.add_argument("--repo-path", type=str, help="Path to repository")
    loc_parser.add_argument("--instance-id", type=str, help="SWE-bench instance ID")
    loc_parser.add_argument("--dataset", type=str, help="Dataset name override")
    loc_parser.add_argument("--no-graph-rag", action="store_true", help="Disable Graph RAG")

    # evaluate command
    eval_parser = subparsers.add_parser("evaluate", help="Run benchmark evaluation")
    eval_parser.add_argument("--limit", type=int, help="Max instances to evaluate")
    eval_parser.add_argument("--output", type=str, help="Output JSON path")
    eval_parser.add_argument("--verbose", action="store_true", help="Verbose output")
    eval_parser.add_argument("--no-graph-rag", action="store_true", help="Disable Graph RAG")

    # index command
    idx_parser = subparsers.add_parser("index", help="Index codebase for RAG")
    idx_parser.add_argument("--repo-path", type=str, required=True, help="Repo path")
    idx_parser.add_argument("--clear", action="store_true", help="Clear existing index")

    # graph command
    graph_parser = subparsers.add_parser("graph", help="Build & query Code Property Graph (Graph RAG)")
    graph_parser.add_argument("--repo-path", type=str, required=True, help="Path to repository")
    graph_parser.add_argument("--language", type=str, default="auto", choices=["auto", "python", "java"], help="Language hint")
    graph_parser.add_argument("--query", type=str, help="Search query for Graph RAG")
    graph_parser.add_argument("--callers", type=str, help="Find callers of a function")
    graph_parser.add_argument("--callees", type=str, help="Find callees of a function")
    graph_parser.add_argument("--stats", action="store_true", help="Show graph statistics")
    graph_parser.add_argument("--visualize", action="store_true", help="Generate interactive HTML + static PNG graph visualization")
    graph_parser.add_argument("--focus-file", type=str, help="Focus visualization on a specific file path (substring match)")
    graph_parser.add_argument("--top-k", type=int, default=10, help="Number of results")
    graph_parser.add_argument("--backend", type=str, default="memory", choices=["memory", "neo4j"], help="Graph storage backend (default: memory)")
    graph_parser.add_argument("--neo4j-uri", type=str, default="bolt://localhost:7687", help="Neo4j Bolt URI")
    graph_parser.add_argument("--neo4j-password", type=str, default="password", help="Neo4j password")

    # defects4j command
    d4j_parser = subparsers.add_parser("defects4j", help="Evaluate on Defects4J benchmark")
    d4j_parser.add_argument("--project", type=str, help="Project name (Lang, Math, Closure, Mockito, Time)")
    d4j_parser.add_argument("--instance-id", type=str, help="Single instance ID (e.g. Lang_1)")
    d4j_parser.add_argument("--repo-path", type=str, help="Path to checked-out buggy repo")
    d4j_parser.add_argument("--limit", type=int, help="Max instances to evaluate")
    d4j_parser.add_argument("--workers", type=int, default=1, help="Number of concurrent bug evaluations (default 1)")
    d4j_parser.add_argument("--output", type=str, help="Output JSON path")
    d4j_parser.add_argument("--verbose", action="store_true", help="Verbose output")
    d4j_parser.add_argument("--list-bugs", action="store_true", help="Just list available bugs")
    d4j_parser.add_argument("--no-graph-rag", action="store_true", help="Disable Graph RAG")

    # bugsinpy command
    bip_parser = subparsers.add_parser("bugsinpy", help="Evaluate on BugsInPy benchmark (Python)")
    bip_parser.add_argument("--project", type=str, help="Project name (thefuck, pandas, fastapi, etc.)")
    bip_parser.add_argument("--instance-id", type=str, help="Single instance ID (e.g. thefuck_1)")
    bip_parser.add_argument("--repo-path", type=str, help="Path to checked-out buggy repo")
    bip_parser.add_argument("--limit", type=int, help="Max instances to evaluate")
    bip_parser.add_argument("--output", type=str, help="Output JSON path")
    bip_parser.add_argument("--verbose", action="store_true", help="Verbose output")
    bip_parser.add_argument("--list-bugs", action="store_true", help="Just list available bugs")
    bip_parser.add_argument("--no-graph-rag", action="store_true", help="Disable Graph RAG")

    args = parser.parse_args()
    setup_logging(args.log_level)

    if args.command is None:
        parser.print_help()
        return

    commands = {
        "localize": cmd_localize,
        "evaluate": cmd_evaluate,
        "index": cmd_index,
        "graph": cmd_graph,
        "defects4j": cmd_defects4j,
        "bugsinpy": cmd_bugsinpy,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
