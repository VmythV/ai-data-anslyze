# 系统架构设计

## 1. 总体架构

```mermaid
flowchart TB
    user(["用户浏览器<br/>React 单页应用"])

    subgraph app["app 容器（FastAPI）"]
        api["API 层<br/>会话 · 设置 · 产物 · SSE 事件流"]
        runs["运行管理<br/>后台任务 · 事件缓冲 · 并发限制"]
        store[("SQLite<br/>配置 · 会话 · 消息 · 产物 · 关联")]
        engine["Agent 引擎<br/>Claude Agent SDK / Codex SDK"]
        cli["Claude Code CLI<br/>每轮一个子进程 · resume 续接"]
        skills[("Skill 插件<br/>只读")]
        tools["平台工具（进程内 MCP）<br/>找表 · 查询 · 计算 · 图表 · 报告 · 提问"]
        services["共享服务<br/>驱动池 · 表结构缓存 · 联邦同步 · 关联推断"]
        files[("数据卷<br/>工作区 · 报告 · 对话记录")]
        api --> runs
        runs --> store
        runs --> engine --> cli
        cli -.->|"按需加载"| skills
        cli <-->|"工具调用"| tools
        tools --> services
        tools --> files
    end

    llm["大模型接口<br/>Anthropic 兼容协议"]
    sandbox["sandbox 容器<br/>Python 执行<br/>无网络 · 无数据卷"]
    trino["trino 容器（可选）<br/>联邦查询"]
    db[("业务数据库（只读账号）<br/>MySQL · PostgreSQL<br/>ClickHouse · MongoDB")]

    user <-->|"HTTPS / SSE"| api
    cli <-->|"Messages API"| llm
    tools -->|"按次传入工作区文件"| sandbox
    tools -->|"联邦 SQL"| trino
    services -->|"只读查询"| db
    trino -->|"各连接器"| db
```

| 组件 | 职责 | 技术 |
|---|---|---|
| 前端 | 对话界面、设置页、图表与报告展示 | React 19、Vite、Tailwind 4、ECharts 5、TanStack Query |
| API 层 | REST 接口、SSE 事件流、访问口令校验、托管前端静态文件 | FastAPI、sse-starlette |
| 运行管理 | 把一轮对话放到后台任务里跑，与 HTTP 连接解耦；缓冲事件供断线重放；全局并发上限 | asyncio |
| Agent 引擎 | 组装系统提示、工具、权限，驱动模型多轮调用工具 | Anthropic 协议：Claude Agent SDK（内置 Claude Code CLI）；OpenAI 协议：Codex Python SDK（内置 codex app-server，走 Responses API） |
| 平台工具 | AI 能做的所有动作，与引擎无关的定义，包装成进程内 MCP 工具 | 自研 |
| Skill | 分析方法论（Markdown），以 Claude Code 插件形式挂载 | SKILL.md |
| 数据源驱动 | 四种数据库的连接、表结构读取、只读查询 | aiomysql、asyncpg、clickhouse-connect、pymongo |
| 联邦查询 | 把数据源同步成 Trino catalog，执行跨引擎 SQL | Trino 484 |
| Python 沙箱 | 执行 AI 写的分析脚本 | 独立容器、标准库 HTTP 服务、pandas / DuckDB 等 |
| 平台存储 | 配置、会话、消息、产物、关联、表结构缓存 | SQLite（WAL）+ SQLAlchemy |

## 2. 部署拓扑

```mermaid
flowchart LR
    browser(["浏览器"]) -->|":8180 → :8000"| app

    subgraph host["服务器（Docker Compose）"]
        subgraph defnet["default 网络"]
            app["app<br/>页面 + API + Agent<br/>挂载 app-data、workspaces 两个数据卷"]
            trino["trino<br/>profile: federation"]
            mocks["mock 数据库 ×4<br/>profile: mock"]
        end
        subgraph sbnet["sandbox 网络（internal，无外网出口）"]
            sandbox["sandbox<br/>Python 执行<br/>不挂数据卷"]
        end
    end

    app -->|"内部网络 :8901"| sandbox
    app -->|":8080"| trino
    app -->|"HTTPS"| llm["模型接口"]
    app --> realdb[("业务数据库")]
    trino --> realdb
    app --> mocks
    trino --> mocks
```

- **app** 同时连接 default 网络（访问数据库、模型、Trino）和 sandbox 网络（访问沙箱）；只有 app 挂载数据卷：`app-data`（`/data`：平台库、对话记录、报告）和 `workspaces`（`/workspaces`：各会话的查询结果与脚本）
- **sandbox** 只在 internal 网络上：没有外网出口，也连不到任何数据库；不挂数据卷；根文件系统只读
- **trino** 不对外暴露端口，只有 app 能访问；Web UI 关闭
- 对外只暴露 app 的一个端口，建议前面再加一层 HTTPS 反向代理

## 3. 后端模块

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 640}}}%%
flowchart TB
    L1["<b>接口层</b>　api/<br/>conversation_routes · settings_routes · artifact_routes · app（登录、静态文件）"]
    L2["<b>运行层</b>　runs/<br/>manager（运行生命周期）· blocks（事件 → 消息块）· housekeeping（文件清理）"]
    L3["<b>引擎层</b>　engine/<br/>base（引擎接口、事件）· claude（Claude Agent SDK 实现）· codex（Codex SDK 实现）· mcp_gateway（给 Codex 的 HTTP MCP）<br/>prompts（系统提示）· guard（文件路径守卫）· workspace（会话工作区）"]
    L4["<b>工具层</b>　tools/（与引擎无关）<br/>data_tools（找表、SQL、Mongo、联邦）· python_tool + bundle<br/>chart_tool · kpi_tool · report_tool · ask_tool · relation_tool"]
    L5["<b>基础设施</b><br/>datasources/：四种驱动 · sql_guard · catalog（表结构缓存）· federation（Trino）· sources<br/>relations/infer（关联推断）· report/（报告模板、图表主题）· store/（模型、迁移）· plugin/skills/"]
    L1 --> L2 --> L3 --> L4 --> L5
    L1 -.->|"设置页：表结构、联邦状态、推断关联"| L5
```

### 引擎的可替换性

`engine/base.py` 定义了引擎接口 `AgentEngine.run(turn, sink) -> TurnOutcome`，并把过程统一投影成 UI 事件（文本增量、工具调用、工具结果、表格、图表……）。工具定义在 `tools/` 里，不依赖任何引擎。

按渠道协议选引擎（`create_engine`）：

- **Anthropic 协议 → Claude Agent SDK**：平台工具以进程内 MCP 提供；文件工具和 Skill 用 Claude Code 内置的。
- **OpenAI 协议 → Codex Python SDK**：每轮起一个 `codex app-server` 子进程，`thread_resume` 续接历史。Codex 的 MCP 只能连外部服务，所以平台工具经 `engine/mcp_gateway.py` 以 Streamable HTTP MCP 挂在本服务的 `/internal/mcp/` 上（每轮一个随机令牌，只在这一轮有效），handler 仍在本进程执行。Codex 自带的 shell、补丁、联网搜索、子代理等全部关掉，缺的文件和 Skill 能力由平台工具 `read_file / list_files / grep_files / write_file / edit_file / load_skill`（`tools/file_tools.py`）补上。渠道必须支持 Responses API。

两个引擎共用 `tools/` 的工具定义和 `plugin/skills/` 的技能；系统提示按 `AgentEngine.kind` 介绍各自的文件 / 技能工具。

### Agent 的权限边界

| 能力 | 处理 |
|---|---|
| 能力 | Claude 引擎 | Codex 引擎 |
|---|---|---|
| 平台工具 | 进程内 MCP，白名单放行 | 本服务 `/internal/mcp/`，每轮随机 Bearer 令牌 |
| 文件读写 | Read / Write / Edit / Glob / Grep，PreToolUse 钩子限制在本会话工作区（Skill 目录只读） | 平台的 read_file 等工具，只接受相对工作区的路径 |
| 禁写的文件 | `.claude`、`CLAUDE.md`、`AGENTS.md`、`.codex`、`.agents` 等会被当成配置或指令加载的文件，两个引擎都不能写，每轮开始前清掉 | 同左 |
| Skill | 只开放平台的分析技能，CLI 自带的编码类技能不可见 | `load_skill` 只读平台技能；Codex 自带技能不注入提示词 |
| Bash、联网工具 | 不提供 | shell / unified_exec / web_search / view_image / multi_agent 等全部关闭；只读沙箱 + 审批一律拒绝兜底 |
| 本机配置 | `setting_sources=[]`，独立的 `CLAUDE_CONFIG_DIR` | 每个会话独立的 `CODEX_HOME`（`<data_dir>/codex/<会话>`），`project_doc_max_bytes=0` 不读 AGENTS.md |
| 平台密钥 | `DA_*` 环境变量不传给子进程 | 同左；渠道 Key 和 MCP 令牌只经环境变量传，不出现在命令行 |
| 结束本轮 | `ask_user` 之后由 PostToolUse 钩子直接结束 | `ask_user` 的结果返回后中断这一轮，记为正常完成 |
| 单轮上限 | `max_turns` | 工具调用次数达到 `DA_AGENT_MAX_TURNS` 后中断 |

## 4. 数据模型

```mermaid
erDiagram
    MODEL_CHANNEL ||--o{ CONVERSATION : "使用"
    DATA_DOMAIN ||--o{ CONVERSATION : "选择"
    DATA_DOMAIN ||--o{ DOMAIN_DATASOURCE : "包含"
    DATASOURCE ||--o{ DOMAIN_DATASOURCE : ""
    CONVERSATION ||--o{ CONVERSATION_DATASOURCE : "手动勾选"
    DATASOURCE ||--o{ CONVERSATION_DATASOURCE : ""
    CONVERSATION ||--o{ MESSAGE : "包含"
    CONVERSATION ||--o{ ARTIFACT : "产出"
    DATASOURCE ||--o{ RELATION : "一端"
    DATASOURCE ||--o{ SCHEMA_ENTRY : "表结构缓存"

    MODEL_CHANNEL {
        string id PK
        string protocol "anthropic 或 openai"
        string base_url
        string api_key_enc "加密"
        string model
        bool is_default
    }
    DATASOURCE {
        string id PK
        string alias "英文标识，唯一"
        string kind "mysql postgresql clickhouse mongodb"
        string host
        int port
        string password_enc "加密"
        string database
        json options
        text description "业务说明"
    }
    DATA_DOMAIN {
        string id PK
        string name
        text description
    }
    CONVERSATION {
        string id PK
        string title
        string channel_id FK
        string domain_id FK
        string engine_session_id "续接对话用"
    }
    MESSAGE {
        string id PK
        string conversation_id FK
        string role
        json blocks "文本 工具 表格 图表 报告 问题"
        string status
        json usage
    }
    ARTIFACT {
        string id PK
        string kind "chart 或 report"
        json spec
    }
    RELATION {
        string id PK
        string left_table
        string left_column
        string right_table
        string right_column
        string cardinality
        string source "manual 或 ai"
        string status "confirmed 或 pending"
        float match_rate
        string conversation_id "AI 在哪个会话提交的"
    }
    SCHEMA_ENTRY {
        int id PK
        string datasource_id FK
        string table_name
        string column_name "为空表示表本身"
        string data_type
        text comment
    }
```

会话最终可用的数据源 = 选中数据域里的数据源 ∪ 手动勾选的数据源。数据域里增减数据源后，已有会话也会跟着变化。

## 5. 安全边界

```mermaid
flowchart LR
    subgraph trust["可信"]
        platform["平台代码与 Skill"]
    end
    subgraph semi["半可信：AI"]
        agent["Agent（模型 + CLI）"]
    end
    subgraph untrusted["不可信：数据"]
        data["数据库里的内容<br/>用户上传的问题"]
    end

    agent -->|"只读 SQL<br/>三层校验"| db[("业务数据库")]
    agent -->|"文件读写<br/>限工作区"| ws[("会话工作区")]
    agent -->|"Python<br/>隔离执行"| sb["沙箱"]
    agent -->|"联邦 SQL<br/>只读身份"| tr["Trino"]
    data -.->|"可能含提示注入"| agent
```

| 风险 | 控制 |
|---|---|
| AI 修改数据 | ① sqlglot 按方言解析，只放行单条 SELECT / WITH，禁止读文件、访问外部系统、睡眠、加锁的函数；Mongo 禁止 `$out` / `$merge` / `$where` / `$function` ② 数据库会话级只读（MySQL 只读事务、PG `default_transaction_read_only`、ClickHouse `readonly=2`）③ 建议只读账号 |
| 越权跨库 | 一条 SQL 只能引用本会话已选、且同一连接的库；联邦 SQL 表名必须写全，MySQL / ClickHouse / MongoDB 的 schema 必须是该数据源自己的库 |
| 拖垮数据库 | 自动补 LIMIT（默认 5 万行）、服务端执行超时、客户端超时 |
| AI 读写服务器文件 | 不给 Bash / shell；文件工具限工作区；工作区里的 `.claude` / `CLAUDE.md` / `AGENTS.md` / `.codex` 等每轮清掉 |
| 脚本逃逸 | 沙箱：无外网、无数据卷、只读根文件系统、只保留切换 uid 所需的 4 项能力、内存 / CPU / 进程数 / 时长受限 |
| 会话之间互相窥探 | 每次执行只传入本会话文件、一次性目录、并发任务各用独立 uid、结束后清掉 uid 下所有进程 |
| 报告里的脚本 | 固定模板渲染，Markdown 不允许原始 HTML；预览走 sandbox iframe + CSP |
| 凭据泄漏 | Fernet 加密入库；页面只回显末四位；Trino catalog 只在内存、Web UI 关闭 |
| 提示注入 | 系统提示声明数据库内容是数据不是指令；即使被注入，Agent 能做的事也受以上限制 |

## 6. 关键设计取舍

| 决策 | 理由 |
|---|---|
| 用 Claude Agent SDK / Codex SDK，而不是自己写 Agent 循环 | 自带多轮工具调用、上下文压缩、会话续接；两种协议各用官方 Agent 运行时，通过引擎接口接入 |
| 每轮对话起一个 CLI 子进程、用 resume 续接 | 不常驻进程，空闲时不占资源；等用户回答问题时也不占运行名额 |
| 运行与 HTTP 连接解耦 | 刷新页面、断网不影响分析；按事件序号续上 |
| 查询结果落成 parquet，只给模型 30 行预览 | 省 token；Python、图表、报告都直接读文件，不用把数据塞进对话 |
| 报告由固定模板渲染，AI 只提供内容 | 风格稳定，不会混进脚本 |
| 沙箱不挂共享卷，按次传文件 | 会话之间彻底隔离，代价是每次执行多一次打包传输 |
| Trino catalog 只放内存 | 不把数据库密码写盘；Trino 重启后由平台自动重新同步 |
| 平台替 Trino 写 MongoDB `_schema` | Trino 只看一个文档推断结构，会漏掉可空字段 |
| AI 推断的关联先进入待审核 | 关联错了会让所有会话的结论出错，需要人确认 |
| 元数据用 SQLite | 内部小团队足够；以后需要时改连接串即可换数据库 |
