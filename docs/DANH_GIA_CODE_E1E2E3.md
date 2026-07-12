# Đánh giá source code — vòng E1/E2/E3 (nhánh `feat/improve`, 2026-07-12)

**Phạm vi:** toàn bộ thay đổi chưa commit tại thời điểm đánh giá (12 file sửa + 17 file mới, ~5.500 dòng diff/code mới). Không đánh giá phần code đã commit trước đó (baseline `ec0e9dd`).
**Phương pháp:** đọc trực tiếp diff/toàn văn từng file, chạy test suite, kiểm chứng chéo các tham chiếu hàm/field giữa các module.

---

## 1. Tóm tắt thay đổi

| Nhóm | File | Mục đích |
|---|---|---|
| **E1 — Giả thuyết cạnh tranh** | `agents/hypothesis.py` (mới), `agents/verification.py` (mới) | K=3-5 giả thuyết falsifiable, belief tracking bằng log-odds thuần Python, `VerificationAgent` thu bằng chứng ủng hộ/bác bỏ |
| **E2 — Khám phá hàng đợi ưu tiên** | `core/explorer.py` (mới), `agents/priority_navigation.py` (mới) | Thay vòng lặp tool-calling tự do bằng priority-queue scheduler, LLM chỉ chấm điểm quan sát với context O(1) |
| **E3 — Rerank** | `evaluation/reranker.py` (mới), `evaluation/rank_fusion.py` (mới), `tools/repo_skeleton.py` (sửa) | Thu hẹp phân cấp file→function, listwise rerank top-K bằng evidence card, RRF đồng thuận đa tầng |
| **Comprehension v2/v3** | `agents/comprehension.py` (+359) | Single-shot tool-free đầu tiên, escalation có cấu trúc, verify-shot chống symptom/fix confusion, file-tree đầy đủ trong prompt |
| **Confirmation hybrid + H1** | `agents/confirmation.py` (+497) | 3 chế độ `loop/single/hybrid`, evidence pack từ explorer, patch-duel giữa #1/#2 |
| **Orchestrator wiring** | `agents/orchestrator.py` (+207) | Gắn 3 tính năng vào pipeline, patch-duel hook, never-empty-prediction fallback |
| **Config** | `config.py` (+119), `.env.example` (+38) | ~30 flag/tham số mới, mọi tính năng mặc định **tắt** |
| **Test** | 7 file test mới (~1.400 dòng) | Đơn vị cho tracker, explorer, reranker, RRF, confirmation, patch-duel |
| **Script vận hành** | `bare_llm_baseline.py`, `run_cross_model.py`, `run_swebench_lite_full.py` | Baseline không-agent để đo contamination, harness cross-model, driver chạy full 300 bug theo batch (giới hạn ổ đĩa) |

Tài liệu thiết kế (`docs/SYSTEM_DESIGN.md` §3.4-3.6, §4.6) đã mô tả rất chi tiết *lý do* và *giả thuyết nghiên cứu* (H6/H7/H8) đứng sau từng tính năng — tài liệu này tập trung vào **đánh giá chất lượng cài đặt**, không lặp lại phần thiết kế.

---

## 2. Đánh giá kiến trúc

### 2.1 Nguyên tắc thiết kế nhất quán, được tuân thủ tốt

- **Fail-open ở mọi nơi:** mỗi tính năng mới bọc trong `try/except` riêng và fallback về hành vi cũ khi lỗi (`ListwiseReranker.rerank`, `PriorityNavigationAgent.run`, `ComprehensionAgent._verify_shot`, `Orchestrator._run_patch_duel`). Không có đường nào mà bug ở tính năng mới có thể làm sập cả pipeline.
- **Contract bất biến cho downstream:** `fault_hypothesis` luôn = statement của hypothesis có prior cao nhất (dù E1 bật hay tắt); schema `suspicious_locations` của `PriorityNavigationAgent` giống hệt `NavigationAgent` cũ → Confirmation/scorer/pool không cần biết Navigation nào đang chạy.
- **Permutation-only cho E3:** `ListwiseReranker.rerank` chỉ hoán vị top-K, không bao giờ đổi tập hợp file (`validate_permutation` + kiểm tra `set(new_order) != set(top_files)`) — cô lập đúng biến số cần đo (Top-1/MRR) mà không ảnh hưởng Top-10/recall. Đây là điểm thiết kế thực nghiệm tốt, hiếm gặp trong code nghiên cứu.
- **Chống position bias có chủ đích:** evidence card không lộ rank/điểm tổng cho LLM rerank; thứ tự trình bày dùng `deterministic_shuffle` theo SHA-256(instance_id) — tái lập được nhưng khử tương quan với thứ hạng gốc.
- **Toán học belief tách khỏi LLM:** `HypothesisTracker` — LLM chỉ gắn nhãn direction/strength, mọi phép cộng log-odds là Python thuần, có thể audit độc lập với calibration của model.

### 2.2 Điểm cần lưu ý (không phải lỗi chặn, nhưng đáng sửa)

1. **`AgentContext.exploration_visited` là thuộc tính động, không khai báo trong dataclass.**
   `agents/priority_navigation.py:65` gán `context.exploration_visited = ...` nhưng field này không tồn tại trong `AgentContext` (`agents/base_agent.py`), khác với các field E1 khác (`hypotheses`, `hypothesis_tracker`, `navigation_was_freeform`...) đều được khai báo tường minh kèm docstring. Do dataclass không có `__slots__` nên gán vẫn chạy được (và nơi đọc dùng `getattr(..., None)` nên không crash), nhưng nó phá vỡ tính "một chỗ nhìn thấy toàn bộ state" mà các field khác đang tuân thủ, và không được reset khi context bị tái sử dụng giữa các bug khác nhau trong benchmark loop nhiều luồng — cần xác nhận `AgentContext` luôn được tạo mới mỗi instance (có vẻ đúng qua cách benchmark khởi tạo, nhưng nên khai báo field tường minh để không phụ thuộc giả định ngầm đó).
   → **Đề xuất:** thêm `exploration_visited: set = field(default_factory=set)` vào `AgentContext`.

2. **Hai cài đặt Reciprocal Rank Fusion song song.**
   `evaluation/rank_fusion.py::reciprocal_rank_fusion` (mới, trả `dict[str,float]` chuẩn hoá [0,1]) và `utils/ranking.py::reciprocal_rank_fusion` (đã có từ trước, trả `list[tuple]` có trọng số) làm cùng một việc với API khác nhau. `evaluation/reranker.py` lại **dùng cái cũ** (`utils.ranking.reciprocal_rank_fusion`) cho 2-pass merge trong khi `orchestrator.py` dùng cái mới cho RRF đồng thuận — tức cả hai đang cùng tồn tại và cùng được dùng ở hai nơi khác nhau trong cùng một PR. Không sai về hành vi, nhưng là cơ hội hợp nhất bị bỏ lỡ (một hàm nhận thêm tham số `normalize: bool` là đủ).

3. **`ComprehensionAgent.run` gọi `super().run()` (vòng lặp tool cũ) làm fallback escalation** — nghĩa là khi single-shot thiếu tự tin, chi phí có thể **cộng dồn** (1 shot + verify-shot + toàn bộ vòng lặp cũ), không thay thế. Đây là hành vi được ghi nhận đúng trong docstring/config comment ("trades 5-6đ Top-1 for saved calls") nên không phải bug, nhưng đáng nhắc: chế độ single-shot mặc định **tắt** (`comprehension_single_shot=false`) đúng vì ablation 50-run cho thấy kém hơn baseline — cấu hình mặc định trong repo hiện tại là an toàn.

4. **`ConfirmationAgent._verify_top` (hybrid mode) dùng field dùng chung `context._verify_stage_message`** để truyền payload cho `super().run()`, có comment giải thích rõ lý do (agent instance chia sẻ giữa các worker thread, nhưng context thì per-instance) — cách làm đúng, nhưng khá ngầm (side-channel qua context thay vì tham số). Chấp nhận được vì đã có `try/finally` dọn dẹp và đã có test (`test_confirmation_single_shot.py`) phủ đường này.

5. **`scripts/run_swebench_benchmark.py` lưu `agent_results` per-instance** (trừ `unified_scores` vì "quá cồng kềnh") — hợp lý cho việc attribute lỗi theo stage, nhưng cần theo dõi kích thước file JSON per-instance khi chạy 300 bug vì mỗi `agent_results` giờ có thêm `hypotheses`, `verification`, `listwise_rerank.cards` (có snippet code) — có thể vài chục KB/instance, nhân 300 vẫn ổn (~vài chục MB), không phải vấn đề thực tế nhưng đáng biết trước khi chạy full ablation.

### 2.3 Không phát hiện lỗi logic chặn (blocking bug)

Đã kiểm tra chéo các điểm rủi ro cao nhất và đều nhất quán:
- Field name giữa `AgentResult` (dùng `num_llm_calls`) và `LocalizationResult` (dùng `total_llm_calls`) — `ListwiseReranker._track` dùng đúng field theo đúng loại object được truyền vào ở từng nơi gọi.
- Các hàm được import chéo module (`extract_methods_from_locations`, `utils.ranking.reciprocal_rank_fusion`, `HypothesisTracker`, `EvidenceItem`) đều tồn tại đúng chữ ký.
- `HypothesisTracker.file_scores()` cho điểm âm (`FALSIFIED_FILE_SCORE = -0.4`) chỉ khi file đó **chỉ** xuất hiện trong hypothesis đã bị bác bỏ (không đè lên file cũng thuộc hypothesis còn sống) — đúng logic "không kéo giảm Top-1" như tài liệu thiết kế mô tả.
- `PriorityExplorer.frontier` (heap + visited-set + best-priority dedupe) xử lý đúng trường hợp một action được đẩy lại với priority cao hơn (stale-entry invalidation qua so sánh `_best_priority`) — đây là chi tiết dễ sai trong cài đặt priority-queue thủ công, đã làm đúng và có test riêng (`test_explorer.py`).

---

## 3. Kết quả kiểm thử

```
7 file test mới (hypothesis/rank_fusion/reranker/explorer/confirmation_single_shot/patch_duel/recall_fixes):
  87 passed, 0 failed

Toàn bộ tests/ (trừ test_tools.py không collect được):
  167 passed, 12 skipped, 14 failed
```

- **87 test mới đều pass.** Coverage hợp lý cho logic thuần Python (tracker, frontier, RRF, validate_permutation) — đúng chỗ nên test kỹ (không phụ thuộc LLM thật).
- **14 failure còn lại là nợ kỹ thuật có từ trước, không liên quan tới thay đổi lần này** — đã xác minh bằng `git stash` rồi chạy lại trên đúng commit `ec0e9dd`: cùng 14 test đó **đã fail từ trước** khi chưa có bất kỳ thay đổi nào của nhánh này.
  - `tests/test_reranking.py` (13 fail): gọi `Orchestrator._ensemble_vote(...)` — phương thức này không còn tồn tại trong `orchestrator.py` hiện tại (đã bị refactor/xoá ở một commit trước đó mà test không được cập nhật theo). **Lưu ý đặt tên:** file test này (`test_reranking.py`, kiểm thử ensemble-vote cũ) rất dễ nhầm với `evaluation/reranker.py` + `tests/test_reranker.py` mới (E3 listwise rerank) — hai khái niệm khác nhau, nên cân nhắc đổi tên `test_reranking.py` → `test_ensemble_vote.py` (hoặc xoá nếu tính năng đã chết) để tránh nhầm lẫn khi tìm kiếm.
  - `tests/test_skill.py::test_mcp_server_imports_without_error` (1 fail): thiếu module `mcp_server` — không liên quan đến bug localization pipeline.
  - `tests/test_tools.py` không collect được: `ImportError: cannot import name 'find_files' from tools.code_search` — cũng đã tồn tại từ trước.
- **Khuyến nghị:** dọn 3 file test hỏng này trong một lần dọn dẹp riêng (không thuộc phạm vi E1/E2/E3), vì hiện tại `pytest tests/` không thể chạy sạch toàn bộ, gây nhiễu khi CI/người khác muốn xác nhận "test có xanh không" cho riêng phần E1/E2/E3.

---

## 4. Đánh giá theo tiêu chí thiết kế thực nghiệm (luận văn)

Điểm mạnh nổi bật của vòng code này — quan trọng hơn cả bản thân tính năng — là **kỷ luật thiết kế cho khả năng đo lường (measurability)**:

- Mọi tính năng có **công tắc `.env` riêng, mặc định tắt** → trạng thái hiện tại của repo (checkout mới) tái lập chính xác baseline cũ, không có nguy cơ ablation "vô tình" chạy sai cấu hình.
- `docs/SYSTEM_DESIGN.md` đã định nghĩa **gate khả thi rẻ tiền (FE-1/2/3, §4.6)** trước khi đầu tư ma trận ablation đầy đủ — đúng thực hành khoa học, tránh đốt ngân sách API cho hướng không có tín hiệu.
- E3 (`agent_results["listwise_rerank"]["cards"]`) lưu lại evidence card đã serialize → có thể **replay offline để iterate prompt rerank mà không tốn lượt gọi agent thật** — thiết kế tiết kiệm chi phí thực nghiệm tốt, hiếm khi được nghĩ tới từ đầu.
- Cơ chế đo threat-to-validity đã được viết thành bảng riêng cho từng extension (Construct/Internal validity cho E1/E2/E3) trong SYSTEM_DESIGN.md — cho thấy tác giả đã lường trước câu hỏi phản biện (vd: gain của E2 có phải chỉ do context-pruning chứ không phải priority score → có sub-ablation `EXPLORATION_W_GRAPH=0` tách riêng).

## 5. Việc còn thiếu trước khi chạy ablation đầy đủ (theo đúng TODO đã tự ghi trong SYSTEM_DESIGN.md)

- [ ] Chạy gate FE-1/FE-2/FE-3 (cần API key + checkout benchmark) — **chưa chạy**, code mới chỉ mới qua unit test + smoke.
- [ ] Sửa 2 điểm ở §2.2 (mục 1, 2) — nhỏ, không chặn việc chạy gate.
- [ ] Dọn 3 file test hỏng từ trước (§3) để `pytest tests/` xanh toàn bộ.

## 6. Kết luận

Code mới (E1/E2/E3 + Comprehension v2/v3 + Confirmation hybrid + H1 patch-duel) có chất lượng cài đặt tốt: kiến trúc fail-open nhất quán, tách bạch rõ phần tất định (Python) khỏi phần LLM, có test đơn vị cho đúng phần cần test (logic thuần), và tài liệu thiết kế đi kèm chi tiết hiếm thấy. Không phát hiện lỗi logic chặn. Hai điểm nhỏ (field động chưa khai báo, hai cài đặt RRF song song) nên sửa nhưng không cấp bách. Việc còn lại là **chạy thực nghiệm** (FE-1/2/3 gate), không phải viết thêm code — đúng như trạng thái "đã cài đặt, chờ verify" đã ghi nhận trước đó.
