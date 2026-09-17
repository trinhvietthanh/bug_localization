# Đồ thị Mermaid — Quy trình khi lỗi được báo cáo

## 1. Quy trình truyền thống (Thủ công)

```mermaid
flowchart LR
    A[🐛 Bug Report] --> B[👨‍💻 Lập trình viên]
    B --> C[Đọc hiểu lỗi]
    C --> D[Duyệt mã nguồn]
    D --> E{Tìm thấy?}
    E -->|Chưa| D
    E -->|Rồi| F[🔧 Sửa lỗi]

    style D fill:#ffcdd2,stroke:#e53935,stroke-width:2px
```

> Bước "Duyệt mã nguồn" là **bottleneck** — chiếm 30–50% tổng thời gian.

---

## 2. Quy trình với hệ thống đề xuất

```mermaid
flowchart LR
    A[🐛 Bug Report] --> B[Tiền xử lý]
    B --> C[Comprehension]
    C --> D[Navigation]
    D --> E[Confirmation]
    E --> F[Scoring]
    F --> G[📊 Ranked Files]

    style C fill:#e1f5fe,stroke:#03a9f4
    style D fill:#e1f5fe,stroke:#03a9f4
    style E fill:#e1f5fe,stroke:#03a9f4
    style F fill:#fff3e0,stroke:#ff9800
```

---

## 3. Pipeline chi tiết

```mermaid
flowchart TD
    BR([Bug Report + Mã nguồn]) --> PRE[Tiền xử lý\nStack trace · Error · Log]
    PRE --> P1[Phase 1: Comprehension\nHiểu lỗi → Sinh giả thuyết]
    P1 --> P2[Phase 2: Navigation\nKhám phá codebase]
    P2 --> P3[Phase 3: Confirmation\nXác nhận & Xếp hạng]
    P3 --> CHK{confidence ≥ 0.5?}
    CHK -->|Không| P2
    CHK -->|Có| SC[UnifiedScorer\n10 tín hiệu]
    SC --> RR[Listwise Rerank]
    RR --> OUT([Ranked files/methods])

    style P1 fill:#e1f5fe,stroke:#03a9f4
    style P2 fill:#e1f5fe,stroke:#03a9f4
    style P3 fill:#e1f5fe,stroke:#03a9f4
    style SC fill:#fff3e0,stroke:#ff9800
    style RR fill:#fff3e0,stroke:#ff9800
```

---

## 4. So sánh

```mermaid
flowchart LR
    subgraph T["Truyền thống"]
        direction LR
        T1[Bug Report] --> T2[Duyệt thủ công\n⏰ hàng giờ] --> T3[Vị trí lỗi]
    end

    subgraph H["Hệ thống đề xuất"]
        direction LR
        H1[Bug Report] --> H2[3 Agent + RAG + CPG\n⚡ ~4 phút] --> H3[Top-1: 78%]
    end

    style T fill:#ffebee
    style H fill:#e8f5e9
```
