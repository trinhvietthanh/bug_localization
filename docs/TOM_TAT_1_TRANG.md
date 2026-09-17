# TÓM TẮT LUẬN VĂN — 1 trang

**Đề tài:** Định vị lỗi mã nguồn bằng Hệ đa tác tử LLM kết hợp RAG và Code Property Graph
**Tác giả:** … · **GVHD:** … · **Năm:** 2026

---

### Bài toán
Cho một **bug report** (ngôn ngữ tự nhiên) và **mã nguồn** dự án → tìm **file/method chứa lỗi**, xếp hạng theo khả năng. Thách thức: bug report mơ hồ; lỗi thường **không** ở file được nhắc mà lan truyền qua **chuỗi gọi hàm**.

### Kiến trúc (3 tác tử + RAG + CPG)
`Bug Report → Comprehension (sinh giả thuyết) → PriorityNavigation (khám phá heap) → Confirmation (xác nhận + xếp hạng) → UnifiedScorer (10 tín hiệu) → ListwiseRerank → Ranked files`
Triết lý: **tách phần xác định (Python, reproducible) khỏi phần hiểu (LLM, đắt).** Backbone `qwen-plus`, chuẩn OpenAI (đổi provider được).

### 3 đóng góp phương pháp (bật/tắt độc lập)
- **E1 — Vòng lặp giả thuyết cạnh tranh:** sinh K=4 hypothesis, belief tracking log-odds thuần Python; LLM chỉ gắn nhãn bằng chứng.
- **E2 — Khám phá ưu tiên:** heap scheduler tách khỏi LLM (chỉ chấm điểm); context **O(1)**/bước, không tích lũy.
- **E3 — Listwise rerank:** thu hẹp file→function, hoán vị top-K, fail-open, permutation-only (recall bất biến).

### Kết quả chính — SWE-bench Lite, n=300

| Hệ thống | Top-1 | Top-3 | Top-5 | MRR | calls/inst |
|---|---:|---:|---:|---:|---:|
| Bare-LLM (1 call) | 64.0 | 83.7 | 87.0 | 0.738 | 1 |
| **Đề xuất (E1+E2+E3)** | **78.0** | **87.3** | **89.3** | **0.829** | ~34.7 |

### Claim cốt lõi (paired McNemar, n=300)
**+14 điểm Top-1** (59 thắng / 17 thua), **p = 1.4 × 10⁻⁶** — ý nghĩa rất mạnh.
Top-5 không có ý nghĩa (p=0.28): bare-LLM đã "nhớ" repo phổ biến → recall chạm trần ghi nhớ.
**Top-1 ngang SOTA** (78.0 vs LocAgent 77.7, BLAgent 78.6) dù backbone yếu hơn Claude-3.5/GPT-OSS.

### Phát hiện then chốt (ablation n=50, 6 thí nghiệm)
Mọi cố gắng **nén vòng lặp agent** (single-shot, hybrid) đều giảm **Top-1 5–10 điểm** dù Top-3/5 giữ nguyên.
⇒ **Vòng lặp agent = cơ chế chuyên biệt cho Top-1 discrimination, không thay bằng single-pass.**
Case study: agent & bare-LLA có **failure mode bổ sung nhau** (agent mạnh ở bug ngoài ghi nhớ).

### Negative result (minh bạch)
RRF + Patch Duel: NULL trên n=300 (−1.7đ, p=0.55) — tín hiệu subset n=100 là selection bias. **Giữ tắt mặc định.**

### Hạn chế
Memorization benchmark public (bare = cận trên suy luận thuần) · 300 inst đầu chưa random · đơn model qwen-plus.

### Kết luận (1 câu)
Hệ đa tác tử + RAG + CPG tạo ra **giá trị định vị thực sự, có ý nghĩa thống kê mạnh** so với bare-LLM; vòng lặp agent là cơ chế không thể nén cho Top-1.

---
*Tài liệu chi tiết: `docs/RESULTS_SUMMARY.md`, `docs/CHUONG_KIEN_TRUC.md`, `docs/BARE_LLM_BASELINE_REPORT.md`. Dữ liệu thô: `results/swebench_300_e123_qwen.json`, `results/cross_model/bare_qwen_300_final.json`.*
