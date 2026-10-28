# AI 数据分析

用聊天的方式分析业务数据：用户用自然语言提问，AI 理解意图后自己查表结构、写只读 SQL、用 Python 做进一步计算、画 ECharts 图表，并能把结论沉淀成一份单文件 HTML 报告。

支持 MySQL、PostgreSQL、ClickHouse、MongoDB，一次对话可以同时使用多个数据源（手动勾选，或选择管理员编好的「数据域」）。跨数据源关联有三种方式：同一连接上的库直接在一条 SQL 里 JOIN；不同引擎的数据分别查询、汇总后在 Python 沙箱里用 DuckDB 关联；启用联邦查询（Trino）时，一条 SQL 跨任意引擎关联明细数据。关联关系有三个互相补充的来源：管理员的结构化配置、数据源 / 数据域的文字说明、AI 推断（设置页一键推断，或 AI 在对话中提交），AI 推断的进入「待审核」，管理员采纳后才对所有会话生效。

回答以可视化为主：关键数字做成指标卡，趋势、对比、构成画成图（多张图两列排布），文字只留几条要点；有图时原始查询结果默认折叠。

意图不明确时，AI 会用 `ask_user` 给出单选 / 多选题（每题都可以选「其他」自己填写），显示在输入框上方；这一轮随即结束，用户的选择作为下一条消息发回，AI 按选择继续分析。

详细文档见 [docs/](docs/README.md)：[系统介绍](docs/overview.md) · [使用指南](docs/user-guide.md) · [系统架构设计](docs/architecture.md) · [数据流向](docs/data-flow.md) · [部署与运维](docs/operations.md)。

## 架构

```
浏览器（React 聊天界面）
   │  REST + SSE（运行与连接解耦：刷新页面、断网重连后按事件序号续上）
app 容器（FastAPI）
   ├─ 会话 / 消息 / 产物 / 配置（SQLite，数据源密码与 API Key 加密存储）
   ├─ Agent 引擎：按渠道协议选择，每轮对话起一个子进程并续接历史
   │    ├─ Anthropic 协议 → Claude Agent SDK（Claude Code）；OpenAI 协议 → Codex SDK（codex app-server，Responses API）
   │    ├─ 平台工具（Claude 走进程内 MCP，Codex 走本服务的 /internal/mcp/）：search_schema / list_tables / describe_table / run_sql / run_mongo /
   │    │    run_federated_sql / run_python / render_chart / render_kpis / publish_report / propose_relation / ask_user
   │    ├─ 内置 Skill（插件形式挂载，只读）：数据探查、SQL 分析、Mongo 查询、跨源分析、Python 分析、图表设计、报告撰写
   │    └─ 文件工具只能读写本会话工作区；没有 Bash / shell，不读任何本机配置
   ├─ 表结构缓存（search_schema、关联配置、关联推断用）
   ├─ 联邦查询：每个数据源同步成 Trino 的一个 catalog（只在内存里），AI 以只读的 agent 身份查询
   └─ 业务库连接：MySQL / PostgreSQL / ClickHouse / MongoDB（只读校验 + 行数上限 + 超时）
trino 容器（联邦查询，可选，--profile federation）
   └─ 只在内部网络，不暴露端口；Web UI 关闭；agent 用户只读、不能访问 system 目录
sandbox 容器（Python 执行）
   └─ 只在内部网络：不能出网、连不到业务库；不挂数据卷——每次执行由 app 传入本会话文件、取回产出；
      并发任务各用独立 uid；内存 / CPU / 进程数 / 时长受限
```

目录：

| 路径 | 内容 |
|---|---|
| `backend/src/data_analyze/engine/` | Agent 引擎接口，Claude Agent SDK / Codex SDK 两个实现，给 Codex 的 MCP 网关，系统提示词、路径守卫、工作区 |
| `backend/src/data_analyze/tools/` | 与引擎无关的工具定义（两个引擎共用；`file_tools.py` 给 Codex 补文件和技能工具） |
| `backend/src/data_analyze/plugin/skills/` | 给 AI 的 Skill（SKILL.md），改这里就能调整 AI 的分析习惯 |
| `backend/src/data_analyze/datasources/` | 四种数据源驱动、只读 SQL / Mongo 管道校验、表结构缓存、会话数据源解析 |
| `backend/src/data_analyze/report/` | 报告模板与 ECharts 主题（聊天卡片和报告共用同一份主题） |
| `backend/src/data_analyze/runs/` | 运行管理、事件 → 消息块 |
| `frontend/` | React 19 + Vite + Tailwind 4 + ECharts 5 |
| `sandbox/` | Python 沙箱执行服务（仅标准库） |
| `deploy/` | 生产 / 开发 compose、四种数据库的 mock 数据（`deploy/mock/`） |

## 部署

服务器只需要 Docker（含 compose 插件），并能访问模型接口。

```bash
cp deploy/.env.example deploy/.env
# 编辑 deploy/.env：至少设置 DA_ACCESS_PASSWORD 和 DA_SECRET_KEY
docker compose -f deploy/docker-compose.yml up -d --build
```

浏览器打开 `http://<服务器>:8180`，输入访问口令，在「设置」里：

1. 添加模型渠道：协议选 Anthropic 兼容，填 Base URL（不含 `/v1/messages`）、API Key、模型名，点「测试」确认连通。
2. 添加数据源：选类型，填主机 / 端口 / 只读账号 / 库名和一个英文「标识」（AI 用它指代数据源）；「业务说明」里写上指标口径和表关系，AI 会优先遵循。保存后会在后台读取表结构缓存。
3. （可选）在「数据域」里把相关的数据源编成一组；在「关联关系」里配置跨数据源的关联字段，或点「推断关联」让平台根据字段注释、同名字段、命名约定找候选并抽样核对匹配率，再逐条采纳。

启用联邦查询（不同引擎之间的明细级关联）：在 `deploy/.env` 里取消 `DA_TRINO_URL=http://trino:8080` 的注释，启动时加上 `--profile federation`（Trino 约占 3~4 GB 内存）。平台会自动把每个数据源同步成 Trino 的 catalog，「设置 → 数据源」顶部会显示同步状态。

- MongoDB 参与联邦查询时，平台会把抽样推断的字段结构写进该库的 `_schema` 集合（Trino 自己只看一个文档推断，会漏列），因此账号需要额外拥有**只针对 `_schema` 集合**的写权限，业务集合仍然只读，授权方式见 `deploy/mock/mongo/init/01-load.sh` 里的 `trinoSchemaWriter` 角色。没有这项权限时联邦查询仍可用，但可空字段可能缺失。

想先用 mock 数据体验：`docker compose -f deploy/docker-compose.yml --profile mock up -d --build`，账号统一为 `analyst` / `analyst_ro_2026`：

| 标识建议 | 类型 | 主机:端口 | 库 | 内容 |
|---|---|---|---|---|
| shop | MySQL | mock-mysql:3306 | shop | 电商订单、客户、商品 |
| wms | MySQL | mock-mysql:3306 | wms | 发货单、月度库存（与 shop 同实例，可直接跨库 JOIN） |
| crm | PostgreSQL | mock-postgres:5432 | crm | 会员、积分、营销活动、优惠券 |
| events | ClickHouse | mock-clickhouse:8123 | events | 约 300 万条用户行为日志（2025 年起） |
| service | MongoDB | mock-mongo:27017 | service | 客服工单（嵌套文档） |

注意：

- 对公网开放时建议在前面加一层 HTTPS 反向代理（Nginx / Caddy）。代理需要关闭 `/api/conversations/*/events` 的缓冲（服务端已带 `X-Accel-Buffering: no`），读超时设长一些。
- `DA_SECRET_KEY` 定了就不要改，否则已保存的密码和 API Key 解不开，需要重新填写。
- 数据都在两个数据卷里：`app-data`（SQLite、会话记录、报告）和 `workspaces`（查询结果文件、脚本）。备份这两个卷即可。
- 会话超过 `DA_WORKSPACE_RETENTION_DAYS`（默认 30）天没有新消息，它的工作区（查询结果、脚本）会被自动清理，对话和报告保留；之后再追问时 AI 会重新查询。删除会话会清掉它的全部文件。
- 每个进行中的分析约占 300 MB 内存，并发上限由 `DA_MAX_CONCURRENT_RUNS` 控制，超出的排队。
- 服务器访问模型接口需要代理时，在 `deploy/.env` 里设置 `HTTPS_PROXY`，并把 `sandbox,mock-mysql` 加进 `NO_PROXY`。

## 本地开发

依赖：uv、Node 22 + pnpm、Docker。

```bash
# 1. 四种 mock 业务库与 Python 沙箱（端口见 compose 文件开头的注释）
docker compose -f deploy/docker-compose.dev.yml up -d

# 2. 后端（127.0.0.1:8110）
cd backend
cat > .env <<'ENV'
DA_DATA_DIR=var
DA_PORT=8110
DA_SANDBOX_URL=http://127.0.0.1:8901
ENV
uv sync && uv run data-analyze

# 3. 前端（http://127.0.0.1:5273，/api 代理到后端）
cd frontend && pnpm install && pnpm dev
```

在 Claude Code 里启动后端时，需要先清掉它注入的 `CLAUDECODE` 等环境变量（`env -u CLAUDECODE uv run data-analyze`），否则子进程会以为自己嵌套在另一个会话里。

没有模型 Key 时可以用按剧本应答的假接口跑通整条链路：

```bash
cd backend
uv run python tests/fake_anthropic.py 18080          # 假 Anthropic 接口
uv run python tests/e2e_flow.py http://127.0.0.1:18080 fake claude-fake
```

测试与检查：

```bash
cd backend && uv run pytest && uv run ruff check src tests
cd frontend && pnpm build
```

mock 数据由 `python3 deploy/mock/generate.py` 生成（固定随机种子，只依赖标准库）：shop 约 4.4 万订单、1.8 万客户（2024-01 至 2026-10），并派生出 wms / crm / service 的关联数据；ClickHouse 的行为日志在库内按规则生成。数据里埋了不少规律和「坑」，便于检验 AI 的分析能力：增长趋势、春节低谷、618 / 双 11 高峰、地区差异、服饰退款率偏高；EMS 送达最慢、物流慢的订单工单多且满意度低；约 3% 订单没有发货记录、150 张发货单对不上订单、15% 客户不是会员、会员等级叫法两边不一致、行为日志只覆盖 2025 年起等。

## 安全边界

| 风险 | 控制 |
|---|---|
| AI 改数据 | SQL 经 sqlglot 按各自方言解析，只放行单条 SELECT / WITH，禁 INTO OUTFILE、加锁、SLEEP、读文件和访问外部系统的函数（如 PG 的 pg_read_file、ClickHouse 的 url / remote 表函数）；MySQL 会话级只读事务、PG 只读事务、ClickHouse `readonly=2`；Mongo 只走 aggregate，禁 `$out` / `$merge` / `$where` / `$function` 和跨库 `$lookup`；仍建议使用只读账号 |
| 越权跨库 | 一条 SQL 只能引用本会话已选、且与执行数据源同一连接（同主机、端口、账号）的库；联邦 SQL 的表名必须写全「标识.schema.表」，只能引用已选数据源，MySQL / ClickHouse / MongoDB 的 schema 必须是该数据源自己的库 |
| 联邦查询引擎 | Trino 只在内部网络、不暴露端口、关闭 Web UI（查询历史里有建 catalog 时的连接串）；catalog 只放内存不落盘；平台用 platform 身份管理 catalog，AI 的查询用 agent 身份执行：文件规则限定只读、禁止访问 system 目录 |
| AI 推断的关联 | 一律进入「待审核」；对话中提交的，采纳前只出现在提交它的会话里；平台自己抽样核对匹配率，不采信模型报的数字，匹配率太低直接拒绝 |
| 查询拖垮数据库 | 自动补 LIMIT（默认 5 万行）、`MAX_EXECUTION_TIME`、客户端超时 |
| AI 读写服务器文件 | 不给 Bash；Claude 的 Read / Write / Edit / Glob / Grep 经 PreToolUse 钩子限制在会话工作区（Skill 目录只读）；Codex 关掉全部内置工具，文件操作只能走平台的工作区文件工具；不允许写 `.claude`、`CLAUDE.md`、`AGENTS.md`、`.codex` |
| 脚本逃逸 | Python 在独立容器执行：内部网络无出口、不挂数据卷、只读根文件系统；能力只保留切换 uid 所需的 4 项，脚本本身以无权限 uid 运行；内存 / CPU / 进程数 / 时长受限 |
| 会话之间互相窥探 | 每次执行只传入本会话的文件，在一次性目录里运行后即删除；并发任务各用独立 uid，目录 700、任务根目录不可列举，互相读不到文件和 /proc；任务结束后清掉该 uid 的所有残留进程；传回的文件只接受工作区内的普通文件 |
| 数据里的提示注入 | 系统提示声明库内容是数据而非指令；Agent 能做的事本身受上面几条限制 |
| 报告里的脚本 | 报告由固定模板渲染，Markdown 不允许原始 HTML，内嵌 JSON 转义；预览走 `sandbox` iframe + CSP，拿不到本站 Cookie |
| 凭据泄漏 | 密码与 API Key 用 Fernet 加密入库，页面只回显末四位；平台自身的 `DA_*` 环境变量不会传给 Agent 子进程 |

## 路线

- 更多数据源：在 `datasources/` 新增驱动并登记到 `registry.DRIVERS`，SQL 校验按 `driver.dialect` 走对应方言；联邦查询在 `Federation.catalog_properties` 里加上对应的 Trino 连接器配置。
