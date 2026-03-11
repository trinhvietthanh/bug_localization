# Phương pháp tiếp cận Dựa trên Đa đặc vụ (Multi-Agent Approach) cho bài toán Bug Localization

## 1. Tổng quan phương pháp (Overview)

Thay vì sử dụng các phương pháp truy xuất thông tin (Information Retrieval - IR) truyền thống như TF-IDF hay BM25 để tính độ tương đồng giữa báo cáo lỗi (Bug Report) và mã nguồn (Source Code), phương pháp được đề xuất trong nghiên cứu này áp dụng **Khung làm việc Đa đặc vụ kết hợp RAG** (Multi-Agent RAG-enhanced Framework). 

Điểm khác biệt cốt lõi là hệ thống hoạt động như một thực thể tự chủ (Autonomous Entity). Quá trình tìm lỗi không chỉ là một phép so sánh chuỗi đơn thuần, mà là một quy trình lặp đi lặp lại gồm: Đọc hiểu lỗi $\rightarrow$ Lên kế hoạch tìm kiếm $\rightarrow$ Duyệt mã nguồn qua các công cụ $\rightarrow$ Suy luận và Xác nhận nguyên nhân gốc rễ. 

Mô hình này mô phỏng chân thực cách một kỹ sư phần mềm con người xử lý một bug mới.

## 2. Kiến trúc Đa đặc vụ (Multi-Agent Architecture)

Hệ thống được chia thành một Đặc vụ Điều phối (Orchestrator) và ba Đặc vụ Chuyên trách, tương ứng với 3 giai đoạn cụ thể của quy trình gỡ lỗi.

### 2.1. Đặc vụ Điều phối (Orchestrator)
Orchestrator đóng vai trò là "nhà quản lý" toàn bộ vòng đời của việc định vị lỗi. Nhiệm vụ chính của Orchestrator bao gồm:
- **Khởi tạo dữ liệu**: Tiếp nhận Bug Report, tiến hành tiền xử lý (Preprocessing) và sao chép (checkout) phiên bản mã nguồn chứa lỗi.
- **Khởi tạo Ngữ cảnh và RAG**: Xây dựng AgentContext, đồng thời khởi chạy ngầm tiến trình trích xuất **Code Property Graph** (Graph RAG) từ codebase để cung cấp cho các agent hiểu về cấu trúc dự án.
- **Tiếp nối quy trình**: Gọi lần lượt các tác tử theo thứ tự: *Comprehension* $\rightarrow$ *Navigation* $\rightarrow$ *Confirmation*, đồng thời truyền kết quả (giả thuyết, tập tin nghi ngờ) từ tác tử trước sang tác tử sau.

### 2.2. Giai đoạn 1: Đặc vụ Đọc hiểu (Comprehension Agent)
- **Nhiệm vụ**: Phân tích cú pháp và ngữ nghĩa của báo cáo lỗi do người dùng hoặc hệ thống cung cấp (bao gồm mô tả lỗi, Stack Trace, và các dòng log).
- **Hành động**: Trích xuất các hàm, file hoặc từ khóa quan trọng có trong báo cáo. Xây dựng **Giả thuyết lỗi (Fault Hypothesis)** ban đầu giải thích lý do tại sao lỗi có thể xảy ra.
- **Đầu ra**: Tập hợp danh sách các file khả nghi ban đầu và một văn bản giả thuyết về bản chất của lỗi.

### 2.3. Giai đoạn 2: Đặc vụ Điều hướng và Tìm kiếm (Navigation Agent)
- **Nhiệm vụ**: Đóng vai trò như một điều tra viên để khám phá dự án và thu hẹp phạm vi tìm kiếm.
- **Hành động**: Dựa vào giả thuyết từ Phase 1, đặc vụ này sử dụng các công cụ (Tools) để tương tác trực tiếp với codebase. Nó có thể tìm kiếm từ khóa (Grep), xem trước thư mục gốc (LS), đọc mã nguồn (FileReader) hoặc phân tích Cây cú pháp trừu tượng (AST Parser). Trong quá trình duyệt codebase, đặc vụ này cũng có thể sử dụng kết quả từ **Graph RAG** để tìm ra các hàm gọi chéo (cross-references).
- **Đầu ra**: Danh sách các tập tin ứng viên (Candidate Files) có xác suất chứa lỗi cao nhất đã được thu hẹp.

### 2.4. Giai đoạn 3: Đặc vụ Xác nhận (Confirmation Agent)
- **Nhiệm vụ**: Đánh giá và xếp hạng (Ranking) tính chính xác của các tập tin ứng viên.
- **Hành động**: Xem xét kỹ nội dung mã nguồn của các candidate files. Đưa ra lập luận logic để xác định đoạn code (Function/Class) thực sự gây ra hiện tượng mô tả trong Bug Report. 
- **Đầu ra**: Danh sách các vị trí code gây lỗi được xếp hạng (Ranked Locations) kèm theo một bản báo cáo Phân tích Nguyên nhân Cốt lõi (Root Cause Analysis).

## 3. Hệ thống Công cụ và Nhận thức Môi trường (Tooling & Environment Awareness)

Các đặc vụ được cấp quyền "Nhận thức môi trường" nhờ vào hệ thống Công cụ (Skills) được tích hợp trực tiếp, giúp xóa nhòa khoảng cách giữa Mô hình Ngôn ngữ Lớn (LLM) và hệ thống tập tin thật.

- **Công cụ Tìm kiếm mã nguồn (CodeSearch)**: Tìm kiếm chuỗi văn bản thuần hoặc biểu thức chính quy (Regex) trong toàn bộ repository.
- **Công cụ Điều hướng (Navigation)**: Quan sát cây thư mục của dự án để hiểu cấu trúc mô-đun.
- **Công cụ Đọc tập tin (FileReader)**: Cho phép Agent đọc một phần hoặc toàn bộ một file mã nguồn để phân tích chi tiết.
- **Công cụ trích xuất Cú pháp (ASTParser)**: Phân giải tệp Python/Java thành cấu trúc cây Cú pháp trừu tượng, qua đó thống kê chính xác tên hàm, class và giới hạn phân tích.

## 4. Tăng cường Suy luận bằng RAG và Graph RAG

Để vượt qua giới hạn ngữ cảnh của LLM khi xử lý các repository lên tới hàng ngàn file, phương pháp sử dụng **Hệ thống Truy xuất Tăng cường Kế sinh (RAG)**:
- **Vector Base RAG**: Lưu trữ và truy xuất các đoạn mã nguồn (code snippets / chunks) có sự tương đồng ngữ nghĩa bằng Dense Embeddings (nhúng vecto).
- **Graph RAG (Code Property Graph)**: Tiến xa hơn RAG thông thường bằng cách xây dựng biểu đồ tri thức của toàn bộ project. Graph RAG cung cấp cấu trúc cấu trúc liên kết Topology của mã nguồn. Điều này hỗ trợ quá trình Navigation của Agent có thể dễ dàng lần theo dấu vết từ hàm bị crash ngược lên các hàm gọi thư viện cốt lõi (Call Graph traversal) thông qua các cạnh trên đồ thị. Tốc độ nội suy được cải thiện nhờ việc xây dựng Graph ngầm trong qua trình Phase 1 diễn ra.

## 5. Phương pháp Đánh giá tự động hóa

Phương pháp kết hợp một Pipeline tự động để so sánh tập tin do hệ thống dự đoán với Bản vá gốc (Ground Truth Patch) dựa trên các Benchmark chuẩn (ví dụ Defects4J, SWE-bench). Các độ đo (Metrics) khách quan như **Top-N Accuracy (1, 3, 5, 10)**, tỷ lệ phân tử đảo ngược (Mean Reciprocal Rank - MRR) và **Mean Average Precision (MAP)** được sử dụng để chứng minh tính hiệu quả của phương pháp định vị lỗi tự động đa đặc vụ này.

## 6. Kết quả Thực nghiệm và Phân tích So sánh (Experimental Results & Comparative Analysis)

Hệ thống được đánh giá trên hai tập dữ liệu benchmark phổ biến là **Defects4J** (Java) và **BugsInPy** (Python) sử dụng mô hình ngôn ngữ **Gemini 2.5 Flash**. Các kết quả dưới đây cho thấy hiệu năng vượt trội của phương pháp Multi-Agent RAG so với các phương pháp căn bản.

### 6.1. Kết quả trên tập dữ liệu Defects4J (Java)
Đánh giá trên 170 instances của tập Defects4J cho thấy hệ thống đạt được độ chính xác rất cao:
- **Top-1 Accuracy:** 78.82% (134/170 bugs có file lỗi nằm ngay vị trí top 1).
- **Top-3 & Top-5 & Top-10 Accuracy:** 80.59%.
- **MRR (Mean Reciprocal Rank):** 0.797.
- **MAP (Mean Average Precision):** 0.784.
- **Thời gian xử lý trung bình:** ~44.87 giây/instance.

*Nhận xét:* Với Top-1 Accuracy lên tới gần 79%, hệ thống chứng minh khả năng định vị lỗi chính xác tuyệt vời trên ngôn ngữ Java, vượt xa các phương pháp BM25/TF-IDF thông thường (vốn thường chỉ đạt ~30-40% Top-1). Điều này đạt được nhờ vào khả năng đọc hiểu ngữ nghĩa ngữ cảnh (semantic contextual understanding) của đặc vụ, thay vì chỉ so khớp từ khóa.

### 6.2. Kết quả trên tập dữ liệu BugsInPy (Python)
Kiểm thử trên bộ dữ liệu BugsInPy (5 instances mẫu):
- **Top-1 Accuracy:** 60.0%.
- **Top-3 & Top-5 & Top-10 Accuracy:** 80.0%.
- **MRR & MAP:** 0.667.
- **Thời gian xử lý trung bình:** ~37.09 giây/instance.

*Nhận xét:* Kết quả ban đầu trên Python nhất quán với Java, cho thấy kiến trúc Multi-Agent có tính mở rộng chéo ngôn ngữ (Language-agnostic) mạnh mẽ, không phụ thuộc vào nền tảng hay cú pháp cụ thể của một ngôn ngữ lập trình.

### 6.3. Phân tích So sánh với các Nghiên cứu Liên quan (Comparative Analysis)
Dựa trên khảo sát các nghiên cứu trước đây (Literature Review), phương pháp Multi-Agent giải quyết được các giới hạn cốt lõi:

1. **So với phương pháp Information Retrieval (IR) cơ bản (như BugLocator, BLUiR):** 
   - *Hạn chế của IR:* Vấp phải vấn đề "Lexical Mismatch" (Bất đồng bộ từ vựng giữa ngôn ngữ người dùng trong Bug Report và biểu diễn biến/hàm trong Source Code).
   - *Ưu thế của Multi-Agent:* Tác tử Comprehension có khả năng "dịch" từ khóa ngôn ngữ tự nhiên thành các khái niệm lập trình (ví dụ: "login fails" $\rightarrow$ `authenticateUser`). Công cụ tìm kiếm kết hợp ngữ nghĩa giúp tăng Top-1 Accuracy lên đáng kể.
   
2. **So với Spectrum-Based Fault Localization (SBFL - như Tarantula, Ochiai):**
   - *Hạn chế của SBFL:* Phụ thuộc hoàn toàn vào luồng thực thi động (Dynamic execution traces). Yêu cầu phải có sẵn một bộ Test Suite toàn diện đi kèm để chạy (Coverage-based), điều này tốn kém và không khả thi khi chưa có test case, hoặc với các lỗi Heisenbugs (lỗi không xác định).
   - *Ưu thế của Multi-Agent:* Hoạt động phân tích tĩnh (Static analysis) kết hợp với suy luận. Agent có thể khoanh vùng vị trí trực tiếp dựa trên Stack Trace hoặc Error Log ngoại lệ được cung cấp mà không cần tốn chi phí chạy biên dịch và kiểm thử phức tạp.

3. **So với mô hình Deep Learning/LLM đơn dòng (Single-turn LLM/DeepLoc):**
   - *Hạn chế:* Bị giới hạn nặng nề bởi Context Window. Việc nạp toàn bộ project vào một prompt duy nhất (One-shot retrieval) dễ dẫn đến hiện tượng quá tải (Lost in the middle) và sinh ảo giác (Hallucination), hoặc mô hình tính toán như "hộp đen" (black-box) không thể diễn giải.
   - *Ưu thế của Multi-Agent:* Quy trình chia để trị (Divide-and-Conquer). Hệ thống chia nhỏ công việc (Đọc hiểu $\rightarrow$ Tìm kiếm luân phiên $\rightarrow$ Tổng hợp) kết hợp với **Graph RAG** và **Vector DB** giúp giới hạn bộ nhớ ngữ cảnh ở các không gian tìm kiếm phù hợp, hoạt động minh bạch từng bước. Kết quả MRR 0.797 (~80%) trên quy mô repository lớn là bằng chứng minh họa cho thiết kế Scalability này.
