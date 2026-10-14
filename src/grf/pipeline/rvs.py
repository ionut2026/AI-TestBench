"""Read-only access to Windchill RV&S through the windchill MCP server (stdio)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Sequence
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from grf import project


class RvsError(RuntimeError):
    """An unavailable integration or invalid response from the external read-only server."""


def _default_server_js() -> Path:
    """Explicit external entry point; never infer a bundled server or read a user's MCP registration."""
    env = os.environ.get("WINDCHILL_MCP_SERVER")
    if env:
        return Path(env)
    value = project.current().raw_paths("rvs_server")
    if not value:
        raise RvsError("set WINDCHILL_MCP_SERVER or [paths] rvs_server to the external server's JavaScript entry point")
    return project.current().path("rvs_server").resolve()


def _server_env() -> dict[str, str]:
    """Only explicitly supplied process settings are passed to the external server."""
    return dict(os.environ)


class RvsClient:
    """Async context manager around one MCP stdio session."""

    def __init__(self, server_js: Path | None = None):
        self.server_js = (Path(server_js) if server_js else _default_server_js()).resolve()
        self._stack: AsyncExitStack | None = None
        self.session: Any = None

    async def __aenter__(self) -> RvsClient:
        if not self.server_js.is_file():
            raise FileNotFoundError(f"windchill MCP server not found: {self.server_js} (set WINDCHILL_MCP_SERVER)")
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as err:
            raise RvsError('Windchill ingestion needs the MCP SDK: install "general-robot-framework[windchill]"') from err
        node = shutil.which("node")
        if node is None:
            raise RvsError("Windchill ingestion needs Node.js on PATH")
        params = StdioServerParameters(command=node, args=[str(self.server_js)], env=_server_env(), cwd=str(self.server_js.parent.parent))
        async with AsyncExitStack() as stack:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self.session = session
            self._stack = stack.pop_all()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack:
            await self._stack.aclose()
        self.session = None
        self._stack = None

    async def call(self, tool: str, **args: Any) -> Any:
        if self.session is None:
            raise RuntimeError("RvsClient is not open (use 'async with RvsClient()')")
        result = await self.session.call_tool(tool, {k: v for k, v in args.items() if v is not None}, read_timeout_seconds=300)
        text = "".join(getattr(c, "text", "") for c in (result.content or []))
        if getattr(result, "isError", False) or getattr(result, "is_error", False):
            raise RvsError(f"{tool}: {text}")
        try:
            return json.loads(text)
        except ValueError as err:
            raise RvsError(f"{tool}: expected a JSON response") from err

    async def get_items(self, ids: Sequence[int | str], **opts: Any) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(ids), 50):
            data = await self.call("rvs_get_items", ids=[str(x) for x in ids[i : i + 50]], **opts)
            batch = data.get("items", [data]) if isinstance(data, dict) else None
            if not isinstance(batch, list) or not all(isinstance(item, dict) and "ID" in item for item in batch):
                raise RvsError("rvs_get_items: expected items with ID fields")
            expected = {str(x) for x in ids[i : i + 50]}
            received = {str(item["ID"]) for item in batch}
            if received != expected or len(batch) != len(received):
                raise RvsError(f"rvs_get_items: response IDs do not match request (missing: {sorted(expected - received)}, "
                               f"unexpected: {sorted(received - expected)}; duplicate IDs are not allowed)")
            out.extend(batch)
        return out

    async def search(self, limit: int = 200, **args: Any) -> list[dict]:
        items: list[dict] = []
        seen: set[str] = set()
        offset = 0
        if limit < 1:
            raise ValueError("search limit must be positive")
        while True:
            data = await self.call("rvs_search_items", offset=offset, limit=limit, **args)
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise RvsError("rvs_search_items: expected an items list")
            batch = data["items"]
            if not all(isinstance(item, dict) and "ID" in item for item in batch):
                raise RvsError("rvs_search_items: expected items with ID fields")
            ids = {str(item["ID"]) for item in batch}
            if seen & ids or len(ids) != len(batch):
                raise RvsError("rvs_search_items: duplicate IDs or repeated page")
            if not isinstance(data.get("hasMore", False), bool):
                raise RvsError("rvs_search_items: hasMore must be a boolean")
            if data.get("hasMore") and not batch:
                raise RvsError("rvs_search_items: empty page with hasMore=true")
            items.extend(batch)
            seen |= ids
            if not data.get("hasMore"):
                return items
            offset += len(batch)


def run(coro):
    return asyncio.run(coro)
