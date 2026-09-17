# Kịch bản thuyết trình — Định vị lỗi mã nguồn bằng Hệ đa tác tử LLM + RAG + CPG

> **Cách dùng:** Tài liệu này là *lời nói* đi kèm slide của bạn — không phải nội dung trên slide.
> Mỗi phần có (1) mốc thời gian dự kiến, (2) gợi ý chuyển slide `[→ Slide: <chủ đề>]` để bạn tự map vào deck của mình, (3) chỉ dẫn sân khấu in *nghiêng*. Tổng ~35–37 phút, dư ~3–5 phút cho phần mở đầu/đề nghị câu hỏi.
> Nhịp nói tham khảo ~130–140 từ/phút. Câu in **đậm** là điểm cần nhấn giọng.

---

## 0. Mở đầu — Lời chào (00:00 – 01:30)

*[Đứng thẳng, quét mắt hội đồng, mỉm cười nhẹ. Mở slide tiêu đề.]*

Em xin kính chào Hội đồng. Em tên là …, mã sinh viên …. Hôm nay em xin trình bày đề tài luận văn **"Định vị lỗi mã nguồn bằng hệ đa tác tử LLM kết hợp Retrieval-Augmented Generation và Code Property Graph"**.

Em xin phép được trình bày trong khoảng 35 phút, gồm 7 phần: từ động lực, qua kiến trúc hệ thống, đến kết quả thực nghiệm trên 300 instance của benchmark SWE-bench, trong đó kết quả Top-1 đạt **78%** — ngang với các hệ thống tốt nhất hiện nay.

*[Đ chuyển sang slide lộ trình.]* Sau đây em xin đi vào nội dung.

---

## Phần I — Giới thiệu & động lực (01:30 – 06:30)

`[→ Slide: Bối cảnh / Bug localization là gì]`

Em xin bắt đầu từ câu hỏi: **định vị lỗi là gì, và tại sao nó quan trọng?**

Định vị lỗi — hay fault localization — là bước đầu tiên và cũng tốn kém nhất trong khâu sửa lỗi phần mềm. Cụ thể, cho một bug report bằng ngôn ngữ tự nhiên và mã nguồn của dự án, nhiệm vụ là tìm ra **file hoặc method nào chứa lỗi**, sắp xếp theo thứ tự khả năng.

Tài liệu nghiên cứu chỉ ra rằng, debugging chiếm **30 đến 50% tổng thời gian phát triển phần mềm**, và phần lớn thời gian đó dành cho việc **tìm vị trí** lỗi, chứ không phải sửa nó.

`[→ Slide: Hai thách thức]`

Bài toán này có hai thách thức cốt lõi. Thứ nhất, **bug report thường mơ hồ** — viết bằng ngôn ngữ tự nhiên, thiếu chính xác, đôi khi người báo cáo còn mô tả sai. Thứ hai — và đây là điểm tinh tế hơn — **codebase lớn và lỗi thường không nằm ở file được nhắc tới**. Lỗi có thể sinh ra ở một nơi, rồi lan truyền qua chuỗi gọi hàm và biểu hiện thành triệu chứng ở một nơi khác.

Ví dụ, bug report nói về một thông báo lỗi ở giao diện, nhưng nguyên nhân gốc nằm sâu trong một module phụ trợ mà report không hề nhắc tên.

`[→ Slide: Quy trình truyền thống vs đề xuất]`

*[Chỉ vào sơ đồ so sánh.]*

Với con người, cách truyền thống là: đọc report, rồi **duyệt mã nguồn** bằng từ khóa, bằng grep, dựa vào kinh nghiệm. Bước duyệt này chính là bottleneck — nó tốn hàng giờ, phụ thuộc kinh nghiệm, và rất dễ bỏ sót.

Hệ thống đề xuất tự động hóa bước đó. Đầu vào chỉ cần bug report và đường dẫn tới repository. Hệ thống sử dụng **ba tác tử LLM, kết hợp RAG và đồ thị thuộc tính mã nguồn**, xử lý trong khoảng **4 phút cho một instance**, và đạt **Top-1 78%**.

`[→ Slide: Câu hỏi nghiên cứu & đóng góp]`

Tuy nhiên, em muốn nhấn mạnh một điều: **mục tiêu của đề tài không chỉ là "đúng hơn một chút".** Cộng đồng nghiên cứu hiện vẫn đang tranh cãi rằng: *liệu một hệ thống agent phức tạp có thực sự tạo ra giá trị định vị, hay nó chỉ đắt hơn mà không đúng hơn so với việc gọi thẳng một LLM?* Đây chính là câu hỏi nghiên cứu trọng tâm.

Để trả lời, đề tài đưa ra ba đóng góp phương pháp — **E1, E2, E3** — và đánh giá nghiêm ngặt trên 300 instance với kiểm định thống kê. Kết luận trước để các anh/chị nắm định hướng: **có, hệ thống agent cải thiện 14 điểm Top-1, với ý nghĩa thống kê rất mạnh.** Em sẽ chứng minh chi tiết ở phần kết quả.

---

## Phần II — Cơ sở lý thuyết & công trình liên quan (06:30 – 10:00)

`[→ Slide: Các hướng tiếp cận]`

Em xin lướt nhanh bối cảnh nghiên cứu để đặt hệ thống vào đúng vị trí. Có ba hướng chính. Hướng thứ nhất là **IR-based** — cổ điển, khớp từ vựng giữa bug report và mã nguồn. Nhanh, nhưng "mù" ngữ nghĩa và bỏ qua cấu trúc. Hướng thứ hai là **học sâu** — học nhúng kết hợp từ vựng và ngữ nghĩa, nhưng cần dữ liệu huấn luyện và khó chuyển domain. Hướng thứ ba, cũng là hướng của đề tài, là **agentic** — dùng LLM suy luận nhiều bước kết hợp công cụ và RAG.

`[→ Slide: Nền tảng kỹ thuật]`

Về nền tảng, đề tài dùng **LLM qwen-plus** của Alibaba, giao tiếp theo chuẩn OpenAI. Đây là một quyết định thiết kế quan trọng: vì tuân thủ chuẩn mở, em có thể đổi provider — OpenRouter, Gemini, Ollama — **mà không phải sửa code**. Phần tri thức gồm **RAG** để bổ sung ngữ cảnh truy xuất và **Code Property Graph** để nắm bắt cấu trúc gọi hàm — và đây là phần khác biệt cốt lõi so với các hệ thống chỉ dùng RAG vector.

`[→ Slide: SOTA & khoảng trống]`

Về các hệ thống tốt nhất hiện nay trên cùng benchmark: LocAgent dùng Claude-3.5 đạt 77.7% Top-1, BLAgent dùng GPT-OSS-120B đạt 78.6%. Em rút ra **ba khoảng trống nghiên cứu** mà đề tài hướng tới lấp đầy. Thứ nhất, phần lớn các báo cáo chỉ công bố Top-1 mà **không so baseline bare-LLM cô lập**, nên không rõ phần đóng góp đến từ agent hay từ backbone mạnh. Thứ hai, ít hệ thống **tách bộ lập lịch ra khỏi LLM** — dẫn đến chi phí context tích lũy. Thứ ba, **thiếu phân tích autopsy** các case thất bại. Đề tài lấp đủ cả ba khoảng trống này.

---

## Phần III — Kiến trúc hệ thống (10:00 – 19:00)

*[Đây là phần dài nhất. Nói chậm rãi, có nhịp.]*

`[→ Slide: Tổng quan 5 lớp]`

Em chuyển sang phần kiến trúc. Hệ thống được tổ chức thành **năm lớp chức năng**. Lớp ngoài cùng là **giao tiếp** — CLI và REST API. Tiếp theo là **Orchestrator**, đóng vai trò điều phối vòng đời các tác tử. Lớp thứ ba là **hệ đa tác tử** — đây là phần trọng tâm. Lớp thứ tư là **tri thức** — gồm vector store Qdrant và Code Property Graph. Và lớp thứ năm là **công cụ** — 16 công cụ sandbox chia sẻ cache.

Một đặc điểm thiết kế then chốt: **ba đóng góp E1, E2, E3 đều là công tắc cấu hình độc lập, mặc định tắt, và không thay đổi schema đầu ra.** Điều này cho phép em thực hiện **ablation nghiêm ngặt** — bật tắt từng thành phần để đo tác động — mà không ảnh hưởng phần còn lại. Đây là cơ sở cho phần thực nghiệm ở sau.

`[→ Slide: C4 Context]`

*[Đ chuyển sang biểu đồ ngữ cảnh.]*

Biểu đồ ngữ cảnh định vị hệ thống trong môi trường vận hành. Người dùng chỉ cần cung cấp **bug report và đường dẫn repository**. Hệ thống tự đọc mã nguồn qua lớp công cụ, gọi LLM để suy luận, và trả về danh sách file xếp hạng. Bên ngoài có ba hệ thống: **LLM Provider, mã nguồn Git, và Bug Tracker**.

`[→ Slide: C4 Container]`

*[Chỉ vào biểu đồ container.]*

Bên trong ranh giới hệ thống có bảy container chính. Em muốn hội đồng chú ý **ba điểm thiết kế**. Thứ nhất, **Graph DB có fallback tự động**: nếu Neo4j kết nối lỗi, hệ thống chuyển sang đồ thị in-memory mà không sập pipeline — đây là tính fail-open quan trọng khi chạy benchmark quy mô lớn. Thứ hai, **Vector DB và Graph DB tách rời**: semantic search và structural traversal là hai luồng độc lập, chỉ hợp nhất ở tầng reranking cuối. Thứ ba — và đây là quyết định kiến trúc then chốt cho khả năng lặp lại thí nghiệm — **Explorer Engine là một container riêng biệt**, tách bộ lập lịch khỏi LLM, khiến em có thể **unit-test** nó mà không cần gọi API.

`[→ Slide: C4 Component]`

*[Đ chuyển sang component diagram.]*

Đây là góc nhìn chi tiết bên trong container hệ đa tác tử. Em highlight hai thành phần: **AgentContext** — một dataclass đóng vai trò bộ nhớ chung giữa các tác tử; và **HypothesisTracker** — thực hiện belief tracking bằng **log-odds thuần Python**. Ý tưởng là: LLM chỉ phụ trách gắn nhãn bằng chứng, còn việc tính toán cập nhật niềm tin là Python — vừa rẻ, vừa reproducible.

`[→ Slide: Luồng xử lý pipeline]`

*[Chỉ vào pipeline.]*

Em xin mô tả luồng xử lý theo cấu hình chuẩn — tức E1, E2, E3 bật, và hai cơ chế Patch Duel cùng RRF consensus **tắt** (em sẽ giải thích vì sao ở phần kết quả).

Một bug report đi qua **bốn pha**. **Phase 0 — Tiền xử lý**: trích stack trace, error message, file được nhắc, phân tích log. Song song, **Code Property Graph được dựng ở nền**. **Phase 1 — Comprehension**: tác tử này hiểu lỗi và sinh giả thuyết, sinh ra **bốn giả thuyết cạnh tranh** nếu E1 bật. **Phase 2 — PriorityNavigation**: khám phá mã nguồn qua heap scheduler. **Phase 3 — Confirmation**: xác nhận và xếp hạng. Nếu confidence thấp, hệ thống **chạy lại Phase 2-3 tối đa hai lần** với phản hồi định hướng — đây là vòng reflection duy nhất.

`[→ Slide: Sơ đồ tuần tự]`

*[Tóm tắt nhanh, không đọc hết.]*

Sơ đồ tuần tự minh họa tương tác theo thời gian. Em chỉ nhấn **hai đặc trưng**. Thứ nhất, trong Explorer, mỗi action gọi LLM với một **prompt độc lập** — không mang lịch sử — nên **chi phí token không tích lũy**, còn gọi là context O(1). Đây là khác biệt lớn so với tool loop tự do. Thứ hai, vòng reflection được dẫn dắt bởi `reflection_summary` từ HypothesisTracker.

`[→ Slide: Ba tác tử]`

Bảng này tổng kết ba tác tử. **Comprehension** có cap thấp — chỉ 4 vòng — vì nhiệm vụ của nó là sinh giả thuyết. **PriorityNavigation** có cap cao — 20 action — vì khám phá là phần tốn kém nhất. **Confirmation** ở giữa với 10 vòng cho việc xác minh. Việc gán cap khác nhau **phản ánh bản chất khác nhau** của từng nhiệm vụ.

`[→ Slide: Priority Explorer E2]`

*[Công thức priority trên slide.]*

Đóng góp E2 — Priority Explorer — vay mượn ý tưởng OrcaLoca: một frontier heap ưu tiên các action. LLM **chỉ chấm điểm relevance**, còn **scheduler quyết định thứ tự khám phá**. Công thức priority kết hợp ba số hạng: điểm relevance từ LLM, khoảng cách đồ thị từ CPG, và signal prior. Điểm graph tính theo `1/(1+distance)` từ multi-source BFS có cache. **Ý nghĩa lớn nhất: tách "quyết định đi đâu" — thuộc về Python — khỏi "hiểu gì" — thuộc về LLM.**

`[→ Slide: Unified Scorer]`

Sau khi các tác tử chạy xong, **Unified Scorer hợp nhất 10 tín hiệu** thành điểm tổng. Tín hiệu mạnh nhất là **stack trace**, với trọng số 2.5 — vì đỉnh stack thường gần lỗi nhất. Tín hiệu thứ 10 là **hypothesis support** từ E1. Thiết kế này cho phép thêm bớt tín hiệu dễ dàng mà không động tới agent.

`[→ Slide: RAG & CPG]`

Cuối cùng, lớp tri thức. Vector search cho ngữ nghĩa, CPG cho cấu trúc. Điểm quan trọng: **Graph Retriever kết hợp anchor matching với BFS expansion** — đây là cơ chế giúp hệ thống định vị được **file không được nhắc** trong bug report, đáp ứng đúng đặc tính lỗi lan truyền qua call chain mà em nhắc ở phần giới thiệu.

---

## Phần IV — Đóng góp phương pháp E1 / E2 / E3 (19:00 – 22:30)

`[→ Slide: Tổng quan 3 đóng góp]`

Em xin tóm gọn ba đóng góp và vấn đề mà mỗi cái giải quyết. **E1** giải quyết việc agent dừng sớm ở giả thuyết đầu tiên. **E2** giải quyết việc tool loop tự do đốt context tích lũy. **E3** giải quyết việc pointwise scoring sai thứ tự tương đối. **Triết lý chung xuyên suốt: tách phần xác định — Python, reproducible — khỏi phần hiểu — LLM, đắt.** Mọi quyết định mang tính thuật toán không nên giao cho LLM.

`[→ Slide: E1 — Giả thuyết cạnh tranh]`

Đóng góp E1 nhắm vào một pattern cụ thể: **nhầm "tầng liền kề" cùng subsystem** — ví dụ nhầm `options.py` với `base.py`. Giải pháp là sinh **bốn giả thuyết cạnh tranh** và theo dõi posterior bằng Python. Quyết định thiết kế: **LLM chỉ gắn nhãn bằng chứng**, không tự cập nhật niềm tin — vì ba lý do: tin cậy kém, không reproducible, và tốn call thừa.

`[→ Slide: E2 + E3]`

E2 em đã trình bày. E3 — ListwiseReranker — đáng chú ý ở ba điểm: evidence card **không mang điểm số hay thứ hạng** để tránh anchoring bias; permutation-only nên recall bất biến; và fail-open mọi lỗi. Còn **Patch Duel** — em đã đo trên 300 instance và kết luận **không đáng bật**, sẽ trình bày ở phần sau.

---

## Phần V — Thiết lập thí nghiệm (22:30 – 24:30)

`[→ Slide: Benchmark & metrics]`

Phần thiết lập em nói ngắn. Benchmark là **SWE-bench Lite**, em dùng 300 instance đầu. Ground truth là file được sửa trong gold patch. Các metric chuẩn: Top-1, Top-3, Top-5, MRR, MAP. **Em dùng đúng các hàm metric của hệ thống cho cả baseline lẫn agent**, để đảm bảo so sánh công bằng.

`[→ Slide: Baselines & kiểm định]`

Phần quan trọng nhất của tính thuyết phục nằm ở **cách thiết kế baseline**. Baseline là **bare-LLM cô lập** — chỉ một LLM call, không agent, không RAG, không tool. Và **cùng backbone qwen-plus** cho cả hai hệ thống. Nghĩa là, **biến đổi duy nhất là khung agent**. Kiểm định dùng **paired McNemar** trên từng cặp instance. Temperature = 0 để loại nhiễu lấy mẫu. Thiết kế này khiến kết luận "14 điểm là do agent" **rất khó bị phản bác**.

---

## Phần VI — Kết quả & phân tích (24:30 – 32:30)

*[Phần quan trọng nhất. Nói rõ, chậm.]*

`[→ Slide: Kết quả chính]`

*[Chỉ vào bảng/biểu đồ kết quả.]*

Em đi vào kết quả. Trên 300 instance, hệ thống đề xuất đạt **Top-1 78%, Top-3 87.3%, Top-5 89.3%**. So với bare-LLM, cải thiện **14 điểm Top-1**. Đáng chú ý, **Top-1 của đề tài ngang các hệ thống SOTA** — 78 so với 77.7 của LocAgent và 78.6 của BLAgent — **dù backbone qwen-plus yếu hơn nhiều** so với Claude-3.5 hay GPT-OSS-120B. Điều này cho thấy thiết kế agent hợp lý có thể **bù đắp backbone yếu hơn**.

`[→ Slide: Claim thống kê]`

*[Nhấn mạnh con số p-value.]*

Đây là claim cốt lõi, và em xin được trình bày cẩn thận. So sánh paired trên cùng 300 instance: hệ thống agent thắng bare-LLM **59 case**, thua 17 case, và cải thiện 14 điểm Top-1, với **p-value bằng 1.4 phần triệu**. Điều này có nghĩa: khả năng kết quả này do ngẫu nhiên là cực kỳ thấp.

**Nhưng em xin thành thật về một điểm**: ở Top-5, sự cải thiện **không có ý nghĩa thống kê** — p = 0.28. Lý do là bare-LLM đã **"nhớ"** nhiều repo phổ biến trong SWE-bench từ dữ liệu huấn luyện, nên đã tiếp cận giới hạn recall. Em sẽ nói rõ về vấn đề memorization này ở phần hạn chế.

`[→ Slide: Bare-LLM baseline]`

Phần baseline có hai kết luận tinh tế. Thứ nhất, **Chain-of-Thought không phải lúc nào cũng tốt**: nó đẩy Top-1 lên 3.7 điểm nhưng lại giảm Top-3 và Top-5 — vì CoT làm mô hình **tự tin và kén chọn**. Thứ hai — và quan trọng hơn — **ép "ít nhất năm ứng viên" là net-negative**: nó giảm mọi metric, kể cả Top-5. Đây là **negative result có giá trị**, bác bỏ giả thuyết phổ biến rằng "chỉ cần list nhiều file hơn thì Top-5 sẽ cao". Thực tế, các file pad thêm chỉ là noise.

`[→ Slide: Chi phí LLM]`

Về chi phí: hệ thống dùng khoảng **34.7 LLM calls mỗi instance** — gấp 34 lần bare. Em phát hiện: **57% số calls là cap-hits** — tức agent chạy hết vòng cho phép mà chưa ra kết quả. Điều này cho thấy phần lớn chi phí nằm ở **xác minh lặp**, không phải khám phá mới. Đây là headroom tối ưu rõ ràng.

`[→ Slide: Ablation]`

*[Slide trọng tâm — nói chậm.]*

Nhưng — và đây là điểm em muốn hội đồng đặc biệt lưu ý — khi em thử **nén vòng lặp agent** để giảm chi phí đó, kết quả rất nhất quán. Em thực hiện **sáu thí nghiệm ablation** khác nhau: comprehension single-shot, self-consistency, confirmation single-shot, confirmation hybrid... **Tất cả đều cho cùng một chữ ký thất bại**: Top-3 và Top-5 giữ nguyên hoặc tăng, nhưng **Top-1 giảm 5 đến 10 điểm**.

**Kết luận rút ra**: vòng lặp xác minh lặp đi lặp lại của agent **không phải để recall**, mà là **cơ chế chuyên biệt cho Top-1 discrimination**. Nó không thể thay thế bằng một lần gọi listwise rẻ hơn. Đây là một trong những đóng góp khoa học cốt lõi của đề tài.

`[→ Slide: Autopsy case thất bại]`

Em không chỉ báo số tốt mà còn **mổ xẻ case thất bại**. 22% case trượt Top-1 chia ba nhóm. Phần lớn — 11.3% — là **vấn đề phân định**: gold có trong top-5 nhưng không phải vị trí số một. Đặc biệt có **21 case gold nằm ở rank 2** — đây là headroom lớn nhất, tiềm năng cộng 7 điểm nếu phân định đúng.

`[→ Slide: Case study django vs seaborn]`

*[Hai instance đối lập.]*

Cuối cùng, một case study minh họa rõ nhất. Có hai instance đối lập. **django-10914**: đây là bug nổi tiếng, LLM đã "thuộc" — bare trả đúng ngay ở Top-1. Nhưng hệ thống agent lại **làm hỏng** vì listwise rerank đẩy sai hạng. Ngược lại, **seaborn-2848**: bug nằm ngoài tri thức LLM, bare sai cả hai chế độ, nhưng agent nhờ exploration và RRF **tìm ra đúng**. 

**Kết luận**: hai hệ thống có **failure mode bổ sung cho nhau**. Đây là lý do em không nói "agent luôn tốt hơn". Em nói: **agent thêm giá trị đúng chỗ — ở những bug ngoài tri thức ghi nhớ của mô hình.**

`[→ Slide: RRF + Patch Duel — kết quả âm]`

*[Minh bạch về negative result.]*

Em xin minh bạch một kết quả âm. Trên subset 100 instance, RRF và Patch Duel có vẻ cải thiện 6 điểm. Nhưng khi kiểm chứng trên đủ 300, kết quả là **NULL** — nằm trong vùng nhiễu, thực tế hơi âm. Em quy cho **selection bias**: subset tình cờ chứa nhiều case mà Duel thắng. Đây là bài học về kích thước mẫu, và là lý do em **giữ hai cơ chế này tắt mặc định**.

---

## Phần VII — Thảo luận & kết luận (32:30 – 36:00)

`[→ Slide: Threats to validity]`

Em xin trình bày các hạn chế của nghiên cứu. Quan trọng nhất là **memorization**: SWE-bench là benchmark public, bare-LLM có thể đã "thuộc" nhiều bug từ train. Vì vậy 64% Top-1 của bare là **cận trên** của khả năng suy luận thuần, không phải đo thuần. 

Em muốn hội đồng lưu ý một điểm nghịch lý thú vị: **hạn chế này lại củng cố kết luận của em.** Vì nếu bare đã lợi thế từ ghi nhớ mà vẫn thua agent 14 điểm, thì **giá trị thật của agent còn lớn hơn** con số bề ngoài.

`[→ Slide: Định hướng]`

Về hướng phát triển, autopsy chỉ ra hai hướng khả thi nhất. Thứ nhất — và khả thi nhất — là **tầng recall**: 29 case gold không vào pool, tiềm năng cộng 10 điểm nếu thêm BM25 và query augmentation. Thứ hai là **phân định tầng liền kề**: 21 case rank-2, cần cơ chế mạnh hơn Patch Duel một lần gọi — có thể multi-round.

`[→ Slide: Đóng góp chính]`

Em tóm tắt năm đóng góp. Hệ đa tác tử với ba đóng góp E1/E2/E3 có thể bật tắt độc lập. Giá trị gia tăng được chứng minh thống kê: **14 điểm, p = 1.4 phần triệu**. Top-1 ngang SOTA dù backbone yếu hơn. Kết luận ablation: vòng lặp agent là cơ chế chuyên biệt cho Top-1. Và phân tích autopsy lượng hóa headroom.

`[→ Slide: Kết luận]`

*[Nhìn hội đồng, chậm.]*

Để kết thúc, em xin đúc kết một câu: **hệ đa tác tử LLM kết hợp RAG và Code Property Graph tạo ra giá trị định vị thực sự so với bare-LLM**, với triết lý thiết kế xuyên suốt là **tách phần xác định — reproducible — khỏi phần hiểu — đắt**.

---

## Kết — Lời cảm ơn (36:00 – 37:00)

*[Slide cảm ơn / Q&A. Cúi nhẹ.]*

Trên đây là phần trình bày của em. Em xin **chân thành cảm ơn** Hội đồng, cùng các anh/chị/cô/thầy đã dành thời gian lắng nghe. 

Em đã chuẩn bị đầy đủ **dữ liệu thô, log chi tiết và tài liệu kiến trúc** để trả lời bất kỳ câu hỏi nào về tính khả lặp của kết quả. Em rất mong nhận được câu hỏi và những góp ý quý báu để hoàn thiện đề tài.

Em xin cảm ơn.

---

## Phụ lục A — Ghi chú luyện tập

- **Kiểm tra thời gian** ở 3 mốc: sau Phần III (target ~19:00), sau Phần V (target ~24:30), trước Q&A (target ~37:00). Nếu lệch >2 phút, cắt/bồi ở Phần III (linh hoạt nhất).
- **Câu dễ vấp**: số p-value "1.4 phần triệu" — luyện nói trơn; và "BFS expansion" — đọc chậm.
- **Nếu bị cắt giờ**: bỏ slide "Bare-LLM baseline" (CoT) và "Sơ đồ tuần tự" — không mất mạch.
- **Nếu được thêm giờ**: mở rộng case study django/seaborn và phần headroom.
- **Quản lý câu hỏi**: nếu câu hỏi xoáy số liệu cụ thể (chi phí, p-value), có bảng tra cứu sẵn; nếu câu hỏi ngoài phạm vi, trả lời ngắn rồi mời thảo luận thêm sau.

## Phụ lục B — Bản đồ slide ↔ kịch bản (để bạn điền số slide thực tế)

| Phần kịch bản | Slide chủ đề | Số slide của bạn |
|---|---|---|
| 0. Mở đầu | Tiêu đề, Lộ trình | … / … |
| I. Giới thiệu | Bối cảnh, Thách thức, Truyền thống vs Đề xuất, Câu hỏi NC | … |
| II. Cơ sở | Hướng tiếp cận, Nền tảng, SOTA | … |
| III. Kiến trúc | 5 lớp, C4×3, Pipeline, Tuần tự, 3 tác tử, E2, Scorer, RAG | … |
| IV. Đóng góp | Tổng quan, E1, E2+E3 | … |
| V. Thí nghiệm | Benchmark, Baselines | … |
| VI. Kết quả | KQ chính, Claim, Bare, Chi phí, Ablation, Autopsy, Case study, RRF NULL | … |
| VII. Kết luận | Threats, Định hướng, Đóng góp, Kết luận | … |
| Kết | Cảm ơn / Q&A | … |
