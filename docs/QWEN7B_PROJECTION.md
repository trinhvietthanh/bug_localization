# Ước lượng sơ bộ: Backbone Qwen2.5-7B trên hệ thống này

> ⚠️ **CẢNH BÁO: TOÀN BỘ SỐ TRONG BẢNG DƯỚI ĐÂY LÀ ƯỚC LƯỢNG, CHƯA CHẠY THỰC NGHIỆM.**
> Mục đích: có một điểm neo hợp lý để đưa vào luận văn dưới dạng "kết quả dự kiến / cần kiểm chứng", **không được trích dẫn như kết quả đã đo**. Khi chạy thật (script + lệnh ở cuối tài liệu), thay bảng này bằng số thật và xóa khối cảnh báo.

---

## 1. Phương pháp ước lượng (căn cứ, không phải đoán tùy tiện)

Vì không có số đo trực tiếp của Qwen2.5-7B trên hệ thống này, ước lượng dựa trên **3 điểm neo** kết hợp suy luận về đặc tính mô hình nhỏ:

### Điểm neo 1 — Δ đóng góp của framework (đo được, trong chính hệ thống này)


|                                     | Top-1     | Top-3    | Top-5    |
| ----------------------------------- | --------- | -------- | -------- |
| qwen-plus, bare (1 call)            | 64.0%     | 83.7%    | 87.0%    |
| qwen-plus, full pipeline (E1+E2+E3) | 78.0%     | 87.3%    | 89.3%    |
| **Δ đóng góp framework**            | **+14.0** | **+3.6** | **+2.3** |


Nguồn: `results/cross_model/bare_qwen_300_final.json`, `results/swebench_300_e123_qwen.json` (n=300, paired, p=1.4×10⁻⁶ cho Top-1).

### Điểm neo 2 — Suy giảm theo kích cỡ model trên tác vụ SWE-bench agentic


| Model                                       | Kích cỡ | Resolve rate SWE-bench | Nguồn                                |
| ------------------------------------------- | ------- | ---------------------- | ------------------------------------ |
| DeepSeek-V3                                 | 671B    | 38.8%                  | khảo sát Skywork-SWE                 |
| Qwen2.5-72B-Instruct (SWE-agent)            | 72B     | 30.2%                  | khảo sát Skywork-SWE                 |
| SWE-SynInfer (Qwen2.5-Coder-7B, agentless)  | 7B      | 18.2%                  | search kết quả (nêu trong hội thoại) |
| Qwen2.5-7B-Instruct (EffGen, agentless thô) | 7B      | 0.67–2.67%             | EffGen paper                         |


Nhận xét: bước từ 72B → 7B giảm resolve-rate tuyệt đối khoảng 12 điểm (30.2 → 18.2) khi model 7B đã được fine-tune chuyên biệt cho agentless; nếu dùng model 7B **base, không fine-tune** trong vòng lặp agentic đầy đủ (nhiều tool call, JSON output có cấu trúc), độ suy giảm dự kiến lớn hơn đáng kể — mô hình nhỏ known yếu ở: (a) tuân thủ định dạng JSON nhiều vòng, (b) suy luận đa bước giữ ngữ cảnh, (c) độ chính xác quyết định "file nào patch sẽ sửa" (chính là cơ chế Patch Duel/Confirmation dựa vào).

### Điểm neo 3 — Cơ chế fail-open của hệ thống giảm nhẹ rủi ro suy giảm

Hệ thống có nhiều lớp fail-open được xác nhận qua mã nguồn (không phụ thuộc chất lượng suy luận của LLM): file-path validation never-empty (`orchestrator.py`), candidate pool đa nguồn (retriever + graph + path-keyword, không chỉ dựa LLM), forced-final-answer khi hết cap vòng. Các cơ chế này giữ cho **Top-5/recall ít bị sập hoàn toàn** ngay cả khi LLM yếu, nhưng **không cứu được Top-1** (phân định #1 luôn cần LLM suy luận tốt) — khớp với phát hiện nội bộ (Chương 4): mọi thử nghiệm nén suy luận đều mất Top-1 trước.

---

## 2. Bảng ước lượng


| Cấu hình                                  | Top-1     | Top-3     | Top-5     | MRR   | Calls/instance | Ghi chú                                                                                                               |
| ----------------------------------------- | --------- | --------- | --------- | ----- | -------------- | --------------------------------------------------------------------------------------------------------------------- |
| **Bare Qwen2.5-7B** (1 call)              | **28%**   | **45%**   | **56%**   | ~0.30 | 1              | Model nhỏ, ít kiến thức ngầm về repo cụ thể → không có "lợi thế contamination" như qwen-plus                          |
| **Full pipeline Qwen2.5-7B** (E1+E2+E3)   | **56%**   | **68%**   | **74%**   | ~0.60 | 40–50*         | Framework bù đắp phần lớn nhưng không hết; cap-hit dự kiến cao hơn (~70-80% thay vì 57%) do 7B khó tuân thủ tool-loop |
| Bare qwen-plus (đã đo)                    | 64.0%     | 83.7%     | 87.0%     | 0.738 | 1              | tham chiếu                                                                                                            |
| Full pipeline qwen-plus (đã đo, headline) | **78.0%** | **87.3%** | **89.3%** | 0.829 | 34.7           | tham chiếu chính                                                                                                      |


 *Số call dự kiến cao hơn qwen-plus vì model nhỏ thường cần nhiều vòng hơn để hội tụ, hoặc chạm cap thường xuyên hơn (forced-answer).*

**Diễn giải cho luận văn**: nếu số đo thật rơi vào khoảng trên, đây sẽ là bằng chứng bổ sung cho luận điểm "framework đóng góp giá trị độc lập với backbone" — mức tăng tuyệt đối Δ Top-1 có thể còn **lớn hơn** so với qwen-plus (vì bare 7B yếu hơn nhiều), dù điểm tuyệt đối cuối cùng thấp hơn do giới hạn năng lực suy luận của model nhỏ.

---

## 3. Cách chạy thật khi có thời gian (thay bảng ước lượng bằng số thật)

```bash
# .env — trỏ về Qwen2.5-7B qua Ollama/vLLM local hoặc DashScope
LLM_PROVIDER=openai
LLM_API_BASE=http://localhost:11434/v1        # nếu dùng Ollama
LLM_MODEL=qwen2.5:7b-instruct
LLM_TEMPERATURE=0.0

# Pilot 50 trước (bộ chuẩn, so paired được với mọi kết quả hiện có)
ENABLE_HYPOTHESIS_LOOP=true ENABLE_PRIORITY_EXPLORATION=true \
ENABLE_LISTWISE_RERANK=true ENABLE_HIERARCHICAL_NARROWING=true \
.venv/bin/python scripts/run_swebench_benchmark.py \
  --dataset princeton-nlp/SWE-bench_Lite --split test \
  --instance-ids-file results/cross_model/pilot50_ids.txt --workers 4 \
  --output results/swebench_50_qwen7b

# Bare baseline (để đo Δ framework của riêng 7B)
.venv/bin/python scripts/bare_llm_baseline.py \
  --instance-ids-file results/cross_model/pilot50_ids.txt --workers 2 \
  --output results/cross_model/bare_qwen7b_50
```

Sau khi có 2 file JSON trên, so paired McNemar bằng đúng script đã dùng nhiều lần trong dự án (đối chiếu `results/swebench_50_e123_qwen.json` để biết cách tính).

---

## 4. Giới hạn của ước lượng này (phải nêu trong luận văn nếu dùng)

- Không có dữ liệu neo trực tiếp cho "file-level localization accuracy của Qwen2.5-7B trong một pipeline agentic multi-tool giống hệ thống này" — các số neo ở mục 1 đo **resolve rate** (sửa lỗi thành công) hoặc **model khác kích cỡ**, không phải cùng metric/task chính xác.
- Khoảng tin cậy cố ý để rộng (±10 điểm) vì thiếu dữ liệu thực nghiệm trực tiếp.
- Đây là ngoại suy hợp lý cho mục đích trình bày dự kiến, **không thay thế được cho việc chạy thật** trước khi bảo vệ luận văn.

