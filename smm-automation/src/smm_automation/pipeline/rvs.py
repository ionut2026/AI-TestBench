"""Read-only access to Windchill RV&S through the windchill MCP server (stdio)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from smm_automation import FRAMEWORK_ROOT


def _default_server_js() -> Path:
    env = os.environ.get("WINDCHILL_MCP_SERVER")
    if env:
        return Path(env)
    return FRAMEWORK_ROOT.parent / "windchill-mcp-server" / "src" / "server.js"


def _server_env() -> dict[str, str]:
    """Connection settings for the MCP server: RVS_* from the environment, else from ~/.copilot/mcp-config.json."""
    env = {k: v for k, v in os.environ.items()}
    if "RVS_HOSTNAME" not in env:
        cfg = Path.home() / ".copilot" / "mcp-config.json"
        try:
            servers = json.loads(cfg.read_text(encoding="utf-8")).get("mcpServers", {})
            for name, server in servers.items():
                if "windchill" in name.lower():
                    env.update({k: str(v) for k, v in (server.get("env") or {}).items()})
                    break
        except (OSError, ValueError):
            pass
    return env


class RvsClient:
    """Async context manager around one MCP stdio session."""

    def __init__(self, server_js: Path | None = None):
        self.server_js = Path(server_js) if server_js else _default_server_js()
        self._stack = None
        self.session = None

    async def __aenter__(self) -> "RvsClient":
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if not self.server_js.exists():
            raise FileNotFoundError(f"windchill MCP server not found: {self.server_js} (set WINDCHILL_MCP_SERVER)")
        node = shutil.which("node") or "node"
        params = StdioServerParameters(command=node, args=[str(self.server_js)], env=_server_env(), cwd=str(self.server_js.parent.parent))
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack:
            await self._stack.aclose()

    async def call(self, tool: str, **args: Any) -> Any:
        result = await self.session.call_tool(tool, {k: v for k, v in args.items() if v is not None}, read_timeout_seconds=300)
        text = "".join(getattr(c, "text", "") for c in (result.content or []))
        if getattr(result, "isError", False) or getattr(result, "is_error", False):
            raise RuntimeError(f"{tool}: {text}")
        try:
            return json.loads(text)
        except ValueError:
            return text

    async def get_items(self, ids: list[int | str], **opts: Any) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(ids), 50):
            data = await self.call("rvs_get_items", ids=[str(x) for x in ids[i : i + 50]], **opts)
            out.extend(data.get("items", [data]) if isinstance(data, dict) else [])
        return out

    async def search(self, limit: int = 200, **args: Any) -> list[dict]:
        items: list[dict] = []
        offset = 0
        while True:
            data = await self.call("rvs_search_items", offset=offset, limit=limit, **args)
            batch = data.get("items", [])
            items.extend(batch)
            if not data.get("hasMore") or not batch:
                return items
            offset += len(batch)


def run(coro):
    return asyncio.run(coro)
