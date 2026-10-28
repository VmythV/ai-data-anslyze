# 部署与运维

## 1. 容器与 profile

| 容器 | 何时启动 | 端口 | 说明 |
|---|---|---|---|
| app | 总是 | 对外 `DA_PUBLIC_PORT`（默认 8180）→ 8000 | 页面 + API + Agent |
| sandbox | 总是 | 不对外 | Python 执行，只在 internal 网络 |
| trino | `--profile federation` | 不对外 | 联邦查询引擎，约 3~4 GB 内存 |
| mock-mysql / mock-postgres / mock-clickhouse / mock-mongo | `--profile mock` | 不对外 | 演示数据，账号 `analyst` / `analyst_ro_2026` |

```bash
cp deploy/.env.example deploy/.env      # 至少设置 DA_ACCESS_PASSWORD、DA_SECRET_KEY
docker compose -f deploy/docker-compose.yml up -d --build                                   # 最小部署
docker compose -f deploy/docker-compose.yml --profile federation up -d --build              # + 联邦查询
docker compose -f deploy/docker-compose.yml --profile mock --profile federation up -d --build  # + 演示数据
```

启用联邦查询时，还要在 `deploy/.env` 里设置 `DA_TRINO_URL=http://trino:8080`。

## 2. 配置项

### app（`deploy/.env`，前缀 `DA_`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DA_ACCESS_PASSWORD` | 空 | 全站访问口令。为空则任何人可访问，只适合本地开发 |
| `DA_SECRET_KEY` | 空（自动生成并存到数据目录） | 加密密码 / API Key、签发登录凭证。**定了就不要改**，否则已保存的凭据解不开 |
| `DA_PUBLIC_PORT` | 8180 | 对外端口（compose 用） |
| `DA_MAX_CONCURRENT_RUNS` | 4 | 同时进行的分析数，每个约占 300 MB 内存；超出的排队 |
| `DA_AGENT_MAX_TURNS` | 40 | 单轮对话 Agent 最多调用模型的次数（Codex 引擎按工具调用次数计） |
| `DA_SQL_MAX_ROWS` | 50000 | 单次查询结果行数上限 |
| `DA_SQL_TIMEOUT_SECONDS` | 30 | 单库查询超时 |
| `DA_FEDERATED_TIMEOUT_SECONDS` | 120 | 联邦查询超时 |
| `DA_PYTHON_TIMEOUT_SECONDS` | 120 | Python 脚本超时 |
| `DA_WORKSPACE_RETENTION_DAYS` | 30 | 会话多少天没有新消息就清理工作区；0 = 不清理 |
| `DA_TRINO_URL` | 空 | 联邦查询引擎地址；空 = 不启用 |
| `DA_TRINO_HOST_ALIASES` | 空 | Trino 访问数据库时的主机名替换，如 `127.0.0.1=host.docker.internal`（本地开发用） |
| `DA_SANDBOX_URL` | `http://sandbox:8901`（容器内） | 沙箱地址 |
| `DA_INTERNAL_URL` | `http://127.0.0.1:<DA_PORT>` | Codex 子进程回调本服务取平台工具的地址（`/internal/mcp/`），一般不用改；端口被反向代理改写或只监听特定网卡时才需要设 |
| `DA_LOG_LEVEL` | INFO | 日志级别 |
| `HTTPS_PROXY` / `NO_PROXY` | — | 访问模型接口需要代理时设置；`NO_PROXY` 要包含 `sandbox,trino` 和各数据库主机 |

### sandbox（compose 里的 `environment`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `SANDBOX_MAX_CONCURRENT` | 4 | 同时执行的脚本数（每个占一个独立 uid） |
| `SANDBOX_MEMORY_MB` | 2048 | 单个脚本内存上限 |
| `SANDBOX_MAX_TIMEOUT` | 300 | 单个脚本时长上限（秒） |
| `SANDBOX_MAX_REQUEST_MB` / `SANDBOX_MAX_RESULT_MB` | 256 / 128 | 传入文件包、产出文件包的大小上限 |

## 3. 数据与备份

| 数据卷 | 内容 | 是否需要备份 |
|---|---|---|
| `app-data`（`/data`） | SQLite（配置、会话、消息、关联）、加密凭据、Claude / Codex 对话记录（`claude/`、`codex/<会话>`）、HTML 报告 | **需要** |
| `workspaces`（`/workspaces`） | 各会话的查询结果文件、脚本 | 建议（丢失后 AI 追问时会重新查询） |
| `mock-*-data` | 演示数据 | 不需要（可由 `deploy/mock/generate.py` 重新生成） |

另外要单独保管好 `DA_SECRET_KEY`。

## 4. 接入真实数据库的账号要求

| 数据库 | 建议权限 |
|---|---|
| MySQL | 只读：`GRANT SELECT, SHOW VIEW ON 库.* TO 账号` |
| PostgreSQL | 只读：`CONNECT` + 各 schema 的 `USAGE` + `SELECT`；可再 `ALTER ROLE 账号 SET default_transaction_read_only = on` |
| ClickHouse | 只读用户，`SETTINGS readonly = 2`（允许设置执行超时） + `GRANT SELECT` |
| MongoDB | `read` 角色。**参与联邦查询时**，再加一个只针对 `_schema` 集合的写权限（见 `deploy/mock/mongo/init/01-load.sh` 的 `trinoSchemaWriter`），业务集合仍然只读 |

app 容器需要能访问这些数据库；启用联邦查询时，trino 容器也需要能访问。

## 5. 升级

```bash
git pull
docker compose -f deploy/docker-compose.yml [--profile ...] up -d --build
```

- 平台库的表结构变更由启动时的轻量迁移自动处理（新增可空列、旧数据迁移），不需要手工操作
- Trino 的 catalog 在 app 启动后自动重新同步

## 6. 排障

| 现象 | 排查 |
|---|---|
| 对话报「模型渠道鉴权失败 / 404」 | 设置 → 模型渠道 → 测试；检查 Base URL 是否多写了 `/v1` |
| 「Python 沙箱不可用」 | `docker compose ps` 看 sandbox 是否运行；`docker logs` 看报错 |
| 数据源测试连接失败 | 从 app 容器内测试连通性：`docker exec <app 容器> python -c "import socket;socket.create_connection(('主机',端口),5)"` |
| 联邦查询状态显示同步失败 | 设置 → 数据源顶部的提示里有具体错误；`docker logs <trino 容器>` |
| 联邦查询 MongoDB 缺字段 | 账号缺少 `_schema` 集合的写权限，见第 4 节 |
| 页面一直显示「思考中」 | `docker logs <app 容器>`；SSE 经过反向代理时，确认关闭了缓冲、读超时足够长 |
| 磁盘增长 | 调小 `DA_WORKSPACE_RETENTION_DAYS`；删除不需要的会话 |

日志：`docker compose -f deploy/docker-compose.yml logs -f app`。

## 7. 本地开发

见仓库根目录 [README](../README.md#本地开发)。开发依赖（四种 mock 库、沙箱、Trino）用 `deploy/docker-compose.dev.yml` 启动，后端和前端在本机跑。

没有模型 Key 时，`backend/tests/fake_anthropic.py` 提供按剧本应答的假接口，用来跑通整条链路（剧本：多数据源 `MULTITEST`、联邦查询 `FEDTEST`、引导提问 `ASKTEST`、可视化 `VIZTEST`、排版 `LAYOUTTEST`、安全探针 `GUARDTEST`）。
