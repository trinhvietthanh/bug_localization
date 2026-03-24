#!/bin/bash
# ============================================================================
# 🐞 Full Defects4J Benchmark Runner
#
# This script runs the benchmark evaluation for each Defects4J project separately,
# using 3 concurrent threads per project.
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

PROJECTS=(
    "Chart"
    "Closure"
    "Lang"
    "Math"
    "Mockito"
    "Time"
)

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
OUTPUT_DIR="results/defects4j_full_${TIMESTAMP}"
LOG_DIR="logs"

mkdir -p "$OUTPUT_DIR" "$LOG_DIR"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          🐞 Full Defects4J Benchmark (6 projects)             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "📅 Started at: $(date '+%Y-%m-%d %H:%M:%S')"
echo "📂 Output dir: $OUTPUT_DIR"
echo "🧵 Workers: 3"
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 Running benchmark evaluation for EACH project..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

for project in "${PROJECTS[@]}"; do
    echo "══════════════════════════════════════════════════════════"
    echo "  🔬 Evaluating project: $project"
    echo "══════════════════════════════════════════════════════════"

    python main.py defects4j \
        --project "$project" \
        --repo-path "data/defects4j_checkouts" \
        --workers 3 \
        --output "$OUTPUT_DIR/defects4j_${project}" \
        --verbose \
        2>&1 | tee "$LOG_DIR/eval_defects4j_${project}_${TIMESTAMP}.log"

    echo ""
    echo "✅ $project evaluation complete!"
    echo ""
done

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                   🏁 Benchmark Complete!                    ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  📂 Results:   $OUTPUT_DIR"
echo "║  📝 Logs:      $LOG_DIR/"
echo "║  📅 Finished:  $(date '+%Y-%m-%d %H:%M:%S')"
echo "╚══════════════════════════════════════════════════════════════╝"
