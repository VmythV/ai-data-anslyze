# 数据流向

本文按「一个问题从提出到得出结论」的顺序，说明数据在系统里怎么流动。

## 1. 一轮对话的全过程

```mermaid
sequenceDiagram
    autonumber
    actor U as 用户
    participant FE as 前端
    participant API as API
    participant RM as 运行管理
    participant DB as 平台库 SQLite
    participant EN as Agent 引擎
    participant CLI as Claude Code CLI
    participant LLM as 大模型
    participant T as 平台工具

    U->>FE: 输入问题
    FE->>API: POST /conversations/{id}/messages
    API->>RM: start()
    RM->>DB: 保存用户消息，首条消息作为会话标题
    RM-->>API: run_id
    API-->>FE: 立即返回
    FE->>API: GET /conversations/{id}/events（SSE）
    Note over RM: 后台任务（受全局并发上限约束，超出排队）
    RM->>DB: 读取会话、模型渠道、数据源、数据域、关联关系
    RM->>EN: 系统提示 + 工具 + 工作区 + 续接 ID
    EN->>CLI: 启动子进程（resume 上一轮）
    loop 模型自主多轮调用工具
        CLI->>LLM: 对话历史 + 工具定义
        LLM-->>CLI: 文本增量 / 工具调用
        CLI->>T: 调用平台工具
        T-->>CLI: 给模型的文字结果
        T-->>RM: UI 事件（表格、图表、指标卡……）
        EN-->>RM: 文本增量、工具调用、工具结果
        RM-->>FE: SSE 事件（带序号，断线后按序号续上）
    end
    CLI-->>EN: 本轮结束（用量、会话 ID）
    RM->>DB: 保存助手消息（消息块 + 状态 + 用量）、更新续接 ID
    RM-->>FE: done
    FE->>API: 重新拉取会话详情，落库版本替换实时版本
```

要点：

- **运行与连接解耦**：分析在后台任务里跑，页面刷新、断网都不影响；重新打开会话时按事件序号重放、接着看
- **事件统一成消息块**：引擎和工具发出的事件，前端实时渲染、后端落库，用的是同一套规则（`runs/blocks.py` ↔ `frontend/src/lib/blocks.ts`）
- **停止生成**：前端调 `/stop` → 引擎向 CLI 发中断 → 已经执行的步骤保留，状态记为「已停止」

## 2. 取数：从数据库到结果文件

```mermaid
flowchart LR
    A["AI 调用 run_sql<br/>数据源 + SQL + 目的"] --> B{"只读校验<br/>sqlglot 按方言"}
    B -->|"写操作 / 危险函数 / 多条语句"| X1["拒绝，告诉 AI 原因"]
    B --> C{"跨库范围检查"}
    C -->|"引用了未选或非同一连接的库"| X2["拒绝，提示改用结果级关联或联邦查询"]
    C --> D["补 LIMIT<br/>中文别名补引号"]
    D --> E["驱动执行<br/>会话只读 + 超时"]
    E --> F[("工作区 data/q003_shop_xxx.parquet<br/>完整结果")]
    E --> G["表格事件 → 页面<br/>前 200 行"]
    E --> H["文字结果 → 模型<br/>前 30 行 + 文件名"]
```

- 完整结果只落文件，模型只看 30 行预览：省 token，也避免大表把对话撑爆
- 后续的 Python、图表、报告都直接读这些 parquet 文件
- MongoDB 走 `run_mongo`，同样的流程；结果先拍平（嵌套字段变成 `a.b` 列）再落文件

## 3. Python 计算：工作区 ⇄ 沙箱

```mermaid
sequenceDiagram
    participant T as run_python 工具
    participant WS as 会话工作区
    participant SB as 沙箱服务
    participant J as 一次性任务目录（独立 uid）

    T->>WS: 打包 data/、output/ 等文件（不含脚本留档）
    T->>WS: 脚本存到 scripts/ 留档
    T->>SB: POST /run（脚本 + 文件包 + 超时）
    SB->>J: 解包、切换到空闲 uid、限制内存 / CPU / 进程数
    J->>J: python 执行（不能联网）
    SB->>J: 收集新增 / 修改的文件，清掉该 uid 的所有进程
    SB-->>T: stdout、stderr、退出码、产出文件包
    SB->>J: 删除任务目录
    T->>WS: 解包产出文件（只接受工作区内的普通文件）
    T-->>T: 结果回给模型
```

## 4. 图表、指标卡与报告

```mermaid
flowchart LR
    F[("工作区数据文件")] -->|"data_file"| C["render_chart<br/>ECharts option"]
    C --> P["统一排版<br/>图例放底部 · 轴名不截断<br/>少点折线显示数据点"]
    P --> AR[("产物表<br/>图表 option")]
    P --> CE["图表事件 → 页面<br/>多张图两列排布"]
    K["render_kpis"] --> KE["指标卡事件 → 页面"]
    AR -->|"chart_id"| R["publish_report<br/>结论 + 指标卡 + 图表 + 解读 + 表格"]
    F -->|"data_file"| R
    R --> H["Jinja 模板渲染<br/>ECharts 与主题内联"]
    H --> RF[("报告 HTML 文件")]
    H --> RE["报告事件 → 页面卡片"]
    RF -->|"预览：sandbox iframe + CSP<br/>下载：单文件离线可看"| U(["用户"])
```

聊天里的图表、报告里的图表、前端的主题，用的是同一份 ECharts 主题（`report/theme.py`，前端经 `/api/chart-theme` 获取）。

## 5. 跨数据源关联的三条路径

```mermaid
flowchart TB
    Q["需要两个数据源的数据"] --> S{"在同一连接上？<br/>同主机、端口、账号"}
    S -->|"是"| A["A. 一条 SQL 直接 JOIN<br/>run_sql，库名.表名"]
    S -->|"否"| V{"两边都能先聚合到关联键<br/>且各自几万行以内？"}
    V -->|"是"| B["B. 结果级关联<br/>各自 run_sql / run_mongo → parquet<br/>→ run_python 里 DuckDB 关联"]
    V -->|"否，需要明细级关联"| F{"启用了联邦查询？"}
    F -->|"是"| C["C. 联邦查询<br/>run_federated_sql，Trino 方言<br/>标识.schema.表"]
    F -->|"否"| B2["退回 B：尽量先过滤、聚合"]
    A --> M["核对匹配率，在结论里说明"]
    B --> M
    C --> M
    B2 --> M
```

### 联邦查询的数据流

```mermaid
sequenceDiagram
    participant S as 设置页
    participant FED as 联邦同步服务
    participant MG as MongoDB
    participant TR as Trino
    participant T as run_federated_sql
    participant DBS as 各业务库

    S->>FED: 新增 / 修改 / 删除数据源
    opt MongoDB
        FED->>MG: 抽样推断字段结构，写入 _schema 集合
    end
    FED->>TR: platform 身份：DROP / CREATE CATALOG（只在内存）
    Note over FED,TR: 启动时、每 5 分钟检查一次；Trino 重启丢失 catalog 后自动重建
    T->>T: 只读校验 + 表名写全 + 只能引用已选数据源
    T->>TR: agent 身份执行（只读，不能访问 system 目录）
    TR->>DBS: 各连接器查询，过滤条件下推
    DBS-->>TR: 结果
    TR-->>T: 关联后的结果
    T->>T: 和单库查询一样：落 parquet、推表格、给模型预览
```

## 6. 关联关系的来源与生命周期

```mermaid
flowchart LR
    M["管理员手动配置"] --> C["已确认"]
    I["设置页「推断关联」<br/>注释 / 同名字段 / 命名约定<br/>→ 抽样核对匹配率 ≥ 50%"] --> P["待审核"]
    D["对话中 AI 提交<br/>propose_relation<br/>平台抽样核对 ≥ 30%"] --> P2["待审核<br/>（记录来源会话）"]
    P -->|"管理员采纳"| C
    P2 -->|"管理员采纳"| C
    P -->|"删除"| Z["丢弃"]
    P2 -->|"删除"| Z
    C --> ALL["写进所有相关会话的系统提示"]
    P2 -.->|"采纳前"| ONE["只写进提交它的那个会话"]
```

推断时会去掉噪音：同一数据源内部的外键不进审核队列；两端已经能通过已知关系连通的「兄弟字段」（比如都指向订单号的两个字段）不重复提。

## 7. 引导式提问

```mermaid
sequenceDiagram
    actor U as 用户
    participant FE as 前端
    participant CLI as Agent
    participant T as ask_user 工具

    U->>FE: 「最近卖得怎么样？」
    FE->>CLI: 第一轮
    CLI->>T: 1~4 个单选 / 多选题
    T-->>FE: 问题事件
    Note over CLI: PostToolUse 钩子直接结束本轮<br/>不让模型替用户猜答案
    FE->>U: 输入框上方显示选择面板
    U->>FE: 点选（或选「其他」自己填写）
    FE->>CLI: 第二轮：选择结果作为新消息
    CLI->>CLI: 按选择继续分析
```

## 8. 数据存放与清理

| 数据 | 位置 | 生命周期 |
|---|---|---|
| 配置、会话、消息、关联、表结构缓存 | `app-data` 卷 `/data/app.db` | 长期保存 |
| 模型渠道 API Key、数据源密码 | 同上，Fernet 加密 | 长期保存；`DA_SECRET_KEY` 变了就解不开 |
| Claude 对话记录（续接用） | `/data/claude/projects/…` | 随会话删除 |
| HTML 报告 | `/data/artifacts/<会话>/` | 随会话删除 |
| 查询结果、脚本、脚本产出 | `workspaces` 卷 `/workspaces/<会话>/` | 会话超过 30 天没有新消息自动清理（对话与报告保留）；随会话删除 |
| 沙箱任务目录 | sandbox 容器 tmpfs | 每次执行结束即删除 |
| Trino catalog | Trino 内存 | Trino 重启即丢，平台自动重建 |

后台每小时清理一次：过期工作区，以及数据库里已经没有对应会话的孤儿目录（只清理一小时以上没变动的，避免误删刚建好的会话）。
