# Tài liệu Kiến trúc Hệ thống Bug Localization (Multi-Agent & RAG)

> Cập nhật: 2026-07-28 — đồng bộ với cấu hình chuẩn đã xác nhận bằng thực nghiệm (n=300, xem [RESULTS_SUMMARY.md](RESULTS_SUMMARY.md)): **E1+E2+E3**. Patch Duel và RRF consensus đã bị loại khỏi source chạy chính vì không cho cải thiện có ý nghĩa trên mẫu đủ lớn; artifact lịch sử vẫn được giữ trong báo cáo kết quả. Bản trình bày rút gọn xem [CHUONG_KIEN_TRUC.md](CHUONG_KIEN_TRUC.md).

Tài liệu này mô tả kiến trúc tổng thể của hệ thống Bug Localization. Hệ thống sử dụng Multi-Agent kết hợp Retrieval-Augmented Generation (RAG) và Code Property Graph (CPG). Các tính năng mở rộng E1/E2/E3 có công tắc `.env` riêng và không đổi schema output nên có thể bật/tắt độc lập.

---

## 1. Luồng xử lý tổng thể (Overall Pipeline Flowchart)

Luồng xử lý mô tả cách một Bug Report đầu vào được tiền xử lý và trải qua các vòng lặp của Multi-Agent System để ra kết quả định vị cuối cùng. Sơ đồ vẽ theo **cấu hình chuẩn** (E1+E2+E3 bật, H1/RRF tắt) — vì E2 bật, `VerificationAgent` (E1) bị bỏ qua hoàn toàn (`orchestrator.py:932`) do Explorer đã tự gắn nhãn bằng chứng ngay trong bước `observe()` của mỗi action. Bảng hành vi thay thế theo từng flag nằm ngay dưới sơ đồ.

```mermaid
flowchart TD
    BR([Bug Report Text]) --> Preprocessor[BugReportPreprocessor]

    subgraph Phase 0: Preprocessing & Setup
        Preprocessor --> ST[Extract Stack Traces]
        Preprocessor --> EM[Extract Error Messages]
        Preprocessor --> MF[Extract Mentioned Files/Functions]
        Preprocessor --> DP[Drain3 Log Analysis]
        Preprocessor --> TD["Java: Test-class → Source-class\n(WeekTests → Week.java)"]
    end

    Preprocessing --> ORCH[Orchestrator]

    Codebase[(Source Code)] -.-> BCPG["Build Code Property Graph\n(Neo4j nếu NEO4J_ENABLED, else in-memory)\nBackground Process"]
    BCPG -.-> ORCH

    subgraph Multi-Agent Pipeline
        ORCH --> CA["Phase 1: Comprehension Agent\ntool loop (cap 4)\n[E1] K=4 giả thuyết cạnh tranh"]
        CA -- "Fault Hypothesis + Suspected Files + hypotheses[]" --> PNA["Phase 2: Priority Navigation Agent [E2]\npriority-queue scheduler,\nLLM chỉ chấm điểm (context O(1))\nObservation gắn nhãn hypothesis_evidence trực tiếp"]

        PNA --> COA["Phase 3: Confirmation Agent\ntool loop (cap 10)"]
        COA -- "Low Confidence?" --> REF{Reflection\nRound ≤ 2?}
        REF -- "Yes (hyp_summary từ\nHypothesisTracker)" --> PNA
        REF -- "No" --> FS[Final Ranked Locations]
    end

    FS --> US["Unified Scorer\n9 tín hiệu (bao gồm E1 hypothesis_support)"]
    US --> LR2["ListwiseReranker [E3]\nnarrow file→function + rerank top-K\n(permutation-only, fail-open)"]
    LR2 --> VAL[File-path Validation\nnever-empty fallback]
    VAL --> LR([Localization Result\nRanked Files, Methods, & Locations])

    %% Styles
    classDef default fill:#f9f9f9,stroke:#333,stroke-width:1px;
    classDef agent fill:#e1f5fe,stroke:#03a9f4,stroke-width:2px;
    classDef process fill:#fff3e0,stroke:#ff9800,stroke-width:2px;
    classDef ext fill:#fce4ec,stroke:#e91e63,stroke-width:2px,stroke-dasharray: 4 2;
    class CA,PNA,COA agent;
    class ORCH,US process;
    class PNA,LR2 ext;
```

**Hành vi khi tắt từng flag** (tham khảo kỹ thuật — không xuất hiện trong sơ đồ trên vì đó là ảnh chụp cấu hình chuẩn):

| Flag | Khi TẮT | Khi BẬT (cấu hình chuẩn) |
|---|---|---|
| `ENABLE_HYPOTHESIS_LOOP` (E1) | Comprehension chỉ sinh 1 fault hypothesis; không có `hypothesis_support` trong Unified Scorer | Sinh K=4 giả thuyết cạnh tranh; posterior cập nhật qua `HypothesisTracker` |
| `ENABLE_PRIORITY_EXPLORATION` (E2) | Dùng `NavigationAgent` (tool loop tự do, cap 10); nếu E1 bật thì chạy thêm `VerificationAgent` riêng ở Phase 2.5 | Dùng `PriorityNavigationAgent`; Verification bị bỏ qua vì Explorer tự gắn nhãn evidence |
| `ENABLE_LISTWISE_RERANK` (E3) | Bỏ qua bước rerank, dùng thẳng thứ tự Unified Scorer | Rerank top-K bằng 1 LLM call bổ sung (permutation-only) |

---

## 2. Mô hình Kiến trúc C4 (C4 Model)

Các biểu đồ dưới đây mô tả hệ thống theo tiêu chuẩn kiến trúc C4 Model (Context, Container, Component).

### 2.1 C4 - Context Diagram

Biểu đồ Context thể hiện hệ thống Bug Localization tương tác như thế nào với người dùng (Developer) và các hệ thống bên ngoài như VCS (Git), LLM Provider và Bug Tracker.

```mermaid
C4Context
    title Context Diagram - Bug Localization System

    Person(dev, "Developer / QA / AI System", "Người dùng hoặc hệ thống muốn tìm vị trí file/method gây ra lỗi.")
    System(bugSystem, "Bug Localization System", "Hệ thống AI đa tác vụ (Multi-Agent) hỗ trợ RAG và CPG để định vị nguồn gốc lỗi. Cấu hình chuẩn gồm giả thuyết cạnh tranh (E1), khám phá ưu tiên (E2) và listwise rerank (E3).")

    System_Ext(llm, "LLM Provider", "OpenAI-compatible: Qwen/DashScope, OpenRouter, Gemini, Ollama/vLLM. Cung cấp API sinh ngôn ngữ và logic cho Agent.")
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
        Container(orchestrator, "Orchestrator", "Python", "Khởi tạo Agent Context, điều phối vòng đời agent (rerank E3; Verification E1 và patch duel H1 là nhánh tùy chọn, tắt trong cấu hình chuẩn), background graph building và multi-pass execution.")

        Container(agents, "Multi-Agent System", "Python (BaseAgent)", "Comprehension, Navigation (hoặc Priority Navigation - E2), Verification (E1), Confirmation thực hiện suy luận vòng lặp.")
        Container(core_engine, "Core Explorer Engine", "Python", "[E2] core/explorer.py — priority-queue scheduler agnostic với LLM/tool, unit-testable riêng.")
        Container(rag, "RAG & Graph Engine", "Python", "Xử lý code chunking, vector embedding, Code Property Graph builders (Python/Java), hop-distance API cho E2.")
        Container(tools, "Tools Registry", "Python", "Cung cấp các sandbox tools (semantic search, code search, file read/outline, git log) để agents sử dụng.")
        Container(eval, "Evaluation Module", "Python", "Benchmarks (SWE-Bench, Defects4J), Metrics (Top-N, MRR, MAP), Unified Scorer (9 tín hiệu), Listwise Reranker (E3).")

        ContainerDb(qdrant, "Vector Database", "Qdrant", "Lưu trữ các vector nhúng (embeddings) của code chunks dùng cho RAG Hybrid Search.")
        ContainerDb(neo4j, "Graph Database", "Neo4j (mặc định khi NEO4J_ENABLED) / In-memory fallback", "Lưu trữ cấu trúc đồ thị (AST, caller/callee, class hierarchy) để truy vấn Graph RAG. Kết nối lỗi → tự fallback in-memory, không crash pipeline.")
    }

    System_Ext(llm, "LLM API", "OpenAI-compatible (Qwen/OpenRouter/Gemini/Ollama)")
    System_Ext(codebase, "Local Codebase", "Source code files")

    Rel(dev, cli_api, "Kích hoạt bằng CLI hoặc REST", "JSON / args")
    Rel(cli_api, orchestrator, "Khởi tạo session localization")
    Rel(cli_api, eval, "Khởi tạo benchmark jobs")
    Rel(orchestrator, agents, "Điều phối trạng thái & context")
    Rel(orchestrator, eval, "Gọi UnifiedScorer + ListwiseReranker sau khi agent chạy xong")
    Rel(agents, core_engine, "[E2] PriorityNavigationAgent dùng PriorityExplorer làm engine")
    Rel(agents, tools, "Gọi công cụ điều tra codebase", "OpenAI Tool Format")
    Rel(core_engine, tools, "Thực thi action qua tool registry sẵn có")
    Rel(agents, llm, "Giao tiếp, nhận quyết định hành động", "API")
    Rel(tools, codebase, "Đọc trực tiếp (Read, Grep, Tree)")
    Rel(tools, rag, "Truy xuất thông tin ngữ nghĩa và quan hệ mã nguồn")
    Rel(rag, qdrant, "Truy vấn/Cập nhật Vector", "Hybrid Search (BM25 + Semantic)")
    Rel(rag, neo4j, "Truy vấn Graph Traversal", "Cypher / In-memory BFS")
    Rel(rag, codebase, "Index nội dung, parse AST")
```

### 2.3 C4 - Component Diagram (Multi-Agent Subsystem)

Biểu đồ Component mô tả sâu hơn cơ chế hoạt động tương tác bên trong `Multi-Agent System` kết nối với `Tools Registry`, `Core Explorer Engine` và `RAG & Graph Engine`.

```mermaid
C4Component
    title Component Diagram - Multi-Agent Subsystem & Orchestrator

    Container_Boundary(orchestrator_bnd, "Orchestrator Module") {
        Component(context, "Agent Context", "Dataclass", "Lưu trữ trạng thái dùng chung (bug_info, suspicious_files, stack_traces, hypotheses, hypothesis_tracker).")
        Component(unified_scorer, "Unified Scorer", "Python", "Multi-signal reranking (9 tín hiệu) sau khi agents chạy xong.")
    }

    Container_Boundary(agents_bnd, "Multi-Agent System") {
        Component(comprehension, "Comprehension Agent", "Agent", "v2/v3: single-shot + verify-shot + escalation. Sinh fault hypothesis, [E1] K giả thuyết cạnh tranh.")
        Component(navigation, "Navigation Agent", "Agent", "Khám phá codebase tự do dựa trên keyword, semantic search, CPG.")
        Component(priority_nav, "Priority Navigation Agent", "Agent [E2]", "Khám phá qua priority-queue scheduler; fallback về Navigation Agent nếu tìm được quá ít.")
        Component(verification, "Verification Agent", "Agent [E1]", "Thu bằng chứng ủng hộ/bác bỏ cho từng giả thuyết cạnh tranh.")
        Component(confirmation, "Confirmation Agent", "Agent", "loop/single/hybrid — xếp hạng candidates và tạo Reflection feedback.")
        Component(hypothesis_tracker, "Hypothesis Tracker", "Python [E1]", "Belief tracking bằng log-odds thuần Python — LLM chỉ gắn nhãn bằng chứng.")
    }

    Container_Boundary(core_bnd, "Core Explorer Engine [E2]") {
        Component(explorer, "Priority Explorer", "Python", "Heap frontier + visited-set dedupe; agnostic với LLM/tool, unit-testable riêng.")
    }

    Container_Boundary(eval_bnd, "Evaluation — Post-hoc Reranking") {
        Component(reranker, "Listwise Reranker", "Python [E3]", "Narrowing (file→function) + listwise rerank top-K trên evidence card; permutation-only, fail-open.")
    }

    Container_Boundary(tools_bnd, "Tools Registry & Cache") {
        Component(tool_reg, "Tool Registry", "Registry", "Đăng ký schema công cụ chuẩn OpenAI.")
        Component(file_tools, "File & Search Tools", "Python", "code_search, read_file, parse_logs, get_function_source.")
        Component(rag_tools, "Semantic & Graph Tools", "Python", "semantic_search, find_callers, find_callees.")
        Component(tool_cache, "Tool LRU Cache", "Python", "Tránh việc đọc file hay parse AST liên tục trên disk.")
    }

    Rel(comprehension, context, "Đọc/Ghi Hypothesis + hypotheses[] (E1)")
    Rel(navigation, context, "Đọc/Ghi Suspicious Locations")
    Rel(priority_nav, context, "Đọc/Ghi Suspicious Locations (schema giống Navigation)")
    Rel(priority_nav, explorer, "Dùng làm engine — execute/observe callback")
    Rel(verification, hypothesis_tracker, "update() theo evidence quan sát được")
    Rel(priority_nav, hypothesis_tracker, "[E1+E2] Observation cũng gắn nhãn hypothesis_evidence — thay Verification riêng")
    Rel(confirmation, context, "Đọc/Ghi Confidence Score & Ranks")
    Rel(confirmation, navigation, "Gửi Reflection feedback", "Nếu confidence < threshold")
    Rel(hypothesis_tracker, context, "surviving_files_ordered() / file_scores() / reflection_summary()")

    Rel(comprehension, tool_reg, "Sử dụng tools")
    Rel(navigation, tool_reg, "Sử dụng tools")
    Rel(verification, tool_reg, "Sử dụng tools")
    Rel(confirmation, tool_reg, "Sử dụng tools")
    Rel(explorer, tool_reg, "Thực thi action qua tool registry (không tự chọn action)")

    Rel(tool_reg, file_tools, "Dispatch")
    Rel(tool_reg, rag_tools, "Dispatch")

    Rel(file_tools, tool_cache, "Read / Write Cache")
    Rel(rag_tools, tool_cache, "Read / Write Cache")
    Rel(context, unified_scorer, "Gửi kết quả cuối cùng để re-rank")
    Rel(hypothesis_tracker, unified_scorer, "file_scores() — tín hiệu thứ 10")
    Rel(unified_scorer, reranker, "unified_scores + ranked_locations → evidence card")
    Rel(reranker, tool_reg, "skeleton_for_files() (không qua registry, gọi trực tiếp)")
```

---

## 3. Kiến Trúc RAG và Code Property Graph (CPG)

Một phần cốt lõi của hệ thống để hỗ trợ tác vụ Navigation của LLM là engine RAG và Code Property Graph. Neo4j nay là backend mặc định khi `NEO4J_ENABLED=true` — kết nối lỗi tự fallback in-memory (bắt `ConnectionError` từ driver), không làm crash pipeline.

```mermaid
flowchart LR
    CB[(Local Codebase)] --> IDX[Codebase Indexer]
    CB --> CPG[Code Graph Builders\nAST Python / Regex Java]

    IDX --> EMB[Code Embedder\nJina/OpenAI-compatible]
    EMB --> QD[(Qdrant Vector DB)]
    IDX --> BM25[(BM25 Fulltext Index)]

    CPG --> N4JCHECK{NEO4J_ENABLED\nvà kết nối OK?}
    N4JCHECK -- Yes --> N4J[(Neo4j\npersistent, partition theo repo_id)]
    N4JCHECK -- "No / lỗi kết nối" --> MEM[(In-memory\nCodePropertyGraph)]

    subgraph Retrieval Layer
        RET[Code Retriever]
        GRET[Graph Retriever]
    end

    QD -. "Semantic Vector Search" .-> RET
    BM25 -. "Lexical Search" .-> RET
    RET -- "Reciprocal Rank Fusion\n(Hybrid)" --> RES1[Semantic Results]

    N4J -. "Cypher / Graph Traversal" .-> GRET
    MEM -. "In-memory BFS" .-> GRET
    GRET -- "BFS Expansion\n(Callers/Callees)" --> RES2[Graph RAG Results — search()]
    GRET -- "[E2] find_anchor_nodes()" --> RES3[Anchor nodes cho priority scheduler]
    GRET -- "[E2] file_hop_distances() / hop_distance()\n(LRU cache 2048)" --> RES4[Graph-proximity term\ntrong priority()]

    %% styles
    classDef db fill:#e8f5e9,stroke:#4caf50,stroke-width:2px;
    classDef logic fill:#e3f2fd,stroke:#1976d2,stroke-width:2px;
    classDef ext fill:#fce4ec,stroke:#e91e63,stroke-width:2px,stroke-dasharray: 4 2;
    class QD,BM25,N4J,MEM,CB db;
    class IDX,CPG,EMB,RET,GRET logic;
    class RES3,RES4 ext;
```

---

## 4. Các luồng phụ thuộc Dependencies giữa các Modules

Biểu đồ thể hiện cách các module mã nguồn được tổ chức và liên kết phụ thuộc lẫn nhau trong file system (tham chiếu đến phần `10. Sơ Đồ Dependency Giữa Các Module` trong [ARCHITECTURE.md](../ARCHITECTURE.md)).

```mermaid
flowchart TD
    MAIN[main.py] --> CMD[commands/]

    CMD --> C_LOC[localize.py]
    CMD --> C_EVAL[swebench.py / defects4j.py]

    C_LOC --> ORCH[agents/orchestrator.py]
    C_EVAL --> ORCH
    SCRIPTS["scripts/run_swebench_benchmark.py\n(+ bare_llm_baseline.py, run_cross_model.py)"] --> ORCH

    ORCH --> AGENTS[agents/]
    AGENTS --> A_COMP[comprehension.py]
    AGENTS --> A_NAV[navigation.py]
    AGENTS --> A_PNAV["priority_navigation.py [E2]"]
    AGENTS --> A_CONF[confirmation.py]
    AGENTS --> A_VER["verification.py [E1]"]
    AGENTS --> A_HYP["hypothesis.py [E1]\n(HypothesisTracker)"]

    A_PNAV --> CORE["core/explorer.py [E2]\n(PriorityExplorer — agnostic engine)"]
    A_VER --> A_HYP
    A_COMP --> A_HYP
    A_PNAV -.-> A_HYP

    A_COMP & A_NAV & A_PNAV & A_CONF & A_VER --> REG[tools/registry.py]
    REG --> TOOLS[tools/]

    TOOLS --> RAG[rag/]
    RAG --> R_RET[retriever.py]
    RAG --> R_GRET["graph_retriever.py\n(+ find_anchor_nodes/hop_distance cho E2)"]
    RAG --> R_IDX[indexer.py]

    ORCH --> EVAL2["evaluation/unified_scorer.py\n(9 tín hiệu)"]
    ORCH --> RR["evaluation/reranker.py [E3]\n(ListwiseReranker)"]
    RR --> TOOLS

    EVAL[evaluation/] --> ORCH
    API[api/] --> CMD

    classDef ext fill:#fce4ec,stroke:#e91e63,stroke-width:2px,stroke-dasharray: 4 2;
    class A_PNAV,A_VER,A_HYP,CORE,RR ext;
```

---

Văn bản tài liệu này có thể được sử dụng làm file tham khảo để nhúng vào Github Wiki, Markdown Viewer, hoặc xuất báo cáo chính thức. Hệ thống hỗ trợ xem bằng các công cụ hiển thị Mermaid.js mặc định.
