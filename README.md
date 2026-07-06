# 🐛 Bug Localization System — Agentic AI

Hệ thống **Bug Localization** sử dụng kiến trúc **Multi-Agent AI** để tự động xác định vị trí lỗi trong codebase dựa trên bug report.

> 📐 **Tài liệu:** [Thiết kế hệ thống & định hướng nghiên cứu](docs/SYSTEM_DESIGN.md) (giả thuyết, phương pháp thực nghiệm, lộ trình) · [Kiến trúc chi tiết](ARCHITECTURE.md)

## Architecture

```
Bug Report ──→ [Fault Comprehension Agent] ──→ [Codebase Navigation Agent] ──→ [Fault Confirmation Agent] ──→ Ranked Buggy Locations
                      ↕                              ↕                              ↕
                  [Tools]                         [Tools]                        [Tools]
              (search, read)            (search, read, AST,              (search, read)
                                        semantic, git)
```

### Agents
1. **Fault Comprehension Agent**: Phân tích bug report, hiểu bản chất lỗi, sinh fault hypothesis
2. **Codebase Navigation Agent**: Duyệt codebase bằng nhiều chiến lược tìm kiếm để thu hẹp vị trí lỗi
3. **Fault Confirmation Agent**: Review code candidates, xác nhận và rank kết quả cuối cùng

### Tools
- `code_search` — Tìm kiếm text/regex trong codebase
- `file_reader` — Đọc nội dung file với line numbers
- `ast_parser` — Phân tích cấu trúc Python file (classes, functions)
- `semantic_search` — Tìm kiếm code theo ngữ nghĩa (RAG)
- `git_history` — Xem lịch sử thay đổi code

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your API keys
```

## Usage

### 1. Localize a bug (manual)
```bash
python main.py localize \
  --bug-report "The date parser fails when timezone offset is negative" \
  --repo-path /path/to/repo
```

### 2. Localize from SWE-bench
```bash
python main.py localize \
  --instance-id "astropy__astropy-12907" \
  --repo-path /path/to/astropy
```

### 3. Index codebase for semantic search
```bash
python main.py index --repo-path /path/to/repo
```

### 4. Run evaluation benchmark (SWE-bench)
```bash
# Evaluate on first 10 instances
python main.py evaluate --limit 10 --output results/eval_10.json

# Full evaluation
python main.py evaluate --output results/eval_full.json --verbose
```

### 5. Defects4J Benchmark
```bash
# List available bugs
python main.py defects4j --list-bugs --project Lang

# View a single bug (with ground truth)
python main.py defects4j --instance-id Lang_1

# Run pipeline on a single bug with checked-out repo
python main.py defects4j --instance-id Lang_1 --repo-path data/defects4j_checkouts/Lang/Lang_1

# Batch evaluate on Lang project (first 10 bugs)
python main.py defects4j --project Lang --limit 10 --repo-path data/defects4j_checkouts --output results/d4j_lang.csv
```

**Setup Defects4J Checkouts:**
We provide a helper script to checkout Defects4J bugs without the full framework installation (requires Git and Internet):
```bash
python scripts/checkout_d4j_manual.py
```
This will checkout `Lang_1` to `Lang_15` into `data/defects4j_checkouts/`.

**Supported Projects**: Closure (156), Lang (56), Math (85), Mockito (22), Time (23)

### 6. Web UI

A React + FastAPI web interface for testing the bug localization system.

**Start the backend:**
```bash
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

**Start the frontend (in a new terminal):**
```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000 to access the UI.

**Features:**
- **Localize**: Input bug reports and repo paths, view ranked results
- **Evaluate**: Run benchmark evaluations with metrics charts
- **Graph**: Explore Code Property Graph visualization

## Evaluation Metrics
- **Top-N Accuracy**: % bugs localized in top N results (N=1,3,5,10)
- **MRR (Mean Reciprocal Rank)**: Average of 1/rank
- **MAP (Mean Average Precision)**: Average precision per query

## LLM Providers

The system supports multiple LLM providers via the `.env` config:

| Provider | Model | Config |
|----------|-------|--------|
| **Gemini** (default) | gemini-2.5-flash | `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=...` |
| OpenAI | gpt-4o | `LLM_PROVIDER=openai`, `OPENAI_API_KEY=...` |
| Ollama | any | `LLM_PROVIDER=ollama`, `LLM_API_BASE=http://localhost:11434/v1` |

## Project Structure
```
thesis/
├── main.py              # CLI entry point
├── config.py            # Configuration (multi-provider LLM support)
├── agents/              # Multi-agent system
│   ├── base_agent.py    # Abstract base agent (agentic loop)
│   ├── comprehension.py # Fault comprehension
│   ├── navigation.py    # Codebase navigation
│   ├── confirmation.py  # Fault confirmation
│   └── orchestrator.py  # Pipeline coordinator
├── tools/               # Agent tools
├── rag/                 # RAG pipeline (embedding, indexing, retrieval)
├── data/                # Data loading
│   ├── loader.py        # SWE-bench loader
│   ├── defects4j_loader.py  # Defects4J benchmark loader
│   └── preprocessor.py  # Bug report preprocessing
├── evaluation/          # Metrics and benchmark runner
├── api/                 # FastAPI backend for web UI
├── frontend/            # React frontend for web UI
└── tests/               # Unit & integration tests
```

