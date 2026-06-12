"""
swebench command — evaluate on SWE-bench benchmark.
"""

import importlib.util
import sys
from pathlib import Path

from config import config


def cmd_swebench(args):
    """Evaluate on SWE-bench benchmark by calling the separate runner script."""
    if getattr(args, "no_graph_rag", False):
        config.enable_graph_rag = False

    script_path = str(Path(__file__).parent.parent / "scripts" / "run_swebench_benchmark.py")
    spec = importlib.util.spec_from_file_location("run_swebench", script_path)
    runner = importlib.util.module_from_spec(spec)
    sys.modules["run_swebench"] = runner
    spec.loader.exec_module(runner)
    runner.run_benchmark(args)
