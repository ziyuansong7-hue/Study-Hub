# StudyHub 架构说明

[English architecture](architecture.md) · [返回中文 README](../README.zh-CN.md)

StudyHub 是一个面向单个本地学生的模块化单体应用。网页界面、MCP 服务、调度器与命令行任务共享同一套应用服务和持久化模型，从而保持学习规则一致，同时避免当前规模并不需要的分布式系统复杂度。

## 系统上下文

```mermaid
flowchart TB
    Student[学生]
    Client[MCP 兼容客户端]
    Operator[本地调度器 / 命令行]

    subgraph StudyHub[StudyHub 进程边界]
        Web[Flask + Jinja 网页应用]
        MCP[MCP stdio 服务]
        Jobs[定时任务与维护脚本]
        Services[应用服务层]
        Repositories[仓储层]
        Providers{模型提供接口}
        Fake[确定性 FakeProvider]
        External[DeepSeek / OpenAI]
    end

    DB[(本地 SQLite 数据库)]
    Files[本地课程资料]

    Student --> Web
    Client --> MCP
    Operator --> Jobs
    Web --> Services
    MCP --> Services
    Jobs --> Services
    Services --> Repositories
    Services --> Providers
    Providers --> Fake
    Providers --> External
    Repositories --> DB
    Services --> DB
    Web --> Files
```

默认不会跨越外部模型边界。只有在全局配置了外部提供商，并且当前课程明确同意使用外部 AI 时，才会发出相关请求。

## 内部分层

| 层级 | 职责 | 主要位置 |
|---|---|---|
| 交付层 | HTTP 页面与操作、MCP 响应结构、命令行入口 | `app/routes/`、`mcp_server/`、`scripts/` |
| 应用层 | 课程、内容、学习、计划、掌握度、备份与调度流程 | `services/` |
| 契约层 | 经过校验的模型与领域数据结构 | `schemas/` |
| 持久化层 | SQLAlchemy 实体与可复用学习数据操作 | `models/`、`repositories/` |
| 基础设施层 | SQLite、Alembic、模型适配器与 Flask 安全 | `migrations/`、`services/llm/`、`app/security.py` |

路由按照产品领域拆分为 `courses`、`materials`、`questions`、`study`、`planning` 和 `operations`，但继续使用一个 Flask Blueprint，并保持已有端点名称稳定。

## 学习闭环

```mermaid
flowchart LR
    A[导入课程资料] --> B[提取有证据的概念候选]
    B --> C{学生审核}
    C -->|确认 / 合并| D[已确认概念]
    C -->|忽略 / 修改| B
    D --> E[生成题目草稿]
    E --> F{质量检查 + 人工批准}
    F -->|通过| G[学习会话]
    F -->|拒绝 / 编辑| E
    G --> H[评分]
    H --> I[诊断主要缺口]
    I --> J[更新掌握度与复习需求]
    J --> K{下一步动作}
    K -->|继续练习| G
    K -->|补先修知识| L[子专注会话]
    K -->|安排复习| M[计划器 / 每日复习]
    L --> G
    M --> G
```

`AgentService.submit_attempt` 实现核心的“观察 → 诊断 → 决策 → 行动 → 更新”流程：

1. 读取题目、信心值、历史失败、概念状态、先修关系和允许使用的资料证据。
2. 客观题由程序评分；简答与推导题的模型输出会经过校验，失败后重试一次，再安全降级。
3. 约束分数范围、单一主要缺口、已知错误分类、可见讲解和允许的下一步动作。
4. 保存调用方提供的 `attempt_id`；重复提交直接返回已有决策。
5. 更新掌握度、连续表现、平均信心、近期错误、最后练习时间和复习需求。
6. 记录摘要化输入与结构化决策，不保存隐藏思维链。

## 内容与题目生命周期

```mermaid
stateDiagram-v2
    [*] --> 已上传
    已上传 --> 已解析
    已解析 --> 概念候选
    概念候选 --> 已确认: 学生批准
    概念候选 --> 已忽略: 学生拒绝
    已确认 --> 题目待审核: 生成
    题目待审核 --> 题目已批准: 审核通过
    题目待审核 --> 题目已拒绝: 编辑或拒绝
    题目已批准 --> 已练习
    已练习 --> 到期复习: 掌握度策略
    到期复习 --> 已练习
```

资料按页解析，并尽可能去除重复页眉页脚、识别标题。只有扫描图像的 PDF 会返回明确的“不支持 OCR”错误。原始文本块仍可查看，但只有通过内容质量检查后，才能用于概念和引用。

生成题目会保存提供商、来源策略、证据文本块、预期答案、评分标准、解题步骤、难度维度、审核记录和管理状态。常规会话只使用已启用、已批准且通过质量检查的题目。

## 难度与选题

题目难度使用可解释的加权启发式规则：

| 维度 | 权重 |
|---|---:|
| 概念深度 | 25% |
| 推理步骤 | 20% |
| 主题整合 | 20% |
| 新颖程度 | 15% |
| 计算量 | 10% |
| 先修知识广度 | 10% |

每个维度取值 0–4，再映射为 0–100 分和 1–5 级。选题综合薄弱项、到期项、错题与保持记忆等信号，排除未启用或未审核题目，并减少短时间内重复出现。这些是产品启发式规则，不代表心理测量学结论。

## 持久化与迁移

SQLite 适合单个本地用户，并支持一致的在线备份。Alembic 管理截至 `0005_content_quality` 的版本历史。启动时还会执行幂等、只增加不破坏的兼容检查，使早期本地原型创建的数据库仍可安全打开。已有迁移刻意不提供破坏性降级，因为用户学习历史比模式可逆性更重要。

关键完整性规则包括：全局唯一作答 ID、每次作答最多一条掌握度记录、每门课程唯一概念 slug、同一课程内资料内容去重，以及每个学生/概念/日期唯一复习任务。

备份恢复流程：

```mermaid
sequenceDiagram
    actor User as 用户
    participant Web as 数据页面
    participant Backup as 备份服务
    participant DB as SQLite
    User->>Web: 选择要恢复的备份
    Web->>Backup: 验证并恢复
    Backup->>Backup: 检查 SQLite 文件头和必需数据表
    Backup->>DB: 创建带时间戳的安全快照
    Backup->>DB: 使用已验证备份替换数据库
    Backup-->>Web: 返回成功或结构化错误
```

## 安全与隐私边界

- 开发服务器只绑定 `127.0.0.1`，不应直接暴露到公网。
- 浏览器表单的修改请求必须提供与会话绑定的 CSRF 令牌；当前本地模型中的 JSON API 不依赖 Cookie 鉴权，因此不适用该表单防护。
- Cookie 使用 `HttpOnly` 和 `SameSite=Strict`。
- Flask 密钥来自 `STUDYHUB_SECRET_KEY`，或保存在已忽略本地目录中的随机密钥文件。
- `.env`、数据库、备份、上传文件和课程资料均排除在版本控制之外。
- 外部 AI 同时要求全局提供商配置和按课程授权。
- 证据引用只能来自已导入资料；无法匹配时会明确返回“没有可靠证据”。

该边界适合当前的单用户桌面工作流。如果未来开放网络访问或多用户功能，还需要身份验证、授权、生产级服务、限流与更完整的威胁模型。

## 质量门禁

```mermaid
flowchart LR
    Change[代码变更] --> Lint[Ruff]
    Lint --> I18N[国际化一致性]
    I18N --> Migration[Alembic 升级]
    Migration --> Tests[单元 + 集成 + 覆盖率]
    Tests --> Audit[依赖安全审计]
    Tests --> Browser[Chromium 端到端测试]
    Audit --> Ready[可以合并]
    Browser --> Ready
```

GitHub Actions 会在 Python 3.11 与 3.12 上运行质量任务，并运行独立的 Playwright/Chromium 任务。本地测试还覆盖模型降级、多课程隔离、幂等性、备份一致性、调度器、MCP 错误结构与 CSRF 行为。

## 有意做出的取舍

- **模块化单体而非微服务：** 本地安装和事务更简单，未来仍可拆分服务。
- **服务端渲染而非单页应用：** 依赖面更小、无障碍更直接，但高度动态交互较少。
- **SQLite 而非远程数据库：** 便携且隐私友好，但不面向多用户并发写入。
- **词法检索而非向量嵌入：** 确定性强且可离线，但语义召回能力较弱。
- **同步生成而非任务队列：** 控制流透明，但较慢的模型调用会阻塞当前请求。
- **人工审核而非完全自治：** 学习安全性与可检查性更强，但需要更多用户参与。
