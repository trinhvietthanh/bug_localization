#!/usr/bin/env python3
"""
Disk-constrained full SWE-bench Lite (300) driver.

Checks out instances in batches, runs the benchmark on each batch, deletes
the batch's checkouts afterwards (pre-existing/pinned checkouts and base
`repo/` clones are never touched), then merges all batch results — plus any
already-computed result files passed via --reuse — into one final JSON with
recomputed aggregate metrics.

Usage:
    python scripts/run_swebench_lite_full.py \
        --reuse results/swebench_50_e123_qwen.json \
        --output results/swebench_300_e123_qwen \
        --batch-size 40 --workers 4
"""

import argparse
import json
import logging
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import config  # noqa: E402
from data.loader import SWEBenchLoader  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from checkout_swebench import checkout_instance  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("lite_full")

CHECKOUT_ROOT = PROJECT_ROOT / "data" / "swebench_checkouts"
MIN_FREE_GB = 4.0


def free_gb(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / 1e9


def load_reused(paths: list[str]) -> dict[str, dict]:
    done: dict[str, dict] = {}
    for p in paths:
        data = json.loads(Path(p).read_text())
        for row in data.get("per_instance", []):
            done.setdefault(row["instance_id"], row)
    return done


def aggregate(per_instance: list[dict]) -> dict:
    n = len(per_instance)
    if n == 0:
        return {}
    return {
        "top_1_accuracy": sum(r.get("top1_hit", False) for r in per_instance) / n,
        "top_3_accuracy": sum(r.get("top3_hit", False) for r in per_instance) / n,
        "top_5_accuracy": sum(r.get("top5_hit", False) for r in per_instance) / n,
        "mrr": sum(r.get("rr", 0.0) for r in per_instance) / n,
        "map": sum(r.get("ap", 0.0) for r in per_instance) / n,
        "total_instances": n,
        "instances_with_match": sum(1 for r in per_instance if r.get("rr", 0) > 0),
        "total_time_seconds": round(sum(r.get("time", 0.0) for r in per_instance), 1),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=config.evaluation.dataset_name)
    parser.add_argument("--split", default="test")
    parser.add_argument("--reuse", nargs="*", default=[],
                        help="Existing result JSONs whose instances are skipped and merged")
    parser.add_argument("--output", default="results/swebench_lite_full")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap total dataset instances (debug)")
    args = parser.parse_args()

    loader = SWEBenchLoader(dataset_name=args.dataset)
    instances = loader.load(split=args.split)
    if args.limit:
        instances = instances[: args.limit]
    logger.info(f"Dataset: {len(instances)} instances")

    reused = load_reused(args.reuse)
    logger.info(f"Reusing {len(reused)} already-computed instances from {args.reuse}")

    # Never delete anything that existed before we started
    pinned: set[Path] = set()
    if CHECKOUT_ROOT.exists():
        for repo_dir in CHECKOUT_ROOT.iterdir():
            for inst_dir in repo_dir.iterdir() if repo_dir.is_dir() else []:
                pinned.add(inst_dir.resolve())

    todo = [i for i in instances if i.instance_id not in reused]
    logger.info(f"To run: {len(todo)} instances in batches of {args.batch_size}")

    all_rows: list[dict] = list(reused.values())
    batch_files: list[str] = []

    for b in range(0, len(todo), args.batch_size):
        batch = todo[b: b + args.batch_size]
        batch_no = b // args.batch_size + 1
        logger.info(f"=== Batch {batch_no}: {len(batch)} instances ===")

        if free_gb(CHECKOUT_ROOT) < MIN_FREE_GB:
            logger.error(f"Disk below {MIN_FREE_GB} GB free — aborting before batch {batch_no}")
            break

        # 1. Checkout (parallel, skips existing)
        with ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(checkout_instance, batch))
        ok_ids = [i.instance_id for i, p in zip(batch, paths) if p]
        failed = [i.instance_id for i, p in zip(batch, paths) if not p]
        if failed:
            logger.warning(f"Checkout failed for {len(failed)}: {failed}")
        if not ok_ids:
            continue

        # 2. Run benchmark on exactly this batch
        ids_file = PROJECT_ROOT / f"{args.output}_batch{batch_no}_ids.txt"
        ids_file.parent.mkdir(parents=True, exist_ok=True)
        ids_file.write_text("\n".join(ok_ids))
        batch_out = f"{args.output}_batch{batch_no}"
        cmd = [
            sys.executable, str(PROJECT_ROOT / "scripts" / "run_swebench_benchmark.py"),
            "--dataset", args.dataset, "--split", args.split,
            "--instance-ids-file", str(ids_file),
            "--workers", str(args.workers),
            "--output", batch_out,
        ]
        logger.info(f"Running: {' '.join(cmd)}")
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT)
        if proc.returncode != 0:
            logger.error(f"Batch {batch_no} benchmark exited {proc.returncode}")

        batch_json = PROJECT_ROOT / f"{batch_out}.json"
        if batch_json.exists():
            rows = json.loads(batch_json.read_text()).get("per_instance", [])
            all_rows.extend(rows)
            batch_files.append(str(batch_json))
            logger.info(f"Batch {batch_no}: merged {len(rows)} rows (total {len(all_rows)})")

        # 3. Delete checkouts created for this batch (never pinned, never base repos)
        for inst, p in zip(batch, paths):
            if not p:
                continue
            path = Path(p).resolve()
            if path in pinned or path.name == "repo":
                continue
            shutil.rmtree(path, ignore_errors=True)
        logger.info(f"Batch {batch_no} checkouts cleaned; free {free_gb(CHECKOUT_ROOT):.1f} GB")

        # 4. Incremental merged snapshot (crash-safe resume point)
        merged = {
            "metadata": {
                "dataset": args.dataset,
                "model": config.llm.model,
                "reused": args.reuse,
                "batches": batch_files,
                "progress": f"{len(all_rows)}/{len(instances)}",
            },
            "metrics": aggregate(all_rows),
            "per_instance": all_rows,
        }
        Path(f"{args.output}.json").write_text(json.dumps(merged, indent=1))

    metrics = aggregate(all_rows)
    logger.info(
        f"FINAL n={metrics.get('total_instances')} | "
        f"Top-1 {metrics.get('top_1_accuracy', 0):.1%} | "
        f"Top-3 {metrics.get('top_3_accuracy', 0):.1%} | "
        f"Top-5 {metrics.get('top_5_accuracy', 0):.1%} | "
        f"MRR {metrics.get('mrr', 0):.4f}"
    )
    logger.info(f"Merged output: {args.output}.json")


if __name__ == "__main__":
    main()
