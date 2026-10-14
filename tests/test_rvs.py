import asyncio
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from grf.pipeline import rvs


def test_external_server_must_be_explicit(widget, monkeypatch):
    monkeypatch.delenv("WINDCHILL_MCP_SERVER", raising=False)
    with pytest.raises(rvs.RvsError, match="external server"):
        rvs.RvsClient()
    widget.data["paths"]["rvs_server"] = "external/server.js"
    assert rvs.RvsClient().server_js == (widget.root / "external/server.js").resolve()
    monkeypatch.setenv("WINDCHILL_MCP_SERVER", str(widget.root / "configured.js"))
    assert rvs.RvsClient().server_js == widget.root / "configured.js"


def test_server_settings_do_not_read_home_config(monkeypatch):
    def forbidden():
        pytest.fail("must not read the user's home configuration")

    monkeypatch.setattr(rvs.Path, "home", forbidden)
    monkeypatch.setenv("RVS_HOSTNAME", "synthetic")
    assert rvs._server_env()["RVS_HOSTNAME"] == "synthetic"


@pytest.mark.parametrize("payload", [[], {"items": "bad"}, {"items": [{}]}, {"items": []},
                                     {"items": [{"ID": "1"}, {"ID": "1"}]}, {"items": [{"ID": "2"}]}])
def test_incomplete_or_invalid_items_fail(payload):
    client = rvs.RvsClient(rvs.Path("unused.js"))

    async def call(*args, **kwargs):
        return payload

    client.call = call
    with pytest.raises(rvs.RvsError):
        asyncio.run(client.get_items([1]))


def test_item_batches_match_requested_ids():
    client = rvs.RvsClient(rvs.Path("unused.js"))
    batches = []

    async def call(tool, **kwargs):
        batches.append(kwargs["ids"])
        return {"items": [{"ID": sid} for sid in kwargs["ids"]]}

    client.call = call
    assert len(asyncio.run(client.get_items(list(range(101))))) == 101
    assert list(map(len, batches)) == [50, 50, 1]


@pytest.mark.parametrize("payload", [
    [], {}, {"items": "bad"}, {"items": [{}]}, {"items": [], "hasMore": True},
    {"items": [{"ID": "1"}], "hasMore": "true"},
])
def test_invalid_search_pages_fail(payload):
    client = rvs.RvsClient(rvs.Path("unused.js"))

    async def call(*args, **kwargs):
        return payload

    client.call = call
    with pytest.raises(rvs.RvsError):
        asyncio.run(client.search())


def test_repeated_search_page_fails_instead_of_looping():
    client = rvs.RvsClient(rvs.Path("unused.js"))

    async def call(*args, **kwargs):
        return {"items": [{"ID": "1"}], "hasMore": True}

    client.call = call
    with pytest.raises(rvs.RvsError, match="repeated page"):
        asyncio.run(client.search())


def test_search_advances_offsets_and_returns_all_items():
    client = rvs.RvsClient(rvs.Path("unused.js"))
    offsets = []

    async def call(*args, **kwargs):
        offsets.append(kwargs["offset"])
        return {"items": [{"ID": str(kwargs["offset"] + 1)}], "hasMore": kwargs["offset"] == 0}

    client.call = call
    assert asyncio.run(client.search(limit=1)) == [{"ID": "1"}, {"ID": "2"}]
    assert offsets == [0, 1]


def test_invalid_json_is_not_success_shaped():
    client = rvs.RvsClient(rvs.Path("unused.js"))

    async def call_tool(*args, **kwargs):
        return SimpleNamespace(content=[SimpleNamespace(text="not JSON")], isError=False)

    client.session = SimpleNamespace(call_tool=call_tool)
    with pytest.raises(rvs.RvsError, match="JSON response"):
        asyncio.run(client.call("rvs_get_items"))


def test_failed_initialization_closes_transports(tmp_path, monkeypatch):
    server = tmp_path / "server.js"
    server.touch()
    closed = []

    @asynccontextmanager
    async def stdio(params):
        try:
            yield ("read", "write")
        finally:
            closed.append("stdio")

    class Session:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append("session")

        async def initialize(self):
            raise RuntimeError("synthetic initialization failure")

    monkeypatch.setitem(sys.modules, "mcp", SimpleNamespace(
        ClientSession=Session, StdioServerParameters=lambda **kwargs: kwargs,
    ))
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", SimpleNamespace(stdio_client=stdio))
    monkeypatch.setattr(rvs.shutil, "which", lambda _: "node")
    client = rvs.RvsClient(server)
    with pytest.raises(RuntimeError, match="initialization"):
        asyncio.run(client.__aenter__())
    assert closed == ["session", "stdio"]
    assert client.session is None and client._stack is None
