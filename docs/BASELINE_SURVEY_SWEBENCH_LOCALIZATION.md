# Khảo sát baseline công bố — Bug Localization trên SWE-bench Lite / Verified

> **Ngày khảo sát:** 2026-07-13
> **Mục đích:** Tổng hợp các kết quả file-level bug localization đã công bố trên SWE-bench Lite / SWE-bench Verified với các model LLM nhỏ (Qwen2.5-Coder 7B/14B/32B) và model tham chiếu (Claude-3.5, GPT-4o), làm baseline so sánh cho hệ multi-agent của luận văn.
> **Nguồn giá & tình trạng hosting:** kiểm tra trực tiếp qua OpenRouter API và DashScope API ngày 2026-07-13.

---

## 1. Cảnh báo về metric — đọc trước khi so sánh

Hai paper chủ lực dùng **định nghĩa metric khác nhau**, không được so chéo bảng:

| Paper | Metric | Định nghĩa | Số instances |
|---|---|---|---|
| CoSIL [1] | Top-N | **Ít nhất một** gold file nằm trong top N | 300 (đủ SWE-bench Lite) |
| LocAgent [2] | Acc@k | **Tất cả** gold files phải nằm trong top k (khắt khe hơn) | 274 (loại case không sửa function) |

- Định nghĩa của CoSIL **trùng với `top_k_accuracy`** trong `evaluation/` của hệ này → **so sánh trực tiếp được**.
- Muốn so với bảng LocAgent, phải tính thêm biến thể "all-gold-in-top-k" trên đúng 274 instances.
- Đa số instance SWE-bench Lite chỉ có 1 gold file nên hai metric gần nhau, nhưng thesis phải ghi rõ khác biệt này (mục Threats to Validity).

---

## 2. SWE-bench Lite (300 instances) — Qwen2.5-Coder, metric "ít nhất 1 hit"

Nguồn: **CoSIL, Table II** [1]. Nhóm tác giả CoSIL chạy lại cả 4 method trên cùng model → grid apples-to-apples.

| Model | Method | Top-1 | Top-3 | Top-5 |
|---|---|---:|---:|---:|
| Qwen2.5-Coder-**7B** | CoSIL | 41.0% | 57.7% | **63.7%** |
| Qwen2.5-Coder-7B | Agentless-FL | 33.3% | 53.7% | 57.7% |
| Qwen2.5-Coder-7B | OrcaLoca | **46.7%** | 49.0% | 49.0% |
| Qwen2.5-Coder-7B | LocAgent | 40.0% | 50.7% | 53.3% |
| Qwen2.5-Coder-**14B** | CoSIL | **58.3%** | **73.3%** | **76.7%** |
| Qwen2.5-Coder-14B | Agentless-FL | 53.7% | 68.7% | 72.3% |
| Qwen2.5-Coder-14B | OrcaLoca | 43.3% | 47.7% | 47.7% |
| Qwen2.5-Coder-14B | LocAgent | 51.0% | 63.3% | 65.0% |
| Qwen2.5-Coder-**32B** | CoSIL | 61.3% | **78.0%** | **83.7%** |
| Qwen2.5-Coder-32B | Agentless-FL | 58.3% | 73.0% | 77.3% |
| Qwen2.5-Coder-32B | OrcaLoca | 59.0% | 64.0% | 64.0% |
| Qwen2.5-Coder-32B | LocAgent | **64.7%** | 72.7% | 74.0% |

**Con số cần vượt (best-of-4-methods, per model):**

| Model | Top-1 cần vượt | Top-5 cần vượt |
|---|---|---|
| Coder-7B | 46.7% (OrcaLoca) | 63.7% (CoSIL) |
| Coder-14B | 58.3% (CoSIL) | 76.7% (CoSIL) |
| Coder-32B | 64.7% (LocAgent) | 83.7% (CoSIL) |

---

## 3. SWE-bench Verified — metric "ít nhất 1 hit"

Nguồn: **CoSIL, Table III** [1].

| Model | Method | Top-1 | Top-3 | Top-5 |
|---|---|---:|---:|---:|
| Qwen2.5-Coder-32B | CoSIL | 64.0% | 82.2% | 86.4% |

## 4. SWE-bench full (2,294 instances)

**Không có số liệu localization công bố** trên tập full trong các paper khảo sát được. Leaderboard SWE-bench full chỉ báo *% resolved* (bài toán sinh patch) — khác metric, không dùng làm baseline localization được.

---

## 5. SWE-bench Lite (274 instances) — metric "ALL gold files", model tham chiếu

Nguồn: **LocAgent, Table 3** [2]. Lưu ý: metric khắt khe hơn + tập nhỏ hơn (274) so với mục 2.

| Method + Model | Acc@1 | Acc@3 | Acc@5 |
|---|---:|---:|---:|
| BM25 (không LLM) | 38.7% | 51.8% | 61.7% |
| E5-base-v2 (embedding) | 49.6% | 74.5% | 80.3% |
| CodeRankEmbed (embedding) | 52.6% | 77.7% | 84.7% |
| Agentless + GPT-4o | 67.2% | 74.5% | 74.5% |
| Agentless + Claude-3.5-Sonnet | 72.6% | 79.2% | 79.6% |
| MoatlessTools + GPT-4o | 73.4% | 84.3% | 85.0% |
| MoatlessTools + Claude-3.5-Sonnet | 72.6% | 85.8% | 86.1% |
| SWE-agent + GPT-4o | 57.3% | 65.0% | 69.0% |
| SWE-agent + Claude-3.5-Sonnet | 77.4% | 87.2% | 90.2% |
| OpenHands + GPT-4o | 61.0% | 71.9% | 73.7% |
| OpenHands + Claude-3.5-Sonnet | 76.3% | 89.8% | 90.2% |
| LocAgent + Qwen2.5-7B (**fine-tuned**) | 70.8% | 84.7% | 88.3% |
| LocAgent + Qwen2.5-32B (**fine-tuned**) | 75.9% | 90.5% | 92.7% |
| LocAgent + Claude-3.5-Sonnet | **77.7%** | **92.0%** | **94.2%** |

Chi phí LocAgent báo cáo (Table 4 [2]): Claude-3.5 ≈ $0.66/instance; Qwen2.5-32B-ft ≈ $0.09; Qwen2.5-7B-ft ≈ $0.05.

**Lưu ý khi trích dẫn:** các hàng LocAgent + Qwen là bản **fine-tuned trên 768 trajectory** từ SWE-bench train set. Hệ của luận văn không fine-tune → khi so phải ghi rõ điều kiện khác nhau.

---

## 6. Vị trí hiện tại của hệ (tham chiếu nội bộ)

Kết quả hệ multi-agent (qwen-plus, 300 instances SWE-bench Lite, metric "ít nhất 1 hit", `results/swebench_300_e123_qwen.json`, 2026-07-10):

| Cấu hình | Top-1 | Top-3 | Top-5 | MRR |
|---|---:|---:|---:|---:|
| Full (E1+E2+E3) | 78.0% | — | 89.3% | 0.829 |
| Bare (1 LLM call) | 64.0% | 83.7% | 87.0% | 0.738 |

- Cao hơn mọi số ở mục 2, **nhưng dùng model lớn hơn nhiều (qwen-plus)** → chưa phải bằng chứng; cần chạy lại với đúng Qwen2.5-Coder 7B/14B/32B.
- Δ(full−bare) Top-1 = +14đ (McNemar p = 1.4e-6) — luận điểm chống contamination.

---

## 7. Tình trạng hosting Qwen2.5-Coder (kiểm tra 2026-07-13)

| Provider | Tình trạng | Ghi chú |
|---|---|---|
| OpenRouter | ❌ Không dùng được | Coder-7B/14B đã bị gỡ; Coder-32B còn ($0.66/$1.00 per M) nhưng `tools=False` — full mode bắt buộc tool calling |
| DashScope intl | ❌ Không có | Verify qua `GET /compatible-mode/v1/models`: chỉ còn họ qwen3-coder |
| SiliconFlow | ✅ Ứng viên chính | Docs xác nhận Coder-32B hỗ trợ function calling + parallel FC; OpenAI-compatible; ~$0.05–0.2/M [5] |
| Self-host (vLLM/Ollama) | ✅ Backup cho 7B | Codebase đã hỗ trợ OpenAI-compatible base URL |
| OpenRouter `qwen/qwen-2.5-7b-instruct` | ⚠️ Backup | $0.04/$0.10, tools OK — nhưng là bản non-coder, so với Table II yếu lý hơn |

**Chi phí ước tính full mode 300 instances** (~35 LLM calls/instance từ run e123 → ~120M input + ~5.5M output tokens/model, sai số ±35%): Coder-7B ≈ $7–12; Coder-14B ≈ $15–18; Coder-32B ≈ $25–35. **Tổng 3 size ≈ $50–70**; bare mode < $2/model.

## 8. Việc cần làm trước khi chạy

1. Tạo key SiliconFlow, thêm 3 spec Coder-7B/14B/32B vào `MODEL_SPECS` (`scripts/run_cross_model.py`).
2. Pre-check tool calling: `--models <tag> --modes full --limit 1` (model 7B hay lỗi tool schema; nếu fail vẫn còn 14B/32B).
3. Export token usage ra `per_instance` trong `run_swebench_benchmark.py` (orchestrator đã track `total_tokens`) → calibrate lại chi phí sau pilot 10 instances.
4. Khi viết thesis: tính thêm metric biến thể "all-gold" trên 274 instances nếu muốn so với bảng LocAgent (mục 5).

---

## Tài liệu tham khảo

[1] Jiang, Z. et al. *"CoSIL: Issue Localization via LLM-Driven Code Graph Searching"* (Table II, III). arXiv:2503.22424. https://arxiv.org/abs/2503.22424 — bản HTML: https://arxiv.org/html/2503.22424v2/

[2] Chen, Z. et al. *"LocAgent: Graph-Guided LLM Agents for Code Localization"* (Table 3, 4). ACL 2025. arXiv:2503.09089. https://aclanthology.org/2025.acl-long.426/ — bản HTML: https://arxiv.org/html/2503.09089v1 — code: https://github.com/gersteinlab/LocAgent

[3] OrcaLoca & Agentless-FL: số liệu lấy từ bản chạy lại của CoSIL (Table II [1]), không phải từ paper gốc.

[4] OpenRouter models API (giá + tool support, snapshot 2026-07-13): https://openrouter.ai/api/v1/models — trang model Coder-32B: https://openrouter.ai/qwen/qwen-2.5-coder-32b-instruct

[5] SiliconFlow — function calling support: https://docs.siliconflow.cn/en/userguide/capabilities/text-generation

[6] Kết quả nội bộ: `results/swebench_300_e123_qwen.json` (full, 2026-07-10), `results/cross_model/bare_qwen_300_final.json` (bare, 2026-07-10).
