# Literature Review: Automated Bug Localization

## 1. Information Retrieval (IR) Based Approaches
Early research in bug localization primarily utilized Information Retrieval (IR) techniques to match textual similarity between bug reports and source code files.
- **BugLocator (Zhou et al., 2012):** Introduced a revised Vector Space Model (VSM) that considers code structure (e.g., class vs method) and length normalization. It remains a strong baseline.
- **BLUiR (Saha et al., 2013):** Enhanced IR by parsing source code into structured documents (AST elements: class names, method names, comments) to enable structured retrieval.
- **AmaLgam (Wang et al., 2015):** Combined version history, structured IR, and stack trace analysis, showing that integrating multiple information sources improves accuracy.
* **Limitation:** These methods suffer from the **"Lexical Mismatch"** problem, where bug reports use user-centric language (e.g., "login fails") while code uses implementation details (e.g., `authenticateUser`), failing to bridge the semantic gap.

## 2. Spectrum-Based Fault Localization (SBFL)
Unlike static IR methods, SBFL relies on dynamic execution traces.
- **Tarantula (Jones et al., 2005) & Ochiai (Abreu et al., 2007):** Statistical formulas that rank code elements based on their execution frequency in failing vs. passing test cases.
* **Limitation:** Requires a comprehensive test suite and reproducible steps. It is ineffective for bugs without existing test cases or Heisenbugs (non-deterministic).

## 3. Deep Learning & Representation Learning
To address lexical mismatch, researchers applied Deep Learning to learn semantic representations of code and natural language.
- **DeepLoc (Ye et al., 2016):** Used Convolutional Neural Networks (CNN) to learn features from bug reports and source code, capturing semantic relationships beyond keyword matching.
- **CODEBERT (Feng et al., 2020):** A pre-trained bimodal model (NL-PL) that generates embeddings for code and text, enabling vector-based similarity search.
* **Limitation:** While better at semantics, these models are still "black boxes" and perform **"One-Shot Retrieval"**. They lack reasoning capabilities to navigate complex project structures or follow a chain of thought to verify their findings.

## 4. Large Language Models (LLMs) & Agentic Workflows (State-of-the-Art)
The emergence of Large Language Models (GPT-4, Gemini, Claude) has shifted the paradigm from retrieval to reasoning.
- **Auto-Debiggers (Render et al., 2023):** Demonstrated LLMs' capability to explain bugs but struggled with localization in large repositories due to context window limits.
- **SWE-agent (Yang et al., 2024):** Introduced an autonomous agent framework for software engineering tasks. Agents use tools (`ls`, `grep`, `edit`) to explore codebases iteratively, mimicking human developers.
* **Current Gap:** Most existing LLM approaches treat localization as a retrieval task (RAG) or a full repair task. There is limited research on specialized **Multi-Agent Architectures** specifically optimized for *localization per se*—separating comprehension (Business Analyst) from navigation (Developer) to reduce search space efficiently before attempting repair.

## 5. Summary & Thesis Contribution
This thesis proposes a **Multi-Agent RAG-enhanced Framework**, addressing the limitations of prior works:
1.  **Iterative Reasoning:** Unlike IR/DL (One-Shot), the agentic workflow allows self-correction and multi-step investigation.
2.  **Context-Awareness:** Unlike SBFL, it does not require test suites, leveraging static analysis and LLM reasoning.
3.  **Scalability:** By using specialized agents (Comprehension vs. Navigation), the system manages context limits effectively, outperforming naive context-stuffing methods.
4.  **Generality:** Tested on diverse benchmarks (Defects4J - Java, SWE-bench - Python), demonstrating cross-language applicability.
