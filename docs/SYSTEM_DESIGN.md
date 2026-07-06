# Tài liệu Thiết kế Hệ thống & Định hướng Nghiên cứu

**Đề tài:** Định vị lỗi phần mềm (Bug Localization) bằng hệ đa tác tử LLM kết hợp phân tích log và đồ thị mã nguồn
**Loại tài liệu:** Thiết kế nghiên cứu (research design) — chi tiết cài đặt xem [ARCHITECTURE.md](../ARCHITECTURE.md)
**Cập nhật:** 2026-07-05

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

### 3.3 Quyết định thiết kế then chốt (và lý do)

1. **Tác tử khám phá chủ động thay vì nhồi context:** codebase lớn (django ~3k file Python) không vừa context window; agent dùng tool để đọc đúng chỗ cần — chi phí token tỉ lệ với độ khó bug, không tỉ lệ với kích thước repo.
2. **Graph RAG build nền song song Phase 1:** che độ trễ build CPG (~10-30s) bằng thời gian ComprehensionAgent chạy; cache theo commit HEAD.
3. **Danh sách cuối không bao giờ mỏng:** bài học từ baseline — mọi con đường thất bại (JSON parse hỏng, agent hết vòng lặp, confirmation từ chối) đều phải đổ về candidate pool có thứ bậc, kèm nguồn "path-keyword" thuần filesystem làm lưới an toàn cuối (không phụ thuộc LLM/index).
4. **Xếp hạng bằng tín hiệu tường minh, không chỉ LLM confidence:** trọng số stack-trace cao nhất (2.5) phản ánh phát hiện thực nghiệm: khi có traceback, frame gần cuối gần như luôn là đáp án; LLM confidence chỉ là một trong 9 tín hiệu → giảm phụ thuộc vào tính bất định của model.
5. **Mọi tính năng có công tắc env:** thiết kế phục vụ ablation (RQ2) ngay từ đầu, không phải gắn thêm sau.

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

### 4.5 Tái lập (reproducibility)

- Toàn bộ cấu hình qua `.env` (đã version mẫu trong `.env.example`); model, provider, temperature ghi vào metadata của mỗi file kết quả JSON.
- Checkout benchmark cố định theo commit (scripts/checkout_*).
- Kết quả thô (per-instance JSON/CSV) commit vào `results/` để người khác kiểm chứng lại metrics mà không cần chạy lại LLM.

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

- [ ] **Listwise rerank:** 1 LLM call cuối nhận top-10 + snippet, xếp lại thứ tự (nhắm nhóm hit@2-3).
- [ ] **Tune trọng số scorer theo profile benchmark:** django ít stack trace → tăng mentioned/semantic; grid search trên dev set, giữ profile Java/Python riêng.
- [ ] **Log tổng hợp cho bug không log (gắn H3):** ComprehensionAgent sinh "log giả định" (exception message khả dĩ) làm query tìm kiếm — đo riêng trên tầng bug không log.

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
