#!/bin/bash
# ============================================================================
# 🐍 Full BugsInPy Benchmark Runner
#
# This script checks out ALL BugsInPy bugs and runs the benchmark evaluation.
# Total: 493 bugs across 17 Python projects.
#
# Usage:
#   bash scripts/run_full_bugsinpy.sh           # Full run (checkout + evaluate)
#   bash scripts/run_full_bugsinpy.sh --skip-checkout  # Skip checkout, evaluate only
#   bash scripts/run_full_bugsinpy.sh --checkout-only   # Only checkout, don't evaluate
#
# Estimated time:
#   - Checkout: ~30-60 minutes (depends on network)
#   - Evaluation: ~5-10 hours (depends on LLM speed, ~37s/bug avg)
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Activate virtualenv
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
    echo "✅ Virtual environment activated"
else
    echo "❌ .venv not found. Create it first: python3 -m venv .venv && pip install -r requirements.txt"
    exit 1
fi

# Parse arguments
SKIP_CHECKOUT=false
CHECKOUT_ONLY=false

for arg in "$@"; do
    case $arg in
        --skip-checkout)  SKIP_CHECKOUT=true ;;
        --checkout-only)  CHECKOUT_ONLY=true ;;
        --help|-h)
            echo "Usage: bash scripts/run_full_bugsinpy.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --skip-checkout   Skip checkout step, run evaluation only"
            echo "  --checkout-only   Only checkout bugs, don't run evaluation"
            echo "  --help, -h        Show this help"
            exit 0
            ;;
    esac
done

# All 17 BugsInPy projects
PROJECTS=(
    "ansible"
    "black"
    "cookiecutter"
    "fastapi"
    "httpie"
    "keras"
    "luigi"
    "matplotlib"
    "pandas"
    "PySnooper"
    "sanic"
    "scrapy"
    "spacy"
    "thefuck"
    "tornado"
    "tqdm"
    "youtube-dl"
)

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
OUTPUT_DIR="results/bugsinpy_full_${TIMESTAMP}"
LOG_DIR="logs"

mkdir -p "$OUTPUT_DIR" "$LOG_DIR"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         🐍 Full BugsInPy Benchmark (493 bugs)              ║"
echo "║         17 Python projects                                  ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "📅 Started at: $(date '+%Y-%m-%d %H:%M:%S')"
echo "📂 Output dir: $OUTPUT_DIR"
echo ""

# ─────────────────────────────────────────────────────────────────────
# Step 1: Checkout all bugs
# ─────────────────────────────────────────────────────────────────────

if [ "$SKIP_CHECKOUT" = false ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📥 Step 1: Checking out all BugsInPy bugs..."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    for project in "${PROJECTS[@]}"; do
        echo "──── Checking out: $project ────"
        python scripts/checkout_bugsinpy.py \
            --project "$project" \
            --all \
            2>&1 | tee -a "$LOG_DIR/checkout_${project}.log"
        echo ""
    done

    echo "✅ All checkouts complete!"
    echo ""
fi

if [ "$CHECKOUT_ONLY" = true ]; then
    echo "📦 Checkout-only mode. Skipping evaluation."
    echo "📊 To run evaluation later:"
    echo "   bash scripts/run_full_bugsinpy.sh --skip-checkout"
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────
# Step 2: Run benchmark evaluation
# ─────────────────────────────────────────────────────────────────────

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 Step 2: Running benchmark evaluation on ALL bugs..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Run per-project to get granular results + a combined full run
for project in "${PROJECTS[@]}"; do
    echo "══════════════════════════════════════════════════════════"
    echo "  🔬 Evaluating project: $project"
    echo "══════════════════════════════════════════════════════════"

    python scripts/run_bugsinpy_benchmark.py \
        --project "$project" \
        --output "$OUTPUT_DIR/bugsinpy_${project}" \
        --verbose \
        2>&1 | tee "$LOG_DIR/eval_${project}_${TIMESTAMP}.log"

    echo ""
    echo "✅ $project evaluation complete!"
    echo ""
done

# ─────────────────────────────────────────────────────────────────────
# Step 3: Run combined full evaluation (all projects at once)
# ─────────────────────────────────────────────────────────────────────

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 Step 3: Combined full evaluation (all projects)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

python scripts/run_bugsinpy_benchmark.py \
    --output "$OUTPUT_DIR/bugsinpy_full" \
    --verbose \
    2>&1 | tee "$LOG_DIR/eval_full_${TIMESTAMP}.log"

# ─────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                   🏁 Benchmark Complete!                    ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  📂 Results:   $OUTPUT_DIR"
echo "║  📄 Full report: $OUTPUT_DIR/bugsinpy_full_report.md"
echo "║  📊 Full JSON:   $OUTPUT_DIR/bugsinpy_full.json"
echo "║  📝 Logs:        $LOG_DIR/"
echo "║  📅 Finished at: $(date '+%Y-%m-%d %H:%M:%S')"
echo "╚══════════════════════════════════════════════════════════════╝"
