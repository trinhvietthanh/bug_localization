# Kiến Trúc Hệ Thống Bug Localization (Thesis)

> Cập nhật: 2026-07-12 (phản ánh nhánh `feat/improve`: E1 giả thuyết cạnh tranh, E2 khám phá hàng đợi ưu tiên, E3 rerank, Comprehension v2/v3, Confirmation hybrid, patch-duel, Neo4j graph backend). Bối cảnh nghiên cứu (giả thuyết H1-H8, thiết kế thực nghiệm) xem [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md). Đánh giá chất lượng cài đặt phần mới xem [docs/DANH_GIA_CODE_E1E2E3.md](docs/DANH_GIA_CODE_E1E2E3.md).

## 1. Tổng Quan

Hệ thống bug localization là một pipeline AI đa agent, kết hợp bốn kỹ thuật chính:

- **Multi-Agent Orchestration**: Comprehension → (Verification) → Navigation → Confirmation phối hợp để phân tích và định vị bug, với vòng reflection tối đa 2 lần khi confidence thấp.
- **Retrieval-Augmented Generation (RAG)**: Vector search (Qdrant) + BM25 hybrid search để tìm đoạn code liên quan theo ngữ nghĩa.
- **Code Property Graph (CPG)**: Đồ thị AST của codebase (in-memory hoặc Neo4j persistent), lưu quan hệ giữa hàm/lớp (gọi nhau, kế thừa, import) — dùng cho Navigation, scoring, và priority exploration.
- **Post-hoc reranking**: Unified multi-signal scoring (9 tín hiệu) + tùy chọn listwise LLM rerank trên top-K.

Ba tính năng mở rộng (E1/E2/E3) đều có công tắc `.env` riêng và **mặc định TẮT** — bật/tắt không đổi schema output nên downstream (scorer, evaluator) không cần biết cấu hình nào đang chạy:

| Mã | Tên | Cơ chế | Mặc định |
|---|---|---|---|
| **E1** | Giả thuyết cạnh tranh | `ComprehensionAgent` sinh K=3-5 giả thuyết falsifiable; `VerificationAgent` thu bằng chứng; `HypothesisTracker` cập nhật belief bằng log-odds thuần Python | `ENABLE_HYPOTHESIS_LOOP=false` |
| **E2** | Khám phá hàng đợi ưu tiên | `PriorityNavigationAgent` + `core/explorer.py` thay vòng lặp tool-calling tự do bằng priority-queue scheduler; LLM chỉ chấm điểm quan sát với context O(1) | `ENABLE_PRIORITY_EXPLORATION=false` |
| **E3** | Thu hẹp phân cấp + Listwise rerank | `evaluation/reranker.py` — 1 call structured thu hẹp file→function, 1 call listwise xếp lại top-K (permutation-only) | `ENABLE_HIERARCHICAL_NARROWING=false`, `ENABLE_LISTWISE_RERANK=false` |

### Luồng xử lý tổng thể (đầy đủ, mọi flag bật)

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
│  - Test-class → source-class derivation (Java)  │
└──────────────────┬──────────────────────────────┘
                   │
       ┌───────────▼───────────────────────────────┐
       │         Orchestrator                       │
       │                                            │
       │  [Background] Build Code Property Graph    │
       │  (Neo4j nếu NEO4J_ENABLED, else in-memory) │
       │                                            │
       │  ┌─────────────────────────────────────┐  │
       │  │ Phase 1: ComprehensionAgent          │  │
       │  │ v2/v3: single-shot tool-free trước,  │  │
       │  │ verify-shot, escalate to tool loop    │  │
       │  │ nếu thiếu tự tin/unstable             │  │
       │  │ [E1] sinh K giả thuyết cạnh tranh     │  │
       │  │ → fault hypothesis + suspected files │  │
       │  └───────────────┬─────────────────────┘  │
       │                  │ + stack trace boosting  │
       │  ┌───────────────▼─────────────────────┐  │
       │  │ Phase 2: NavigationAgent             │  │
       │  │  (hoặc [E2] PriorityNavigationAgent   │  │
       │  │   nếu ENABLE_PRIORITY_EXPLORATION)    │  │
       │  │ → suspicious_locations (scored)      │  │
       │  └───────────────┬─────────────────────┘  │
       │                  │                         │
       │  ┌───────────────▼─────────────────────┐  │
       │  │ Phase 2.5 [E1]: VerificationAgent     │  │
       │  │ (bỏ qua nếu E2 bật — probe verify     │  │
       │  │  đã chạy trong explorer loop; hoặc    │  │
       │  │  fast-path stack-trace giàu+prior cao)│  │
       │  │ → cập nhật posterior giả thuyết       │  │
       │  └───────────────┬─────────────────────┘  │
       │                  │ reflection rounds (≤2)  │
       │  ┌───────────────▼─────────────────────┐  │
       │  │ Phase 3: ConfirmationAgent           │  │
       │  │  loop | single | hybrid               │  │
       │  │  (evidence pack từ explorer)          │  │
       │  │ → ranked_locations (final)           │  │
       │  └───────────────┬─────────────────────┘  │
       │                  │                         │
       │  ┌───────────────▼─────────────────────┐  │
       │  │ UnifiedScorer (10 tín hiệu)           │  │
       │  │ → multi-signal reranking             │  │
       │  └───────────────┬─────────────────────┘  │
       │  ┌───────────────▼─────────────────────┐  │
       │  │ [E3] ListwiseReranker                 │  │
       │  │ narrow file→function + rerank top-K   │  │
       │  │ (permutation-only, fail-open)         │  │
       │  └───────────────┬─────────────────────┘  │
       │  ┌───────────────▼─────────────────────┐  │
       │  │ [H1] Patch duel #1 vs #2              │  │
       │  └─────────────────────────────────────┘  │
       └───────────────────────────────────────────┘
                          │
                          ▼
         File-path validation (loại path không tồn tại;
         never-empty fallback nếu tất cả bị lọc)
                          │
                          ▼
              LocalizationResult
         (ranked files + methods + locations)
```

---

## 2. Entry Point & Cấu Hình

### `main.py`

CLI dispatcher với các subcommand:

| Command | Mục đích |
|---------|----------|
| `localize` | Localize single bug instance |
| `evaluate` | Generic evaluation via SWEBenchLoader |
| `index` | Index codebase vào Qdrant |
| `graph` | Build/query Code Property Graph |
| `defects4j` | Benchmark Defects4J (Java) |
| `swebench` | Benchmark SWE-bench (Python) |
| `bugsinpy` | Benchmark BugsInPy (Python) |

### `config.py` — Dataclasses cấu hình

```
Config (singleton)
├── LLMConfig        # provider (openai/gemini/custom), model, API key, temperature, timeout
├── EmbeddingConfig  # model (jina-embeddings-v3), local vs API, chunk size
├── RAGConfig        # Qdrant collection, top_k
├── Neo4jConfig      # URI, user, password, enabled flag
├── EvaluationConfig # dataset, output dir, top_N values
└── ScoringConfig    # weights cho 10 tín hiệu UnifiedScorer
```

### Feature flags cốt lõi (qua `.env`)

| Flag | Default | Mô tả |
|------|---------|-------|
| `ENABLE_GRAPH_RAG` | `true` | Bật Code Property Graph (Navigation/Confirmation/scorer dùng graph) |
| `NEO4J_ENABLED` | `false` | Dùng Neo4j persistent thay in-memory graph. Kết nối lỗi (`ServiceUnavailable`/`AuthError`) tự fallback về in-memory — không crash pipeline |
| `ENABLE_UNIFIED_SCORING` | `true` | Multi-signal reranking cuối pipeline |
| `ENABLE_REPO_SKELETON` | `true` | Compact repo summary trong prompt Comprehension |
| `ENABLE_STRUCTURED_BUG_EXTRACTION` | `true` | Pre-extract `bug_phenomenon`/`bug_explanation`/`bug_traceback` bằng 1 LLM call nhẹ (BugCerberus-style) |
| `MAX_AGENT_ITERATIONS` | `10` | Vòng lặp tool tối đa mỗi agent (chế độ `loop`) |
| `MAX_PARALLEL_TOOLS` | `8` | Số tool call chạy song song trong 1 turn |
| `MAX_PARALLEL_EVALS` | `4` | Số worker song song khi eval benchmark |
| `REFLECTION_MAX_ROUNDS` | `2` | Số reflection round tối đa (Navigation+Confirmation lặp lại) |
| `REFLECTION_CONF_THRESHOLD` | `0.5` | Ngưỡng confidence để trigger reflection |
| `MULTI_PASS_TEMPERATURE` | `0.3` | Temperature cho Best-of-N passes khi `LLM_TEMPERATURE=0` |
| `PER_BUG_TIMEOUT` | `300s` | Timeout mỗi bug instance (eval job) |
| `LLM_CALL_TIMEOUT` | `120s` | Timeout mỗi HTTP call tới LLM |
| `MIN_RANKED_FILES` | `10` | Pool tối thiểu — pad từ navigation/stack-trace/retriever/path-keyword nếu thiếu |

### Feature flags Comprehension v2/v3

| Flag | Default | Mô tả |
|------|---------|-------|
| `COMPREHENSION_SINGLE_SHOT` | `false` | 1 call tool-free trước; escalate sang tool loop nếu thiếu tự tin/rỗng. **Tắt mặc định**: 2 ablation 50-run cho thấy single-shot đánh đổi 5-6đ Top-1 (72-74% vs baseline 80%) |
| `COMPREHENSION_ESCALATION_CONF` | `0.5` | Ngưỡng `hypothesis_confidence` để escalate |
| `COMPREHENSION_MAX_ITERATIONS` | `4` | Số vòng tool loop tối đa khi escalate |
| `COMPREHENSION_VERIFY_SHOT` | `true` | 1 call follow-up cho single-shot xem outline top file nghi vấn, chống nhầm "nơi triệu chứng" với "nơi patch sửa"; escalate nếu top-1 đổi giữa 2 lần (unstable) |
| `COMPREHENSION_VERIFY_FILES` | `3` | Số file đưa vào verify-shot |
| `COMPREHENSION_FILE_TREE` | `true` | Đưa toàn bộ danh sách file source vào prompt (bare-LLM baseline đạt 64% Top-1 chỉ từ file tree) |
| `COMPREHENSION_FILE_TREE_MAX` | `2500` | Trần số file trong tree |

### Feature flags Confirmation v2 + H1

| Flag | Default | Mô tả |
|------|---------|-------|
| `CONFIRMATION_MODE` | `loop` | `loop` (vòng tool đầy đủ, Top-1 tốt nhất) \| `single` (1 call listwise trên evidence pack, Top-3/5 tốt nhưng Top-1 yếu) \| `hybrid` (listwise + vòng verify ngắn chỉ phân định top-3, giữ đuôi recall) |
| `CONFIRMATION_VERIFY_ITERS` | `3` | Số vòng tool cho verify stage của `hybrid` |
| `CONFIRMATION_EVIDENCE_TOP_K` | `8` | Số candidate đưa vào evidence pack |
| `CONFIRMATION_EVIDENCE_MAX_LINES` | `100` | Trần dòng excerpt mỗi candidate |
| `CONFIRMATION_MIN_EVIDENCE` | `3` | Số excerpt tối thiểu để coi evidence "đủ" (nếu không đủ → fallback tool loop cấu trúc) |

### Feature flags E1 — Giả thuyết cạnh tranh

| Flag | Default | Mô tả |
|------|---------|-------|
| `ENABLE_HYPOTHESIS_LOOP` | `false` | Bật vòng giả thuyết cạnh tranh + verification |
| `HYPOTHESIS_K` | `4` | Số giả thuyết Comprehension sinh (clamp 3-5) |
| `HYPOTHESIS_VERIFY_MAX_ITER` | `6` | Số vòng tool tối đa cho `VerificationAgent` |
| `HYPOTHESIS_FALSIFY_THRESHOLD` | `0.15` | Posterior dưới ngưỡng này → `falsified` |
| `HYPOTHESIS_FASTPATH_PRIOR` | `0.85` | Bỏ qua verification nếu có stack-trace + prior top-1 ≥ ngưỡng |
| `SCORE_WEIGHT_HYPOTHESIS` | `0.8` | Trọng số tín hiệu `hypothesis_support` trong UnifiedScorer |

### Feature flags E2 — Khám phá hàng đợi ưu tiên

| Flag | Default | Mô tả |
|------|---------|-------|
| `ENABLE_PRIORITY_EXPLORATION` | `false` | Thay `NavigationAgent` bằng `PriorityNavigationAgent` |
| `EXPLORATION_MAX_ACTIONS` | `20` | Trần số action thực thi |
| `EXPLORATION_W_LLM` / `_W_GRAPH` / `_W_SIGNAL` | `0.5` / `0.3` / `0.2` | Trọng số công thức `priority = w_llm×relevance/10 + w_graph×1/(1+hops) + w_signal×static_prior` |
| `EXPLORATION_W_GRAPH_JAVA` | `0.15` | Trọng số graph riêng cho Java (CPG regex nhiễu hơn AST Python) |
| `EXPLORATION_MIN_PRIORITY` | `0.15` | Dừng khi priority cao nhất trong frontier dưới ngưỡng |
| `EXPLORATION_MAX_DEPTH` | `4` | Độ sâu tối đa lan tỏa offspring action |
| `EXPLORATION_FALLBACK_TO_FREEFORM` | `true` | Chạy `NavigationAgent` thường nếu explorer tìm được < 3 finding relevance ≥ 5 |

### Feature flags E3 — Thu hẹp phân cấp + Listwise rerank

| Flag | Default | Mô tả |
|------|---------|-------|
| `ENABLE_HIERARCHICAL_NARROWING` | `false` | 1 call structured: mỗi file top-K → ≤2 hàm nghi vấn + line range |
| `ENABLE_LISTWISE_RERANK` | `false` | 1 call listwise xếp lại top-K trên evidence card (permutation-only) |
| `LISTWISE_RERANK_TOP_K` | `10` | Số file được rerank |
| `LISTWISE_RERANK_PASSES` | `1` | >1 → nhiều pass shuffle khác nhau, hợp nhất bằng RRF (đo position bias dư) |

### `ScoringConfig` — 9 tín hiệu UnifiedScorer

| Weight | Default | Tín hiệu |
|---|---|---|
| `SCORE_WEIGHT_LLM` | 1.0 | LLM confidence (từ Confirmation) |
| `SCORE_WEIGHT_STACK_TRACE` | 2.5 | Vị trí trong stack trace (decay 0.85^i) |
| `SCORE_WEIGHT_ERROR` | 1.5 | Error message khớp trong file |
| `SCORE_WEIGHT_MENTIONED` | 1.2 | File được nhắc trực tiếp trong bug report |
| `SCORE_WEIGHT_GRAPH` | 0.8 | Graph RAG proximity |
| `SCORE_WEIGHT_SEMANTIC` | 0.6 | Semantic similarity (Qdrant) |
| `SCORE_WEIGHT_METHOD` | 0.3 | Method aggregation (nhiều method cùng file) |
| `SCORE_WEIGHT_GIT_RECENCY` | 0.5 | Recency commit (half-life 90 ngày) |
| `SCORE_WEIGHT_HYPOTHESIS` | 0.8 | **[E1]** Posterior giả thuyết còn sống nêu file này; **âm** nếu file chỉ thuộc giả thuyết đã bác bỏ |
| `SCORE_TEST_PENALTY` | 0.5 | Phạt file test |

---

## 3. Multi-Agent System

### 3.1 `agents/base_agent.py` — Nền tảng chung

#### `AgentContext` — Trạng thái chia sẻ giữa agents

```python
@dataclass
class AgentContext:
    # Input
    instance_id: str
    repo_id: str                           # dùng để phân vùng Qdrant/Neo4j
    problem_statement: str
    repo_path: str

    # Preprocessed
    error_messages: list[str]
    stack_traces: list
    mentioned_files: list[str]
    mentioned_functions: list[str]
    keywords: list[str]
    log_parse_result: LogParseResult | None
    structured_bug_info: dict              # pre-extracted bằng LLM

    # Giữa các agent
    fault_hypothesis: str                  # luôn = statement của hypothesis top-prior (E1 on/off đều đúng)
    candidate_files: list[str]             # tích lũy qua các phase
    candidate_methods: list[str]
    suspicious_locations: list[dict]       # output có cấu trúc của Navigation — bằng chứng cho Confirmation single-shot
    navigation_was_freeform: bool          # True nếu Navigation dùng tool loop tự do (line range kém tin cậy hơn)
    repo_skeleton: str
    reflection_feedback: str

    # RAG
    retriever: CodeRetriever | None
    graph_retriever: GraphRetriever | None

    # [E1] Giả thuyết cạnh tranh
    hypotheses: list[Hypothesis]           # agents.hypothesis.Hypothesis
    hypothesis_tracker: HypothesisTracker | None

    # Control
    temperature_override: float | None
    stack_trace_files: list[str]
    source_root: str                       # "source" (Ant) / "src/main/java" (Maven) / "src"
    test_derived_candidates: list[str]     # suy từ tên test class Java (WeekTests → Week.java)
    _verify_stage_message: str | None      # payload transient cho Confirmation hybrid verify-stage
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
           │                    (OpenAI / Gemini / custom endpoint)
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

**Parallel tool execution**: Nhiều tool calls trong một turn chạy song song bằng `ThreadPoolExecutor(max_workers=8)`.

**Message trimming**: Sliding-window để tránh vượt context limit — giữ system prompt + N recent messages.

**Output capping**: Kết quả tool bị truncate để tránh prompt token tăng bậc hai.

Nhiều agent mới (`PriorityNavigationAgent`, `ListwiseReranker`) chủ động **không** dùng vòng lặp này cho các call phụ trợ — chúng gọi `_call_llm(messages, context, use_tools=False)` trực tiếp với context O(1) (không mang message history) để giữ chi phí phẳng, chỉ `BaseAgent.run()` mới dùng khi thật sự cần vòng lặp tool đầy đủ.

---

### 3.2 `agents/comprehension.py` — Comprehension Agent (v2/v3)

**Vai trò**: Đọc bug report, sinh fault hypothesis, xác định file/hàm nghi ngờ ban đầu.

**Quy trình `run()`** (`COMPREHENSION_SINGLE_SHOT=false` — cấu hình mặc định hiện tại):
1. `_extract_structured_bug_info()` — 1 LLM call nhẹ trích xuất `bug_phenomenon`/`bug_explanation`/`bug_traceback` (nếu `ENABLE_STRUCTURED_BUG_EXTRACTION`).
2. Chạy `BaseAgent.run()` — vòng lặp tool đầy đủ với `code_search`, `read_file`, `list_directory`, `parse_logs`, `search_tests`. Prompt hướng dẫn dùng tool **sparingly** (fast triage, không explore sâu — Navigation sẽ làm việc đó).

**Khi `COMPREHENSION_SINGLE_SHOT=true`** (v2, tắt mặc định — ablation cho thấy kém hơn):
1. `_run_single_shot()` — 1 call tool-free với bug report + file tree đầy đủ (`build_repo_file_tree`).
2. `_verify_shot()` — nếu `COMPREHENSION_VERIFY_SHOT`, 1 call follow-up hiển thị outline (head + `def`/`class` list) của top file nghi vấn, nhắc mô hình phân biệt "nơi triệu chứng" và "nơi patch sửa". Nếu top-1 đổi giữa 2 lần → coi là *unstable* → escalate.
3. `_needs_escalation()` — escalate sang tool loop đầy đủ nếu: không có `suspicious_files`, hoặc `hypothesis_confidence < COMPREHENSION_ESCALATION_CONF`, hoặc unstable ở bước 2.

**[E1] `HYPOTHESES_PROMPT_ADDON`** — khi `ENABLE_HYPOTHESIS_LOOP`, system prompt được nối thêm yêu cầu sinh mảng `hypotheses`: K giả thuyết cạnh tranh, mỗi giả thuyết có `statement` (nêu cơ chế, không nêu triệu chứng), `suspected_component/files/functions`, `causal_chain`, và `probes` (≥1 phải DISCONFIRMING), `prior`. `_process_hypotheses()` parse mảng này thành `context.hypotheses` + khởi tạo `HypothesisTracker`, và hợp nhất file của các giả thuyết (ưu tiên theo prior) vào `candidate_files`.

**Output JSON** (schema cơ bản, cộng thêm `hypotheses` nếu E1 bật):
```json
{
  "bug_summary": "Mô tả ngắn về bug",
  "bug_type": "logic_error | runtime_error | api_misuse | ...",
  "key_components": ["ComponentA", "ComponentB"],
  "suspicious_files": ["src/foo.py", "src/bar.java"],
  "suspicious_functions": ["methodA", "methodB"],
  "fault_hypothesis": "Chi tiết giả thuyết về nguyên nhân bug...",
  "search_keywords": ["keyword1", "keyword2"],
  "hypothesis_confidence": 0.85,
  "hypotheses": [ { "hid": "HYP1", "statement": "...", "suspected_files": [...], "probes": [...], "prior": 0.6 } ]
}
```

---

### 3.3 `agents/navigation.py` — Navigation Agent

**Vai trò**: Khám phá codebase một cách có hệ thống, tìm vị trí nghi ngờ cụ thể.

**Chiến lược (theo system prompt)**:
1. Dùng `semantic_file_search` trước — nhanh, file-level lookup.
2. Tìm kiếm tên class/method chính xác từ bug report.
3. Tìm error message dưới dạng literal string.
4. Dùng Graph RAG (callers/callees/inheritance) để mở rộng ngữ cảnh.
5. **[E1]** Nếu `context.hypotheses` có dữ liệu, system prompt liệt kê toàn bộ tập giả thuyết cạnh tranh ("Explore ALL suspected areas") — không chỉ giả thuyết top-1.
6. Luôn output đường dẫn đầy đủ từ gốc repo.

**Tools**: `code_search`, `read_file`, `list_directory`, `get_file_outline`, `get_function_source`, `semantic_file_search`, `semantic_search`, `git_log`, `graph_search`, `find_callers`, `find_callees`.

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

#### 3.3b `agents/priority_navigation.py` + `core/explorer.py` — [E2] Priority-guided Navigation

**Quyết định kiến trúc**: scheduler tất định chọn action, LLM chỉ chấm điểm.

`core/explorer.py` cung cấp engine agnostic-với-LLM/tool:
- `ExplorationAction`: `(priority, kind, target, origin, depth)` — `kind ∈ {inspect_file, inspect_function, expand_callers, expand_callees, run_search}`.
- `ExplorationFrontier`: heap ưu tiên + visited-set + dedupe theo best-priority (một action được đẩy lại với priority cao hơn sẽ ghi đè bản cũ trong heap).
- `PriorityExplorer.run()`: vòng lặp pop-execute-observe, dừng khi: `max_actions` (mặc định 20) / frontier rỗng / top priority < `min_priority` / 5 quan sát liên tiếp relevance < 3 (stagnation) / ≥8 finding relevance ≥ 8 (early success).

Công thức priority:
```
priority(target) = W_LLM × llm_relevance(parent)/10
                  + W_GRAPH × 1/(1 + graph_distance(target, anchors))
                  + W_SIGNAL × static_prior(target)
```
`static_prior` theo nguồn gốc: stack-trace 1.0 · mentioned/test-derived 0.7 · file giả thuyết E1 0.6×posterior · path-keyword 0.4 · mặc định 0.2.

`PriorityNavigationAgent` wiring:
1. `_build_explorer()` — tính `graph_distances` bằng `GraphRetriever.file_hop_distances()` từ tập anchor (stack-trace + mentioned + file giả thuyết + anchor node từ `find_anchor_nodes(fault_hypothesis)`); tính `static_priors` từ các nguồn trên.
2. `_seed_explorer()` — seed action ban đầu từ stack-trace/test-derived/mentioned/candidate files + probe của mỗi giả thuyết E1 + sub-query phân rã bug report (dùng chính giả thuyết nếu có, tránh tốn thêm 1 call).
3. Mỗi action được `_execute_action()` chạy qua tool registry sẵn có (đọc file/outline/callers/callees/search), rồi `_observe_action()` gọi 1 LLM call **tool-free, context O(1)** (~2-3k token, KHÔNG mang message history — đây là distance-aware context pruning) để chấm relevance 0-10, đề xuất `new_entities`/`new_queries`, và (nếu E1 bật) gắn nhãn `hypothesis_evidence` feed ngược `HypothesisTracker` — khi cả E1+E2 bật, Orchestrator **không** chạy `VerificationAgent` riêng vì việc verify đã lồng trong vòng explorer.
4. `_findings_to_locations()` lắp `suspicious_locations` bằng Python thuần (không parse JSON tổng hợp cuối) ⇒ lớp lỗi "miss_empty"/JSON-parse-failure bất khả thi ở Phase 2. Schema output giống hệt `NavigationAgent` nên Confirmation/pool/scorer không cần thay đổi.
5. **Lưới an toàn**: nếu explorer tìm được < 3 finding relevance ≥ 5, hoặc explorer crash, tự động fallback chạy `NavigationAgent.run()` (tool loop tự do) và gộp kết quả — `context.navigation_was_freeform = True` để Confirmation biết line-range kém tin cậy hơn.
6. Visited-set (`context.exploration_visited`) được giữ qua các reflection round để không lặp lại việc đã làm.

---

### 3.4 `agents/confirmation.py` — Confirmation Agent (v2 hybrid)

**Vai trò**: Xem xét lại danh sách ứng viên, xếp hạng cuối cùng theo confidence.

**3 chế độ (`CONFIRMATION_MODE`)**:
- **`loop`** (mặc định) — vòng tool đầy đủ với `code_search`, `read_file`, `semantic_search`, `find_callers`, `find_callees`. Quy tắc: HIGH (0.8+) chỉ khi bằng chứng rõ ràng; MEDIUM (0.5-0.79) nghi ngờ nhưng chưa chắc; file trong stack trace ưu tiên HIGH.
- **`single`** — `_build_evidence_pack()` gộp `suspicious_locations` (đã có excerpt từ Navigation/Explorer) + candidate files chưa được chấm, đính kèm excerpt nguồn (line-range → AST function source → file head) và marker `[IN STACK TRACE]`/`[MENTIONED IN REPORT]`. 1 call listwise `_run_single_shot()` xếp hạng toàn bộ candidate cùng lúc, không dùng tool. Top-3/5 tốt nhưng Top-1 yếu hơn `loop`.
- **`hybrid`** — chạy `single` trước, rồi `_verify_top()`: vòng tool ngắn (`CONFIRMATION_VERIFY_ITERS`) chỉ phân định rank-1 giữa top-3 của listwise (payload truyền qua `context._verify_stage_message` vì agent instance chia sẻ giữa các worker thread benchmark, còn context là per-instance); phần đuôi ranking giữ nguyên (bảo toàn recall).

**Escalation về `loop` là cấu trúc, không dựa self-reported confidence**: khi evidence pack quá mỏng (`with_excerpts < CONFIRMATION_MIN_EVIDENCE` và không có candidate score ≥ 0.8) hoặc `context.navigation_was_freeform=True`.

**`get_reflection_message()`**: khi confidence thấp, sinh guidance cho reflection round tiếp theo (mở rộng phạm vi tìm kiếm, ưu tiên stack-trace, tìm theo error message literal). **[E1]** Nếu có `hypothesis_tracker`, `Orchestrator` ưu tiên dùng `tracker.reflection_summary()` (chỉ đích danh giả thuyết nào đã bị bác bỏ + giả thuyết nào còn probe chưa kiểm) thay cho message chung chung.

---

### 3.5 [E1] `agents/hypothesis.py` + `agents/verification.py`

**`agents/hypothesis.py`** — mọi phép toán belief là Python thuần, LLM chỉ đề xuất giả thuyết và gắn nhãn bằng chứng:

- `Hypothesis`: `hid`, `statement`, `suspected_component/files/functions`, `causal_chain`, `probes: list[EvidenceProbe]`, `prior` → `log_odds = ln(p/(1-p))` clamp ±2.0, `posterior` là property tính từ `log_odds`.
- `HypothesisTracker.update(hid, evidence)`: `log_odds += direction × LLR`, với `LLR = {weak: 0.25, moderate: 0.6, strong: 1.1}`. `posterior < 0.15` → `falsified`; `> 0.75` → `supported`; còn lại `active`.
- `file_scores()`: tín hiệu cho UnifiedScorer — posterior lớn nhất trong các giả thuyết còn sống nêu file đó; file **chỉ** thuộc giả thuyết đã bác bỏ nhận điểm **âm** (`FALSIFIED_FILE_SCORE = -0.4`) — cơ chế duy nhất có thể giáng hạng ứng viên mà LLM tự tin sai.
- `reflection_summary()`: liệt kê giả thuyết đã bác bỏ (kèm bằng chứng chống mạnh nhất) + giả thuyết còn sống (posterior, probe chưa kiểm, file nghi vấn) — dùng làm `context.reflection_feedback`.
- `parse_hypotheses_from_llm()`: parse khoan dung mảng `hypotheses` từ Comprehension; dedupe theo `suspected_component`; nếu parse thất bại, bọc `fault_hypothesis` cũ thành 1 giả thuyết duy nhất (degrade về hành vi trước khi có E1).

**`agents/verification.py`** — `VerificationAgent` chạy sau Navigation round 1, trước Confirmation (Orchestrator gọi ở "Phase 2.5"). Tools: `read_file`, `get_function_source`, `code_search`, `find_callers`, `find_callees`. Prompt yêu cầu tìm bằng chứng CHỐNG với nỗ lực ngang bằng chứng ỦNG HỘ cho từng giả thuyết ("a hypothesis you falsify is as valuable as one you support"). `process_result()` áp từng evidence item vào `HypothesisTracker.update()` và hấp thụ `new_suspect_files` phát hiện thêm (additive, không thay thế).

**Fast path & guard**: bỏ qua verification khi có stack-trace files và `top_prior ≥ HYPOTHESIS_FASTPATH_PRIOR` (0.85) — bug giàu traceback không cần giả thuyết cạnh tranh, kiểm soát token. Khi `ENABLE_PRIORITY_EXPLORATION` cũng bật, `VerificationAgent` **không chạy riêng** — việc verify đã lồng vào observation loop của E2 (mục 3.3b).

---

### 3.6 `agents/orchestrator.py` — Điều phối Pipeline

**`localize()` — Single-pass localization**:

```
Phase 0:  BugReportPreprocessor.process()
          → AgentContext khởi tạo đầy đủ
          → Java: phát hiện source_root (Ant "source/" vs Maven "src/main/java")
          → Java: suy candidate files từ tên test class (WeekTests → Week.java)

Phase 0   [background thread]:
(parallel) get_or_build_graph() (Neo4j nếu NEO4J_ENABLED, else in-memory)
           + cache theo key (repo_name, git_HEAD, language)

Phase 1:  ComprehensionAgent.run(context)   — xem §3.2
          → context.fault_hypothesis, candidate_files cập nhật
          → Stack trace file boosting (đẩy file trong stack trace lên đầu)

          Chờ background graph thread (timeout 300s, fail-open nếu lỗi)
          → context.graph_retriever = graph

Vòng lặp reflection [round 0..REFLECTION_MAX_ROUNDS]:
  Phase 2:   NavigationAgent / PriorityNavigationAgent.run(context)  — §3.3/§3.3b

  Phase 2.5: (chỉ round 0) _run_verification_stage()                — §3.5
             [E1] nếu bật + không có E2 + không fast-path

  Phase 3:   ConfirmationAgent.run(context)  — §3.4
             if top1_confidence ≥ threshold hoặc round cuối: break
             else: reflection_feedback = hyp_summary (E1) hoặc generic message; lặp lại

Phase 4:  UnifiedScorer (nếu ENABLE_UNIFIED_SCORING) — 9 tín hiệu, §6
          → result.agent_results["unified_scores"] lưu breakdown per-file
            (dùng lại bởi evidence card của E3 + phân tích miss offline)

Phase 5:  [E3] ListwiseReranker.rerank() — narrow + rerank top-K, permutation-only

Phase 6:  _filter_nonexistent_files() — loại path ảo do LLM hallucinate;
          thử biến thể prefix (source root, top-level dir, __init__.py);
          KHÔNG BAO GIỜ trả prediction rỗng — nếu mọi path bị lọc, giữ
          nguyên bản gốc chưa validate (một dự đoán sai vẫn hơn không có gì)

Output:   LocalizationResult
          → ranked_files, ranked_methods, ranked_locations
          → explanation, root_cause, token/LLM call stats theo từng agent
```

**`multi_pass_localize()` — Best-of-N với Reciprocal Rank Fusion**:
- Chạy `localize()` N lần (mặc định temperature `MULTI_PASS_TEMPERATURE` nếu base temperature = 0).
- File/method score = Σ `1/(k + rank_i)` trên tất cả N run (`utils/ranking.py::reciprocal_rank_fusion`).
- `ranked_locations` được re-align theo thứ tự file đã fuse.

**`_build_candidate_pool()`** — hợp nhất mọi nguồn ứng viên theo thứ tự ưu tiên: confirmed locations → **[E1]** file giả thuyết còn sống theo posterior → navigation candidates → stack-trace files → mentioned files → test-derived candidates → (chỉ khi pool < `MIN_RANKED_FILES`) semantic retriever → graph retriever → path-keyword matching (fallback thuần filesystem, không cần index/LLM).

**Graph caching**: `Orchestrator._graph_cache` thread-safe theo `(repo_name, git_HEAD, language)` — instance khác nhau cùng `base_commit` tái dùng graph, tránh rebuild.

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
    ├─► search()
    ├─► find_anchor_nodes(query, top_k)         # [E2] anchor lookup công khai
    ├─► file_hop_distances(seed_files, max_hops) # [E2] multi-source BFS, dùng cho priority
    └─► hop_distance(file_a, file_b, max_hops)   # [E2] khoảng cách cặp, LRU cache 2048 entry
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
- **Local mode**: `sentence-transformers` (jina-embeddings-v3).
- **API mode**: OpenAI-compatible embedding endpoint.
- In-memory embedding cache để tránh re-embed.

### `rag/indexer.py` — Codebase Indexer

- Walk directory tree, skip test/build directories.
- Chunk mỗi file theo ngôn ngữ phát hiện tự động.
- Tạo UUID5 deterministic IDs — tránh duplicate khi re-index.
- Upsert vào Qdrant với metadata: `repo_id`, `package`, `language`, `chunk_type`.
- Hỗ trợ `--clear` để xóa index trước khi build lại.

### `rag/retriever.py` — Semantic Retrieval

**`CodeRetriever.query()`** — hỗ trợ filters:

| Filter | Loại match | Mô tả |
|--------|-----------|-------|
| `repo_filter` | Exact | Isolate theo project |
| `package_filter` | Substring | Java package |
| `language_filter` | Exact | `python` / `java` |
| `chunk_type_filter` | Exact | `function` / `class` / `file_summary` |
| `file_filter` | Substring | Lọc theo path |

**Hybrid search** (`hybrid_get_similar_files()`): (1) BM25 full-text file-level, (2) vector search file summaries, (3) Reciprocal Rank Fusion kết hợp.

### `rag/code_graph.py` — Code Property Graph (in-memory)

**Graph schema**:

```
GraphNode: id, name, node_type ("file"|"class"|"function"|"method"),
           file_path, start_line, end_line, signature, docstring
GraphEdge: source_id, target_id,
           edge_type ("CALLS"|"IMPORTS"|"INHERITS"|"CONTAINS"|"SAME_FILE"), metadata
```

**Builders**: `PythonGraphBuilder` (module `ast`), `JavaGraphBuilder` (regex).

**`get_or_build_graph(..., use_neo4j, repo_id)`**: khi `use_neo4j=True`, kiểm tra partition Neo4j đã có dữ liệu cho `repo_id` chưa (`stats()["total_nodes"] > 0`) — nếu có thì tái sử dụng, nếu không thì build in-memory rồi `import_from_in_memory()` vào Neo4j.

### `rag/graph_retriever.py` — Graph RAG

**`GraphRetriever.search(query)`**: (1) vector search → N anchor node ngữ nghĩa gần nhất, (2) BFS expansion từ mỗi anchor (callers/callees/siblings/inheritance), (3) score = `anchor_score × decay^depth`, (4) dedup và rank tổng hợp.

**API mới cho E2** (`_prepare_indexes()` xây `_file_node_index` lazy, `_get_file_node_index()`/`_nodes_for_file()` chịu lệch prefix/suffix path):
- `find_anchor_nodes(query, top_k)`: wrapper công khai qua `_find_anchors()`.
- `file_hop_distances(seed_files, max_hops)`: multi-source BFS, trả `{file: min_hop}` — file không tới được thì vắng mặt (caller coi là `max_hops+1`).
- `hop_distance(file_a, file_b, max_hops)`: khoảng cách 1 cặp, có LRU cache (`OrderedDict`, trần 2048 entry); graph rỗng/không tới được → trả `max_hops+1` (giá trị trung tính, priority không bị NaN/crash).

### `rag/neo4j_backend.py` — Neo4j Backend (nay là backend mặc định khi `NEO4J_ENABLED=true`)

- `Neo4jGraph.__init__` bắt `ServiceUnavailable`/`AuthError` từ driver `neo4j` và raise lại thành `ConnectionError` chuẩn — `Orchestrator._build_graph()` bắt đúng exception này để fallback về in-memory, nên Neo4j không reachable **không** làm crash pipeline, chỉ mất phần cache liên-lần-chạy.
- Phân vùng theo `repo_id` (không phải toàn bộ DB) — tránh nhiễm chéo giữa các project khi chạy benchmark nhiều repo.
- Cùng interface với `CodePropertyGraph` nhưng lưu persistent, truy vấn Cypher.
- Vận hành cục bộ: `docker run -d --name bug-loc-neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password -v bug-loc-neo4j-data:/data neo4j:5-community` (khớp `NEO4J_USER`/`NEO4J_PASSWORD` mặc định trong `.env`).

### `rag/bm25_index.py` — BM25 Index

Full-text search fallback khi vector search không đủ; dùng trong hybrid search qua RRF.

---

## 5. Tools System (`tools/`)

### `tools/registry.py` — Central Registry

```python
TOOL_REGISTRY: dict[str, tuple[Callable, dict]] = {
    "tool_name": (function, openai_function_schema),
    ...
}
```

Tất cả agent đăng ký tool qua registry — định nghĩa một lần, dùng nhiều nơi. Schema theo format OpenAI function-calling.

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
| `repo_skeleton` | `repo_skeleton.py` | Compact repo summary ưu tiên theo hints (`generate_repo_skeleton`) |
| `parse_logs` | `log_parser.py` | Drain3 log template analysis |
| `git_log` | `git_history.py` | Lịch sử commit gần đây |

`tools/repo_skeleton.py` còn có `skeleton_for_files(repo_path, files, language)` (không đăng ký như tool — gọi trực tiếp) — skeleton **cho một danh sách file cụ thể** (không phải walk toàn repo), dùng bởi `evaluation/reranker.py` giai đoạn thu hẹp phân cấp E3.

### `tools/cache.py` — LRU Cache

`read_file_cached()`, `parse_ast_cached()` — cache chia sẻ giữa tất cả tools, tránh re-read disk trong cùng session.

---

## 6. Evaluation System (`evaluation/`)

### `evaluation/metrics.py`

```python
top_n_accuracy(predictions, ground_truth, n)     # Acc@N, file & method level
reciprocal_rank(predictions, ground_truth)        # 1/rank của kết quả đúng đầu tiên
average_precision(predictions, ground_truth)      # AP
extract_methods_from_locations(ranked_locations)  # ranked_locations → method id list
# _paths_match(): flexible path matching, xử lý source root variants
```

### `evaluation/unified_scorer.py` — Multi-signal Reranking (9 tín hiệu)

Xem bảng đầy đủ ở §2 (`ScoringConfig`). `CandidateScore` giờ có thêm `hypothesis_score`, `consensus_score`; `UnifiedScorer.score_candidates()` nhận thêm `hypothesis_scores`/`consensus_scores` dict.

### `evaluation/reranker.py` — **[mới, E3]** `ListwiseReranker`

Hai giai đoạn, chạy sau `_apply_unified_scoring`, **chỉ hoán vị top-K** (không bao giờ đổi tập hợp file) ⇒ Top-10/recall bất biến theo cấu trúc:

- **Stage (a) narrowing** (`narrow()` + `apply_narrowing()`): 1 call structured trên skeleton (`skeleton_for_files`) của top-K → mỗi file ≤2 hàm nghi vấn + line range. Điền `function_name`/`start_line`/`end_line` cho location chưa có (additive, cải thiện luôn metric method-level); cắt snippet ≤15 dòng cho stage (b).
- **Stage (b) listwise rerank** (`rerank_cards()`): 1 call temperature 0, không tool, trên **evidence card** (`build_evidence_cards()`) lắp bằng Python thuần từ `CandidateScore` (cờ tín hiệu STACK_TRACE/ERROR_MATCH/graph/semantic/recency/test-file — **không lộ điểm tổng hay rank hiện tại**) + verdict/confidence của agent + snippet đã thu hẹp. Chống position bias: ID trung tính `C1..CK`, thứ tự trình bày shuffle **deterministic theo SHA-256(instance_id)** (`deterministic_shuffle`) — tái lập được nhưng khử tương quan với thứ hạng gốc. `LISTWISE_RERANK_PASSES > 1` → nhiều pass shuffle khác nhau, hợp nhất bằng RRF (đo position bias dư).
- **Validate**: `validate_permutation()` coi output không hợp lệ (thiếu ID, ID lạ) → nối phần thiếu theo thứ tự cũ; nếu `set(new_order) != set(top_files)` → bỏ qua toàn bộ, giữ nguyên (fail-open tuyệt đối).
- Card đã serialize vào `result.agent_results["listwise_rerank"]["cards"]` ⇒ có thể **replay offline để iterate prompt rerank mà không tốn lượt gọi agent thật**.

### `evaluation/evaluator.py`

`BenchmarkEvaluator.evaluate()` — chạy pipeline trên toàn bộ dataset, sequential hoặc parallel (configurable workers); per-instance tracking (predictions, ground truth, metrics); aggregate stats (mean Top-N, MRR, MAP).

### `evaluation/export.py`

Export kết quả ra CSV/JSON, Markdown table summary.

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

Jobs chạy async trong background, tracking qua `jobs: dict[str, JobState]`. Layer này không đổi so với trước — mọi tính năng E1/E2/E3 vẫn xuyên qua `Orchestrator.localize()` như cũ, không cần API riêng.

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
    ├─ Extract error messages: Exception names, assertion errors, custom patterns
    ├─ Extract mentioned files/functions: Regex match file.py/ClassName/methodName
    ├─ Drain3 log analysis (nếu enabled): search_terms, assertion_pairs, log_templates
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

## 9. Commands (`commands/`) & Scripts vận hành (`scripts/`)

### `commands/` — dùng qua `main.py`

| File | CLI | Chức năng chính |
|------|-----|----------------|
| `localize.py` | `localize --bug-report --repo-path` | Single bug, hỗ trợ `--multi-pass`, `--no-graph-rag` |
| `index.py` | `index --repo-path` | Index vào Qdrant, hỗ trợ `--clear` |
| `graph.py` | `graph --repo-path` | Build CPG, query `--callers`, `--callees`, `--stats`, `--visualize` |
| `defects4j.py` | `defects4j --project --limit` | Batch eval với `--workers`, `--list-bugs` |
| `swebench.py` | `swebench --split --limit` | Batch eval SWE-bench, `--dry-run` |
| `evaluate.py` | `evaluate --limit` | Generic evaluation via SWEBenchLoader |
| `_shared.py` | — | `make_orchestrator()`, `process_bug()` với timeout |

### `scripts/` — chạy trực tiếp bằng `python scripts/...`, không qua `main.py`

| File | Mục đích |
|------|----------|
| `run_swebench_benchmark.py` | Driver benchmark SWE-bench đầy đủ (dùng bởi các command trên); hỗ trợ `--instance-ids-file` để chạy đúng 1 tập instance cố định, lưu `agent_results` per-instance (trừ `unified_scores` — quá cồng kềnh) |
| `bare_llm_baseline.py` | **[mới]** Baseline KHÔNG agent/RAG/tool/graph — đúng 1 LLM call (bug report + file tree) → ranked files. Control row cho nghiên cứu contamination/memorization cross-model |
| `run_cross_model.py` | **[mới]** Harness so sánh nhiều model, mỗi model chạy cả `full` (hệ thống đầy đủ) và `bare` (baseline trên), tính delta(full − bare) — đại lượng sống sót qua hiệu ứng contamination |
| `run_swebench_lite_full.py` | **[mới]** Driver chạy full SWE-bench Lite (300 bug) theo batch để giới hạn dung lượng đĩa checkout — checkout → chạy → xóa checkout batch → merge kết quả cuối |
| `checkout_swebench.py` / `checkout_d4j_manual.py` | Checkout benchmark repo về `data/*_checkouts/` |
| `prebuild_graphs.py` | Build sẵn Code Property Graph cho một tập repo (tránh build lại mỗi lần benchmark) |
| `analyze_misses.py` | Phân tích off-line các bug bị miss từ kết quả JSON đã chạy |
| `run_ablation_lang.py` | Chạy ma trận ablation theo ngôn ngữ (Java vs Python) |
| `run_full_defects4j.sh` | Shell driver benchmark Defects4J đầy đủ |

---

## 10. Sơ Đồ Dependency Giữa Các Module

```
main.py
    └── commands/
            ├── localize.py ─────────────────────────────┐
            ├── defects4j.py ── _shared.py ──────────────┤
            └── swebench.py  ──     │                    │
                                    ▼                    ▼
                             agents/orchestrator.py
                                    │
        ┌───────────────┬──────────┼───────────┬────────────────┐
        ▼               ▼          ▼           ▼                ▼
 comprehension.py  navigation.py confirmation.py verification.py priority_navigation.py
        │           (+hypothesis.py)   │        (hypothesis.py)   │
        └───────────────┴──────────────┴───────────────┬─────────┘
                                                          ▼
                                                   base_agent.py
                                     priority_navigation.py ──► core/explorer.py (LLM/tool-agnostic)
                                                          │
                              ┌───────────────────────────┤
                              ▼                           ▼
                    tools/registry.py            rag/retriever.py
                              │                   rag/graph_retriever.py
                    ┌─────────┼──────────┐               │
                    ▼         ▼          ▼                ▼
              code_search  semantic  graph_search  rag/code_graph.py
              file_reader  _search   ast_parser    rag/neo4j_backend.py
              log_parser   git_hist  repo_skeleton rag/indexer.py
                                     (+skeleton_for_files) rag/embedder.py
                                                          │
                                                          ▼
                                                       Qdrant / Neo4j

evaluation/
    ├── evaluator.py ──────► orchestrator.py
    ├── metrics.py
    ├── unified_scorer.py ──uses──► hypothesis.py (file_scores)
    ├── reranker.py        (E3, gọi từ orchestrator._maybe_rerank) ──uses──► repo_skeleton.skeleton_for_files
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
├── main.py                          # CLI entry point
├── config.py                        # Cấu hình toàn hệ thống (dataclasses + .env, ~50 flag)
├── requirements.txt
├── .env.example
│
├── core/                            # [mới] Engine agnostic-với-LLM/tool, unit-testable riêng
│   └── explorer.py                  # [E2] PriorityExplorer, ExplorationFrontier, ExplorationAction
│
├── agents/
│   ├── base_agent.py                # AgentContext, BaseAgent, AgentResult
│   ├── orchestrator.py              # Pipeline orchestration, multi-pass, reflection, E1/E2/E3 hooks
│   ├── comprehension.py             # Phase 1: v2/v3 single-shot+verify-shot+escalation, hypotheses (E1)
│   ├── navigation.py                # Phase 2: Codebase exploration (tool loop tự do)
│   ├── priority_navigation.py       # [mới, E2] PriorityNavigationAgent
│   ├── confirmation.py              # Phase 3: loop/single/hybrid
│   ├── hypothesis.py                # [mới, E1] Hypothesis, HypothesisTracker (log-odds thuần Python)
│   └── verification.py              # [mới, E1] VerificationAgent
│
├── rag/
│   ├── embedder.py                  # CodeChunk, CodeEmbedder (local + API)
│   ├── indexer.py                   # CodebaseIndexer → Qdrant
│   ├── retriever.py                 # CodeRetriever (semantic + hybrid BM25)
│   ├── code_graph.py                # CodePropertyGraph in-memory (Python/Java) + get_or_build_graph
│   ├── neo4j_backend.py             # Neo4j persistent backend
│   ├── graph_retriever.py           # GraphRetriever (anchor+BFS, + find_anchor_nodes/hop_distance cho E2)
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
│   ├── repo_skeleton.py             # Compact repo summary + skeleton_for_files (E3)
│   ├── log_parser.py                # Drain3 log template analysis
│   └── git_history.py               # Git log queries
│
├── data/
│   ├── loader.py                    # BugInstance, SWEBenchLoader
│   ├── preprocessor.py              # BugReportPreprocessor
│   └── defects4j_loader.py          # Defects4J benchmark loader
│
├── evaluation/
│   ├── evaluator.py                 # BenchmarkEvaluator (sequential + parallel)
│   ├── metrics.py                   # Top-N, MRR, MAP, file/method level
│   ├── unified_scorer.py            # Multi-signal reranking — 9 tín hiệu
│   ├── reranker.py                  # [mới, E3] ListwiseReranker (narrowing + rerank)
│   └── export.py                    # CSV/JSON/Markdown export
│
├── commands/
│   ├── _shared.py                   # make_orchestrator(), process_bug() + timeout
│   ├── localize.py                  # Single bug localization
│   ├── index.py                     # Codebase indexing
│   ├── graph.py                     # CPG build + query CLI
│   ├── defects4j.py                 # Defects4J batch evaluation
│   ├── swebench.py                  # SWE-bench batch evaluation
│   └── evaluate.py                  # Generic evaluation
│
├── scripts/                         # Chạy trực tiếp, không qua main.py — xem §9
│   ├── run_swebench_benchmark.py
│   ├── bare_llm_baseline.py         # [mới]
│   ├── run_cross_model.py           # [mới]
│   ├── run_swebench_lite_full.py    # [mới]
│   ├── checkout_swebench.py / checkout_d4j_manual.py
│   ├── prebuild_graphs.py / analyze_misses.py / run_ablation_lang.py
│   └── run_full_defects4j.sh
│
├── api/
│   ├── main.py                      # FastAPI app + CORS + routers
│   └── routes/
│       ├── localize.py              # POST /api/localize (async jobs)
│       ├── evaluate.py              # POST /api/evaluate
│       ├── graph.py                 # GET/POST /api/graph
│       └── benchmarks.py            # GET /api/benchmarks
│
├── tests/                           # pytest — unit test cho logic thuần Python (tracker/frontier/RRF/rerank)
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

**Path matching** linh hoạt qua `_paths_match()` — xử lý các biến thể source root: `src/main/java/org/example/Foo.java`, `org/example/Foo.java`, `Foo.java`.

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
| OpenAI-compatible (DashScope/Qwen, OpenRouter, ...) | `LLM_PROVIDER=openai` | `qwen-plus`, `gpt-4o-mini`, `deepseek-*` |
| Google Gemini | `LLM_PROVIDER=gemini` | `gemini-2.5-flash` |
| Custom endpoint | `LLM_PROVIDER=custom` | Ollama, vLLM, LM Studio |

Tất cả đều qua **OpenAI-compatible API** — chỉ đổi base URL và API key. `scripts/run_cross_model.py` đọc key theo model từ env (`DASHSCOPE_KEY`, `OPENROUTER_API_KEY`) để chạy cùng lúc nhiều model/vendor trên cùng tập instance.
