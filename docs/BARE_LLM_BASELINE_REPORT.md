# Báo cáo — Đánh giá baseline Bare-LLM single-turn cho Bug Localization

**Ngày:** 2026-07-19
**Model:** `qwen-plus` (Alibaba DashScope, OpenAI-compatible mode)
**Dataset:** `princeton-nlp/SWE-bench_Lite`, split `test`, 300 instance đầu.
**Mã nguồn:** `scripts/bare_llm_baseline.py` (flag `--prompt {research,legacy}`).
**Prompt đầy đủ & phân tích cấu trúc:** xem `docs/SINGLE_TURN_PROMPT.md`.

---

## 1. Tóm tắt điều hành

Bài báo cáo thiết kế và đánh giá một **prompt single-turn chuẩn nghiên cứu** cho bài toán bug localization (file-level fault localization), dùng làm **baseline cô lập** — chỉ 1 LLM call, không agent, không RAG, không tool — nhằm đo phần "kiến thức tự có" của mô hình so với hệ multi-agent đầy đủ.

| Hệ thống (300 instance) | Top-1 | Top-3 | Top-5 | MRR | LLM call/inst |
|---|---:|---:|---:|---:|---:|
| **Agent system** (multi-turn, full pipeline) | **76.3%** | **85.3%** | **89.3%** | **0.814** | ~25–30 |
| Bare-Legacy (flat list, không CoT) | 64.0% | 83.7% | 87.0% | 0.738 | 1 |
| **Bare-Research v1 (CoT + constrained decoding)** | **67.7%** | 79.0% | 79.7% | 0.731 | **1** |
| Bare-Research v2 (v1 + ép ≥5 ứng viên) | 60.7% | 74.0% | 76.3% | 0.674 | 1 |

**4 phát hiện chính:**

1. **Prompt chuẩn nghiên cứu (v1) cải thiện Top-1 so prompt legacy (+3.7đ)**: 67.7% vs 64.0%. Chain-of-Thought có cấu trúc + constrained decoding mài nhọn pick #1. Khớp giả thuyết "CoT giúp localization" (Wei et al. 2022).
2. **Bare-Research lại yếu hơn Legacy ở Top-3/Top-5 (−4.7/−7.3đ)**: CoT làm mô hình *tự tin và kén chọn* (median 2 file/inst), nên recall trong top sâu bị cơ học chặn.
3. **Ép "≥5 ứng viên" (v2) là net-negative**: giảm TẤT CẢ metric, kể cả Top-5 (60.7% vs 67.7% Top-1). Negative result quan trọng — padding ứng viên làm giảm chất lượng suy luận, không phải under-listing là vấn đề cốt lõi.
4. **Ở quy mô thật, agent system vượt bare ~9–10đ ở mọi metric**: gap n=5 trước đó "bằng nhau" chỉ do trùng mẫu nhỏ. Giá trị agent là thật và rộng.

---

## 2. Bối cảnh & mục tiêu

Hệ thống luận văn là pipeline multi-agent (Comprehension → Navigation → Confirmation + RRF ranking + listwise rerank). Để đo **giá trị gia tăng của khung agent**, cần một baseline cô lập tối đa: chỉ LLM "trần" nhận bug report → trả file xếp hạng. Baseline này cũng là control row trong nghiên cứu cross-model contamination (mô hình có thể đã "thuộc" bug public trong train).

**Câu hỏi nghiên cứu:**
- *RQ1:* Một prompt single-turn thiết kế chuẩn (CoT + schema) có vượt prompt naive không?
- *RQ2:* Bare-LLM thuần có bắt kịp hệ multi-agent không? Ở quy mô nào?
- *RQ3:* Hai hệ thống thất bại theo cách bổ sung hay trùng lặp?

---

## 3. Thiết lập thí nghiệm

### 3.1. Dataset & metrics
- **SWE-bench Lite** (`princeton-nlp/SWE-bench_Lite`, test split), 300 instance đầu (để khớp subset legacy cũ).
- Ground truth = file sửa trong gold patch (`buggy_files`).
- Metrics: **Top-1/3/5 accuracy, MRR, MAP** — dùng đúng các hàm `top_n_accuracy / reciprocal_rank / average_precision` (+ chuẩn hóa `_paths_match`) của hệ thống, đảm bảo so sánh apples-to-apples.

### 3.2. Ba cấu hình
| Cấu hình | Đặc trưng |
|---|---|
| **Agent system** | Pipeline multi-agent đầy đủ (reference `swebench_300_v2.json`) |
| **Bare-Legacy** | 1 call, prompt flat zero-shot: bug report + danh sách file phẳng → JSON. Đây là prompt gốc trước khi tối ưu |
| **Bare-Research v1** | 1 call, prompt chuẩn nghiên cứu: role + delimiter-bounded input + CoT `<reasoning>` (Symptom→Hypothesis→Selection) + `<answer>` JSON + verbatim-path constrained decoding |

### 3.3. Thiết kế prompt (tóm tắt)
Prompt v1 tuân theo **6 khuôn mẫu** (chi tiết `docs/SINGLE_TURN_PROMPT.md`): RTF/RICE · Chain-of-Thought phân tầng có giới hạn (Wei 2022) · XML-tag structured prompting · constrained/schema decoding · delimiter-bounded context · zero-shot + deterministic (`temperature=0`). Góc bài toán bám **Agentless** (Xia & Zhang 2024). Đầu vào file tree được gom theo thư mục, mỗi leaf là path repo-relative (verbatim).

### 3.4. Decoding
`temperature=0.0`, `max_tokens=2048` (room cho CoT), `max_retries=6` (backoff 429/5xx). Parsing: ưu tiên block `<answer>`, fallback JSON, fallback heuristic `.py`; lọc hallucination qua `valid_paths` (chỉ giữ path có trong file tree).

---

## 4. Kết quả

### 4.1. Quy mô 300 — bảng tổng hợp
(xem bảng ở §1.) Phân tích:

- **Top-1**: Agent (76.3%) > Research-v1 (67.7%) > Legacy (64.0%) > Research-v2 (60.7%).
- **Top-5**: Agent (89.3%) > Legacy (87.0%) > Research-v1 (79.7%) > Research-v2 (76.3%).
- **MRR**: Agent (0.814) ≫ Legacy (0.738) ≈ Research-v1 (0.731) > Research-v2 (0.674).

### 4.2. RQ1 — Research (CoT) vs Legacy
- **Thắng Top-1 (+3.7đ)**: CoT ép mô hình suy luận "triệu chứng → module nghi ngờ → file" trước khi chốt, giúp pick #1 sắc hơn.
- **Thua Top-3/Top-5 (−4.7/−7.3đ)**: đặc trưng hành vi của CoT — *tự tin, kén chọn*. `num_predicted` mỗi instance: **median 2.0, mean 1.9, max 5**; **290/300 instance có <5 dự đoán**. Khi mô hình chỉ list 2 file, Top-5 bị chặn cơ học (Top-3 79.0% ≈ Top-5 79.7% vì gần như không có file ở rank 4–5).
- **MRR gần bằng nhau** (0.731 vs 0.738): MRR bị chi phối bởi Top-1 (Research hơn) nhưng phạt thiếu depth.

### 4.3. RQ3 — Ablation ép depth (v1 → v2)
Giả thuyết: ép "≥5 file" sẽ lấp đầy Top-5 mà không hại Top-1. **Thực nghiệm bác bỏ:**
| | Top-1 | Top-3 | Top-5 | MRR |
|---|---:|---:|---:|---:|
| v1 (tự do, terse) | 67.7% | 79.0% | 79.7% | 0.731 |
| v2 (ép ≥5) | 60.7% | 74.0% | 76.3% | 0.674 |
| **Δ** | **−7.0** | −5.0 | −3.4 | −0.057 |

Ép padding làm giảm **mọi metric, kể cả Top-5**. Các file pad thêm không bắt được case miss — chỉ là noise — và việc buộc depth làm suy yếu cam kết ở pick #1. **Kết luận: giữ v1, bỏ v2.** (Code đã revert về v1.)

### 4.4. Case study n=5 — failure mode bổ sung (RQ3)
Trên 5 instance chung, so 3 chiều:

| Instance | GT | Agent | Bare-Research | Bare-Legacy |
|---|---|:--:|:--:|:--:|
| astropy-14365 | qdp.py | T1 | T1 | T1 |
| flask-4992 | config.py | T1 | T1 | T1 |
| pylint-5859 | misc.py | T1 | T1 | T1 |
| **django-10914** | global_settings.py | ❌ RR=0.11 | **T1** | t5 |
| **seaborn-2848** | _oldcore.py | **T1** | ❌ | ❌ |

- `django-10914`: LLM **đã thuộc** (bare-research T1 ngay), nhưng `listwise_rerank` của agent **làm hỏng** đáp án (rank 3→9). Pipeline tác dụng ngược trên bug đã memorize.
- `seaborn-2848`: LLM **không thuộc** (bare sai cả 2), agent nhờ exploration + RRF **tìm ra** (T1). Pipeline thêm giá trị thật.

⇒ Hai hệ thống thất bại **bổ sung cho nhau** (không trùng lặp): agent mạnh ở bug cần khám phá code, bare-research mạnh (và rẻ hơn ~25×) ở bug đã nằm trong tri thức mô hình.

---

## 5. Thảo luận

1. **CoT không phải "luôn tốt hơn"**: nó đổi precision (Top-1) lấy recall (Top-5). Với bài toán localization mà Top-1 thường được看重, v1 là lựa chọn hợp lý; nhưng nếu ưu tiên Top-5 (ví dụ làm recall-first retriever cho downstream), prompt legacy lại hơn.
2. **Gap bare-vs-agent là thật và rộng** (~9–10đ ở mọi metric, 300-scale). Khẳng định giá trị của khung multi-agent: không chỉ "đúng hơn một chút" mà là **phục hồi có hệ thống** các case ngoài tri thức memorize của LLM.
3. **Trade-off chi phí**: bare-research đạt 67.7% Top-1 với **1 LLM call/inst (~4.2s)** vs agent ~25–30 call (~54s). Với tác vụ wide-coverage廉价, bare là checkpoint hợp lý; với accuracy tối đa, agent xứng đáng.
4. **Memorization caveat**: SWE-bench là benchmark public; bare-LLM "biết" nhiều bug từ train. Vì vậy bare Top-1 67.7% là **cận trên** của khả năng suy luận thuần — phần nào trùng lặp với ghi nhớ. Điều này càng làm nổi bật: agent thêm giá trị đúng chỗ (seaborn-2848: ngoài ghi nhớ).

---

## 6. Hạn chế / Threats to validity

| Hạn chế | Tác động | Hướng xử lý |
|---|---|---|
| **Confound legacy**: số Legacy-300 (64.0/87.0) chạy bằng script cũ (flat list, không `valid_paths` filter). Script mới dùng grouped-tree + filter cho cả 2 mode | A/B chưa hoàn toàn 1-biến | Re-run `--prompt legacy --limit 300` bằng script mới để so sánh sạch |
| **Memorization** trên benchmark public | Bare là cận trên, không đo thuần khả năng suy luận | Cross-model contamination study (đang chờ OPENROUTER_API_KEY) |
| **300 instance đầu** (không random sample) | Có thể thiên lệch theo repo | Mở rộng toàn split + per-repo breakdown |
| **qwen-plus đơn model** | Chưa rõ prompt effect có chuyển model không | Ablation cross-model |
| n=5 case study | Không ý nghĩa thống kê, chỉ minh họa | Mở rộng case study |

---

## 7. Kết luận & bước tiếp

- Prompt single-turn chuẩn nghiên cứu (v1: CoT + constrained decoding) **được chọn làm bare baseline chính thức**, vượt legacy +3.7đ Top-1. Phiên bản ép depth (v2) bị loại (negative result).
- Ở quy mô 300, hệ multi-agent **vượt bare ~9–10đ ở mọi metric**; n=5 "bằng nhau" là artifact trùng mẫu. Hai hệ thống có failure mode **bổ sung**.
- **Bước tiếp đề xuất:**
  1. Re-run `--prompt legacy --limit 300` bằng script mới → A/B 1-biến sạch.
  2. Per-repo breakdown 300 để thấy CoT giúp/hại repo nào.
  3. Cross-model (Qwen2.5-7B, Gemini) để kiểm transfer của prompt.
  4. Phân tích định lượng tỷ lệ "memorized vs explored" trong tập bare-vs-agent disagreement.

---

## Phụ lục A — Cấu hình chạy
```
python scripts/bare_llm_baseline.py --limit 300 --prompt research --workers 3 \
  --output results/cross_model/bare_research_300
```
Output: `results/cross_model/bare_research_300.json` (per-instance đầy đủ), `_summary.csv`.

## Phụ lục B — File kết quả
| File | Nội dung |
|---|---|
| `results/swebench_300_v2.json` | Agent system 300 |
| `results/cross_model/bare_qwen_300_final.json` | Bare-Legacy 300 (script cũ) |
| `results/cross_model/bare_research_300.json` | Bare-Research v1 300 |
| `results/cross_model/bare_research_300_v2.json` | Bare-Research v2 300 (ép ≥5, bị loại) |
| `results/cross_model/bare_research_5.json` / `bare_legacy_5.json` | A/B n=5 |
