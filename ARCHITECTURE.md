# System Architecture: Agentic Bug Localization

## 1. System Overview (Tổng quan Hệ thống)

The system is designed as a **Multi-Agent RAG-enhanced Framework** for automated bug localization. Unlike traditional Information Retrieval (IR) or simple RAG approaches, this system employs an **autonomous agent workflow** capable of planning, exploring, and reasoning about the codebase iteratively.

### Key Metrics (Số liệu Thống kê)
- **Total Codebase Size:** ~3,800+ lines of Python code.
- **Core Modules:** 6 (Agents, Tools, RAG, Evaluation, Data, Config).
- **Supported Benchmarks:** Defects4J (Java), SWE-bench (Python).
- **LLM Support:** Multi-provider (Gemini, OpenAI, Ollama).

## 2. High-Level Architecture (Kiến trúc Tổng quát)

The system follows a modular architecture where the **Orchestrator** manages the lifecycle of a bug localization task, coordinating between the **Analysis Agent**, **Tools**, and **Data Loaders**.

```mermaid
graph TD
    User[User / CLI] -->|Input: Bug Report| Orchestrator
    
    subgraph "Data Layer"
        Loader[Defects4J / SWE-bench Loader] -->|Fetch Bug & Repo| Orchestrator
        Repo[Target Repository Source Code]
    end
    
    subgraph "Agentic Core"
        Orchestrator -->|Initialize| Agent[Localization Agent]
        Agent <-->|Context & History| Memory[Conversation Memory]
        
        Agent -->|Thought & Plan| Decisions{Decision Limit?}
        Decisions -->|Continue| Tools
        Decisions -->|Max Steps/Found| Result[Localization Result]
    end
    
    subgraph "Tooling Layer (Skills)"
        Tools -->|Search| CodeSearch[Code Search / Grep]
        Tools -->|Navigate| FileNav[File Navigation / LS]
        Tools -->|Read| FileReader[File Reader / AST]
        Tools -->|Retrieve| RAG[RAG Engine (Embeddings)]
        
        CodeSearch --> Repo
        FileNav --> Repo
        FileReader --> Repo
    end
    
    subgraph "Evaluation Layer"
        Result --> Evaluator[Evaluation Engine]
        Evaluator -->|Compare| GroundTruth[Ground Truth Patch]
        Evaluator -->|Output| Metrics[CSV/JSON Report (Top-N, MRR)]
    end
```

## 3. Component Description (Mô tả Thành phần)

### 3.1. Core Agents (`agents/`)
The brain of the system.
- **`Orchestrator`**: The workflow manager. It sets up the environment, initializes the agent with the bug report, runs the loop, and collects the final output.
- **`BaseAgent`**: Abstract class defining the agent lifecycle (Prompt -> LLM -> Tool Execution -> Observation).
- **`ComprehensionAgent` / `NavigationAgent`**: Specialized roles (conceptually) that parse the bug report and navigate the file system to find the culprit.

### 3.2. Tooling System (`tools/`)
The "hands" of the agent, allowing it to interact with the environment.
- **`CodeSearch`**: Semantic search and regex-based grep to find keywords.
- **`ASTParser`**: Python/Java validation and structure analysis to understand code hierarchy.
- **`FileReader`**: Reads file content for analysis.
- **`Navigation`**: Lists directories to understand project structure.

### 3.3. RAG Engine (`rag/`)
The "long-term memory" or "knowledge base".
- **`Embedder`**: Converts code snippets and bug reports into vector embeddings (using models like `text-embedding-3-small` or local BERT).
- **`VectorDB`**: Stores and retrieves relevant code chunks based on semantic similarity.

### 3.4. Data Ingestion (`data/`)
Handles the complexity of benchmarks.
- **`Defects4JLoader`**: Specialized loader for Java projects (Lang, Chart, Time, etc.), managing checkout, bug report parsing, and ground truth extraction.
- **`SWEBenchLoader`**: Loader for Python-based benchmarks.

### 3.5. Evaluation Engine (`evaluation/`)
Scientific measurement of performance.
- **`Metrics`**: Implements academic metrics:
  - **Top-N Accuracy (1, 3, 5, 10)**: Is the buggy file in the top N predictions?
  - **MRR (Mean Reciprocal Rank)**: How high is the first correct file ranked?
  - **MAP (Mean Average Precision)**: Precision across all relevant retrieved files.
- **`Export`**: Serializes results to CSV for quantitative analysis.

## 4. Implementation Details (Chi tiết Hiện thực)

| Component | Technology / Library | Purpose |
| :--- | :--- | :--- |
| **Language** | Python 3.10+ | Core logic |
| **LLM Interface** | `google-generativeai`, `openai`, `ollama` | Intelligence backend |
| **Vector Search** | `chromadb` / `faiss` (via `rag`) | Semantic retrieval |
| **Data Handling** | `datasets` (Hugging Face), `pandas` | Benchmarking data |
| **CLI** | `argparse`, `rich` | User interface and logging |
| **Testing** | `pytest` | Unit and integration testing |

## 5. Workflow Execution (Quy trình Thực thi)

1.  **Initialization**: User specifies a bug ID (e.g., `Lang_1`).
2.  **Loading**: System checks out the buggy version of the code and loads the bug report.
3.  **Analysis Loop**:
    *   Agent analyzes the Bug Report text.
    *   Agent formulates a search query (e.g., "NumberUtils hex parsing").
    *   Agent executes `code_search` tool.
    *   Agent reads candidate files.
    *   Agent refines hypothesis.
4.  **Localization**: Agent outputs a ranked list of suspicious files and a root cause analysis.
5.  **Evaluation**: System compares predictions against the known fixed files (Ground Truth) and calculates metrics.
