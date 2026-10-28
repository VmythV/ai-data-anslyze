import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from data_analyze.config import Settings
from data_analyze.engine.base import ChannelConfig, TurnRequest
from data_analyze.engine.codex import API_KEY_ENV, MCP_TOKEN_ENV, CodexAgentEngine
from data_analyze.engine.mcp_gateway import McpGateway
from data_analyze.engine.prompts import build_system_prompt
from data_analyze.tools.base import ToolInputError, ToolOutput, ToolSpec
from data_analyze.tools.file_tools import file_tools


def _files(tmp_path: Path) -> dict[str, ToolSpec]:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "q001.parquet").write_bytes(b"PAR1\0\0binary")
    return {s.name: s for s in file_tools(SimpleNamespace(workspace=tmp_path))}  # type: ignore[arg-type]


@pytest.mark.parametrize(("tool", "args"), [
    ("read_file", {"path": "/etc/hosts"}),
    ("read_file", {"path": "../outside.txt"}),
    ("read_file", {"path": "data/q001.parquet"}),
    ("list_files", {"pattern": "../*"}),
    ("write_file", {"path": "AGENTS.md", "content": "x"}),
    ("write_file", {"path": "output/.codex/config.toml", "content": "x"}),
    ("write_file", {"path": "../escape.txt", "content": "x"}),
    ("load_skill", {"name": "code-review"}),
])
async def test_file_tools_stay_in_workspace(tmp_path: Path, tool: str, args: dict) -> None:
    (tmp_path.parent / "outside.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(ToolInputError):
        await _files(tmp_path)[tool].handler(args)
    assert not (tmp_path / "AGENTS.md").exists() and not (tmp_path.parent / "escape.txt").exists()


async def test_file_tools_roundtrip(tmp_path: Path) -> None:
    tools = _files(tmp_path)
    await tools["write_file"].handler({"path": "output/a.md", "content": "第一行\nsecond"})
    await tools["edit_file"].handler({"path": "output/a.md", "old_string": "second", "new_string": "第二行"})
    assert (await tools["read_file"].handler({"path": "output/a.md"})).text == "     1\t第一行\n     2\t第二行"
    assert "output/a.md:2: 第二行" in (await tools["grep_files"].handler({"pattern": "第二"})).text
    listing = (await tools["list_files"].handler({})).text
    assert "output/a.md" in listing and "data/q001.parquet" in listing
    skill = await tools["load_skill"].handler({"name": "analyst:chart-design"})
    assert "name: chart-design" in skill.text


async def _rpc(client: httpx.AsyncClient, token: str, method: str, params: dict, rid: int = 1) -> httpx.Response:
    return await client.post(
        "/", headers={"authorization": f"Bearer {token}", "accept": "application/json, text/event-stream",
                      "mcp-protocol-version": "2025-06-18"},
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
    )


async def test_gateway_serves_turn_tools_only_with_token() -> None:
    calls: list[dict] = []

    async def echo(args: dict) -> ToolOutput:
        calls.append(args)
        return ToolOutput(f"echo {args['x']}")

    async def broken(_args: dict) -> ToolOutput:
        raise ToolInputError("x 不对")

    gateway = McpGateway()
    tools = [
        ToolSpec("echo", "回显", {"type": "object", "properties": {"x": {"type": "string"}}}, echo),
        ToolSpec("broken", "总是报参数错误", {"type": "object"}, broken),
    ]
    transport = httpx.ASGITransport(app=gateway)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        async with gateway.serve(tools) as token:
            assert (await _rpc(client, "wrong", "tools/list", {})).status_code == 401
            listed = (await _rpc(client, token, "tools/list", {})).json()["result"]["tools"]
            assert [t["name"] for t in listed] == ["echo", "broken"]
            ok = (await _rpc(client, token, "tools/call", {"name": "echo", "arguments": {"x": "hi"}})).json()
            assert ok["result"]["content"][0]["text"] == "echo hi" and not ok["result"].get("isError")
            bad = (await _rpc(client, token, "tools/call", {"name": "broken", "arguments": {}})).json()
            assert bad["result"]["isError"] and "参数错误：x 不对" in bad["result"]["content"][0]["text"]
        # 这一轮结束后令牌失效
        assert (await _rpc(client, token, "tools/list", {})).status_code == 401
    assert calls == [{"x": "hi"}]


def test_codex_config_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DA_SECRET_KEY", "platform-secret")
    settings = Settings(data_dir=tmp_path, port=8123)
    turn = TurnRequest(
        conversation_id="a" * 32, prompt="hi", workspace=tmp_path / "ws", system_prompt="sys", tools=[],
        channel=ChannelConfig(protocol="openai", base_url="https://gw.example/v1", api_key="sk-chan", model="m1"),
    )
    config, options = CodexAgentEngine(settings).build_config(turn, "tok")
    overrides = dict(item.split("=", 1) for item in config.config_overrides)
    assert config.env["DA_SECRET_KEY"] == "" and config.env[API_KEY_ENV] == "sk-chan"
    assert config.env[MCP_TOKEN_ENV] == "tok" and "127.0.0.1" in config.env["NO_PROXY"]
    assert config.env["CODEX_HOME"] == str(tmp_path.resolve() / "codex" / turn.conversation_id)
    # 密钥只经环境变量传，不出现在命令行参数里
    assert not any("sk-chan" in item or "tok" == item.split("=", 1)[1] for item in config.config_overrides)
    assert json.loads(overrides["model_providers.da.base_url"]) == "https://gw.example/v1"
    assert json.loads(overrides["mcp_servers.analysis.url"]) == "http://127.0.0.1:8123/internal/mcp/"
    assert overrides["features.shell_tool"] == "false" and json.loads(overrides["web_search"]) == "disabled"
    assert options["base_instructions"] == "sys" and options["model_provider"] == "da"


def test_prompt_names_engine_tools() -> None:
    claude = build_system_prompt([], engine="claude")
    codex = build_system_prompt([], engine="codex")
    assert "Skill：" in claude and "analyst:chart-design" in claude
    assert "load_skill：" in codex and "read_file" in codex and "analyst:" not in codex
