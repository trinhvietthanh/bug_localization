# Tài liệu Thiết kế Hệ thống & Định hướng Nghiên cứu

**Đề tài:** Định vị lỗi phần mềm (Bug Localization) bằng hệ đa tác tử LLM kết hợp phân tích log và đồ thị mã nguồn
**Loại tài liệu:** Thiết kế nghiên cứu (research design) — chi tiết cài đặt xem [ARCHITECTURE.md](../ARCHITECTURE.md)
**Cập nhật:** 2026-07-09 (thêm E1/E2/E3 — §2.2 H6-H8, §3.4-3.6, §4.6, Giai đoạn B′, Phụ lục B)

---

## 1. Bối cảnh & Phát biểu bài toán

### 1.1 Bài toán

Cho một **bug report** (mô tả lỗi bằng ngôn ngữ tự nhiên, có thể kèm log, stack trace, thông báo lỗi) và **mã nguồn** của dự án, hệ thống phải trả về **danh sách xếp hạng các file/method khả nghi** chứa lỗi. Đầu ra được đánh giá bằng việc file sửa lỗi thực tế (ground truth từ commit vá) có nằm ở vị trí cao trong danh sách hay không.

### 1.2 Khoảng trống nghiên cứu

- **Phương pháp IR truyền thống** (VSM, BM25, semantic similarity — nhánh `bug-localization/` của dự án) chỉ so khớp từ vựng/ngữ nghĩa bề mặt, không "hiểu" hành vi chương trình, không truy vết được quan hệ gọi hàm.
- **LLM đơn lượt** (đưa toàn bộ report + code vào một prompt) bị giới hạn context window và không có khả năng khám phá codebase chủ động.
- **Khoảng trống:** chưa có đánh giá hệ thống về việc kết hợp (i) tác tử LLM tự khám phá mã nguồn, (ii) tri thức cấu trúc từ Code Property Graph, (iii) phân tích log có cấu trúc (Drain3), và (iv) hợp nhất đa tín hiệu xếp hạng — trên cả hai hệ sinh thái Java (Defects4J) và Python (SWE-bench, BugsInPy).

### 1.3 Phạm vi

| Trong phạm vi | Ngoài phạm vi |
|---|---|
| Định vị lỗi mức file và method | Tự động sửa lỗi (APR) |
| Bug report văn bản + log/traceback | Lỗi cần tái hiện runtime (dynamic analysis) |
| Java & Python | Các ngôn ngữ khác |
| Benchmark công khai (Defects4J, SWE-bench Lite, BugsInPy) | Dữ liệu công nghiệp đóng |

---

## 2. Câu hỏi nghiên cứu & Giả thuyết

### 2.1 Câu hỏi nghiên cứu (RQ)

- **RQ1 — Hiệu quả:** Kiến trúc đa tác tử LLM đạt độ chính xác định vị lỗi (Top-N, MRR) cao hơn bao nhiêu so với các phương pháp IR truyền thống trên cùng benchmark?
- **RQ2 — Đóng góp thành phần:** Mỗi thành phần (Graph RAG, phân tích log, unified scoring, self-reflection, best-of-N) đóng góp bao nhiêu vào kết quả cuối? (ablation)
- **RQ3 — Vai trò của log:** Chất lượng thông tin log/stack trace trong bug report ảnh hưởng thế nào đến độ chính xác, và phân tích log có cấu trúc (Drain3) khai thác được gì hơn so với xử lý văn bản thô?
- **RQ4 — Chi phí:** Đánh đổi giữa độ chính xác và chi phí (số LLM call, token, thời gian) của từng cấu hình như thế nào?
- **RQ5 — Tổng quát hóa:** Kết quả có nhất quán giữa hai ngôn ngữ (Java/Python) và giữa các kiểu dự án (thư viện tiện ích vs framework lớn) không?

### 2.2 Giả thuyết nghiên cứu (kiểm chứng được)

Mỗi giả thuyết được phát biểu kèm **tiêu chí bác bỏ** — đo trên cùng tập bug, so sánh bằng McNemar's test (cho chỉ số nhị phân Top-N hit) hoặc Wilcoxon signed-rank (cho MRR), mức ý nghĩa α = 0.05.

**H1 — Đa tác tử vượt IR truyền thống.**
Pipeline Comprehension → Navigation → Confirmation đạt Top-1 và MRR cao hơn baseline IR (VSM + token matching + semantic similarity của nhánh `bug-localization/`) trên cùng tập bug.
*Bác bỏ nếu:* chênh lệch Top-1 không có ý nghĩa thống kê hoặc âm.

**H2 — Tri thức cấu trúc (Graph RAG) cải thiện định vị.**
Bổ sung Code Property Graph (graph_search, find_callers/callees) tăng Top-5 so với cấu hình chỉ có semantic RAG, đặc biệt với bug mà file sửa **không được nhắc trực tiếp** trong report (lỗi lan truyền qua call chain).
*Bác bỏ nếu:* ablation `ENABLE_GRAPH_RAG=false` không giảm Top-5 đáng kể trên nhóm bug này.

**H3 — Phân tích log có cấu trúc tăng độ chính xác trên bug có log.**
Trên tập con bug report chứa stack trace/log (phân tầng theo `log_parse_result`), pipeline có Drain3 + trọng số stack-trace (×2.5 trong unified scorer) đạt Top-1 cao hơn đáng kể so với (a) tắt log parsing và (b) chính nó trên tập bug không có log. Bằng chứng sơ bộ: Lang (bug report giàu stack trace) đạt 95.1% Top-1 trong khi Chart/Mockito (report nghèo log) chỉ 25-26%.
*Bác bỏ nếu:* chênh lệch giữa hai tầng không tồn tại sau khi kiểm soát độ khó dự án.

**H4 — Mở rộng pool ứng viên tăng recall mà không hại precision.**
Việc hợp nhất đa nguồn ứng viên (confirmed → navigation → stack trace → mentioned → retriever → path-keyword) và pad danh sách tối thiểu 10 file tăng Top-5 **mà không giảm** Top-1, vì ứng viên pad không mang LLM confidence nên chỉ vượt file confirmed khi có tín hiệu mạnh độc lập. Cơ sở: baseline SWE-bench Lite có Top-3 = Top-5 = 64% (danh sách trung bình 1–3 file — thiếu recall chứ không sai xếp hạng).
*Bác bỏ nếu:* Top-5 không tăng ≥ 5 điểm % hoặc Top-1 giảm > 2 điểm %.

**H5 — Best-of-N + RRF giảm phương sai.**
Chạy N=3 lượt với temperature 0.3 và hợp nhất bằng Reciprocal Rank Fusion cho MRR trung bình cao hơn và độ lệch chuẩn giữa các lần chạy thấp hơn so với 1 lượt greedy (temperature 0).
*Bác bỏ nếu:* chi phí ×3 không đem lại cải thiện MRR có ý nghĩa.

**H6 — Giả thuyết cạnh tranh + falsification tăng recall có lý do (E1).**
Comprehension sinh K=3-5 giả thuyết cạnh tranh (mỗi cái nêu cơ chế, file nghi vấn, probe kiểm chứng — trong đó ≥1 probe DISCONFIRMING); VerificationAgent thu bằng chứng ủng hộ/bác bỏ; cập nhật belief bằng log-odds thuần Python. Pool ứng viên nhận thêm nguồn "file của giả thuyết sống theo posterior" → Top-5 coverage tăng so với 1 giả thuyết duy nhất, mạnh nhất trên bug **không có stack trace**; file của giả thuyết bị bác bỏ nhận điểm âm nên không kéo giảm Top-1.
*Bác bỏ nếu:* Top-5 tăng < 3 điểm % trên dev set, hoặc Top-1 giảm > 2 điểm %.

**H7 — Khám phá theo hàng đợi ưu tiên đạt hiệu quả bằng với chi phí thấp hơn (E2).**
Thay vòng lặp tool-calling tự do của Navigation bằng scheduler ưu tiên (priority = 0.5×LLM-relevance + 0.3×graph-proximity + 0.2×static-prior) với context O(1) mỗi bước (không mang message history): Top-5 không kém free-form, token Phase-2 giảm ≥ 20%, phương sai tool-call giữa instance giảm, và lỗi `miss_empty` bất khả thi về cấu trúc (output lắp ráp bằng Python thuần).
*Bác bỏ nếu:* Top-5 thấp hơn có ý nghĩa thống kê, hoặc token giảm < 10%.

**H8 — Listwise rerank trên evidence card tăng Top-1 với Top-10 bất biến (E3).**
Một LLM call cuối nhìn đồng thời top-10 ứng viên dưới dạng evidence card (cờ tín hiệu định tính từ UnifiedScorer + verdict của agent + snippet đã thu hẹp — KHÔNG lộ điểm tổng/thứ hạng, trình bày theo thứ tự shuffle deterministic) và hoán vị lại thứ tự → Top-1/MRR tăng trong khi Top-10/recall không đổi **theo cấu trúc** (chỉ hoán vị, không thay tập). Kiểm tra position bias dư bằng cấu hình 2 pass shuffle khác nhau + RRF.
*Bác bỏ nếu:* Top-1 không tăng có ý nghĩa, hoặc số case bị giáng khỏi Top-1 ≥ số case được thăng.

---

## 3. Thiết kế hệ thống

### 3.1 Kiến trúc tổng thể

```
Bug report (+ log/traceback)
        │
        ▼
┌─ Preprocessor ──────────────────────────────┐
│ trích error messages, stack traces,         │
│ mentioned files/functions, keywords;        │
│ Drain3 log templates (tools/log_parser.py)  │
└──────────────┬──────────────────────────────┘
               ▼
┌─ ComprehensionAgent ─┐   ┌─ Graph RAG builder ─┐
│ hiểu bug, sinh fault │   │ CPG build nền (bg    │
│ hypothesis           │   │ thread, cache/commit)│
└──────────┬───────────┘   └────────┬────────────┘
           ▼                        │ inject trước Phase 2
┌─ NavigationAgent ────────────────────────────┐
│ khám phá codebase lặp (code_search, AST,     │
│ semantic_search, graph_search, callers/ees,  │
│ git_log) → suspicious_locations              │
└──────────┬───────────────────────────────────┘
           ▼        ◄── self-reflection loop khi confidence thấp
┌─ ConfirmationAgent ──────────────────────────┐
│ xác nhận, xếp hạng ranked_locations (JSON)   │
└──────────┬───────────────────────────────────┘
           ▼
┌─ Candidate Pool (orchestrator) ──────────────┐
│ confirmed → navigation → stack-trace →       │
│ mentioned → hybrid retriever (BM25+semantic) │
│ → graph → path-keyword; pad ≥ MIN_RANKED (10)│
└──────────┬───────────────────────────────────┘
           ▼
┌─ UnifiedScorer (9 tín hiệu) ─────────────────┐
│ llm_conf ×1.0 │ stack_trace ×2.5 │ error ×1.5│
│ mentioned ×1.2 │ graph ×0.8 │ semantic ×0.6  │
│ method ×0.3 │ git_recency ×0.5 │ test −50%   │
└──────────┬───────────────────────────────────┘
           ▼
Ranked files/methods + giải thích root cause
```

### 3.2 Ánh xạ thành phần ↔ giả thuyết

| Thành phần | File chính | Kiểm chứng | Công tắc ablation |
|---|---|---|---|
| Pipeline 3 tác tử | `agents/*.py` | H1 | so với `bug-localization/` |
| Code Property Graph + graph tools | `rag/code_graph.py`, `rag/graph_retriever.py` | H2 | `ENABLE_GRAPH_RAG` |
| Drain3 log parsing + stack-trace seeding | `tools/log_parser.py`, preprocessor | H3 | tắt structured extraction |
| Candidate pool + padding | `orchestrator._build_candidate_pool` | H4 | `MIN_RANKED_FILES=0` vs `10` |
| Unified scoring 9 tín hiệu | `evaluation/unified_scorer.py` | H4 | `ENABLE_UNIFIED_SCORING` |
| Best-of-N + RRF | `orchestrator.multi_pass_localize` | H5 | `--passes 1` vs `3` |
| Self-reflection | orchestrator loop | RQ2 | `REFLECTION_MAX_ROUNDS=0` |
| Vòng giả thuyết cạnh tranh (E1) | `agents/hypothesis.py`, `agents/verification.py` | H6 | `ENABLE_HYPOTHESIS_LOOP` |
| Khám phá hàng đợi ưu tiên (E2) | `core/explorer.py`, `agents/priority_navigation.py` | H7 | `ENABLE_PRIORITY_EXPLORATION` |
| Thu hẹp phân cấp + listwise rerank (E3) | `evaluation/reranker.py` | H8 | `ENABLE_HIERARCHICAL_NARROWING`, `ENABLE_LISTWISE_RERANK` |

### 3.3 Quyết định thiết kế then chốt (và lý do)

1. **Tác tử khám phá chủ động thay vì nhồi context:** codebase lớn (django ~3k file Python) không vừa context window; agent dùng tool để đọc đúng chỗ cần — chi phí token tỉ lệ với độ khó bug, không tỉ lệ với kích thước repo.
2. **Graph RAG build nền song song Phase 1:** che độ trễ build CPG (~10-30s) bằng thời gian ComprehensionAgent chạy; cache theo commit HEAD.
3. **Danh sách cuối không bao giờ mỏng:** bài học từ baseline — mọi con đường thất bại (JSON parse hỏng, agent hết vòng lặp, confirmation từ chối) đều phải đổ về candidate pool có thứ bậc, kèm nguồn "path-keyword" thuần filesystem làm lưới an toàn cuối (không phụ thuộc LLM/index).
4. **Xếp hạng bằng tín hiệu tường minh, không chỉ LLM confidence:** trọng số stack-trace cao nhất (2.5) phản ánh phát hiện thực nghiệm: khi có traceback, frame gần cuối gần như luôn là đáp án; LLM confidence chỉ là một trong 9 tín hiệu → giảm phụ thuộc vào tính bất định của model.
5. **Mọi tính năng có công tắc env:** thiết kế phục vụ ablation (RQ2) ngay từ đầu, không phải gắn thêm sau.

### 3.4 Vòng lặp giả thuyết cạnh tranh — E1 (H6)

**Cơ chế.** Thay 1 `fault_hypothesis` duy nhất bằng K=3-5 giả thuyết cạnh tranh, mỗi cái là một **tuyên bố có thể bác bỏ** (nêu cơ chế, không nêu triệu chứng) kèm: component/file/function nghi vấn, chuỗi nhân quả, và các probe kiểm chứng — bắt buộc ≥1 probe DISCONFIRMING. LLM chỉ **đề xuất giả thuyết và dán nhãn bằng chứng**; toàn bộ số học belief là Python thuần (kiểm toán được, không phụ thuộc calibration của model):

- `prior` → `log_odds = ln(p/(1-p))` clamp ±2.0; mỗi bằng chứng cập nhật `log_odds += direction × LLR` với LLR = 0.25/0.6/1.1 (weak/moderate/strong).
- `posterior < 0.15` → **falsified**; `> 0.75` → supported; còn lại active.

**Vị trí trong pipeline.** Sinh: mở rộng JSON schema của ComprehensionAgent (không thêm call). Kiểm chứng: `VerificationAgent` mới chạy **sau Navigation vòng 1, trước Confirmation** (6 iteration, tool: read_file/get_function_source/code_search/find_callers/find_callees), prompt bắt buộc tìm bằng chứng CHỐNG với nỗ lực ngang bằng chứng ỦNG HỘ. **Fast path:** bỏ qua kiểm chứng khi có stack-trace files và top prior ≥ 0.85 (bug giàu traceback không cần giả thuyết cạnh tranh — kiểm soát token).

**Tích hợp điểm số.** (i) Pool: nguồn mới "file của giả thuyết sống theo posterior" chèn giữa confirmed và navigation; file bị bác bỏ KHÔNG bị xóa (bảo toàn recall) mà tụt hạng qua scoring. (ii) UnifiedScorer: tín hiệu thứ 10 `hypothesis_support` (×0.8), file chỉ thuộc giả thuyết falsified nhận **điểm âm** −0.4×w — cơ chế duy nhất giáng được ứng viên mà LLM tự tin sai. (iii) Reflection: `tracker.reflection_summary()` thay message generic — chỉ đích danh "HYP1 đã bác bỏ vì X; HYP3 còn 2 probe chưa kiểm — tập trung vào đó".

**Fail-open.** JSON hỏng → bọc `fault_hypothesis` thành 1 giả thuyết duy nhất (đúng hành vi hiện tại); mọi giả thuyết bị bác bỏ → tín hiệu = 0 → xếp hạng như cũ. Contract: `fault_hypothesis` luôn = statement của giả thuyết top-prior nên downstream không đổi khi flag off/on.

### 3.5 Khám phá theo hàng đợi ưu tiên — E2 (H7, kiểu OrcaLoca)

**Quyết định kiến trúc: scheduler chọn action, LLM chỉ chấm điểm.** `PriorityNavigationAgent` thay vòng lặp tool-calling tự do bằng engine `core/explorer.py`: heap các action (`inspect_file/inspect_function/expand_callers/expand_callees/run_search`) với

```
priority = 0.5 × llm_relevance/10 + 0.3 × 1/(1 + graph_distance-tới-anchor) + 0.2 × static_prior
```

static_prior theo nguồn gốc: stack-trace 1.0 · mentioned/test-derived 0.7 · file giả thuyết 0.6×posterior (khi E1 bật) · path-keyword 0.4 · mặc định 0.2. Java giảm trọng số graph còn 0.15 (CPG regex nhiễu hơn AST).

**Vòng lặp.** Decomposition (1 call; khi E1 bật thì mỗi giả thuyết sống = 1 sub-query, khỏi call) → pop-execute qua tool registry sẵn có → 1 call observe **context O(1)** (~2-3k token: output tool ≤6k ký tự + tên/điểm top findings; KHÔNG mang message history — đây chính là distance-aware context pruning) → relevance ≥6 thành suspicious location; observation cũng dán nhãn `hypothesis_evidence` feed ngược tracker E1 (nên khi E1+E2 cùng bật, orchestrator KHÔNG chạy VerificationAgent riêng). Dừng khi: 20 action / frontier rỗng / top priority < 0.15 / 5 lần liên tiếp relevance < 3 / ≥8 finding relevance ≥ 8.

**Hệ quả cấu trúc.** Output lắp ráp bằng Python thuần (không parse JSON tổng) ⇒ lớp lỗi `miss_empty` bất khả thi ở Phase 2; token phẳng có trần cứng (~20 × 2.5k) thay vì tăng dần theo history. Visited-set persist qua reflection round (không lặp lại việc cũ). Lưới an toàn: `EXPLORATION_FALLBACK_TO_FREEFORM` chạy NavigationAgent thường nếu explorer tìm được <3 location relevance ≥5. Schema output y hệt Navigation nên Confirmation/pool/scorer không đổi. API graph mới: `GraphRetriever.find_anchor_nodes` + `hop_distance`/`file_hop_distances` (BFS + cache; graph chưa sẵn sàng → khoảng cách trung tính).

### 3.6 Thu hẹp phân cấp + Listwise rerank — E3 (H8, kiểu Agentless)

**Vị trí hook:** `evaluation/reranker.py::ListwiseReranker`, gọi ngay sau `_apply_unified_scoring` (và nhánh fallback pool). **Chỉ hoán vị top-K=10, không bao giờ thay tập** ⇒ Top-10/recall bất biến theo cấu trúc, cô lập H8 vào Top-1/MRR; mọi failure = no-op.

**Stage (a) — thu hẹp phân cấp** (`ENABLE_HIERARCHICAL_NARROWING`): 1 call structured trên skeleton của cả top-10 (`tools/repo_skeleton.py::skeleton_for_files`) → mỗi file ≤2 hàm nghi + line range; điền `ranked_methods`/`function_name/start_line/end_line` cho file pool chưa có method (cải thiện luôn metric method-level) và cắt snippet ≤15 dòng cho stage (b).

**Stage (b) — listwise rerank** (`ENABLE_LISTWISE_RERANK`): 1 call temp 0, không tool, trên **evidence card** lắp bằng Python: cờ tín hiệu định tính từ `CandidateScore` (STACK_TRACE/ERROR_MATCH/graph/semantic/recency), verdict + confidence của agent (file pad ghi rõ "verdict: none"), snippet đã thu hẹp. **Chống position bias:** ID trung tính C1..CK, thứ tự shuffle deterministic theo SHA-256(instance_id) — tái lập được nhưng khử tương quan với thứ hạng unified; không lộ điểm tổng/rank; tùy chọn 2 pass shuffle khác + RRF làm ablation đo bias. **Validate:** output phải là hoán vị (thiếu ID → nối lại theo thứ tự unified; không parse được → giữ nguyên). Card serialize được vào `agent_results["listwise_rerank"]` ⇒ replay offline để iterate prompt không cần chạy lại agent.

**Luận điểm compositional:** đứng riêng E3 chỉ có headroom nhỏ (baseline có Top-3 = Top-5); giá trị chính là E1/E2 nâng recall làm đầy bucket hit@2-10, rồi E3 chuyển thành Top-1 — do đó thứ tự triển khai là E3 → E1 → E2 nhưng thứ tự **đánh giá** phải có cấu hình bật cả ba.

---

## 4. Phương pháp thực nghiệm

### 4.1 Dataset & phân chia

| Benchmark | Ngôn ngữ | Vai trò | Quy mô |
|---|---|---|---|
| Defects4J (Lang, Math, Time, Chart, Closure, Mockito, Jsoup) | Java | đánh giá chính + phân tích theo đặc trưng dự án | 292 bugs đã chạy |
| SWE-bench Lite | Python | đánh giá chính trên Python | 50 dev / phần còn lại validation |
| BugsInPy | Python | kiểm tra tổng quát hóa | theo checkout sẵn có |

**Quy tắc chống overfit:** tập 50 SWE-bench Lite đầu (đã có baseline) là **dev set** — mọi tinh chỉnh trọng số/prompt chỉ dựa trên nó; con số báo cáo cuối lấy từ phần validation chưa từng dùng để tune.

### 4.2 Chỉ số & kiểm định

- **Chính:** Top-1, Top-3, Top-5, Top-10 accuracy; MRR; MAP (file-level; method-level khi có ground truth).
- **Chi phí:** LLM calls, prompt/completion tokens, thời gian/bug.
- **Kiểm định:** McNemar (Top-N hit theo cặp cấu hình trên cùng bug), Wilcoxon signed-rank (RR từng bug), báo cáo cả effect size. Với cấu hình có ngẫu nhiên (H5): 3 seed, báo mean ± std.
- **Taxonomy lỗi:** mọi run đều chạy `scripts/analyze_misses.py` để phân loại `hit@1 / hit@2-3 / hit@4-5 / miss_gt_deep / miss_gt_absent / miss_empty` — theo dõi *loại* lỗi nào được fix chứ không chỉ con số tổng.

### 4.3 Baseline so sánh

1. IR truyền thống của chính dự án (`bug-localization/`: token matching + VSM + semantic, trọng số DE).
2. BM25 thuần trên file (bm25_index).
3. Semantic embedding thuần (retriever top-k).
4. LLM một lượt (không tool, đưa skeleton + report, hỏi trực tiếp) — để tách đóng góp của "agentic loop".

### 4.4 Ma trận ablation (RQ2)

Mỗi dòng tắt một thành phần so với cấu hình đầy đủ, chạy trên cùng dev set:

| Cấu hình | Thay đổi |
|---|---|
| Full | tất cả bật, passes=1 |
| −GraphRAG | `ENABLE_GRAPH_RAG=false` |
| −LogParsing | tắt Drain3/structured extraction |
| −UnifiedScoring | `ENABLE_UNIFIED_SCORING=false` |
| −Pool padding | `MIN_RANKED_FILES=0` |
| −Reflection | `REFLECTION_MAX_ROUNDS=0` |
| +BestOf3 | passes=3, temp 0.3 |
| +HypLoop (E1) | `ENABLE_HYPOTHESIS_LOOP=true` |
| +PriorityExp (E2) | `ENABLE_PRIORITY_EXPLORATION=true` |
| +Rerank only (E3b) | `ENABLE_LISTWISE_RERANK=true` |
| +Narrow+Rerank (E3) | cả hai flag E3 |
| +Rerank 2-pass | `LISTWISE_RERANK_PASSES=2` (đo position bias dư) |
| +HypLoop+Rerank | E1 + E3 (recall → precision) |
| +PriorityExp+Rerank | E2 + E3 (bù so sánh chéo ứng viên mà explorer thiếu) |
| PriorityExp −graph | E2 với `EXPLORATION_W_GRAPH=0` (tách "priority" khỏi "context pruning") |
| Full+E1+E2+E3 | cả ba bật |

### 4.5 Tái lập (reproducibility)

- Toàn bộ cấu hình qua `.env` (đã version mẫu trong `.env.example`); model, provider, temperature ghi vào metadata của mỗi file kết quả JSON.
- Checkout benchmark cố định theo commit (scripts/checkout_*).
- Kết quả thô (per-instance JSON/CSV) commit vào `results/` để người khác kiểm chứng lại metrics mà không cần chạy lại LLM.

### 4.6 Thí nghiệm khả thi FE-1/2/3 (gate đăng ký trước, chạy trước ablation đầy đủ)

Mỗi extension phải qua gate rẻ (<20 instance) trước khi đầu tư chạy ma trận đầy đủ:

- **FE-1 (E1, ~15 instance, chỉ cần Comprehension):** 10 case `miss_gt_absent` + 5 case hit từ baseline 50; đo *hypothesis coverage* = % instance có ground-truth file ∈ ∪suspected_files của K=4 giả thuyết, và *distinctness* = số component khác nhau trung bình. **Gate: coverage ≥ 40% trên nhóm miss (hiện ≈0% theo định nghĩa) và distinctness ≥ 3.**
- **FE-2 (E2, 16 instance stratified 8 giàu/8 nghèo stack-trace):** baseline vs `ENABLE_PRIORITY_EXPLORATION=true`; đo recall của suspicious_locations, token Phase-2, số tool call, wall time. **Gate: recall không tệ hơn (paired) và token Phase-2 ≤ mean baseline.**
- **FE-3 (E3, 20 instance, 1 lượt chạy):** log thứ tự trước/sau rerank (đã stash trong `agent_results["listwise_rerank"]`); đo `gained_top1`/`lost_top1`, Kendall-τ với thứ tự unified, tỉ lệ permutation hợp lệ. **Gate: net Top-1 ≥ +2/20, validity ≥ 90%, lost < gained.** Card đã serialize nên prompt rerank có thể iterate offline không tốn lượt agent.

---

## 5. Hiện trạng thực nghiệm

### 5.1 Kết quả đã có

| Benchmark | Model | Top-1 | Top-5 | MRR | Ghi chú |
|---|---|---|---|---|---|
| Defects4J (292 bugs) | — | 65.8% | 69.2% | 0.673 | Lang 95.1% ↔ Chart 25.0%: chênh lệch gắn với độ giàu log của report (bằng chứng sơ bộ cho H3) |
| SWE-bench Lite (50) | gemini-2.5-flash | 58.0% | 64.0% | 0.607 | baseline trước các fix bên dưới |

### 5.2 Taxonomy lỗi của baseline SWE-bench (động lực cho vòng cải tiến hiện tại)

18/50 miss Top-5, gồm: 4 case `predicted=[]` (1 do JSON chứa escape regex không parse được **dù agent đã tìm đúng file**; 3 do agent hết vòng lặp không chốt); ~4 case ground truth là `__init__.py` bị dự đoán thành tên module (`fields.py` vs `fields/__init__.py`); phần còn lại danh sách chỉ 1–3 file nên ground truth không có mặt (miss_gt_absent). Top-3 = Top-5 chứng tỏ vấn đề là **recall của danh sách**, không phải thứ tự xếp hạng.

### 5.3 Cải tiến đã cài đặt (chờ verify — sandbox đang lỗi hạ tầng)

1. Parse JSON lenient (sửa escape không hợp lệ, trailing comma) + regression test trên blob thật của django-11099.
2. Ép agent chốt kết quả khi hết vòng lặp (1 call cuối không tool).
3. Confirmation/Navigation **merge** thay vì ghi đè candidate_files.
4. Candidate pool đa nguồn + pad ≥ 10 file (`MIN_RANKED_FILES`), nguồn path-keyword thuần filesystem làm lưới cuối.
5. Resolve `X.py` → `X/__init__.py` trước khi drop path không tồn tại.
6. Truyền semantic_scores vào UnifiedScorer; pool dùng hybrid BM25+semantic khi có retriever.
7. Công cụ: `scripts/analyze_misses.py`, 19 unit test mới.

**Dự đoán kiểm chứng được (đăng ký trước khi chạy):** trên dev set 50, taxonomy sau fix phải có `miss_empty = 0`, độ dài danh sách trung bình ≈ 10, Top-5 ≥ 72%, Top-1 không giảm quá 2 điểm. Nếu sai, xem lại H4.

---

## 6. Lộ trình phát triển

### Giai đoạn A — Kiểm chứng vòng cải tiến hiện tại (1-2 tuần)

- [ ] Chạy unit tests (25 test) khi hạ tầng sandbox hồi phục.
- [ ] Benchmark 50 dev (qwen-plus trước để smoke, gemini-2.5-flash để A/B với baseline).
- [ ] So taxonomy trước/sau bằng `analyze_misses.py`; đối chiếu với dự đoán §5.3.
- [ ] Nếu H4 đứng vững: chạy validation set (100-250 instance SWE-bench Lite còn lại).

### Giai đoạn B — Nâng precision Top-1 (2-4 tuần)

- [x] **Listwise rerank (E3, §3.6):** đã cài đặt `evaluation/reranker.py` (narrowing + rerank, 2026-07-09) — chờ FE-3.
- [ ] **Tune trọng số scorer theo profile benchmark:** django ít stack trace → tăng mentioned/semantic; grid search trên dev set, giữ profile Java/Python riêng.
- [ ] **Log tổng hợp cho bug không log (gắn H3):** ComprehensionAgent sinh "log giả định" (exception message khả dĩ) làm query tìm kiếm — đo riêng trên tầng bug không log.

### Giai đoạn B′ — Vòng đột phá E1/E2/E3 (đã cài đặt 2026-07-09, nhánh feat/improve)

- [x] E3: `evaluation/reranker.py` + `skeleton_for_files` + hook orchestrator + 19 unit test.
- [x] E1: `agents/hypothesis.py` (tracker log-odds) + `agents/verification.py` + schema hypotheses trong Comprehension + tín hiệu thứ 10 UnifiedScorer + reflection có đích + 20 unit test.
- [x] E2: `core/explorer.py` (frontier/termination) + `agents/priority_navigation.py` + `find_anchor_nodes`/`hop_distance` trong GraphRetriever + 19 unit test.
- [ ] Chạy FE-1/2/3 theo gate §4.6 (cần API + checkouts).
- [ ] Nếu qua gate: đưa các dòng E1/E2/E3 vào ma trận ablation Giai đoạn C.

### Giai đoạn C — Thí nghiệm luận văn (4-8 tuần)

- [ ] Chạy đầy đủ ma trận ablation §4.4 trên cả Defects4J + SWE-bench Lite.
- [ ] Chạy baseline IR (`bug-localization/`) trên cùng tập bug để trả lời RQ1.
- [ ] Phân tầng theo đặc trưng report (có/không stack trace, độ dài, số file nhắc) → RQ3.
- [ ] Phân tích chi phí-hiệu quả (RQ4): đường Pareto accuracy vs token.
- [ ] Kiểm định thống kê + effect size cho H1-H5.

### Giai đoạn D — Mở rộng (nếu còn thời gian)

- Method-level localization đầy đủ trên SWE-bench (ground truth từ diff của commit vá).
- Đánh giá cross-model (gemini / qwen / mô hình local qua Ollama) để tách "năng lực model" khỏi "đóng góp kiến trúc".
- Neo4j backend cho CPG trên repo lớn (Closure) — kiểm tra giả thuyết phụ: chất lượng graph tốt hơn cải thiện nhóm dự án đang yếu.

---

## 7. Rủi ro & giới hạn (threats to validity)

| Loại | Rủi ro | Giảm thiểu |
|---|---|---|
| Internal | Data leakage: LLM có thể đã "thuộc" các bug công khai của django/astropy trong pretraining | So sánh **tương đối** giữa các cấu hình trên cùng model; ablation không bị ảnh hưởng vì mọi cấu hình cùng hưởng leakage như nhau |
| Internal | Overfit dev set khi tune trọng số | Tách dev/validation cứng (§4.1), báo cáo số validation |
| Construct | Ground truth = file trong commit vá, có thể vá ở nơi khác nơi gây lỗi | Ghi nhận là quy ước chuẩn của lĩnh vực; thảo luận trong luận văn |
| External | 2 ngôn ngữ, 10 dự án — chưa chắc tổng quát cho công nghiệp | Nêu rõ giới hạn; BugsInPy bổ sung đa dạng |
| Stochastic | Kết quả LLM dao động giữa các lần chạy | temperature 0 cho main runs; 3 seeds cho cấu hình ngẫu nhiên; kiểm định theo cặp |
| Chi phí | API bị rate-limit/đổi giá làm gián đoạn thí nghiệm | Cache kết quả per-instance, resume được; hỗ trợ đa provider |
| Construct (E1) | Nhãn bằng chứng (direction/strength) do LLM tự dán — chủ quan | Số học belief deterministic + LLR nhỏ (1 nhãn không lật trạng thái); log toàn bộ evidence để audit |
| Construct (E2) | Gain của E2 có thể đến từ context pruning chứ không phải priority | Sub-ablation `EXPLORATION_W_GRAPH=0` (giữ pruning, tắt tín hiệu graph) tách hai cơ chế |
| Internal (E3) | Rerank có thể học position bias từ thứ tự trình bày | Shuffle deterministic + không lộ rank/điểm tổng + cấu hình 2-pass RRF đo bias dư |

---

## 8. Phụ lục — Công tắc cấu hình phục vụ thí nghiệm

| Biến | Mặc định | Dùng cho |
|---|---|---|
| `ENABLE_GRAPH_RAG` | true | H2, ablation |
| `ENABLE_UNIFIED_SCORING` | true | H4, ablation |
| `MIN_RANKED_FILES` | 10 | H4, ablation |
| `REFLECTION_MAX_ROUNDS` / `REFLECTION_CONF_THRESHOLD` | 2 / 0.5 | RQ2 |
| `MULTI_PASS_TEMPERATURE` | 0.3 | H5 |
| `SCORE_WEIGHT_*` | xem `.env.example` | Giai đoạn B tuning |
| `LLM_PROVIDER` / `LLM_MODEL` | theo `.env` | cross-model (Giai đoạn D) |
| `PER_BUG_TIMEOUT` / `LLM_CALL_TIMEOUT` | 300 / 120 giây | ổn định eval |
| `ENABLE_HYPOTHESIS_LOOP` | false | H6, E1 |
| `HYPOTHESIS_K` / `HYPOTHESIS_VERIFY_MAX_ITER` | 4 / 6 | E1 budget |
| `HYPOTHESIS_FALSIFY_THRESHOLD` / `HYPOTHESIS_FASTPATH_PRIOR` | 0.15 / 0.85 | E1 ngưỡng |
| `SCORE_WEIGHT_HYPOTHESIS` | 0.8 | E1 tín hiệu scorer |
| `ENABLE_PRIORITY_EXPLORATION` | false | H7, E2 |
| `EXPLORATION_MAX_ACTIONS` / `EXPLORATION_MAX_DEPTH` | 20 / 4 | E2 budget |
| `EXPLORATION_W_LLM` / `_W_GRAPH` / `_W_SIGNAL` / `_W_GRAPH_JAVA` | 0.5/0.3/0.2/0.15 | E2 priority; `_W_GRAPH=0` cho sub-ablation |
| `EXPLORATION_MIN_PRIORITY` / `EXPLORATION_FALLBACK_TO_FREEFORM` | 0.15 / true | E2 dừng/lưới an toàn |
| `ENABLE_HIERARCHICAL_NARROWING` / `ENABLE_LISTWISE_RERANK` | false / false | H8, E3 |
| `LISTWISE_RERANK_TOP_K` / `LISTWISE_RERANK_PASSES` | 10 / 1 | E3; passes=2 đo bias |

---

## Phụ lục B — Tín hiệu spectrum/test-execution (E4, nghiên cứu khả thi — future work)

Tín hiệu mạnh nhất trong văn liệu FL nhưng cần hạ tầng docker; phác thảo để Giai đoạn D cân nhắc:

- **Precompute offline** (`scripts/precompute_spectrum.py`, pattern như `prebuild_graphs.py`): kéo docker image chính thức per-instance của SWE-bench, chạy các test `FAIL_TO_PASS` tại buggy commit dưới `coverage.py --branch` + các test `PASS_TO_PASS` cùng module làm contrast → `data/spectrum/<instance_id>.json` với execution count per-file/per-line và Ochiai suspiciousness (degrade về tần suất "được test fail chạm tới" khi thiếu coverage của test pass).
- **Consume online:** trọng số mới `SCORE_WEIGHT_SPECTRUM` ≈ 1.5-2.0 trong UnifiedScorer (tín hiệu động lịch sử luôn trội tín hiệu tĩnh); đồng thời làm `static_prior` cho E2 và một dòng evidence card cho E3 ("executed by failing test: yes, 14 hits").
- **Effort:** ~2-3 tuần (docker plumbing + quirks test-runner per-repo), ~5-10 phút/instance, ~1-2 GB/image family. Chỉ SWE-bench (Defects4J cần harness riêng — ngoài phạm vi). **Spike trước:** chạy tay 5 instance xác nhận image chạy được `FAIL_TO_PASS` dưới coverage mà không phải vá test config.
