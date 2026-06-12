"""
bugsinpy command — evaluate on the BugsInPy (Python) benchmark.
"""

from rich.console import Console
from rich.table import Table

from commands._shared import make_orchestrator, run_batch, export_and_print

console = Console()


def cmd_bugsinpy(args):
    """Evaluate on the BugsInPy (Python) benchmark."""
    from data.bugsinpy_loader import BugsInPyLoader, to_bug_instance, BUGSINPY_PROJECTS
    from evaluation.metrics import top_n_accuracy

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
                bug.instance_id, bug.project,
                (bug.bug_report[:60] + "...") if len(bug.bug_report) > 60 else bug.bug_report,
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

        if args.repo_path:
            console.print(f"\n[bold cyan]Running agent pipeline...[/bold cyan]")
            orchestrator = make_orchestrator(args)
            bug_instance = to_bug_instance(bug)
            result = orchestrator.localize(bug_instance, repo_path=args.repo_path, verbose=True)
            console.print(f"\n[bold]Predicted files:[/bold] {result.ranked_files}")
            for n in [1, 3, 5]:
                hit = top_n_accuracy(result.ranked_files, bug.buggy_files, n)
                console.print(f"  Top-{n}: {'✅ HIT' if hit else '❌ MISS'}")
        return

    # Batch evaluation mode
    bugs = loader.load(limit=args.limit)
    if not args.repo_path:
        console.print("[yellow]⚠ No --repo-path provided. Showing dataset info only.[/yellow]")
        console.print(f"Total bugs: {len(bugs)}")
        for p in BUGSINPY_PROJECTS:
            cnt = sum(1 for b in bugs if b.project == p)
            if cnt > 0:
                console.print(f"  {p}: {cnt}")
        return

    # Attach _to_bug_instance helper for the shared runner
    for bug in bugs:
        bug._to_bug_instance = lambda b=bug: to_bug_instance(b)

    orchestrator = make_orchestrator(args)
    all_preds, all_gts, all_pred_methods, all_gt_methods, per_instance = run_batch(
        bugs, orchestrator, args, has_methods=False
    )
    export_and_print(
        per_instance, all_preds, all_gts, all_pred_methods, all_gt_methods, args,
        dataset_name="bugsinpy",
        has_methods=False,
        default_output="results/bugsinpy",
    )
