# Tổng hợp kết quả thực nghiệm — Bug Localization trên SWE-bench Lite

**Hệ thống**: Multi-agent (Comprehension → Navigation/Explorer → Confirmation) + RAG + Code Property Graph
**Backbone LLM**: qwen-plus (DashScope, alias qwen-plus-2026-0330 ≈ Qwen3.6-Plus)
**Benchmark**: SWE-bench Lite, file-level localization
**Ngày tổng hợp**: 2026-07-13

---

## 1. Kết quả chính (n=300, đầy đủ)


| Hệ thống                                      | Top-1     | Top-3     | Top-5     | MRR       | LLM calls/instance | Thời gian/instance |
| --------------------------------------------- | --------- | --------- | --------- | --------- | ------------------ | ------------------ |
| Bare LLM (1 call, chỉ bug report + file tree) | 64.0%     | 83.7%     | 87.0%     | 0.738     | 1                  | 2.6s               |
| **Hệ thống đề xuất (E1+E2+E3)**               | **78.0%** | **87.3%** | **89.3%** | **0.829** | 34.7               | 262s               |
| E1+E2+E3 + RRF + Patch Duel                   | 76.3%     | 85.3%     | 89.3%     | 0.814     | 31.2               | 230s               |


### So sánh với SOTA (cùng benchmark, file-level)


| Hệ thống             | Backbone     | Top-1    | Top-3    | Top-5    |
| -------------------- | ------------ | -------- | -------- | -------- |
| **Hệ thống đề xuất** | qwen-plus    | **78.0** | **87.3** | **89.3** |
| LocAgent             | Claude-3.5   | 77.7     | 92.0     | 94.2     |
| BLAgent              | GPT-OSS-120B | 78.6     | —        | —        |
| Agentless            | GPT-4o       | 63.0     | —        | —        |
| CoSIL                | Qwen2.5-32B  | 61.3     | 78.0     | 83.7     |


Top-1 của hệ thống ngang bằng SOTA cùng phân khúc backbone; Top-3/5 thấp hơn LocAgent (dùng backbone Claude-3.5 mạnh hơn nhiều).

---

## 2. Claim chính đã chứng minh thống kê

### Agentic pipeline tạo giá trị thực so với bare LLM (paired, n=300)

So sánh trên cùng 300 instance, từng cặp:


| Metric | Bare LLM        | Hệ thống            | McNemar p    | Kết luận            |
| ------ | --------------- | ------------------- | ------------ | ------------------- |
| Top-1  | 64.0% (192/300) | **78.0%** (234/300) | **1.4×10⁻⁶** | Có ý nghĩa rất mạnh |
| Top-5  | 87.0% (261/300) | 89.3% (268/300)     | 0.28         | Không có ý nghĩa    |


**Diễn giải**: Hệ thống agentic cải thiện **+14 điểm Top-1** so với bare LLM (59 case agent thắng, 17 case bare thắng), với ý nghĩa thống kê rất mạnh. Trên Top-5, sự cải thiện không có ý nghĩa thống kê — bare LLM đã tiếp cận giới hạn recall nhờ kiến thức ngầm về các repo phổ biến trong dữ liệu huấn luyện.

**Chi phí đổi lại**: ~34× số LLM calls và ~100× thời gian.

---

## 3. Phân tích chi phí LLM (hệ thống đề xuất, ~34.7 calls/instance)


| Thành phần                     | Calls/instance | Tỷ lệ | Ghi chú                                                  |
| ------------------------------ | -------------- | ----- | -------------------------------------------------------- |
| Comprehension (cap 4 iter)     | ~2             | 6%    | step0: file tree trong prompt thay thế phần lớn khám phá |
| Navigation / Priority Explorer | ~12            | 35%   | 1 call "observe" mỗi action, cap 20 actions              |
| Confirmation (cap 10 iter)     | ~10.4          | 30%   | **62% run đốt hết cap**, đọc từng candidate file         |
| Reflection re-run (max 3 vòng) | ~9             | 26%   | chạy lại Nav+Conf khi confidence < 0.5                   |
| Rerank E3 + Patch Duel         | ~1.3           | 4%    | listwise + duel 1 call                                   |


**Phát hiện**: 57% tổng số calls là "cap-hits" (agent chạy hết số vòng cho phép) — Comprehension 87%, Confirmation 65%. Đây là dấu hiệu vòng lặp agent đang làm việc xác minh lặp, không phải khám phá mới.

---

## 4. Autopsy các case trượt (n=300 baseline)

### 4.1 Cấu trúc 22% trượt Top-1


| Bucket               | Số case | Tỷ lệ | Bản chất                                                      |
| -------------------- | ------- | ----- | ------------------------------------------------------------- |
| Gold ở rank 2–5      | 34      | 11.3% | Vấn đề **phân định** (chọn sai file trong cặp cùng subsystem) |
| Gold ở rank 6–10     | 3       | 1.0%  | Vấn đề xếp hạng                                               |
| Gold ngoài danh sách | 29      | 9.7%  | Vấn đề **recall** (gold không vào pool)                       |


Trong đó 21 case có gold ở **rank 2** (7%) — tiềm năng +7đ Top-1 nếu phân định đúng. Pattern phổ biến: nhầm "tầng liền kề" cùng subsystem (`options.py` vs `base.py`, `recorder.py` vs `executor.py`, `hooks.py` vs `models.py`).

### 4.2 Trượt Top-5 theo repo (29 case recall failure)


| Repo           | Số miss |
| -------------- | ------- |
| sympy          | 13      |
| django         | 10      |
| sphinx-doc     | 4       |
| pytest-dev     | 3       |
| sklearn/pylint | 2       |


3 case predicted rỗng (sphinx 10325/11445/8273) — lỗi FileValidation drop hết path không backfill.

---

## 5. Thí nghiệm ablation (n=50, paired)

Mục tiêu: đo tác động của từng thành phần lên Top-1/Top-5 và chi phí. Bộ 50 instance cố định, so sánh paired McNemar với baseline 80%/86%/90% trên cùng subset.


| #   | Cấu hình                                       | Top-1 | Top-3 | Top-5 | calls | Δ Top-1 vs base     | Ý nghĩa                     |
| --- | ---------------------------------------------- | ----- | ----- | ----- | ----- | ------------------- | --------------------------- |
| 0   | Baseline (subset)                              | 80%   | 86%   | 90%   | 34.6  | —                   | tham chiếu                  |
| 1   | Comprehension step0 (file tree + cap 4)        | 78%   | 84%   | 88%   | 29.3  | −2 (nhiễu, p=1.0)   | **giữ default**             |
| 2   | Comprehension single-shot                      | 74%   | 82%   | 94%   | 25.8  | −6 (mất cụm django) | Top-5 tăng, Top-1 giảm thật |
| 3   | Comprehension self-consistency escalation      | 72%   | 82%   | 88%   | 27.7  | −8                  | kém nhất Top-1              |
| 4   | Confirmation single-shot (listwise)            | 70%   | 92%   | 92%   | 20.9  | −10 (mất django)    | Top-3 cao nhất, rẻ nhất     |
| 5   | Confirmation hybrid (listwise + verify 3 iter) | 74%   | 90%   | 90%   | 22.2  | −6                  |                             |
| 6   | RRF consensus (4 stage rankings)               | 76%   | 88%   | 90%   | 31.3  | −4 (nhiễu)          | Top-3 +2                    |
| 7   | RRF + Patch Duel                               | 78%   | 88%   | 94%   | 31.4  | −2                  | Top-5 cao nhất              |


### 5.1 Kết luận nhất quán từ 6 thí nghiệm

Mọi cố gắng **nén vòng lặp agent** (single-shot comprehension, single-shot confirmation, hybrid) đều cho cùng **chữ ký thất bại**: Top-3/Top-5 giữ hoặc tăng, nhưng **Top-1 giảm 5–10 điểm**, mất cụm theo repo django (kiến trúc phân tầng: file triệu chứng ≠ file cần sửa).

→ **Vòng lặp xác minh lặp đi lặp lại của agent là cơ chế chuyên biệt cho Top-1 discrimination, không phải cho recall.** Đây là kết luận trần thuật chính của chương ablation.

---

## 6. Đánh giá RRF + Patch Duel trên mẫu đủ lớn (n=300)

Sau khi thí nghiệm n=50 và n=100 cho tín hiệu tích cực (100-subset: 87%/93%/94%, +6đ paired), chạy kiểm chứng trên đầy đủ 300 instance:


| Metric | Baseline | RRF + Patch Duel | Δ     | paired p     |
| ------ | -------- | ---------------- | ----- | ------------ |
| Top-1  | 78.0%    | 76.3%            | −1.7đ | 0.55 (nhiễu) |
| Top-3  | 87.3%    | 85.3%            | −2.0đ | 0.39 (nhiễu) |
| Top-5  | 89.3%    | 89.3%            | 0     | 1.00         |


**Kết quả: NULL.** Mọi delta nằm trong vùng nhiễu, thực tế hơi âm. Cải thiện quan sát trên subset 100 instance là **selection bias** (subset tình cờ chứa nhiều case rank-2 mà Patch Duel thắng) + nhiễu LLM, không tổng quát hóa ra 300.

### 6.1 Attribution Patch Duel trên 300 (đo chính xác qua `agent_results` per-instance)


| Kết quả                           | Số case |
| --------------------------------- | ------- |
| Duel phân xử                      | 296/300 |
| Swap (can thiệp)                  | 20      |
| Swap lật đúng (rank-2 → #1, gold) | 9       |
| Swap phá (demote gold #1 → #2)    | 5       |
| Swap trung tính                   | 6       |


Tỷ lệ đúng:sai thực = **1.8:1** (không phải 3:1 như subset 100 từng báo). Net +4 Top-1 từ Duel, nhưng RRF đánh đổi mất ~5 → tổng ≈ 0.

---

## 7. Kết luận và định hướng

### 7.1 Những gì đã chứng minh được

1. **Value-over-bare-LLM** (claim chính): +14đ Top-1, p=1.4×10⁻⁶, n=300 — đóng góp cốt lõi, sạch thống kê.
2. **Top-1 ngang SOTA** cùng phân khúc backbone (78.0% vs LocAgent 77.7%, BLAgent 78.6).
3. **Vòng lặp agent không thể nén**: 6 thí nghiệm ablation độc lập cho kết luận nhất quán — iterative verification là cơ chế tạo Top-1, không thay được bằng single-pass listwise.

### 7.2 Những gì không cải thiện

- RRF consensus và Patch Duel: tín hiệu thật (Duel đúng:sai 1.8:1) nhưng net ≈ 0 trên mẫu đủ lớn.
- Vòng tối ưu chi phí (giảm calls): khả thi (step0: 34.7→29.3) nhưng đánh đổi Top-1.

### 7.3 Headroom còn lại (theo autopsy)

- **Recall layer** (29 case gold-không-vào-pool, +10đ tiềm năng): BM25 + query augmentation (FastCode-style). Đây là hướng khả thi nhất chưa khai thác.
- **Phân định tầng-liền-kề** (21 case rank-2, +7đ): cần cơ chế hiểu "file nào patch sẽ sửa" mạnh hơn Patch Duel 1-call — có thể multi-round hoặc repair simulation.

### 7.4 Cấu hình khuyến nghị (production)

Giữ **baseline E1+E2+E3** (78.0/87.3/89.3 @ 34.7 calls) làm cấu hình chính. RRF/Patch Duel giữ dưới flag (`ENABLE_PATCH_DUEL`, `SCORE_WEIGHT_CONSENSUS`) làm tùy chọn, không bật default.

---

## Phụ lục: Tài liệu tham khảo

- LocAgent (Graph-Guided LLM Agents): arxiv.org/abs/2503.09089
- FastCode (86.1% Acc@1): arxiv.org/html/2603.01012v2
- CoSIL (Iterative Code Graph Search): arxiv.org/abs/2503.22424
- BLAgent (Agentic RAG): arxiv.org/pdf/2605.17965
- SweRank+ (Multi-turn reranking): arxiv.org/pdf/2512.20482
- SWE-Bench Illusion (contamination analysis): arxiv.org/abs/2506.12286

## Phụ lục: File dữ liệu thô


| File                                           | Mô tả                                           |
| ---------------------------------------------- | ----------------------------------------------- |
| `results/swebench_300_e123_qwen.json`          | Baseline 300 (headline chính)                   |
| `results/cross_model/bare_qwen_300_final.json` | Bare LLM 300                                    |
| `results/swebench_300_v2.json`                 | RRF+Duel 300 (kiểm chứng NULL)                  |
| `results/ablation_*.json`                      | 7 thí nghiệm ablation (n=50/100)                |
| `results/swebench_300_e123_qwen.log`           | Log chi tiết baseline (iter counts, tool calls) |


