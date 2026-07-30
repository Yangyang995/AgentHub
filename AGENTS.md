# AgentHub 项目执行规范

## 1. 文档作用域

本文件约束 AgentHub 仓库内所有人工与 AI 辅助开发工作。开始任何任务前，必须先阅读本文件、根目录 `README.md` 和 `DEVELOPMENT_PLAN.md` 中当前阶段。若当前代码、需求和本文件冲突，先说明冲突、影响与可选处理方式，不得静默选择。

## 2. 产品定位与首版边界

AgentHub 是以群聊写代码为核心的多 Agent 软件交付工作台。首版核心闭环为：

`群聊 -> 需求输入 -> Orchestrator 拆解 -> 子Agent并行/串行执行 -> Diff审核 -> 网页预览 -> Vercel部署`

首版固定边界：

- 面向本地单用户，不实现账号体系、组织、多租户和计费。
- 单聊仅保留 DeepSeek（通过 OpenAI 兼容接口）。移除 Claude Code 和 Codex CLI Adapter 作为可选提供方。
- 群聊预置 6 个代码垂直子 Agent：需求分析专家、架构设计专家、代码生成专家、代码审查专家、测试专家、技术报告撰写专家。
- Agent 接入实现确定性的 Mock Adapter 和真实 DeepSeek Adapter。
- Orchestrator 通过 LangGraph + OpenAI 兼容接口实现群聊隐式消息的任务拆解、子 Agent 调度和结果汇总。
- PostgreSQL + pgvector 同时保存业务数据和向量嵌入，支撑 RAG 知识库和会话记忆。
- pg_trgm 支持模糊搜索。
- 支持本地网页预览和 Vercel 部署。
- 高风险动作必须由用户明确批准，不允许以"自动化体验"为由绕过确认。

不得在未修改需求基线的情况下把后续设想提前加入首版。

## 3. 目标技术栈

### 3.1 后端

- Python 3.13
- FastAPI、Pydantic v2
- SQLAlchemy 2 async、asyncpg、Alembic
- PostgreSQL、pgvector、pg_trgm
- LangGraph
- WebSocket
- pytest、pytest-asyncio、httpx、mypy 或 pyright、Ruff

所有数据库、网络、文件流和子进程等待均使用异步接口。仅 CPU 密集型或第三方同步库可进入受控线程池，并在调用点用中文注释解释原因和资源边界。

### 3.2 前端

- React 19、Vite、TypeScript strict
- React Router、TanStack Query
- Lucide 图标
- Vitest、React Testing Library、Playwright

前端是高信息密度的软件交付工作台。必须覆盖加载、空数据、失败、断连、重连、取消和重试状态；桌面与移动端不得出现文本遮挡、不可达操作或流式消息导致的布局跳动。

## 4. 目标目录

```text
AgentHub/
|-- AGENTS.md
|-- DEVELOPMENT_PLAN.md
|-- README.md
|-- .env.example
|-- .gitignore
|-- backend/
|   |-- pyproject.toml
|   |-- alembic.ini
|   |-- alembic/
|   |-- prompts/
|   |   |-- orchestrator/
|   |   |-- adapters/
|   |   `-- agents/          # 各子Agent的System Prompt
|   |-- src/agenthub/
|   |   |-- api/
|   |   |-- core/
|   |   |-- db/
|   |   |-- models/
|   |   |-- repositories/
|   |   |-- schemas/
|   |   |-- services/
|   |   |-- adapters/
|   |   |-- orchestrator/
|   |   |-- rag/              # RAG 知识库与会话记忆
|   |   `-- main.py
|   `-- tests/
|-- frontend/
|   |-- src/
|   |   |-- api/
|   |   |-- components/
|   |   |-- features/
|   |   |-- hooks/
|   |   |-- routes/
|   |   |-- schemas/
|   |   `-- styles/
|   |-- tests/
|   `-- e2e/
|-- infra/
|   |-- compose.yaml
|   |-- backend.Dockerfile
|   `-- frontend.Dockerfile
`-- docs/
    |-- architecture/
    |-- api/
    `-- acceptance/
```

目录可以在对应阶段按实际框架惯例小幅调整，但必须说明理由。禁止为了预想需求建立没有使用者的空抽象层。

## 5. 核心业务对象

- `Project`：已注册本地项目及其受信任根目录，是工作区和安全隔离边界。
- `Agent`：Agent 注册信息、平台类型、能力、启停状态和适配器配置引用。预置 6 个代码子 Agent（需求分析、架构设计、代码生成、代码审查、测试、技术报告撰写）。
- `Conversation`：项目内单聊或群聊会话。
- `Message`：用户、Agent 或系统消息，保存稳定顺序和内容类型。
- `AgentExecution`：一次 Adapter 执行，记录状态、序号、取消信息和错误。
- `Task`、`TaskDependency`：Orchestrator 子任务与依赖关系。
- `Artifact`：Agent 产生的代码 Diff、预览包、报告或部署描述。
- `Deployment`：部署请求、审批、状态、目标和结果 URL。
- `UsageEvent`：调用次数、Token、耗时、结果与 Agent 维度的原始统计事件。
- `Approval`：高风险操作的持久化确认记录，必须可在页面刷新后恢复。
- `Memory`：会话记忆摘要，向量化后存入 pgvector，支撑跨时间对话连贯性。
- `KnowledgeDocument`：RAG 知识文档分块，包含向量嵌入和元数据。

API Schema、领域对象和 ORM 模型职责分离。仓储层不得把 SQLAlchemy ORM 实例直接返回给路由层。

## 6. 架构边界

### 6.1 Pipeline 与 Orchestrator

普通消息链路负责确定性步骤：保存消息、解析显式 `@Agent`、调用 Adapter、持久化事件和推送 WebSocket。显式点名多个 Agent 时只做并行分发，不进行隐式任务拆解。

Orchestrator 仅处理群聊中**无显式 @Agent** 的复杂任务：通过 LangGraph 管理意图分析 → 计划生成 → 能力匹配 → 并行调度 → 结果汇总的状态机。Orchestrator 在执行前通过 RAG 检索项目上下文，通过记忆模块检索相关历史摘要。LLM 输出是不可信输入，必须经过 Pydantic 校验和策略检查后才能执行。

### 6.2 Adapter 契约

所有 Agent Adapter 公开同一语义接口：

```python
class AgentAdapter(Protocol):
    capabilities: frozenset[AgentCapability]

    async def healthcheck(self) -> AdapterHealth: ...
    async def run(self, task: AgentTask) -> AsyncIterator[AgentEvent]: ...
    async def cancel(self, execution_id: UUID) -> None: ...
```

`AgentEvent` 必须是可判别联合类型，至少覆盖：

- 内容增量 `content.delta`
- 执行状态 `execution.status`
- 结构化错误 `execution.error`
- 用量 `execution.usage`
- 产物 `artifact.created`

Adapter 负责平台协议转换，不负责业务路由、审批或数据库事务。平台错误必须映射为稳定错误码，不得把包含凭据、绝对隐私路径或完整 stderr 的内容直接发送给前端。

首版仅保留两个 Adapter 实现：
- `MockAdapter`：确定性脚本控制，用于自动化测试。
- `OpenAICompatibleAdapter`（DeepSeek）：单聊和所有 6 个子 Agent 均使用此 Adapter，通过不同的 System Prompt 区分能力。

### 6.3 REST 与 WebSocket 契约

- REST 统一前缀：`/api/v1`
- WebSocket：`/ws/conversations/{conversation_id}`
- 所有资源 ID 使用 UUID。
- 数据库存储 UTC；接口时间使用带时区的 UTC ISO 8601；前端按用户本地时区显示。
- WebSocket 和 Adapter 事件必须在 Pydantic 与 TypeScript 中采用同源 Schema，避免手工维护两套不一致定义。

WebSocket 事件信封：

```json
{
  "event_id": "00000000-0000-0000-0000-000000000000",
  "conversation_id": "00000000-0000-0000-0000-000000000000",
  "execution_id": "00000000-0000-0000-0000-000000000000",
  "sequence": 1,
  "type": "message.delta",
  "timestamp": "2026-01-01T00:00:00Z",
  "payload": {}
}
```

同一执行的 `sequence` 必须单调递增。服务端持久化可重放事件，客户端按 `event_id` 去重，并在重连时携带最后确认序号请求补发。

### 6.4 Artifact 与审批

Agent 输出代码 Diff 必须登记为 Artifact，至少记录项目、执行、类型（`DIFF`）、内容哈希、大小、创建时间和元数据。

以下动作必须先产生 `approval.required` 事件和持久化审批记录，获得批准后再执行，并产生 `approval.resolved`：

- 应用代码 Diff 到目标项目
- 启动本地预览进程
- 发起 Vercel 部署

审批必须绑定动作摘要和内容哈希，批准后动作发生变化则原批准失效。

### 6.5 工作区与 Git（简化）

Phase 7 起实现 Code Diff 功能。首版不要求隔离 worktree，Agent 直接在项目根目录操作。对所有输入路径执行规范化和根目录包含检查，拒绝路径逃逸。

- 子进程使用参数数组启动，禁止 `shell=True`。
- 取消、失败和完成后执行可观测的资源清理。

### 6.6 RAG 与会话记忆

RAG 知识库为 Orchestrator 和子 Agent 提供项目代码和文档的上下文检索：

- 文档摄入：代码按函数/类边界分块，文档按段落/标题分块。
- 向量存储：pgvector，嵌入模型通过 OpenAI 兼容 API。
- 混合检索：pg_trgm 关键词 + pgvector 向量相似度，限定 project_id。
- 重排序：提升检索相关性。

会话记忆确保跨时间的对话连贯性：

- 短期记忆：当前会话完整消息历史（已有）。
- 长期记忆：LLM 生成会话摘要 → 向量化存入 pgvector。
- 记忆检索：新消息到达时自动检索相关历史摘要，注入上下文窗口。

### 6.7 Prompt 管理

Orchestrator 计划 Prompt、子 Agent System Prompt 和 Adapter 任务模板保存在 `backend/prompts/` 下的版本化文件中，由运行时代码加载。文档只描述 Prompt 的目标与约束，不再维护一份会与实际代码漂移的完整运行时 Prompt。

Prompt 变更必须有版本标识、输入输出 Schema 说明和回归用例。不得记录或向模型注入密钥、Cookie、会话令牌及不必要的环境变量。

## 7. 阶段纪律

1. 开始阶段前阅读该 Phase 的完整提示词，核对所有前置交付物和当前工作树状态。
2. 明确本次假设、范围、成功标准和实际验证命令；不确定且影响数据或接口时先询问。
3. 只实现当前阶段，不提前实现依赖尚未验收的后续能力。
4. 每个可观察行为优先建立失败测试或契约测试，再完成最小实现。
5. 当前阶段所有必需验收通过后，记录命令和结果，才可进入下一阶段。
6. 若环境不具备某项集成条件，执行可行的静态或 Mock 验证，明确标记未执行项；不得把"未运行"写成"已通过"。
7. Phase 验收完成后进行一次知识复盘，只筛选实际遇到且可复用的问题，以及与本阶段实现直接相关的面试知识点。
8. 复盘有高价值内容时随阶段完成报告发给用户；没有符合条件的内容时省略整个复盘部分，不输出空标题、占位语或泛化知识清单。

### 7.1 阶段知识复盘

复盘不是 Phase 的额外产品交付物，不得为了生成复盘而扩大实现范围。内容必须以本阶段代码、测试、命令输出或技术决策为依据，并遵守以下规则：

- **有价值的问题**：只记录实际发生、影响实现或验收且对后续阶段有复用价值的问题。每条说明现象、根因、解决方法和预防方式。
- **面试知识点**：只整理与本阶段真实实现直接相关、能够结合本项目代码讲清楚的原理或工程权衡。每条给出典型问题、回答要点和 AgentHub 中的实践证据。
- 优先保留架构边界、异步与并发、类型与契约、数据一致性、安全、测试策略、性能和故障恢复等高信号内容。
- 不收录单纯拼写错误、一次性命令失误、没有结论的猜测，以及脱离本阶段的通用八股文。
- 不展示密钥、凭据、敏感日志或不必要的本机隐私路径；问题示例和日志必须脱敏。
- 默认每类最多 5 条，按价值排序，表达应足以用于复盘和面试准备，但不重复阶段改动摘要。

## 8. 编码原则

### 8.1 编码前思考

- 不替用户做未经验证的产品假设。
- 存在多种解释时列出差异、影响和建议选择。
- 更简单的实现能满足需求时，主动说明并采用简单方案。
- 需求或安全边界不清楚时停止破坏性操作并提问。

### 8.2 简洁与精准

- 使用满足当前验收标准的最少代码。
- 不为单一调用建立通用框架，不增加未被要求的可配置性。
- 不顺手重构、格式化或删除无关代码。
- 只清理本次改动产生的未使用导入、变量、文件和引用。
- 每一行改动都应能追溯到当前任务或必要测试。

### 8.3 中文注释

生成的代码使用清晰、详细但有信息量的中文注释，重点解释：

- 公共接口的输入、输出、不变量和错误语义。
- 并发、重试、恢复、事务和复杂状态转换。
- 路径校验、审批、脱敏等安全边界。
- 看似不直观的设计选择及其原因。

禁止逐行翻译代码、重复变量名含义或给显然表达式添加旁白式注释。注释与代码行为同时维护。

### 8.4 后端规范

- 开启严格类型检查；公共函数、协议和 Schema 必须完整标注类型。
- 路由层只负责协议转换、依赖注入和响应映射，业务逻辑放在服务层。
- 事务边界由服务层明确控制；仓储方法不隐式提交多个业务动作。
- 外部输入全部通过 Pydantic 校验；错误响应使用稳定错误码和安全消息。
- 不在 async 路径中调用阻塞式数据库、网络或 `subprocess.run`。
- 并行执行优先使用 `asyncio.TaskGroup`，明确取消传播和部分失败语义。

### 8.5 前端规范

- TypeScript 开启 `strict`，禁止用 `any` 绕过事件和 API 类型。
- 服务端状态由 TanStack Query 管理；临时 UI 状态留在组件或局部 store。
- WebSocket 更新必须幂等，按事件 ID 去重并保持消息稳定键。
- 使用语义化 HTML、键盘可达焦点和明确的 aria 标签。
- 工具按钮优先使用 Lucide 图标并提供 tooltip；命令按钮使用清楚的图标加文字。
- 卡片圆角不超过 8px；不嵌套装饰性卡片；避免营销页式大标题和无功能装饰。
- 所有固定工具栏、消息列表和预览区域定义稳定尺寸与响应式约束。

## 9. 安全与密钥保护

- 绝不能提交、打印、记录、返回或写入文档真实密码、API Key、令牌、Cookie、数据库凭据和会话值。
- 示例值统一使用 `YOUR_API_KEY_HERE`、`YOUR_DATABASE_URL_HERE` 等明显占位符。
- 禁止创建输出环境变量全集或凭据的调试代码。
- 日志对 Authorization、Cookie、连接串、查询参数和外部 stderr 做结构化脱敏。
- 若发现疑似真实密钥，只显示 `<REDACTED>`，提醒用户立即轮换，并从当前文件及 Git 历史中移除。
- 文件上传、归档解包、预览资源和 Diff 路径必须防止目录穿越。
- HTML 预览使用受控临时目录、严格资源范围和 sandboxed iframe，不执行用户机器上的任意服务。

## 10. 测试与验收

测试范围随风险增加：

- 纯函数和 Schema：单元测试。
- 仓储和迁移：真实 PostgreSQL 集成测试。
- API、WebSocket、Adapter：契约和集成测试。
- 聊天、审批、Diff、预览、部署：Playwright 端到端测试。
- DeepSeek 集成：默认 Mock 保证确定性，发布前另做具备条件的真实冒烟测试。
- RAG 检索：向量相似度 + 关键词混合搜索的集成测试。

测试必须验证成功路径、失败路径、取消、超时、重试、项目隔离和敏感信息不泄漏。不得只断言 HTTP 状态码而忽略持久化状态和事件顺序。

## 11. 标准命令

在 Windows PowerShell 中，前端命令使用 `npm.cmd`：

```powershell
# 后端依赖与运行
cd backend
uv sync --all-groups
uv run uvicorn agenthub.main:app --reload

# 后端质量检查
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest

# 数据库迁移
uv run alembic upgrade head
uv run alembic downgrade -1

# 前端依赖与运行
cd frontend
npm.cmd ci
npm.cmd run dev

# 前端质量检查
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test -- --run
npm.cmd run build
npm.cmd run e2e
```

若实际脚本名在阶段中确定为其他名称，应同步更新本文件和 README，保持命令可复制执行。

## 12. 当前开发环境事实

截至 2026-07-30 已核对：

- Python 3.13.7 可用。
- uv 0.11.8 可用。
- Node.js v24.16.0 可用。
- PowerShell 执行策略会阻止 `npm.ps1`，使用 `npm.cmd`。
- Docker CLI 与守护进程可用，版本为 29.6.2。
- psql 当前不在 PATH，数据库命令行集成尚未验证。
- DeepSeek API 通过 OpenAI 兼容接口调用，是唯一的生产 Agent 提供方。
- Claude Code CLI 和 Codex CLI Adapter 已移除作为可选提供方，相关代码保留在仓库中但不再向前端暴露。
- pgvector 扩展已在 Compose 配置中启用，用于 RAG 知识库和会话记忆（Phase 8）。

这些事实可能随环境变化。每个阶段开始时重新探测其依赖，并记录本次实际结果。

## 13. 完成定义

任务完成必须同时满足：

- 实现范围与当前 Phase 一致，没有偷偷扩展产品边界。
- 新增行为有对应测试，相关既有测试未回归。
- 类型检查、Lint、测试和构建按阶段要求通过。
- 安全边界、审批、项目隔离和日志脱敏有可验证证据。
- 文档、迁移、Schema 和运行命令与实现同步。
- 最终报告列出改动、实际执行命令、通过结果、未执行项及原因。
- 已完成知识复盘筛选；存在高价值条目时已在最终报告中提供，不存在时未生成占位内容。
