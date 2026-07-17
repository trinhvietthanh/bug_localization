# Báo cáo Code Review — Vòng E1/E2/E3 (working tree, nhánh `feat/improve`)

**Ngày review:** 2026-07-15
**Phương pháp:** `/code-review` mức high — 8 finder agent theo 8 góc nhìn (line-by-line, removed-behavior, cross-file tracer, reuse, simplification, efficiency, altitude, conventions) → dedupe ~28 candidate → 4 verifier agent kiểm chứng bằng cách đọc code thực tế.
**Kết quả kiểm chứng:** 25 CONFIRMED / 1 PLAUSIBLE / 2 REFUTED.

## 1. Phạm vi review

- Toàn bộ diff chưa commit (`git diff HEAD`, ~3.400 dòng): `agents/base_agent.py`, `agents/comprehension.py`, `agents/confirmation.py`, `agents/navigation.py`, `agents/orchestrator.py`, `config.py`, `evaluation/unified_scorer.py`, `rag/graph_retriever.py`, `scripts/run_swebench_benchmark.py`, `tools/repo_skeleton.py`.
- 9 module mới chưa track: `agents/hypothesis.py`, `agents/priority_navigation.py`, `agents/verification.py`, `core/explorer.py`, `evaluation/rank_fusion.py`, `evaluation/reranker.py`, `scripts/bare_llm_baseline.py`, `scripts/run_cross_model.py`, `scripts/run_swebench_lite_full.py`.
- **Không** review phần đã commit trên nhánh so với `master` (~18k dòng) — đã có báo cáo trước tại `docs/DANH_GIA_CODE_E1E2E3.md` (2026-07-12).

## 2. Kết luận tổng quát

Kiến trúc tổng thể (E1 hypothesis loop / E2 priority exploration / E3 listwise rerank, flag-gated, có 58 test mới đi kèm) là **code nghiêm túc, không phải rác**. Tuy nhiên review tìm ra **10 bug correctness CONFIRMED**, trong đó 3 bug (#1, #4, #9) **trực tiếp làm sai lệch chính các con số FE-1/2/3 gates mà vòng này tồn tại để đo**. Cần sửa trước khi chạy ablation.

## 3. Mười bug correctness (CONFIRMED, xếp theo mức nghiêm trọng)

### #1 — File tree giấu `django/db/migrations/` khỏi prompt Comprehension
**Vị trí:** `agents/comprehension.py:92`
`build_repo_file_tree` loại mọi path chứa segment `migrations` (`set(rel.split('/')) & _TREE_EXCLUDE_DIRS`), trong khi `django/db/migrations/` là source thật của framework và là gold file của nhiều instance SWE-bench django. Prompt (dòng 601) lại bắt model dùng path "EXACTLY như đã liệt kê".
**Kịch bản lỗi:** Instance django có gold file dưới `django/db/migrations/` (vd. `serializer.py`, `autodetector.py`): tree không liệt kê file đó → comprehension bị lái chủ động ra khỏi đáp án đúng. `comprehension_file_tree` mặc định BẬT (`config.py:217`) nên ảnh hưởng **mọi** run django.

### #2 — `_excerpt` crash cả instance khi LLM trả `start_line` không phải số
**Vị trí:** `agents/confirmation.py:305`
`int(loc.get("start_line") or 0)` trên JSON do LLM sinh, không bắt `ValueError`. Cả 3 đường gọi đều không có try bao quanh: `_build_evidence_pack` (qua `run():199`), `_build_verify_message` (:426, trước try/finally), patch-duel `_excerpt` (:514, ngoài try chỉ bọc `_call_llm` ở :527).
**Kịch bản lỗi:** LLM trả `"start_line": "42-60"` hoặc `"unknown"` → ValueError xuyên thẳng qua `ConfirmationAgent.run`/patch-duel, cả instance `localize()` sập ở giai đoạn cuối sau khi đã tốn toàn bộ LLM call; benchmark còn retry pipeline 3 lần cho lỗi deterministic này.

### #3 — `_verify_top` cắt tail sai sau khi dedupe → location trùng + rớt im lặng
**Vị trí:** `agents/confirmation.py:464`
Top-3 dedupe theo `file_path` có thể tiêu thụ >3 entry, nhưng `tail = locs[len(top):]` cắt theo số lượng đã dedupe → head và tail chồng lấn.
**Kịch bản lỗi:** `ranked_locations = [f1.A, f1.B, f2, f3, …]`: top tiêu thụ 4 entry để lấy 3 file, `tail = locs[3:]` chứa lại f3 → f3 xuất hiện 2 lần với 2 rank, f1.B bị rớt im lặng; `ranked_files`/`ranked_methods` bị đánh số lại trên list hỏng — làm bẩn metric ở chế độ hybrid.

### #4 — Guard "free-form navigation → tool loop" chết với NavigationAgent thường
**Vị trí:** `agents/confirmation.py:211`
Chỉ `PriorityNavigationAgent` set `navigation_was_freeform`/`suspicious_locations`; `NavigationAgent` thường (`navigation.py:216-257`) chỉ set `candidate_files`/`candidate_methods`/`agent_traces` — guard không bao giờ kích hoạt.
**Kịch bản lỗi:** `ENABLE_PRIORITY_EXPLORATION=false` (mặc định) + `CONFIRMATION_MODE=single/hybrid`: evidence pack backfill bằng excerpt 40 dòng đầu file (:324), đủ đếm `with_excerpts >= confirmation_min_evidence` (mặc định 3, `config.py:260`) → "sufficient" pass, tool loop bị bỏ qua, prompt còn dán nhãn sai file-head là "explorer-scored evidence" — đúng cấu hình mà guard được viết ra để chặn.

### #5 — `_verify_shot` thay nguyên output khi model echo JSON thiếu field
**Vị trí:** `agents/comprehension.py:436`
`result.output = revised` toàn phần hễ reply verify có `suspicious_files`, làm mất `fault_hypothesis`/`search_keywords`/`hypotheses` khi model chỉ echo JSON một phần.
**Kịch bản lỗi:** Verify pass trả JSON chỉ có `suspicious_files` (model hay echo delta dù prompt yêu cầu "SAME complete JSON"): `context.fault_hypothesis` rỗng → "No hypothesis available" lan sang Navigation/Confirmation, E1 không build hypothesis, `_needs_escalation` (:453) đọc confidence 0 và escalate tool loop vô ích dù ranking đang tốt.

### #6 — Retry wrapper: `raise None` khi retries=0 + phân loại lỗi bằng substring
**Vị trí:** `agents/base_agent.py:468` (vòng lặp :459-478)
Phân loại non-retryable bằng substring `'401'/'403'/'invalid_api_key'` trên `str(e)`; kết thúc bằng `raise last_exc` với `last_exc = None` khi `LLM_CALL_RETRIES=0` (`config.py:185` không có floor).
**Kịch bản lỗi:** Lỗi transient có request-id/port chứa chuỗi "401"/"403" bị coi là fatal và bỏ qua toàn bộ retry (đúng loại lỗi dashscope mà wrapper sinh ra để chống); 400 context-length/404 sai model thì lại retry đủ 5 vòng backoff (~30s phí/call); set `LLM_CALL_RETRIES=0` thì mọi LLM call chết bằng `TypeError: exceptions must derive from BaseException` trước khi gọi API.

### #7 — Reflection round 2 wipe sạch kết quả explorer round 1
**Vị trí:** `agents/priority_navigation.py:95` (+ `core/explorer.py:92`)
Visited-set persist qua `context.exploration_visited`; round 2 rebuild seed từ đúng các context field cũ → `FrontierQueue.push` từ chối tất cả vì `(kind, target)` đã visited → explorer trả 0 finding, rồi `context.suspicious_locations = locations` ghi đè **vô điều kiện** thành `[]`.
**Kịch bản lỗi:** Cấu hình ablation E2-only đã document (`EXPLORATION_FALLBACK_TO_FREEFORM=false`) + confirmation confidence dưới threshold → ranking explorer và evidence pack mất sạch tín hiệu explorer ở các round sau.

### #8 — Bare function name lọt vào ranking như file path
**Vị trí:** `agents/priority_navigation.py:439` (+ `core/explorer.py:~265-278, :324`)
Target `expand_callers`/`expand_callees`/`inspect_function` không có prefix `file.py::` được lưu bare function name làm `file_path` (`target.split('::')[0]`) và lọt vào `candidate_files`/RRF ranking.
**Kịch bản lỗi:** Observer đề xuất `{"target": "get_FIELD_display", "kind": "expand_callers"}` với relevance ≥6 → location `{file_path: "get_FIELD_display"}` đẩy lệch consensus rank của file thật; `_filter_nonexistent_files` của orchestrator cho path không extension đi qua (dòng ~843), và nếu filter rớt hết thì fallback never-empty (dòng ~861) trả nguyên **tên hàm** làm predicted file.

### #9 — Reranker bỏ qua retry wrapper, fail-open làm bẩn đo FE-3
**Vị trí:** `evaluation/reranker.py:195`
`_complete` gọi thẳng `self._client.chat.completions.create`, bỏ qua retry/backoff wrapper mà chính changeset này thêm vào `BaseAgent._call_llm`; rerank lại fail-open (:405-407).
**Kịch bản lỗi:** Connection reset transient (đúng loại lỗi làm mất ~69 instance ở run 300) → exception bị nuốt, kết quả ghi nhận im lặng là "no rerank" thay vì retry → số đo gate FE-3 bị pha loãng bởi các instance thực tế không được rerank.

### #10 — Sửa hid trùng có thể gán lại đúng hid đang trùng
**Vị trí:** `agents/hypothesis.py:295`
Repair duplicate bằng `h.hid = f"HYP{i+1}"` có thể gán lại đúng cái hid đang bị trùng.
**Kịch bản lỗi:** LLM trả hids `['HYP2','HYP2']`: i=1 đổi tên thành "HYP2" — vẫn trùng; `HypothesisTracker._by_id` (:95) map "HYP2" về chỉ hypothesis thứ hai → evidence verify cập nhật log-odds sai hypothesis, posteriors/`file_scores()`/`reflection_summary()` của E1 bị hỏng.

## 4. Mười lăm finding CONFIRMED ngoài top-10

### Crash / lãng phí tài nguyên
| # | Vị trí | Vấn đề |
|---|--------|--------|
| 11 | `scripts/run_swebench_lite_full.py:113` | `free_gb(CHECKOUT_ROOT)` gọi `shutil.disk_usage` không guard trên thư mục chưa tồn tại → `FileNotFoundError` sập cả run 300 instance trên máy mới (pinned-scan ở dòng 97 có guard `.exists()` nhưng disk-check thì không). |
| 12 | `scripts/run_swebench_benchmark.py:253` | Retry ×3 cho **mọi** exception (bare `except Exception` ở :295), kể cả lỗi deterministic → phí 2 lần pipeline đầy đủ cho lỗi chắc chắn lặp lại. |
| 13 | `config.py:60` | Monkey-patch `socket.getaddrinfo` **toàn process** ngay lúc import — pin DNS cho LLM host nhưng ảnh hưởng cả Neo4j/ChromaDB/git. |

### Trùng lặp nguy hiểm cho nghiên cứu (drift âm thầm làm lệch phép so sánh)
| # | Vị trí | Vấn đề |
|---|--------|--------|
| 14 | `evaluation/rank_fusion.py:17` vs `utils/ranking.py:7` | 2 bản RRF khác semantics (dict chuẩn hoá [0,1] mới vs list-of-tuple cũ); `orchestrator.py` import **cả hai** (~562 và ~1097-1102), `reranker.py` dùng bản cũ. |
| 15 | `agents/comprehension.py:74-99` vs `scripts/bare_llm_baseline.py:51-117` | File-tree builder + exclusion sets copy tay, byte-identical hôm nay — drift sẽ âm thầm làm lệch so sánh bare-vs-agentic (khác tree = khác input prompt). |
| 16 | `scripts/bare_llm_baseline.py:296-306` | Tự viết lại metrics aggregation thay vì gọi `evaluation/metrics.py::compute_metrics` (:102-155). `run_swebench_lite_full.py::aggregate()` tương tự nhưng PLAUSIBLE — không drop-in được vì merged rows chỉ giữ `predicted[:5]`; nên tách helper averaging chung. |
| 17 | `scripts/bare_llm_baseline.py:78-97` vs `scripts/run_swebench_benchmark.py:51-73` | `find_checked_out_instances` copy-paste. |
| 18 | `evaluation/reranker.py:213-227` vs `agents/base_agent.py:549-577` | `_parse_json` tự viết lại logic `_parse_output` (strip fence, tìm `{`...`}`). |

### Dead code
| # | Vị trí | Vấn đề |
|---|--------|--------|
| 19 | `rag/graph_retriever.py:492` | `hop_distance()` + `_hop_distance_cache` LRU 2048 entry không ai gọi. |
| 20 | `config.py:227` | `confirmation_single_shot` không ai đọc (đã thay bằng `confirmation_mode`). |
| 21 | `core/explorer.py:72` | `Observation.is_likely_fault_location` được prompt/parse ở mọi observe call nhưng không consumer nào đọc — tốn token vô ích. `EvidenceProbe.check_type` (`hypothesis.py:32`) tương tự. |
| 22 | 6 chỗ trong agents/scripts | Block usage-accounting (prompt/completion tokens) copy-paste 6 lần. |

### Hiệu năng
| # | Vị trí | Vấn đề |
|---|--------|--------|
| 23 | `agents/comprehension.py:87` | `build_repo_file_tree` walk toàn repo 2 lần/instance, không cache. |
| 24 | `rag/graph_retriever.py:480-482` | `file_hop_distances` chạy BFS riêng per-seed thay vì multi-source BFS. |
| 25 | `evaluation/reranker.py:392, :357` | Rerank passes chạy tuần tự (có thể song song); `_read_snippet` bỏ qua `tools/cache.py`. |

## 5. Hai candidate bị REFUTED khi verify (không phải bug)

1. **"Tạo mới `ListwiseReranker` mỗi `localize()` tạo OpenAI client mới"** — SAI: `BaseAgent.client` được phục vụ từ cache module-level `_shared_clients` (`base_agent.py:160-182`), chi phí không đáng kể.
2. **"`_verify_stage_message` smuggle context mong manh"** — ĐÃ ĐƯỢC KIỀM CHẾ: field khai báo trong dataclass `AgentContext` (`base_agent.py:80`), có try/finally reset (`confirmation.py:428-432`), không có đường re-entry.

## 6. Khuyến nghị thứ tự sửa

**Bắt buộc trước khi chạy FE-1/2/3 gates** (làm sai lệch trực tiếp số đo):
1. **#1** — bỏ `migrations` khỏi exclusion khi path nằm trong source tree của repo (hoặc chỉ exclude `migrations` của app-level test fixtures).
2. **#4** — set `navigation_was_freeform=True` trong `NavigationAgent` thường, hoặc guard theo `suspicious_locations` rỗng.
3. **#9** — route `_complete` của reranker qua retry wrapper; log rõ khi rerank fail thay vì fail-open im lặng (hoặc đánh dấu instance để loại khỏi phép đo FE-3).

**Chống crash giữa run benchmark:**
4. **#2** — bọc `int(...)` bằng try/except trong `_excerpt`.
5. **#6** — floor `LLM_CALL_RETRIES >= 1` + phân loại lỗi bằng `e.status_code` thay vì substring.
6. **#11** — guard `CHECKOUT_ROOT.exists()` trước `disk_usage`.

**Đúng đắn logic E1/E2:**
7. **#3, #5, #7, #8, #10** — theo mô tả từng mục ở trên.

**Khi rảnh:** hợp nhất RRF (#14), tách file-tree builder dùng chung (#15), dọn dead code (#19-22), tối ưu hiệu năng (#23-25).

## 7. Liên hệ với review trước

Báo cáo tĩnh 2026-07-12 (`docs/DANH_GIA_CODE_E1E2E3.md`) đã chỉ ra 2 điểm: `exploration_visited` chưa khai báo dataclass field, và 2 bản RRF song song. Review lần này xác nhận lại cả hai (RRF = finding #14) và bổ sung 10 bug correctness mà review tĩnh trước không phát hiện — khác biệt chính là lần này verify bằng cách truy vết đường gọi thực tế qua nhiều file (cross-file tracer + verifier đọc code gốc).
