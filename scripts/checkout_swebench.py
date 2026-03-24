#!/usr/bin/env python3
"""
Script to checkout SWE-bench instances for local evaluation.
Clones the upstream repository and checks out the buggy commit (`base_commit`)
for each instance so the agents have a local filesystem to explore.

Usage:
    python scripts/checkout_swebench.py --limit 5
    python scripts/checkout_swebench.py --dataset princeton-nlp/SWE-bench_Lite --limit 10
    python scripts/checkout_swebench.py --instance-id scikit-learn__scikit-learn-13439
"""

import argparse
import subprocess
import sys
from pathlib import Path
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import config
from data.loader import SWEBenchLoader, BugInstance

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

CHECKOUT_ROOT = PROJECT_ROOT / "data" / "swebench_checkouts"


def setup_repo(repo_name: str) -> bool:
    """
    Ensure the base repo is cloned. 
    Repo name in SWE-bench is usually like 'scikit-learn/scikit-learn'.
    We will clone it from github.
    """
    repo_path = CHECKOUT_ROOT / repo_name.replace("/", "__") / "repo"
    
    if repo_path.exists():
        logger.debug(f"Repository {repo_name} already cloned at {repo_path}")
        return True

    logger.info(f"Cloning base repository {repo_name}...")
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    
    github_url = f"https://github.com/{repo_name}.git"
    
    try:
        subprocess.run(
            ["git", "clone", github_url, str(repo_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Error cloning {repo_name}: {e.stderr.decode()}")
        return False


def checkout_instance(instance: BugInstance) -> str:
    """
    Checkout a specific buggy version from the base repo.
    """
    repo_name_safe = instance.repo.replace("/", "__")
    base_repo_path = CHECKOUT_ROOT / repo_name_safe / "repo"
    
    # Target directory for this specific instance
    dest_dir = CHECKOUT_ROOT / repo_name_safe / instance.instance_id

    if dest_dir.exists():
        logger.info(f"Skipping {instance.instance_id} (already checked out)")
        return str(dest_dir)

    if not base_repo_path.exists():
        if not setup_repo(instance.repo):
            return ""

    logger.info(f"Checking out {instance.instance_id} at commit {instance.base_commit[:7]}...")
    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Clone locally from the cached base repo to save bandwidth/time
        subprocess.run(
            ["git", "clone", str(base_repo_path), str(dest_dir)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # Checkout the specific base commit (the buggy version)
        subprocess.run(
            ["git", "checkout", instance.base_commit],
            cwd=str(dest_dir),
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        
        return str(dest_dir)
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Error checking out {instance.instance_id}: {e.stderr.decode() if e.stderr else str(e)}")
        # Clean up failed checkout
        import shutil
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        return ""


def main():
    parser = argparse.ArgumentParser(
        description="Checkout SWE-bench instances to local directories.",
    )
    parser.add_argument("--dataset", type=str, default=config.evaluation.dataset_name, 
                        help="SWE-bench dataset to use (e.g., princeton-nlp/SWE-bench_Lite)")
    parser.add_argument("--split", type=str, default="test", 
                        help="Dataset split (default: test)")
    parser.add_argument("--limit", type=int, help="Limit number of instances to checkout")
    parser.add_argument("--instance-id", type=str, help="Single instance ID to checkout")
    parser.add_argument("--workers", type=int, default=4, help="Number of concurrent checkouts")
    
    args = parser.parse_args()

    CHECKOUT_ROOT.mkdir(parents=True, exist_ok=True)
    
    loader = SWEBenchLoader(dataset_name=args.dataset)
    
    if args.instance_id:
        logger.info(f"Loading single instance: {args.instance_id}")
        instance = loader.load_instance(args.instance_id, split=args.split)
        if not instance:
            logger.error(f"Instance {args.instance_id} not found in dataset {args.dataset}.")
            sys.exit(1)
        instances_to_process = [instance]
    else:
        logger.info(f"Loading {args.dataset} dataset...")
        all_instances = loader.load(split=args.split)
        if args.limit:
            instances_to_process = all_instances[:args.limit]
            logger.info(f"Limited to first {args.limit} instances.")
        else:
            instances_to_process = all_instances
            
    if not instances_to_process:
        logger.warning("No instances found to process.")
        return

    logger.info(f"Starting checkout for {len(instances_to_process)} instances using {args.workers} workers...")
    
    total_checked_out = 0
    failed = 0
    
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_instance = {
            executor.submit(checkout_instance, inst): inst for inst in instances_to_process
        }
        
        for future in as_completed(future_to_instance):
            instance = future_to_instance[future]
            try:
                result = future.result()
                if result:
                    total_checked_out += 1
                else:
                    failed += 1
            except Exception as exc:
                logger.error(f"{instance.instance_id} generated an exception: {exc}")
                failed += 1

    logger.info(f"\n✅ Checkout complete.")
    logger.info(f"   Successfully checked out (or already existed): {total_checked_out}")
    if failed > 0:
        logger.warning(f"   Failed to checkout: {failed}")


if __name__ == "__main__":
    main()
