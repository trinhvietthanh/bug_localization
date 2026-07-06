# Tài liệu Kiến trúc Hệ thống Bug Localization (Multi-Agent & RAG)

Tài liệu này mô tả kiến trúc tổng thể của hệ thống Bug Localization. Hệ thống sử dụng mô hình Multi-Agent kết hợp với Retrieval-Augmented Generation (RAG) và Code Property Graph (CPG) để tìm kiếm và định vị nguyên nhân gây lỗi trong mã nguồn một cách tự động.

---

## 1. Luồng xử lý tổng thể (Overall Pipeline Flowchart)

Luồng xử lý mô tả cách một Bug Report đầu vào được tiền xử lý và trải qua các vòng lặp của Multi-Agent System để ra kết quả định vị cuối cùng.

```mermaid
flowchart TD
    BR([Bug Report Text]) --> Preprocessor[BugReportPreprocessor]
    
    subgraph Phase 0: Preprocessing & Setup
        Preprocessor --> ST[Extract Stack Traces]
        Preprocessor --> EM[Extract Error Messages]
        Preprocessor --> MF[Extract Mentioned Files/Functions]
        Preprocessor --> DP[Drain3 Log Analysis]
    end
    
    Preprocessing --> ORCH[Orchestrator]
    
    Codebase[(Source Code)] -.-> BCPG[Build Code Property Graph\nBackground Process]
    BCPG -.-> ORCH
    
    subgraph Multi-Agent Pipeline
        ORCH --> CA[Phase 1: Comprehension Agent]
        CA -- "Fault Hypothesis &\nSuspected Files" --> NA[Phase 2: Navigation Agent]
        
        NA -- "Suspicious Locations\n(Scored)" --> COA[Phase 3: Confirmation Agent]
        COA -- "Low Confidence?" --> REF{Reflection\nRound <= 2?}
        REF -- "Yes (Feedback Message)" --> NA
        REF -- "No" --> FS[Final Ranked Locations]
    end
    
    FS --> US[Unified Scorer\nMulti-signal Reranking]
    US --> LR([Localization Result\nRanked Files, Methods, & Locations])
    
    %% Styles
    classDef default fill:#f9f9f9,stroke:#333,stroke-width:1px;
    classDef agent fill:#e1f5fe,stroke:#03a9f4,stroke-width:2px;
    classDef process fill:#fff3e0,stroke:#ff9800,stroke-width:2px;
    class CA,NA,COA agent;
    class ORCH,US process;
```

---

## 2. Mô hình Kiến trúc C4 (C4 Model)

Các biểu đồ dưới đây mô tả hệ thống theo tiêu chuẩn kiến trúc C4 Model (Context, Container, Component).

### 2.1 C4 - Context Diagram

Biểu đồ Context thể hiện hệ thống Bug Localization tương tác như thế nào với người dùng (Developer) và các hệ thống bên ngoài như VCS (Git), LLM Provider và Bug Tracker.

```mermaid
C4Context
    title Context Diagram - Bug Localization System
    
    Person(dev, "Developer / QA / AI System", "Người dùng hoặc hệ thống muốn tìm vị trí file/method gây ra lỗi.")
    System(bugSystem, "Bug Localization System", "Hệ thống AI đa tác vụ (Multi-Agent) hỗ trợ RAG và CPG để định vị nguồn gốc lỗi.")
    
    System_Ext(llm, "LLM Provider", "Ví dụ: Gemini, OpenAI, vLLM. Cung cấp API sinh ngôn ngữ và logic cho Agent.")
    System_Ext(vcs, "Codebase / VCS", "Git repository cục bộ chứa mã nguồn cần fix.")
    System_Ext(bugTracker, "Bug Tracker", "Hệ thống chứa Bug Report (Jira, GitHub Issues).")
    
    Rel(dev, bugSystem, "Gửi Bug Report & Yêu cầu định vị (CLI/API)")
    Rel(bugSystem, vcs, "Quét AST, đọc file, truy vấn log git")
    Rel(bugSystem, llm, "Gửi Context, Prompt & Xử lý Tool Calls")
    Rel(bugSystem, bugTracker, "Trích xuất thông tin Bug (Tuỳ chọn)")
    Rel(bugSystem, dev, "Trả về kết quả danh sách xếp hạng các vị trí nghi ngờ")
```

### 2.2 C4 - Container Diagram

Biểu đồ Container đi sâu vào các khối kiến trúc chính (vùng lưu trữ, logic nghiệp vụ, các pipeline module) bên trong Hệ thống Bug Localization.

```mermaid
C4Container
    title Container Diagram - Bug Localization System
    
    Person(dev, "Developer", "Người dùng hệ thống")
    
    System_Boundary(c1, "Bug Localization System") {
        Container(cli_api, "CLI & API Layer", "Python, FastAPI", "Tiếp nhận yêu cầu (localize, evaluate, index, graph) thông qua CLI hoặc REST API.")
        Container(orchestrator, "Orchestrator", "Python", "Khởi tạo Agent Context, điều phối vòng đời của các agent, background graph building và multi-pass execution.")
        
        Container(agents, "Multi-Agent System", "Python (BaseAgent)", "Chứa Comprehension Agent, Navigation Agent, Confirmation Agent thực hiện suy luận vòng lặp.")
        Container(rag, "RAG & Graph Engine", "Python", "Xử lý code chunking, vector embedding, Code Property Graph builders (Python/Java).")
        Container(tools, "Tools Registry", "Python", "Cung cấp các sandbox tools (semantic search, code search, file read/outline, git log) để agents sử dụng.")
        Container(eval, "Evaluation Module", "Python", "Thực thi Benchmarks (SWE-Bench, Defects4J) và tính toán Metrics (Top-N, MRR, MAP).")
        
        ContainerDb(qdrant, "Vector Database", "Qdrant", "Lưu trữ các vector nhúng (embeddings) của code chunks dùng cho RAG Hybrid Search.")
        ContainerDb(neo4j, "Graph Database", "Neo4j / In-memory", "Lưu trữ cấu trúc đồ thị (AST, caller/callee, class hierarchy) để truy vấn Graph RAG.")
    }
    
    System_Ext(llm, "LLM API", "Gemini / OpenAI")
    System_Ext(codebase, "Local Codebase", "Source code files")
    
    Rel(dev, cli_api, "Kích hoạt bằng CLI hoặc REST", "JSON / args")
    Rel(cli_api, orchestrator, "Khởi tạo session localization")
    Rel(cli_api, eval, "Khởi tạo benchmark jobs")
    Rel(orchestrator, agents, "Điều phối trạng thái & context")
    Rel(agents, tools, "Gọi công cụ điều tra codebase", "OpenAI Tool Format")
    Rel(agents, llm, "Giao tiếp, nhận quyết định hành động", "API")
    Rel(tools, codebase, "Đọc trực tiếp (Read, Grep, Tree)")
    Rel(tools, rag, "Truy xuất thông tin ngữ nghĩa và quan hệ mã nguồn")
    Rel(rag, qdrant, "Truy vấn/Cập nhật Vector", "Hybrid Search (BM25 + Semantic)")
    Rel(rag, neo4j, "Truy vấn Graph Traversal", "Cypher / In-memory BFS")
    Rel(rag, codebase, "Index nội dung, parse AST")
```

### 2.3 C4 - Component Diagram (Multi-Agent Subsystem)

Biểu đồ Component mô tả sâu hơn cơ chế hoạt động tương tác bên trong `Multi-Agent System` kết nối với `Tools Registry` và `RAG & Graph Engine`.

```mermaid
C4Component
    title Component Diagram - Multi-Agent Subsystem & Orchestrator
    
    Container_Boundary(orchestrator_bnd, "Orchestrator Module") {
        Component(context, "Agent Context", "Dataclass", "Lưu trữ trạng thái dùng chung (bug_info, suspicious_files, stack_traces).")
        Component(unified_scorer, "Unified Scorer", "Python", "Multi-signal reranking sau khi agents chạy xong.")
    }
    
    Container_Boundary(agents_bnd, "Multi-Agent System") {
        Component(comprehension, "Comprehension Agent", "Agent", "Sinh Fault hypothesis, trích xuất keywords, tìm component tình nghi.")
        Component(navigation, "Navigation Agent", "Agent", "Khám phá codebase dựa trên keyword và tìm kiếm ngữ nghĩa, CPG.")
        Component(confirmation, "Confirmation Agent", "Agent", "Xếp hạng (rank) danh sách files/methods và tạo Reflection feedback nếu điểm nghi ngờ thấp.")
    }
    
    Container_Boundary(tools_bnd, "Tools Registry & Cache") {
        Component(tool_reg, "Tool Registry", "Registry", "Đăng ký schema công cụ chuẩn OpenAI.")
        Component(file_tools, "File & Search Tools", "Python", "code_search, read_file, parse_logs, get_function_source.")
        Component(rag_tools, "Semantic & Graph Tools", "Python", "semantic_search, find_callers, find_callees.")
        Component(tool_cache, "Tool LRU Cache", "Python", "Tránh việc đọc file hay parse AST liên tục trên disk.")
    }
    
    Rel(comprehension, context, "Đọc/Ghi Hypothesis")
    Rel(navigation, context, "Đọc/Ghi Suspicious Locations")
    Rel(confirmation, context, "Đọc/Ghi Confidence Score & Ranks")
    Rel(confirmation, navigation, "Gửi Reflection feedback", "Nếu confidence < threshold")
    
    Rel(comprehension, tool_reg, "Sử dụng tools")
    Rel(navigation, tool_reg, "Sử dụng tools")
    Rel(confirmation, tool_reg, "Sử dụng tools")
    
    Rel(tool_reg, file_tools, "Dispatch")
    Rel(tool_reg, rag_tools, "Dispatch")
    
    Rel(file_tools, tool_cache, "Read / Write Cache")
    Rel(rag_tools, tool_cache, "Read / Write Cache")
    Rel(context, unified_scorer, "Gửi kết quả cuối cùng để re-rank")
```

---

## 3. Kiến Trúc RAG và Code Property Graph (CPG)

Một phần cốt lõi của hệ thống để hỗ trợ tác vụ Navigation của LLM là engine RAG và Code Property Graph.

```mermaid
flowchart LR
    CB[(Local Codebase)] --> IDX[Codebase Indexer]
    CB --> CPG[Code Graph Builders\nAST Python / Regex Java]
    
    IDX --> EMB[Code Embedder\nJina/OpenAI]
    EMB --> QD[(Qdrant Vector DB)]
    IDX --> BM25[(BM25 Fulltext Index)]
    
    CPG --> N4J[(Neo4j / In-memory\nCode Graph)]
    
    subgraph Retrieval Layer
        RET[Code Retriever]
        GRET[Graph Retriever]
    end
    
    QD -. "Semantic Vector Search" .-> RET
    BM25 -. "Lexical Search" .-> RET
    RET -- "Reciprocal Rank Fusion\n(Hybrid)" --> RES1[Semantic Results]
    
    N4J -. "Cypher / Graph Traversal" .-> GRET
    GRET -- "BFS Expansion\n(Callers/Callees)" --> RES2[Graph RAG Results]
    
    %% styles
    classDef db fill:#e8f5e9,stroke:#4caf50,stroke-width:2px;
    classDef logic fill:#e3f2fd,stroke:#1976d2,stroke-width:2px;
    class QD,BM25,N4J,CB db;
    class IDX,CPG,EMB,RET,GRET logic;
```

---

## 4. Các luồng phụ thuộc Dependencies giữa các Modules

Biểu đồ thể hiện cách các module mã nguồn được tổ chức và liên kết phụ thuộc lẫn nhau trong file system (tham chiếu đến phần `10. Sơ Đồ Dependency Giữa Các Module` trong báo cáo kiến trúc).

```mermaid
flowchart TD
    MAIN[main.py] --> CMD[commands/]
    
    CMD --> C_LOC[localize.py]
    CMD --> C_EVAL[swebench.py / defects4j.py]
    
    C_LOC --> ORCH[agents/orchestrator.py]
    C_EVAL --> ORCH
    
    ORCH --> AGENTS[agents/]
    AGENTS --> A_COMP[comprehension.py]
    AGENTS --> A_NAV[navigation.py]
    AGENTS --> A_CONF[confirmation.py]
    
    A_COMP & A_NAV & A_CONF --> REG[tools/registry.py]
    REG --> TOOLS[tools/]
    
    TOOLS --> RAG[rag/]
    RAG --> R_RET[retriever.py]
    RAG --> R_GRET[graph_retriever.py]
    RAG --> R_IDX[indexer.py]
    
    EVAL[evaluation/] --> ORCH
    API[api/] --> CMD
```

Văn bản tài liệu này có thể được sử dụng làm file tham khảo để nhúng vào Github Wiki, Markdown Viewer, hoặc xuất báo cáo chính thức. Hệ thống hỗ trợ xem bằng các công cụ hiển thị Mermaid.js mặc định.
