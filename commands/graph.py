"""
graph command — build and query the Code Property Graph (Graph RAG).
"""

from rich.console import Console

console = Console()


def cmd_graph(args):
    """Build and query the Code Property Graph (Graph RAG)."""
    from tools.graph_search import get_graph_retriever, graph_stats, graph_search
    from tools.graph_search import find_callers, find_callees

    use_neo4j = args.backend == "neo4j"

    console.print(f"[bold cyan]Code Property Graph — Graph RAG[/bold cyan]")
    console.print(f"   Repository: {args.repo_path}")
    console.print(f"   Language:   {args.language}")
    console.print(f"   Backend:    {args.backend}\n")

    if use_neo4j:
        # Override config for this command with CLI args
        from config import config
        config.neo4j.enabled = True
        config.neo4j.uri = args.neo4j_uri
        config.neo4j.password = args.neo4j_password

    retriever = get_graph_retriever(
        args.repo_path, language=args.language, use_neo4j=use_neo4j,
    )
    stats = retriever.graph.stats()

    backend_label = stats.get("backend", "in-memory")
    console.print(f"[green]Graph ready ({backend_label})![/green]")
    console.print(f"   Nodes: {stats['total_nodes']}  |  Edges: {stats['total_edges']}")

    if args.stats:
        result = graph_stats(args.repo_path, language=args.language)
        console.print(f"\n{result}")

    if args.query:
        console.print(f"\n[bold]Graph RAG Search: '{args.query}'[/bold]\n")
        result = graph_search(args.query, args.repo_path, top_k=args.top_k, language=args.language)
        console.print(result)

    if args.callers:
        console.print(f"\n[bold]Finding callers of: '{args.callers}'[/bold]\n")
        result = find_callers(args.callers, args.repo_path, language=args.language)
        console.print(result)

    if args.callees:
        console.print(f"\n[bold]Finding callees of: '{args.callees}'[/bold]\n")
        result = find_callees(args.callees, args.repo_path, language=args.language)
        console.print(result)

    if use_neo4j:
        console.print(f"\n[bold]Neo4j Browser:[/bold] http://localhost:7474")
        console.print(f"[dim]   Try: MATCH (n:CodeNode)-[r]->(m) RETURN n, r, m LIMIT 50[/dim]")

    if not any([args.stats, args.query, args.callers, args.callees,
                args.visualize, use_neo4j]):
        console.print(
            "\n[yellow]Use --stats, --query, --callers, --callees, "
            "--visualize, or --backend neo4j to explore the graph.[/yellow]"
        )

    if args.visualize:
        from rag.graph_visualizer import visualize_interactive, visualize_static
        focus = args.focus_file or None
        out_dir = "results"

        html_path = f"{out_dir}/code_graph.html"
        visualize_interactive(
            retriever.graph, output_path=html_path,
            title=f"Code Graph: {args.repo_path}", focus_file=focus, max_nodes=300,
        )
        console.print(f"\n[bold green]Interactive graph:[/bold green] {html_path}")

        png_path = f"{out_dir}/code_graph.png"
        visualize_static(
            retriever.graph, output_path=png_path,
            title=f"Code Property Graph: {args.repo_path}", focus_file=focus, max_nodes=150,
        )
        console.print(f"[bold green]Static image:[/bold green]     {png_path}")
