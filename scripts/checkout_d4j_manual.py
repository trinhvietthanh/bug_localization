"""
Script to checkout Defects4J bugs manually from GitHub repo.
Bypass heavy Defects4J framework setup.
"""

import csv
import subprocess
import argparse
import sys
from pathlib import Path
import shutil

# Config
D4J_ROOT = Path("/home/thanhtv45/Desktop/bug-localization/defects4j")
CHECKOUT_ROOT = Path("/home/thanhtv45/Desktop/bug-localization/thesis/data/defects4j_checkouts")

# Project configurations
PROJECT_CONFIGS = {
    "Chart": {
        "repo_url": "https://github.com/jfree/jfreechart.git",
        "repo_name": "jfreechart-repo",
    },
    "Closure": {
        "repo_url": "https://github.com/google/closure-compiler.git",
        "repo_name": "closure-compiler-repo",
    },
    "Lang": {
        "repo_url": "https://github.com/apache/commons-lang.git",
        "repo_name": "commons-lang-repo",
    },
    "Math": {
        "repo_url": "https://github.com/apache/commons-math.git",
        "repo_name": "commons-math-repo",
    },
    "Mockito": {
        "repo_url": "https://github.com/mockito/mockito.git",
        "repo_name": "mockito-repo",
    },
    "Time": {
        "repo_url": "https://github.com/JodaOrg/joda-time.git",
        "repo_name": "joda-time-repo",
    }
}

def setup_repo(project):
    """Ensure base repo is cloned for the given project."""
    config = PROJECT_CONFIGS.get(project)
    if not config:
        print(f"Error: No configuration for project {project}")
        return False

    repo_path = CHECKOUT_ROOT / config["repo_name"]
    if not repo_path.exists():
        print(f"Cloning {project} repository to {repo_path}...")
        CHECKOUT_ROOT.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", config["repo_url"], str(repo_path)],
            check=True
        )
    else:
        print(f"{project} repository already cloned at {repo_path}.")
    return True

def checkout_bug(project, bug_id, commit_hash):
    """Checkout a specific bug version."""
    config = PROJECT_CONFIGS[project]
    base_repo_path = CHECKOUT_ROOT / config["repo_name"]
    dest_dir = CHECKOUT_ROOT / project / f"{project}_{bug_id}"
    
    if dest_dir.exists():
        print(f"Skipping {project}_{bug_id} (already exists)")
        return str(dest_dir)

    print(f"Checking out {project}_{bug_id} at {commit_hash[:7]}...")
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        # Clone locally from the cached repo to save bandwidth/time
        subprocess.run(
            ["git", "clone", str(base_repo_path), str(dest_dir)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        
        # Check if the commit is an SVN revision number instead of a git hash (e.g. "2240")
        target_commit = commit_hash
        if commit_hash.isdigit() and len(commit_hash) < 10:
            # Try to find the git commit corresponding to this SVN revision
            # Look for "SVN: {commit_hash}" or similar in git log
            print(f"   Converting SVN revision {commit_hash} to Git Hash...")
            try:
                # Sometimes defects4j maps old SVN revisions using git log
                # We'll try a few common patterns or just use git rev-list if it was a tag
                res = subprocess.run(
                    ["git", "log", "--all", f"--grep=trunk@{commit_hash} ", "--format=%H"],
                    cwd=str(dest_dir), capture_output=True, text=True
                )
                if not res.stdout.strip():
                     res = subprocess.run(
                        ["git", "log", "--all", f"--grep=r{commit_hash} ", "--format=%H"],
                        cwd=str(dest_dir), capture_output=True, text=True
                    )
                if res.stdout.strip():
                    target_commit = res.stdout.strip().split("\n")[0]
                    print(f"   Mapped SVN {commit_hash} -> Git {target_commit[:7]}")
                else:
                    # Fallback for projects like Chart where the framework uses a commit-db map internally.
                    # As a hack, we can try to find release tags or fallback to skipping
                    print(f"   Warning: Could not map SVN revision {commit_hash} to git hash automatically.")
            except Exception as e:
                pass
                
        # Checkout specific commit
        subprocess.run(
            ["git", "checkout", target_commit],
            cwd=str(dest_dir),
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return str(dest_dir)
        
    except subprocess.CalledProcessError as e:
        print(f"   ❌ Git checkout failed for {project}_{bug_id} at {target_commit}. Removing corrupted folder...")
        if dest_dir.exists():
            import shutil
            shutil.rmtree(dest_dir, ignore_errors=True)
        return None

def main():
    parser = argparse.ArgumentParser(description="Checkout Defects4J bugs manually.")
    parser.add_argument("--project", type=str, choices=list(PROJECT_CONFIGS.keys()), help="Project to checkout")
    parser.add_argument("--limit", type=int, help="Limit number of bugs to checkout per project")
    parser.add_argument("--all", action="store_true", help="Checkout all bugs for specified projects")
    
    args = parser.parse_args()
    
    projects_to_process = [args.project] if args.project else list(PROJECT_CONFIGS.keys())
    
    for project in projects_to_process:
        print(f"\n--- Processing {project} ---")
        if not setup_repo(project):
            continue
            
        csv_path = D4J_ROOT / "framework/projects" / project / "active-bugs.csv"
        
        if not csv_path.exists():
            print(f"Error: {csv_path} not found!")
            continue

        with open(csv_path, "r") as f:
            import csv
            reader = csv.DictReader(f)
            bugs = list(reader)

        limit = args.limit if args.limit else (len(bugs) if args.all else 200)
        print(f"Checking out {limit} bugs for {project}...")
        
        for row in bugs[:limit]:
            bug_id = row["bug.id"]
            commit = row["revision.id.buggy"]
            checkout_bug(project, bug_id, commit)
            # Dù có lỗi thì hàm checkout_bug đã dọn dẹp thư mục và chỉ print error, vòng lặp vẫn tiếp tục (skip gracefully)

    print("\n✅ Checkout process complete.")

if __name__ == "__main__":
    main()
