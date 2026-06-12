# Kiến Trúc Hệ Thống Bug Localization (Thesis)

## 1. Tổng Quan

Hệ thống bug localization là một pipeline AI đa agent, kết hợp ba kỹ thuật chính:

- **Multi-Agent Orchestration**: Ba agent chuyên biệt (Comprehension → Navigation → Confirmation) phối hợp để phân tích và định vị bug
- **Retrieval-Augmented Generation (RAG)**: Vector search (Qdrant) + BM25 hybrid search để tìm đoạn code liên quan theo ngữ nghĩa
- **Code Property Graph (CPG)**: Đồ thị AST của codebase, lưu quan hệ giữa hàm/lớp (gọi nhau, kế thừa, import)

### Luồng xử lý tổng thể

```
Bug Report (text)
       │
       ▼
┌─────────────────────────────────────────────────┐
│  BugReportPreprocessor                          │
│  - Stack traces (Python/Java)                   │
│  - Error messages                               │
│  - Mentioned files/functions                    │
│  - Log analysis (Drain3)                        │
└──────────────────┬──────────────────────────────┘
                   │
       ┌───────────▼───────────────────────────────┐
       │         Orchestrator                       │
       │                                            │
       │  [Background] Build Code Property Graph    │
       │                                            │
       │  ┌─────────────────────────────────────┐  │
       │  │ Phase 1: ComprehensionAgent          │  │
       │  │ → fault hypothesis + suspected files │  │
       │  └───────────────┬─────────────────────┘  │
       │                  │ + stack trace boosting  │
       │  ┌───────────────▼─────────────────────┐  │
       │  │ Phase 2: NavigationAgent             │  │
       │  │ → suspicious_locations (scored)      │  │
       │  └───────────────┬─────────────────────┘  │
       │                  │ reflection rounds (≤2)  │
       │  ┌───────────────▼─────────────────────┐  │
       │  │ Phase 3: ConfirmationAgent           │  │
       │  │ → ranked_locations (final)           │  │
       │  └───────────────┬─────────────────────┘  │
       │                  │                         │
       │  ┌───────────────▼─────────────────────┐  │
       │  │ UnifiedScorer (optional)             │  │
       │  │ → multi-signal reranking             │  │
       │  └─────────────────────────────────────┘  │
       └───────────────────────────────────────────┘
                          │
                          ▼
              LocalizationResult
         (ranked files + methods + locations)
```

---

## 2. Entry Point & Cấu Hình

### `main.py`

CLI dispatcher với 7 subcommand:

| Command | Mục đích |
|---------|----------|
| `localize` | Localize single bug instance |
| `evaluate` | SWE-bench benchmark |
| `index` | Index codebase vào Qdrant |
| `graph` | Build/query Code Property Graph |
| `defects4j` | Benchmark Defects4J (Java) |
| `bugsinpy` | Benchmark BugsInPy (Python) |
| `swebench` | Benchmark SWE-bench |

### `config.py` — Dataclasses cấu hình

```
Config (singleton)
├── LLMConfig        # provider (gemini/openai/custom), model, API key, temperature
├── EmbeddingConfig  # model (jina-embeddings-v3), local vs API, chunk size
├── RAGConfig        # Qdrant URI, collection name, top_k
├── Neo4jConfig      # URI, user, password, enabled flag
├── EvaluationConfig # dataset, output dir, top_N values
└── ScoringConfig    # weights for multi-signal scoring
```

**Feature flags** (qua `.env`):

| Flag | Default | Mô tả |
|------|---------|-------|
| `ENABLE_GRAPH_RAG` | `true` | Code Property Graph |
| `ENABLE_UNIFIED_SCORING` | `false` | Multi-signal reranking |
| `ENABLE_REPO_SKELETON` | `false` | Compact repo summary |
| `ENABLE_STRUCTURED_BUG_EXTRACTION` | `true` | Pre-extract bug info bằng LLM |
| `NEO4J_ENABLED` | `false` | Dùng Neo4j thay in-memory graph |
| `MAX_AGENT_ITERATIONS` | `10` | Vòng lặp tối đa mỗi agent |
| `REFLECTION_MAX_ROUNDS` | `2` | Số reflection rounds tối đa |
| `REFLECTION_CONF_THRESHOLD` | `0.5` | Ngưỡng confidence để trigger reflection |
| `PER_BUG_TIMEOUT` | `300s` | Timeout mỗi bug instance |
| `MULTI_PASS_TEMPERATURE` | `0.3` | Temperature cho Best-of-N passes |

---

## 3. Multi-Agent System

### `agents/base_agent.py` — Nền tảng chung

#### `AgentContext` — Trạng thái chia sẻ giữa agents

```python
@dataclass
class AgentContext:
    # Input
    instance_id: str
    problem_statement: str
    repo_path: str

    # Preprocessed
    error_messages: list[str]
    stack_traces: list[StackTrace]
    mentioned_files: list[str]
    mentioned_functions: list[str]
    keywords: list[str]
    log_parse_result: LogParseResult | None
    structured_bug_info: dict | None      # pre-extracted by LLM

    # Between agents
    fault_hypothesis: str | None          # từ ComprehensionAgent
    candidate_files: list[str]            # accumulates qua các phase
    candidate_methods: list[str]          # accumulates qua các phase

    # RAG
    retriever: CodeRetriever | None
    graph_retriever: GraphRetriever | None

    # Control
    temperature_override: float | None
    reflection_feedback: str | None
```

#### `BaseAgent` — Agentic loop

```
BaseAgent.run()
    │
    ├─ Build system prompt + initial user message
    │
    └─ Loop (max MAX_AGENT_ITERATIONS):
           │
           ├─ _call_llm()  ──► OpenAI-compatible API
           │                    (Gemini / OpenAI / custom endpoint)
           │
           ├─ if tool_calls:
           │      _execute_tool()  ──► Parallel (≤8 workers)
           │                          Auto-inject: repo_path, retriever,
           │                          graph_retriever vào kwargs
           │      Append tool results, trim history nếu quá dài
           │
           └─ elif final answer:
                  _parse_output()  ──► Extract JSON từ response
                  return AgentResult
```

**Parallel tool execution**: Nhiều tool calls trong một turn được chạy song song bằng `ThreadPoolExecutor(max_workers=8)`.

**Message trimming**: Sliding-window để tránh vượt context limit — giữ system prompt + N recent messages.

**Output capping**: Kết quả tool bị truncate để tránh prompt token tăng bậc hai.

---

### `agents/comprehension.py` — Comprehension Agent

**Vai trò**: Đọc bug report, sinh fault hypothesis, xác định file/hàm nghi ngờ ban đầu.

**Quy trình**:
1. Pre-call `_extract_structured_bug_info()` — LLM call nhẹ trích xuất:
   - `bug_phenomenon`: triệu chứng quan sát được
   - `bug_explanation`: giải thích nguyên nhân
   - `bug_traceback`: stack trace hoặc code snippet liên quan
2. Chạy agentic loop với tools:
   - `code_search` — tìm pattern trong code bằng text/regex
   - `read_file` — đọc nội dung file
   - `list_directory` — duyệt cấu trúc repo
   - `parse_logs` — phân tích log bằng Drain3
   - `search_tests` — tìm test case liên quan

**Output JSON**:
```json
{
  "bug_summary": "Mô tả ngắn về bug",
  "bug_type": "logic_error | runtime_error | api_misuse | ...",
  "key_components": ["ComponentA", "ComponentB"],
  "suspicious_files": ["src/foo.py", "src/bar.java"],
  "suspicious_functions": ["methodA", "methodB"],
  "fault_hypothesis": "Chi tiết giả thuyết về nguyên nhân bug...",
  "search_keywords": ["keyword1", "keyword2"],
  "hypothesis_confidence": 0.85
}
```

---

### `agents/navigation.py` — Navigation Agent

**Vai trò**: Khám phá codebase một cách có hệ thống, tìm vị trí nghi ngờ cụ thể.

**Chiến lược (theo system prompt)**:
1. Dùng `semantic_file_search` trước — nhanh, file-level lookup
2. Tìm kiếm tên class/method chính xác từ bug report
3. Tìm error message dưới dạng literal string
4. Dùng Graph RAG (callers/callees/inheritance) để mở rộng ngữ cảnh
5. Luôn output đường dẫn đầy đủ từ gốc repo

**Tools**:
- `code_search`, `read_file`, `list_directory`
- `get_file_outline` — danh sách class/method trong file (AST)
- `get_function_source` — full source của một hàm
- `semantic_file_search` — fast file-level semantic lookup
- `semantic_search` — method/class-level semantic search
- `git_log` — lịch sử commit gần đây
- `graph_search`, `find_callers`, `find_callees` — Graph RAG

**Output JSON**:
```json
{
  "suspicious_locations": [
    {
      "file_path": "src/main/java/org/example/Foo.java",
      "function_name": "buggyMethod",
      "class_name": "Foo",
      "start_line": 42,
      "end_line": 60,
      "suspicion_score": 0.95,
      "reason": "Lý do nghi ngờ vị trí này..."
    }
  ],
  "investigation_summary": "Tóm tắt quá trình điều tra..."
}
```

---

### `agents/confirmation.py` — Confirmation Agent

**Vai trò**: Xem xét lại danh sách ứng viên, xếp hạng cuối cùng theo confidence.

**Quy tắc xếp hạng**:
- **HIGH (0.8+)**: Chỉ khi có bằng chứng rõ ràng — stack trace trực tiếp, logic lỗi hiển nhiên
- **MEDIUM (0.5–0.79)**: Đáng nghi ngờ nhưng chưa chắc chắn
- **LOW (<0.5)**: Suy đoán
- File xuất hiện trong stack trace → ưu tiên HIGH confidence
- Chủ động tìm ứng viên mới nếu danh sách cũ quá yếu

**Tools**: `code_search`, `read_file`, `semantic_search`, `find_callers`, `find_callees`

**`get_reflection_message()`**: Khi confidence thấp, sinh guidance cho reflection round tiếp theo:
- Gợi ý mở rộng phạm vi tìm kiếm
- Ưu tiên file có trong stack trace
- Tìm theo error message literal

---

### `agents/orchestrator.py` — Điều phối Pipeline

**`localize()` — Single-pass localization**:

```
Phase 0:  BugReportPreprocessor.process()
          → AgentContext được khởi tạo đầy đủ

Phase 0   [background thread]:
(parallel) build_code_graph() + GraphRetriever.build_graph()

Phase 1:  ComprehensionAgent.run(context)
          → context.fault_hypothesis cập nhật
          → context.candidate_files cập nhật
          → Stack trace file boosting (ưu tiên file có trong stack trace)

          Wait for background graph thread
          → context.graph_retriever = graph

Phase 2+  [reflection loop, max REFLECTION_MAX_ROUNDS]:
          NavigationAgent.run(context)
          ConfirmationAgent.run(context)
          if top1_confidence < threshold:
              context.reflection_feedback = get_reflection_message()
              continue
          else:
              break

Phase 3:  UnifiedScorer (nếu bật)
          → Multi-signal reranking

Output:   LocalizationResult
          → ranked_files, ranked_methods, ranked_locations
          → explanation, root_cause, token/LLM call stats
```

**`multi_pass_localize()` — Best-of-N với Reciprocal Rank Fusion**:
- Chạy `localize()` N lần với temperature khác nhau
- File score = Σ 1/(k + rank_i) trên tất cả N runs
- Method score tương tự
- Trả về ranking tổng hợp

**Graph caching**: Thread-safe cache theo `(repo_path, language, git_HEAD)` — tránh rebuild khi cùng commit.

**`_filter_nonexistent_files()`**: Loại bỏ file path ảo do LLM hallucinate.

---

## 4. RAG System (`rag/`)

### Sơ đồ tổng thể

```
Codebase
    │
    ▼
CodebaseIndexer (rag/indexer.py)
    │  chunking theo ngôn ngữ:
    │    Python → ast module (function/class level)
    │    Java   → regex-based (method/class level)
    │  embedding via CodeEmbedder
    ▼
Qdrant (vector store)
    │
    ├─► CodeRetriever.query()               → semantic search
    ├─► CodeRetriever.get_similar_files()   → file-level lookup
    └─► CodeRetriever.hybrid_*()            → BM25 + semantic RRF

Codebase
    │
    ▼
CodePropertyGraph / Neo4jGraph
    │  nodes: File, Class, Function
    │  edges: CALLS, IMPORTS, INHERITS, CONTAINS, SAME_FILE
    ▼
GraphRetriever
    │  vector anchors → BFS expansion → ranked results
    └─► search()
```

### `rag/embedder.py` — Chunking & Embedding

**`CodeChunk`** — đơn vị lưu trữ cơ bản:

```python
@dataclass
class CodeChunk:
    text: str
    file_path: str
    start_line: int
    end_line: int
    function_name: str | None
    class_name: str | None
    chunk_type: Literal["function", "class", "module", "file_summary"]
    package_name: str | None      # Java package
    language: str                 # "python" | "java"
    embedding: list[float] | None
```

**`CodeEmbedder`**:
- **Local mode**: `sentence-transformers` (jina-embeddings-v3)
- **API mode**: OpenAI-compatible embedding endpoint
- In-memory embedding cache để tránh re-embed

### `rag/indexer.py` — Codebase Indexer

- Walk directory tree, skip test/build directories
- Chunk mỗi file theo ngôn ngữ phát hiện tự động
- Tạo UUID5 deterministic IDs — tránh duplicate khi re-index
- Upsert vào Qdrant với metadata: `repo_id`, `package`, `language`, `chunk_type`
- Hỗ trợ `--clear` để xóa index trước khi build lại

### `rag/retriever.py` — Semantic Retrieval

**`CodeRetriever.query()`** — hỗ trợ filters:

| Filter | Loại match | Mô tả |
|--------|-----------|-------|
| `repo_filter` | Exact | Isolate theo project |
| `package_filter` | Substring | Java package |
| `language_filter` | Exact | `python` / `java` |
| `chunk_type_filter` | Exact | `function` / `class` / `file_summary` |
| `file_filter` | Substring | Lọc theo path |

**Hybrid search** (`hybrid_get_similar_files()`):
1. BM25 full-text search trên file-level
2. Vector search trên file summaries
3. Reciprocal Rank Fusion kết hợp hai kết quả

### `rag/code_graph.py` — Code Property Graph (in-memory)

**Graph schema**:

```
GraphNode:
    id          str    # hash(file_path + name)
    name        str
    node_type   str    # "file" | "class" | "function" | "method"
    file_path   str
    start_line  int
    end_line    int
    signature   str
    docstring   str

GraphEdge:
    source_id   str
    target_id   str
    edge_type   str    # "CALLS" | "IMPORTS" | "INHERITS" | "CONTAINS" | "SAME_FILE"
    metadata    dict
```

**Builders**:
- `PythonGraphBuilder`: dùng `ast` module — trích xuất classes/functions/calls/imports
- `JavaGraphBuilder`: dùng regex (javalang) — trích xuất classes/methods/calls/imports

### `rag/graph_retriever.py` — Graph RAG

**`GraphRetriever.search(query)`**:
1. Vector search → tìm N anchor nodes ngữ nghĩa gần nhất
2. BFS expansion từ mỗi anchor: callers, callees, siblings, inheritance chain
3. Score = anchor_score × decay^depth
4. Dedup và rank tổng hợp

### `rag/neo4j_backend.py` — Neo4j Backend (tùy chọn)

Cùng interface với `CodePropertyGraph` nhưng:
- Lưu persistent trong Neo4j server
- Truy vấn bằng Cypher native
- Hỗ trợ visualization tốt hơn
- Phù hợp codebase lớn cần persistence

### `rag/bm25_index.py` — BM25 Index

- Full-text search fallback khi vector search không đủ
- Dùng trong hybrid search qua RRF

---

## 5. Tools System (`tools/`)

### `tools/registry.py` — Central Registry

```python
TOOL_REGISTRY: dict[str, tuple[Callable, dict]] = {
    "tool_name": (function, openai_function_schema),
    ...
}
```

Tất cả agents đăng ký tool qua registry — định nghĩa một lần, dùng nhiều nơi. Schema theo format OpenAI function-calling.

### Danh sách tools

| Tool | File | Mô tả |
|------|------|-------|
| `code_search` | `code_search.py` | Text/regex search, dùng ripgrep nếu có |
| `read_file` | `file_reader.py` | Đọc file với line range, có cache |
| `list_directory` | `file_reader.py` | Duyệt thư mục theo tree format |
| `semantic_search` | `semantic_search.py` | RAG method/class-level search |
| `semantic_file_search` | `semantic_search.py` | RAG file-level fast lookup |
| `graph_search` | `graph_search.py` | CPG semantic + BFS graph traversal |
| `find_callers` | `graph_search.py` | Tìm tất cả hàm gọi đến target |
| `find_callees` | `graph_search.py` | Tìm tất cả hàm được target gọi |
| `get_file_outline` | `ast_parser.py` | Danh sách class/method trong file (AST) |
| `get_function_source` | `ast_parser.py` | Full source code của một hàm |
| `repo_skeleton` | `repo_skeleton.py` | Compact repo summary ưu tiên theo hints |
| `parse_logs` | `log_parser.py` | Drain3 log template analysis |
| `git_log` | `git_history.py` | Lịch sử commit gần đây |

### `tools/cache.py` — LRU Cache

- `read_file_cached()`: Cache nội dung file, chia sẻ giữa tất cả tools
- `parse_ast_cached()`: Cache kết quả parse AST
- Tránh re-read disk trong cùng session, giảm latency

---

## 6. Evaluation System (`evaluation/`)

### `evaluation/metrics.py`

```python
# File-level metrics
top_n_accuracy(predictions, ground_truth, n)   # Acc@N
reciprocal_rank(predictions, ground_truth)      # 1/rank của kết quả đúng đầu tiên
average_precision(predictions, ground_truth)    # AP

# Method-level metrics (cùng logic, áp dụng cho method identifiers)

# _paths_match(): flexible path matching
# xử lý source root variants (src/main/java/..., src/...)

compute_metrics(results) → {
    "top1": float, "top3": float, "top5": float, "top10": float,
    "mrr": float, "map": float
}
```

### `evaluation/unified_scorer.py` — Multi-signal Reranking

Kết hợp nhiều tín hiệu để rerank danh sách candidates:

| Tín hiệu | Mô tả |
|----------|-------|
| LLM confidence | Score từ ConfirmationAgent |
| Stack trace position | Decay theo vị trí trong stack trace (factor 0.85^i) |
| Error message match | Số lần error string xuất hiện trong file |
| Mentioned files | File được đề cập trực tiếp trong bug report text |
| Graph RAG proximity | Khoảng cách đồ thị từ các file liên quan đã biết |
| Semantic similarity | Score từ Qdrant vector search |
| Method aggregation | Nhiều method trong cùng file → file score cao hơn |
| Test file penalty | Giảm score các file test |

Weights cấu hình qua `ScoringConfig` trong `.env`.

### `evaluation/evaluator.py`

- `BenchmarkEvaluator.evaluate()`: Chạy pipeline trên toàn bộ dataset
- Sequential mode hoặc parallel mode (configurable workers)
- Per-instance tracking: predictions, ground truth, metrics
- Aggregate stats: mean Top-N, MRR, MAP
- Output: JSON với chi tiết từng bug instance + summary

### `evaluation/export.py`

- Export kết quả ra CSV và JSON
- Markdown table summary

---

## 7. API Layer (`api/`)

### `api/main.py` — FastAPI App

```
FastAPI app
├── CORS: all origins enabled
├── GET  /health
├── Router /api/localize    → routes/localize.py
├── Router /api/evaluate    → routes/evaluate.py
├── Router /api/graph       → routes/graph.py
└── Router /api/benchmarks  → routes/benchmarks.py
```

### Endpoints chính

**`POST /api/localize`**:
```json
Request: {
    "bug_report": "...",
    "repo_path": "/path/to/repo",
    "use_graph_rag": true,
    "multi_pass": false,
    "reflection_rounds": 2
}
Response: {"job_id": "uuid", "status": "queued"}
```

**`GET /api/localize/{job_id}`**:
```json
Response: {
    "status": "running | done | error",
    "result": { ...LocalizationResult... }
}
```

Jobs chạy async trong background, tracking qua `jobs: dict[str, JobState]`.

---

## 8. Data Pipeline (`data/`)

### `data/preprocessor.py` — Bug Report Preprocessing

```
BugReportPreprocessor.process(problem_statement, repo_path)
    │
    ├─ Extract stack traces:
    │    Python: 'File "...", line N, in function_name'
    │    Java:   'at package.ClassName.method(File.java:N)'
    │
    ├─ Extract error messages:
    │    Exception names, assertion errors, custom patterns
    │
    ├─ Extract mentioned files/functions:
    │    Regex match: file.py, ClassName, methodName patterns
    │
    ├─ Drain3 log analysis (nếu enabled):
    │    → search_terms, assertion_pairs, log_templates
    │
    └─ Return ProcessedBugReport
```

### `data/loader.py` — BugInstance

```python
@dataclass
class BugInstance:
    instance_id: str
    repo: str
    problem_statement: str
    base_commit: str
    patch: str             # ground truth diff
    test_patch: str
    buggy_files: list[str] # extracted from patch via regex
```

### Data Loaders

| Loader | Benchmark | Ngôn ngữ | Nguồn |
|--------|-----------|---------|-------|
| `SWEBenchLoader` | SWE-bench | Python | HuggingFace dataset |
| `Defects4JLoader` | Defects4J | Java | Local checkouts |
| `BugsInPyLoader` | BugsInPy | Python | Local checkouts |

---

## 9. Commands (`commands/`)

| File | CLI | Chức năng chính |
|------|-----|----------------|
| `localize.py` | `localize --bug-report --repo-path` | Single bug, hỗ trợ `--multi-pass`, `--no-graph-rag` |
| `index.py` | `index --repo-path` | Index vào Qdrant, hỗ trợ `--clear` |
| `graph.py` | `graph --repo-path` | Build CPG, query `--callers`, `--callees`, `--stats`, `--visualize` |
| `defects4j.py` | `defects4j --project --limit` | Batch eval với `--workers`, `--list-bugs` |
| `bugsinpy.py` | `bugsinpy --project --limit` | Batch eval BugsInPy |
| `swebench.py` | `swebench --split --limit` | Batch eval SWE-bench, `--dry-run` |
| `evaluate.py` | `evaluate --limit` | Generic evaluation via SWEBenchLoader |
| `_shared.py` | — | `make_orchestrator()`, `process_bug()` với timeout |

---

## 10. Sơ Đồ Dependency Giữa Các Module

```
main.py
    └── commands/
            ├── localize.py ─────────────────────────────┐
            ├── defects4j.py ── _shared.py ──────────────┤
            ├── bugsinpy.py  ──     │                    │
            └── swebench.py  ──     │                    │
                                    ▼                    ▼
                             agents/orchestrator.py
                                    │
                    ┌───────────────┼────────────────┐
                    ▼               ▼                 ▼
            comprehension.py  navigation.py  confirmation.py
                    └───────────────┴──────────► base_agent.py
                                                      │
                              ┌───────────────────────┤
                              ▼                       ▼
                    tools/registry.py          rag/retriever.py
                              │                rag/graph_retriever.py
                    ┌─────────┼──────────┐            │
                    ▼         ▼          ▼             ▼
              code_search  semantic  graph_search  rag/code_graph.py
              file_reader  _search   ast_parser    rag/neo4j_backend.py
              log_parser   git_hist  repo_skel     rag/indexer.py
                                                   rag/embedder.py
                                                         │
                                                         ▼
                                                      Qdrant

evaluation/
    ├── evaluator.py ──────► orchestrator.py
    ├── metrics.py
    ├── unified_scorer.py
    └── export.py

api/
    └── routes/ ──────────► commands/ ──► orchestrator.py

frontend/
    └── (React/Vue UI) ───► api/
```

---

## 11. Cấu Trúc Thư Mục Đầy Đủ

```
thesis/
├── main.py                          # CLI entry point (7 subcommands)
├── config.py                        # Cấu hình toàn hệ thống (dataclasses + .env)
├── requirements.txt
├── .env.example
│
├── agents/
│   ├── base_agent.py                # AgentContext, BaseAgent, AgentResult
│   ├── orchestrator.py              # Pipeline orchestration, multi-pass, reflection
│   ├── comprehension.py             # Phase 1: Bug analysis + fault hypothesis
│   ├── navigation.py                # Phase 2: Codebase exploration
│   └── confirmation.py              # Phase 3: Validation + ranking
│
├── rag/
│   ├── embedder.py                  # CodeChunk, CodeEmbedder (local + API)
│   ├── indexer.py                   # CodebaseIndexer → Qdrant
│   ├── retriever.py                 # CodeRetriever (semantic + hybrid BM25)
│   ├── code_graph.py                # CodePropertyGraph in-memory (Python/Java)
│   ├── neo4j_backend.py             # Neo4j persistent backend
│   ├── graph_retriever.py           # GraphRetriever (vector anchor + BFS)
│   ├── graph_visualizer.py          # HTML/PNG graph visualization
│   └── bm25_index.py                # BM25 full-text index
│
├── tools/
│   ├── registry.py                  # TOOL_REGISTRY (central registry)
│   ├── cache.py                     # LRU cache (files + AST parsing)
│   ├── code_search.py               # Text/regex search (ripgrep fallback)
│   ├── file_reader.py               # read_file, list_directory
│   ├── semantic_search.py           # RAG queries (file + method level)
│   ├── graph_search.py              # CPG queries (search, callers, callees)
│   ├── ast_parser.py                # get_file_outline, get_function_source
│   ├── repo_skeleton.py             # Compact repo summary
│   ├── log_parser.py                # Drain3 log template analysis
│   └── git_history.py               # Git log queries
│
├── data/
│   ├── loader.py                    # BugInstance, SWEBenchLoader
│   ├── preprocessor.py              # BugReportPreprocessor
│   ├── defects4j_loader.py          # Defects4J benchmark loader
│   └── bugsinpy_loader.py           # BugsInPy benchmark loader
│
├── evaluation/
│   ├── evaluator.py                 # BenchmarkEvaluator (sequential + parallel)
│   ├── metrics.py                   # Top-N, MRR, MAP, file/method level
│   ├── unified_scorer.py            # Multi-signal reranking (UnifiedScorer)
│   └── export.py                    # CSV/JSON/Markdown export
│
├── commands/
│   ├── _shared.py                   # make_orchestrator(), process_bug() + timeout
│   ├── localize.py                  # Single bug localization
│   ├── index.py                     # Codebase indexing
│   ├── graph.py                     # CPG build + query CLI
│   ├── defects4j.py                 # Defects4J batch evaluation
│   ├── bugsinpy.py                  # BugsInPy batch evaluation
│   ├── swebench.py                  # SWE-bench batch evaluation
│   └── evaluate.py                  # Generic evaluation
│
├── api/
│   ├── main.py                      # FastAPI app + CORS + routers
│   └── routes/
│       ├── localize.py              # POST /api/localize (async jobs)
│       ├── evaluate.py              # POST /api/evaluate
│       ├── graph.py                 # GET/POST /api/graph
│       └── benchmarks.py            # GET /api/benchmarks
│
└── frontend/                        # React/Vue web UI
```

---

## 12. Benchmark Metrics

Hệ thống đo lường theo các metrics chuẩn trong bug localization research:

| Metric | Công thức | Ý nghĩa |
|--------|-----------|---------|
| **Top-1 Accuracy** | `file_đúng ∈ top_1_predictions` | File lỗi có nằm trong dự đoán số 1 không |
| **Top-3 Accuracy** | `file_đúng ∈ top_3_predictions` | File lỗi có nằm trong top 3 không |
| **Top-5 Accuracy** | `file_đúng ∈ top_5_predictions` | File lỗi có nằm trong top 5 không |
| **Top-10 Accuracy** | `file_đúng ∈ top_10_predictions` | File lỗi có nằm trong top 10 không |
| **MRR** | `mean(1 / rank_của_file_đúng_đầu_tiên)` | Trung bình nghịch đảo thứ hạng |
| **MAP** | `mean(average_precision_per_bug)` | Mean Average Precision |

Tất cả metrics áp dụng cho cả **file-level** và **method-level** predictions.

**Path matching** linh hoạt qua `_paths_match()` — xử lý các biến thể source root:
- `src/main/java/org/example/Foo.java`
- `org/example/Foo.java`
- `Foo.java`

---

## 13. Benchmarks Được Hỗ Trợ

| Benchmark | Ngôn ngữ | Ví dụ project | Loader |
|-----------|---------|--------------|--------|
| **Defects4J** | Java | Lang, Math, Time, Closure, Mockito | `Defects4JLoader` |
| **SWE-bench** | Python | django, astropy, flask, pandas | `SWEBenchLoader` |
| **BugsInPy** | Python | pandas, scrapy, keras, black | `BugsInPyLoader` |

---

## 14. LLM Provider Support

| Provider | Config | Model ví dụ |
|----------|--------|------------|
| Google Gemini | `LLM_PROVIDER=gemini` | `gemini-2.0-flash` |
| OpenAI | `LLM_PROVIDER=openai` | `gpt-4o`, `gpt-4o-mini` |
| Custom endpoint | `LLM_PROVIDER=custom` | Ollama, vLLM, LM Studio |

Tất cả đều qua **OpenAI-compatible API** — chỉ đổi base URL và API key.
