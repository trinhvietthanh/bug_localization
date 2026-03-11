#!/usr/bin/env python3
"""
Script to checkout BugsInPy bugs manually from GitHub repos.
Clones the upstream repo once (cached), then creates per-bug checkouts
at the buggy commit.

Usage:
    python scripts/checkout_bugsinpy.py --project thefuck --limit 5
    python scripts/checkout_bugsinpy.py --all
    python scripts/checkout_bugsinpy.py --project pandas --limit 10
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Config
BUGSINPY_ROOT = Path("/home/thanhtv45/Desktop/bug-localization/BugsInPy")
CHECKOUT_ROOT = Path("/home/thanhtv45/Desktop/bug-localization/thesis/data/bugsinpy_checkouts")

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def get_projects() -> list[str]:
    """Discover all projects in BugsInPy."""
    projects_dir = BUGSINPY_ROOT / "projects"
    if not projects_dir.exists():
        print(f"Error: BugsInPy not found at {BUGSINPY_ROOT}")
        sys.exit(1)
    return sorted([
        d.name for d in projects_dir.iterdir()
        if d.is_dir() and (d / "project.info").exists()
    ])


def parse_info_file(path: Path) -> dict:
    """Parse key=value info file (with optional quotes)."""
    info = {}
    if not path.exists():
        return info
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                key, _, value = line.partition("=")
                info[key.strip()] = value.strip().strip('"').strip("'")
    return info


def setup_repo(project: str) -> bool:
    """Ensure base repo is cloned for the given project."""
    project_info = parse_info_file(
        BUGSINPY_ROOT / "projects" / project / "project.info"
    )
    github_url = project_info.get("github_url", "")
    if not github_url:
        print(f"  Error: No github_url in project.info for {project}")
        return False

    repo_path = CHECKOUT_ROOT / f"{project}-repo"
    if not repo_path.exists():
        print(f"  Cloning {project} repository from {github_url}...")
        CHECKOUT_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["git", "clone", github_url, str(repo_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            print(f"  Error cloning {project}: {e.stderr.decode()}")
            return False
    else:
        print(f"  {project} repository already cloned.")

    return True


def get_bug_ids(project: str) -> list[int]:
    """Get sorted bug IDs for a project."""
    bugs_dir = BUGSINPY_ROOT / "projects" / project / "bugs"
    if not bugs_dir.exists():
        return []

    ids = []
    for entry in bugs_dir.iterdir():
        if entry.is_dir():
            try:
                ids.append(int(entry.name))
            except ValueError:
                continue
    return sorted(ids)


def checkout_bug(project: str, bug_id: int) -> str:
    """Checkout a specific buggy version."""
    dest_dir = CHECKOUT_ROOT / project / f"{project}_{bug_id}"

    if dest_dir.exists():
        print(f"  Skipping {project}_{bug_id} (already exists)")
        return str(dest_dir)

    # Read bug.info
    bug_info = parse_info_file(
        BUGSINPY_ROOT / "projects" / project / "bugs" / str(bug_id) / "bug.info"
    )
    buggy_commit = bug_info.get("buggy_commit_id", "")
    if not buggy_commit:
        print(f"  Error: No buggy_commit_id for {project}_{bug_id}")
        return ""

    base_repo = CHECKOUT_ROOT / f"{project}-repo"
    if not base_repo.exists():
        print(f"  Error: Base repo not found for {project}")
        return ""

    print(f"  Checking out {project}_{bug_id} at {buggy_commit[:7]}...")
    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Clone locally from cached repo
        subprocess.run(
            ["git", "clone", str(base_repo), str(dest_dir)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # Checkout buggy commit
        subprocess.run(
            ["git", "checkout", buggy_commit],
            cwd=str(dest_dir),
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as e:
        print(f"  Error checking out {project}_{bug_id}: {e}")
        # Clean up failed checkout
        import shutil
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        return ""

    return str(dest_dir)


def main():
    parser = argparse.ArgumentParser(
        description="Checkout BugsInPy bugs manually.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/checkout_bugsinpy.py --project thefuck --limit 5
  python scripts/checkout_bugsinpy.py --project black --all
  python scripts/checkout_bugsinpy.py --all        # All projects, first 15 each
""",
    )
    parser.add_argument(
        "--project", type=str,
        help="Project to checkout (e.g. thefuck, pandas, fastapi)"
    )
    parser.add_argument("--limit", type=int, help="Limit bugs per project")
    parser.add_argument(
        "--all", action="store_true",
        help="Checkout all bugs (no per-project limit)"
    )

    args = parser.parse_args()

    all_projects = get_projects()
    if args.project:
        if args.project not in all_projects:
            print(f"Error: Unknown project '{args.project}'")
            print(f"Available: {', '.join(all_projects)}")
            sys.exit(1)
        projects_to_process = [args.project]
    else:
        projects_to_process = all_projects

    total_checked_out = 0
    for project in projects_to_process:
        print(f"\n--- Processing {project} ---")

        if not setup_repo(project):
            continue

        bug_ids = get_bug_ids(project)
        if not bug_ids:
            print(f"  No bugs found for {project}")
            continue

        limit = args.limit if args.limit else (len(bug_ids) if args.all else 15)
        print(f"  Checking out {min(limit, len(bug_ids))} of {len(bug_ids)} bugs...")

        for bug_id in bug_ids[:limit]:
            result = checkout_bug(project, bug_id)
            if result:
                total_checked_out += 1

    print(f"\n✅ Checkout complete. Total: {total_checked_out} bug checkouts.")


if __name__ == "__main__":
    main()
