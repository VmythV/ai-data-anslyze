"""运行配置：全部来自环境变量（前缀 `DA_`）或 `.env`。"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).parent
#: 内置 Skill 以 Claude Code 插件的形式挂载：在工作区之外，Agent 和沙箱都写不到
SKILLS_PLUGIN_DIR = PACKAGE_DIR / "plugin"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DA_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    #: 前端构建产物目录；设置后由本服务直接托管页面
    static_dir: Path | None = None
    #: 平台自己的数据：SQLite、Claude / Codex 会话记录、报告文件
    data_dir: Path = Path("var")
    #: 每个会话一个工作区（查询结果、脚本）；运行 Python 时按次打包传给沙箱
    workspaces_dir: Path | None = None
    #: 加密数据源密码 / API Key、签发登录凭证用。留空时在 data_dir 下生成并保存一份
    secret_key: str = ""
    #: 全站访问口令；留空表示不需要登录（仅限本地开发）
    access_password: str = ""
    sandbox_url: str = "http://127.0.0.1:8901"
    #: Codex 子进程回调本服务（取平台工具）用的地址；留空为 http://127.0.0.1:<port>
    internal_url: str = ""
    #: 联邦查询引擎（Trino）地址；留空表示不启用联邦查询
    trino_url: str = ""
    #: Trino 访问数据源时的主机名替换，如 "127.0.0.1=host.docker.internal,localhost=host.docker.internal"
    trino_host_aliases: str = ""
    max_concurrent_runs: int = Field(default=4, ge=1)
    agent_max_turns: int = Field(default=40, ge=1)
    sql_max_rows: int = Field(default=50_000, ge=1)
    sql_timeout_seconds: int = Field(default=30, ge=1)
    #: 联邦查询要跨网络拉数据，给得宽一些
    federated_timeout_seconds: int = Field(default=120, ge=1)
    python_timeout_seconds: int = Field(default=120, ge=1)
    #: 会话多少天没有新消息就清掉它的工作区（查询结果、脚本）；报告和对话记录保留。0 = 不清理
    workspace_retention_days: int = Field(default=30, ge=0)
    #: 传给前端 / CORS；开发时 Vite 走代理，一般不用配
    cors_origins: list[str] = []

    @property
    def workspaces(self) -> Path:
        return (self.workspaces_dir or self.data_dir / "workspaces").resolve()

    @property
    def claude_config_dir(self) -> Path:
        return (self.data_dir / "claude").resolve()

    @property
    def codex_homes_dir(self) -> Path:
        """每个会话一个 CODEX_HOME：会话记录、状态库都在里面，删会话时整个删掉。"""
        return (self.data_dir / "codex").resolve()

    @property
    def internal_base_url(self) -> str:
        return (self.internal_url or f"http://127.0.0.1:{self.port}").rstrip("/")

    @property
    def artifacts_dir(self) -> Path:
        return (self.data_dir / "artifacts").resolve()

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{(self.data_dir / 'app.db').resolve()}"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.workspaces, self.claude_config_dir, self.codex_homes_dir, self.artifacts_dir):
            path.mkdir(parents=True, exist_ok=True)

    def resolved_secret(self) -> str:
        if self.secret_key:
            return self.secret_key
        key_file = self.data_dir / "secret.key"
        if not key_file.exists():
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
            key_file.chmod(0o600)
        return key_file.read_text(encoding="utf-8").strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
