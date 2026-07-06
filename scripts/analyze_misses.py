#!/usr/bin/env python3
"""
Failure-taxonomy analysis for benchmark result JSON files.

Classifies every instance into:
  - hit@1 / hit@2-3 / hit@4-5      (ranking quality)
  - miss_empty                      (no predictions at all — pipeline failure)
  - miss_gt_deep                    (ground truth present but ranked > 5)
  - miss_gt_absent                  (ground truth nowhere in the list — recall failure)
plus flags __init__.py ground-truth cases and per-repo breakdowns.

Usage:
    python scripts/analyze_misses.py results/swebench_50.json
    python scripts/analyze_misses.py results/a.json results/b.json   # side by side
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _matches(pred: str, gt: str) -> bool:
    """Suffix-tolerant path match (mirrors evaluation.metrics._paths_match)."""
    p, g = pred.strip().lstrip("/"), gt.strip().lstrip("/")
    return p == g or p.endswith("/" + g) or g.endswith("/" + p) or p.endswith(g) or g.endswith(p)


def classify(rec: dict) -> str:
    pred = rec.get("predicted") or []
    gts = rec.get("ground_truth") or []
    if not pred:
        return "miss_empty"
    best = None
    for i, p in enumerate(pred):
        if any(_matches(p, g) for g in gts):
            best = i
            break
    if best is None:
        return "miss_gt_absent"
    if best == 0:
        return "hit@1"
    if best <= 2:
        return "hit@2-3"
    if best <= 4:
        return "hit@4-5"
    return "miss_gt_deep"


def analyze(path: str) -> dict:
    data = json.loads(Path(path).read_text())
    records = data.get("per_instance", data if isinstance(data, list) else [])
    out = {
        "path": path,
        "n": len(records),
        "taxonomy": Counter(),
        "by_repo": defaultdict(Counter),
        "init_gt_misses": [],
        "details": defaultdict(list),
        "pred_lens": [],
    }
    for rec in records:
        cat = classify(rec)
        iid = rec.get("instance_id", "?")
        repo = iid.rsplit("-", 1)[0]
        out["taxonomy"][cat] += 1
        out["by_repo"][repo][cat] += 1
        out["pred_lens"].append(len(rec.get("predicted") or []))
        out["details"][cat].append(iid)
        if cat.startswith("miss") and any(
            g.endswith("__init__.py") for g in rec.get("ground_truth") or []
        ):
            out["init_gt_misses"].append(iid)
    return out


def report(res: dict) -> None:
    n = res["n"] or 1
    tax = res["taxonomy"]
    hits5 = tax["hit@1"] + tax["hit@2-3"] + tax["hit@4-5"]
    print(f"\n=== {res['path']} ({res['n']} instances) ===")
    print(f"Top-1: {tax['hit@1'] / n:.2%}   Top-5: {hits5 / n:.2%}")
    lens = res["pred_lens"]
    if lens:
        print(
            f"pred list length: mean {sum(lens) / len(lens):.1f}, "
            f"min {min(lens)}, max {max(lens)}, empty {sum(1 for x in lens if x == 0)}"
        )
    print("\nTaxonomy:")
    order = ["hit@1", "hit@2-3", "hit@4-5", "miss_gt_deep", "miss_gt_absent", "miss_empty"]
    for cat in order:
        if tax[cat]:
            print(f"  {cat:<15} {tax[cat]:>3}  ({tax[cat] / n:.1%})")
    for cat in ("miss_empty", "miss_gt_deep", "miss_gt_absent"):
        if res["details"][cat]:
            print(f"\n{cat}: {', '.join(res['details'][cat])}")
    if res["init_gt_misses"]:
        print(f"\nmisses with __init__.py ground truth: {', '.join(res['init_gt_misses'])}")
    if len(res["by_repo"]) > 1:
        print("\nPer-repo Top-1 / Top-5:")
        for repo, c in sorted(res["by_repo"].items()):
            rn = sum(c.values())
            r_hits5 = c["hit@1"] + c["hit@2-3"] + c["hit@4-5"]
            print(f"  {repo:<30} {c['hit@1'] / rn:>6.1%} / {r_hits5 / rn:>6.1%}  (n={rn})")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for path in sys.argv[1:]:
        report(analyze(path))


if __name__ == "__main__":
    main()
