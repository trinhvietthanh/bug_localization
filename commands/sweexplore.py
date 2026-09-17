"""
sweexplore command — evaluate on SWE-Explore benchmark (ByteDance-Seed/SWE-explore).

SWE-Explore uses the identical schema as SWE-bench, so we reuse the SWE-bench
runner script and simply override the default dataset name.
"""

import importlib.util
import sys
from pathlib import Path

from config import config

_DEFAULT_DATASET = "ByteDance-Seed/SWE-explore"


def cmd_sweexplore(args):
    """Evaluate on SWE-Explore benchmark by calling the SWE-bench runner script."""
    if getattr(args, "no_graph_rag", False):
        config.enable_graph_rag = False

    # Fall back to SWE-Explore dataset if the caller left the default unchanged
    if not getattr(args, "dataset", None) or args.dataset == config.evaluation.dataset_name:
        args.dataset = _DEFAULT_DATASET

    script_path = str(Path(__file__).parent.parent / "scripts" / "run_swebench_benchmark.py")
    spec = importlib.util.spec_from_file_location("run_swebench", script_path)
    runner = importlib.util.module_from_spec(spec)
    sys.modules["run_swebench"] = runner
    spec.loader.exec_module(runner)
    runner.run_benchmark(args)
