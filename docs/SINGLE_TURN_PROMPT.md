k# Single-Turn Prompt chuẩn nghiên cứu — Bug Localization

Nguồn: `scripts/bare_llm_baseline.py` (`SYSTEM_PROMPT_RESEARCH` + user template trong `predict()`).
Chế độ: `--prompt research`. Mô hình: qwen-plus (DashScope). Decoding: `temperature=0.0`, `max_tokens=2048`, **1 LLM call / instance, không retrieval, không tool**.

---

## 1. Prompt đầy đủ (như thực tế gửi đi)

### 1.1. System message

```text
You are a senior software engineer and core maintainer of the repository below. Your task is FAULT LOCALIZATION: from a bug report, determine the source file(s) that most likely contain the defect that must be edited to resolve the report.

You will be given:
- REPOSITORY: the project name.
- BUG REPORT: the issue (title and body) describing the unexpected behaviour.
- FILE TREE: every source file in the repository, grouped by directory; each line is a repository-relative path.

Think step by step, enclosed in <reasoning> ... </reasoning>:
  1. Symptom: restate the observable failure in 1-2 sentences.
  2. Hypothesis: name the component / subsystem that plausibly implements the affected feature, and why it is implicated.
  3. Selection: pick the specific files most likely to contain the defect.

Then state your final decision in an <answer> ... </answer> block containing ONLY a JSON object on one line:
{"ranked_files": ["repo/rel/path_a.py", "repo/rel/path_b.py"]}

Hard rules:
- Rank from MOST to LEAST likely; put the file you would edit first to fix the bug at index 0.
- Copy every path VERBATIM from the FILE TREE (exact spelling, no leading "./", no edits, no comments).
- Do NOT invent paths that do not appear in the FILE TREE.
- De-duplicate; output at most 10 files.
- Emit ONLY the <reasoning> block followed by the <answer> block. No other text.
```

### 1.2. User message template

```text
REPOSITORY: {repo}

BUG REPORT:
"""
{problem_statement}
"""

FILE TREE ({n_files} source files, grouped by directory):
{file_tree_text}

Localize the defect now. Output <reasoning> then <answer>.
```

> `{file_tree_text}` được sinh bởi `build_tree_view()` — gom file theo thư mục, mỗi leaf là path repo-relative tuyệt đối (ví dụ `[django/conf]` rồi `  django/conf/global_settings.py`). `{problem_statement}` là bug report gốc (title + body). Decoding `temperature=0`, `max_tokens=2048`.

---

## 2. Phân tích cấu trúc — prompt tuân theo những khuôn mẫu nào

Prompt này là sự kết hợp của **6 khuôn mẫu prompt-engineering** có cơ sở lý luận, chồng lên nhau. Bảng tra cứu nhanh:

| # | Khuôn mẫu | Nguồn / lý luận | Đoạn trong prompt thể hiện nó |
|---|-----------|-----------------|-------------------------------|
| 1 | **RTF / RICE** (Role–Task–Context–Constraints–Format) | Cấu trúc prompt tiêu chuẩn, được dạy trong tài liệu Anthropic & OpenAI | Toàn bộ: câu 1 = Role+Task, "You will be given" = Context, "Hard rules" = Constraints, `<answer>` = Format |
| 2 | **Chain-of-Thought (CoT) — phân tầng, có giới hạn** | Wei et al. 2022 ("Chain-of-Thought Prompting…"); scratchpad/prefill | "Think step by step" + 3 bước Symptom → Hypothesis → Selection trong `<reasoning>` |
| 3 | **XML-tag Structured Prompting** | Anthropic "XML tagging" best practice; prefilling | Thẻ `<reasoning>...</reasoning>` và `<answer>...</answer>` tách process khỏi product |
| 4 | **Constrained / Schema Decoding** | "JSON mode" + closed-set constrained generation | Schema `{"ranked_files":[...]}` + luật "VERBATIM / Do NOT invent paths" (output phải thuộc tập file tree) |
| 5 | **Delimiter-bounded context** | OpenAI best practice (trích dẫn, """ , XML) | `"""` bao bug report; header `REPOSITORY:` / `FILE TREE:` tách vùng |
| 6 | **Zero-shot + Deterministic decoding** | Chuẩn baseline nghiên cứu (tránh cherry-pick few-shot) | Không có ví dụ mẫu; `temperature=0` để tái lặp |

Ngoài ra, **góc nhìn bài toán** bám theo **Agentless (Xia & Zhang, 2024, NeurIPS)** — giai đoạn file-level localization: cung cấp cấu trúc repo + yêu cầu xếp hạng, suy luận 2 cấp "component → file cụ thể" (giống cách lập trình viên đọc code khi localize lỗi).

### 2.1. Phân tích từng đoạn (annotation)

**① Role priming** — *"You are a senior software engineer and core maintainer…"*
→ Khuôn mẫu **RTF (Role)**. Ghim vai trò chuyên gia để kích hoạt tri thức miền (software engineering) và chuẩn hoá giọng điệu trả lời.

**② Task definition** — *"…FAULT LOCALIZATION: …determine the source file(s) that most likely contain the defect…"*
→ **RTF (Task)**. Đặt tên nhiệm vụ chuẩn học thuật ("fault localization") + định nghĩa output ở mức khái niệm (file cần sửa), không mặc định LLM tự đoán mục đích.

**③ Context contract** — *"You will be given: REPOSITORY / BUG REPORT / FILE TREE…"*
→ **RICE (Context)**. Liệt kê trước các khối đầu vào để mô hình "biết trước" sẽ nhận gì → giảm khuếch tán attention.

**④ Bounded Chain-of-Thought** — *"Think step by step, enclosed in `<reasoning>`… 1. Symptom 2. Hypothesis 3. Selection"*
→ **CoT (Wei 2022) + scratchpad**. "Think step by step" là trigger kinh điển; 3 bước là **decomposed CoT** ép suy luận theo thứ tự nhận dạng lỗi (symptom → chỗ nghi ngờ → file). Bounded (giới hạn 3 bước) tránh lan man; bọc trong thẻ để tách khỏi đáp án.

**⑤ Output schema / Format** — *"…`<answer>`… ONLY a JSON object… `{"ranked_files": [...]}`"*
→ **RTF (Format) + Constrained decoding**. Ép JSON 1 dòng, schema cố định `ranked_files` → parsing deterministic (chỉ cần regex `<answer>.*</answer>` rồi `json.loads`).

**⑥ Hard rules (Constraints)** — *"Rank MOST→LEAST… VERBATIM… Do NOT invent paths… ≤10 files… Emit ONLY…"*
→ **RICE (Constraints) + Closed-set constrained generation**. Quan trọng nhất: luật "VERBATIM / không bịa path" ép output thuộc tập file tree đã cho — dạng **constrained decoding mềm** (còn được cứng hoá bằng filter `valid_paths` sau khi parse để loại hallucination).

**⑦ Delimiter-bounded inputs (user msg)** — `"""…"""` quanh bug report, header rõ ràng
→ **OpenAI delimiter best practice**. Ngăn mô hình nhầm nội dung bug report (có thể chứa code/traceback) với chỉ thị.

**⑧ Determinism** — `temperature=0.0`, `max_tokens=2048`, zero-shot
→ Chuẩn baseline nghiên cứu: tái lặp được, không thiên lệch do few-shot chọn lọc, đủ room cho CoT.

### 2.2. Cấu trúc tổng thể (sơ đồ)

```
[SYSTEM]
  Role ─────────── senior maintainer
  Task ─────────── fault localization → ranked source files
  Context contract  3 khối đầu vào (REPOSITORY/BUG REPORT/FILE TREE)
  CoT scaffold ──── <reasoning>: Symptom → Hypothesis → Selection
  Format ────────── <answer>{ "ranked_files": [...] }
  Constraints ───── verbatim paths, rank order, ≤10, no hallucination
[USER]
  REPOSITORY + BUG REPORT("""…""") + FILE TREE (group-by-dir)
  trigger: "Output <reasoning> then <answer>."
[DECODING] temperature=0, max_tokens=2048, 1 call, no retrieval
```

### 2.3. Đánh giá tuân thủ — checklist nghiên cứu

| Tiêu chí baseline nghiên cứu | Đạt? | Ghi chú |
|---|:--:|---|
| Single-turn, 1 LLM call | ✅ | Cô lập "kiến thức tự có" của LLM |
| Không retrieval / không tool | ✅ | Là control so với agent system |
| Output schema cố định, parse deterministic | ✅ | `<answer>` + JSON + filter valid_paths |
| Chống hallucination (constrained output) | ✅ | Verbatim rule + post-filter |
| Tái lặp (temperature=0) | ✅ | |
| Zero-shot (không cherry-pick) | ✅ | |
| So sánh apples-to-apples (cùng metric) | ✅ | Dùng `top_n_accuracy/RR/AP` của hệ thống |
| Bounded CoT (cải thiện accuracy) | ✅ | A/B: research 80% > legacy 60% Top-1 (n=5) |

### 2.4. Cấu trúc KHÔNG dùng (cố ý)

- **Không few-shot**: tránh chọn ví dụ thiên lệch, giữ tính tổng quát qua repo.
- **Không ReAct / tool-use**: đây là baseline đơn lượt, không khám phá code.
- **Không retrieval (RAG)**: đúng định nghĩa "bare" — cô lập phần LLM "tự biết" (kể cả memorization từ train) khỏi giá trị agent thêm vào.

---

## 3. Kết quả thực nghiệm đi kèm (n=5, qwen-plus)

| Hệ thống | Top-1 | Top-5 | MRR | LLM calls/inst |
|---|---:|---:|---:|---:|
| Bare-Legacy (1 turn) | 60% | 80% | 0.650 | 1 |
| **Bare-Research (1 turn, prompt này)** | **80%** | **80%** | **0.800** | **1** |
| Agent system (multi-turn) | 80% | 80% | 0.822 | 25–30 |

⇒ Prompt chuẩn nghiên cứu (CoT + constrained decoding) thu hẹp gap giữa bare-LLM và hệ multi-agent từ 20đ xuống ~0đ Top-1, với ~1/25 chi phí. Đang chạy `bare_research_300` để lấy số prompt-ablation ở quy mô 300 (so legacy-300 = 64%/87%).
