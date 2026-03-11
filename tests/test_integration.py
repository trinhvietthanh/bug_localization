"""
Integration test — tests the full pipeline with a real LLM (Gemini).
Creates a small sample repo with a known bug, then runs the agents to localize it.
"""

import sys
import tempfile
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from data.loader import BugInstance
from agents.orchestrator import Orchestrator


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)


def create_sample_buggy_repo(tmp_dir: Path) -> Path:
    """Create a small Python project with a known bug for testing."""
    repo = tmp_dir / "sample_project"
    repo.mkdir()

    # Create project structure
    (repo / "calculator").mkdir()

    (repo / "calculator" / "__init__.py").write_text(
        'from calculator.core import Calculator\n'
    )

    # THE BUGGY FILE — divide method has a bug (swapped a and b)
    (repo / "calculator" / "core.py").write_text('''\
class Calculator:
    """A simple calculator class."""

    def __init__(self):
        self.history = []

    def add(self, a, b):
        """Add two numbers."""
        result = a + b
        self.history.append(('add', a, b, result))
        return result

    def subtract(self, a, b):
        """Subtract b from a."""
        result = a - b
        self.history.append(('subtract', a, b, result))
        return result

    def multiply(self, a, b):
        """Multiply two numbers."""
        result = a * b
        self.history.append(('multiply', a, b, result))
        return result

    def divide(self, a, b):
        """Divide a by b."""
        if b == 0:
            raise ValueError("Cannot divide by zero")
        # BUG: operands are swapped! Should be a / b
        result = b / a
        self.history.append(('divide', a, b, result))
        return result

    def get_history(self):
        """Return calculation history."""
        return self.history.copy()
''')

    (repo / "calculator" / "formatter.py").write_text('''\
def format_result(operation, a, b, result):
    """Format a calculation result as a string."""
    symbols = {
        'add': '+',
        'subtract': '-',
        'multiply': '*',
        'divide': '/',
    }
    symbol = symbols.get(operation, '?')
    return f"{a} {symbol} {b} = {result}"

def format_history(history):
    """Format the entire history."""
    lines = []
    for op, a, b, result in history:
        lines.append(format_result(op, a, b, result))
    return "\\n".join(lines)
''')

    (repo / "main.py").write_text('''\
from calculator import Calculator
from calculator.formatter import format_result

def main():
    calc = Calculator()
    print(format_result('add', 2, 3, calc.add(2, 3)))
    print(format_result('divide', 10, 2, calc.divide(10, 2)))

if __name__ == "__main__":
    main()
''')

    return repo


def test_full_pipeline():
    """
    End-to-end test: create a buggy repo, feed a bug report,
    and check if the system finds the right file.
    """
    print("\n" + "=" * 70)
    print("🧪 INTEGRATION TEST: Full Pipeline with Gemini")
    print("=" * 70)
    print(f"Provider: {config.llm.provider}")
    print(f"Model:    {config.llm.model}")
    print(f"API Key:  {'✅ SET' if config.llm.gemini_api_key else '❌ NOT SET'}")
    print()

    if not config.llm.gemini_api_key or config.llm.gemini_api_key == "your-gemini-api-key-here":
        print("⚠️  Skipping integration test — no real API key configured")
        print("   Set GEMINI_API_KEY in .env to run this test")
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Step 1: Create the buggy repo
        repo_path = create_sample_buggy_repo(Path(tmp_dir))
        print(f"📁 Created sample repo at: {repo_path}")

        # Step 2: Create a bug report
        bug_report = (
            "The Calculator.divide() method returns wrong results. "
            "When I call calc.divide(10, 2), I expect 5.0 but I get 0.2. "
            "It seems like the division operands are swapped — it's computing "
            "b/a instead of a/b. The add, subtract, and multiply methods work "
            "correctly. The bug is in the divide method of the Calculator class."
        )

        bug_instance = BugInstance(
            instance_id="test__calculator-001",
            repo="test/calculator",
            problem_statement=bug_report,
            base_commit="abc123",
            patch=(
                "diff --git a/calculator/core.py b/calculator/core.py\n"
                "--- a/calculator/core.py\n"
                "+++ b/calculator/core.py\n"
                "@@ -30,7 +30,7 @@\n"
                "     def divide(self, a, b):\n"
                "         if b == 0:\n"
                "             raise ValueError\n"
                "-        result = b / a\n"
                "+        result = a / b\n"
            ),
            test_patch="",
        )

        print(f"🐛 Bug report: {bug_report[:100]}...")
        print(f"📝 Ground truth: {bug_instance.buggy_files}")

        # Step 3: Run the pipeline
        print("\n🚀 Running multi-agent pipeline...\n")
        orchestrator = Orchestrator(retriever=None)
        result = orchestrator.localize(
            bug_instance,
            repo_path=str(repo_path),
            verbose=True,
        )

        # Step 4: Evaluate results
        print("\n" + "=" * 70)
        print("📊 RESULTS")
        print("=" * 70)
        print(f"Success:      {result.success}")
        print(f"Ranked files: {result.ranked_files}")
        print(f"Ground truth: {bug_instance.buggy_files}")
        print(f"Time:         {result.total_time:.1f}s")
        print(f"LLM calls:    {result.total_llm_calls}")
        print(f"Tool calls:   {result.total_tool_calls}")

        if result.root_cause:
            print(f"\nRoot cause: {result.root_cause[:300]}")

        # Check Top-1 accuracy
        from evaluation.metrics import top_n_accuracy, reciprocal_rank
        hit_1 = top_n_accuracy(result.ranked_files, bug_instance.buggy_files, 1)
        hit_3 = top_n_accuracy(result.ranked_files, bug_instance.buggy_files, 3)
        rr = reciprocal_rank(result.ranked_files, bug_instance.buggy_files)

        print(f"\nTop-1: {'✅ HIT' if hit_1 else '❌ MISS'}")
        print(f"Top-3: {'✅ HIT' if hit_3 else '❌ MISS'}")
        print(f"RR:    {rr:.3f}")

        print("\n" + "=" * 70)
        if hit_1:
            print("🎉 INTEGRATION TEST PASSED — Bug correctly localized at Top-1!")
        elif hit_3:
            print("✅ INTEGRATION TEST PASSED — Bug found in Top-3")
        elif result.success:
            print("⚠️  Pipeline completed but didn't find the right file in Top-3")
        else:
            print("❌ INTEGRATION TEST FAILED — Pipeline did not complete successfully")
        print("=" * 70)


if __name__ == "__main__":
    test_full_pipeline()
