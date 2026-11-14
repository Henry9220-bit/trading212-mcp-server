"""The read-only boundary: GET plus a path allowlist, enforced in the client.

Tool annotations are only hints. These tests pin the enforced behaviour: no
write method and no non-allowlisted path can reach the network, and the source
tree has no way to express one.
"""

import ast
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from hishel import Headers, Request
from mcp import Client

from trading212_mcp.client import ALLOWED_PATHS, Trading212Client
from trading212_mcp.errors import RequestError
from trading212_mcp.registration import annotations
from trading212_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted((ROOT / "src").rglob("*.py"))
BASELINE = json.loads((ROOT / "tests/fixtures/mcp_v1.json").read_text())
WRITE_METHODS = ["POST", "PUT", "PATCH", "DELETE"]
WRITE_PREFIXES = (
    "place_",
    "cancel_",
    "create_",
    "update_",
    "delete_",
    "duplicate_",
    "request_",
)
ALLOWED_EXAMPLES = [
    "/equity/account/summary",
    "/equity/positions",
    "/equity/positions?ticker=AAPL_US_EQ",
    "/equity/orders",
    "/equity/orders/1",
    "/equity/orders/9223372036854775807",
    "/equity/history/orders",
    "/equity/history/orders?cursor=123&limit=50",
    "/api/v0/equity/history/orders?cursor=123&limit=50",
    "/equity/history/dividends",
    "/equity/history/transactions",
    "/equity/history/exports",
    "/equity/metadata/instruments",
    "/equity/metadata/exchanges",
]
REJECTED_PATHS = [
    "/equity/orders/market",
    "/equity/orders/limit",
    "/equity/orders/stop",
    "/equity/orders/stop_limit",
    "/equity/orders/abc",
    "/equity/orders/0",
    "/equity/orders/-1",
    "/equity/orders/12/",
    "/equity/orders/12/cancel",
    "/equity/orders/%31%32",
    "/equity/orders/12345678901234567890",
    "/equity/pies",
    "/equity/pies/1",
    "/equity/pies/1/duplicate",
    "/equity/history/exports/123",
    "/equity/history",
    "/equity/account",
    "/equity/account/summary/",
    "/equity/account/summary/extra",
    "/equity/positions/AAPL_US_EQ",
    "/equity/metadata",
    "/equity/unknown",
]


@pytest.fixture
def recording_client(settings):
    calls = []

    def respond(req):
        calls.append(req)
        return httpx.Response(200, json={})

    with Trading212Client(settings, transport=httpx.MockTransport(respond)) as c:
        yield c, calls


@pytest.mark.parametrize("method", [*WRITE_METHODS, "get", "HEAD", "OPTIONS", ""])
@pytest.mark.parametrize("path", ["/equity/orders", "/equity/orders/market"])
def test_only_get_is_allowed(recording_client, method, path):
    client, calls = recording_client
    with pytest.raises(RequestError, match="Read-only server: only GET is allowed."):
        client._make_request(method, path)
    assert calls == []


@pytest.mark.parametrize("path", REJECTED_PATHS)
def test_paths_off_the_allowlist_are_rejected(recording_client, path):
    client, calls = recording_client
    with pytest.raises(RequestError):
        client._make_request("GET", path)
    assert calls == []


@pytest.mark.parametrize("path", ALLOWED_EXAMPLES)
def test_allowlisted_get_is_sent(recording_client, path):
    client, calls = recording_client
    client._make_request("GET", path)
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.host == "demo.trading212.com"
    assert calls[0].url.path.startswith("/api/v0/equity/")
    assert calls[0].content == b""


def test_allowlist_is_exactly_the_reviewed_set():
    assert ALLOWED_PATHS == (
        r"/equity/account/summary",
        r"/equity/positions",
        r"/equity/orders",
        r"/equity/orders/[1-9][0-9]{0,18}",
        r"/equity/history/orders",
        r"/equity/history/dividends",
        r"/equity/history/transactions",
        r"/equity/history/exports",
        r"/equity/metadata/instruments",
        r"/equity/metadata/exchanges",
    )


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_send_refuses_writes_even_if_called_directly(recording_client, method):
    client, calls = recording_client
    request = Request(
        method=method,
        url=client.base_url + "/equity/orders",
        headers=Headers({}),
    )
    with pytest.raises(RequestError, match="only GET is allowed"):
        client._send(request)
    assert calls == []


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_http_client_hook_refuses_writes(recording_client, method):
    """Even bypassing the client methods, the HTTPX client cannot send a write."""
    client, calls = recording_client
    with pytest.raises(RequestError, match="only GET is allowed"):
        client.http.request(method, client.base_url + "/equity/orders")
    with pytest.raises(RequestError, match="only GET is allowed"):
        with client.http.stream(method, client.base_url + "/equity/orders"):
            pass
    assert calls == []


def test_annotations_have_no_write_variant():
    hints = annotations()
    assert hints.read_only_hint is True
    assert hints.destructive_hint is False
    assert hints.idempotent_hint is True
    with pytest.raises(TypeError):
        annotations(write=True)


def test_every_listed_tool_is_read_only(settings):
    def factory(settings):
        return Trading212Client(
            settings,
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})),
        )

    async def run():
        async with Client(create_server(settings, factory)) as session:
            return (await session.list_tools()).tools

    tools = asyncio.run(run())
    names = {tool.name for tool in tools}
    assert names == {tool["name"] for tool in BASELINE["tools"]}
    assert "fetch_open_lots" in names
    for tool in tools:
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name
        assert not tool.name.startswith(WRITE_PREFIXES), tool.name
    for entry in (*BASELINE["resources"], *BASELINE["templates"]):
        assert "pies" not in json.dumps(entry)


def string_literals(tree):
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_source_has_no_write_method_literals():
    assert len(SOURCES) > 10
    for source in SOURCES:
        tree = ast.parse(source.read_text(), filename=str(source))
        for value in string_literals(tree):
            assert value.strip().upper() not in WRITE_METHODS, (source, value)


def test_source_has_one_http_call_site_and_it_is_get():
    """Only ``Trading212Client._send`` may call the HTTP client, with "GET"."""
    forbidden = {"post", "put", "patch", "delete", "stream", "send", "build_request"}
    sites = []
    for source in SOURCES:
        tree = ast.parse(source.read_text(), filename=str(source))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                target = node.func
                if not isinstance(target, ast.Attribute):
                    continue
                assert target.attr not in forbidden, (source, target.attr)
                receiver = target.value
                if isinstance(receiver, ast.Attribute) and receiver.attr == "http":
                    if target.attr in ("close", "cookies"):
                        continue
                    sites.append((source.name, function.name, target.attr, node))
    assert [(site[0], site[1], site[2]) for site in sites] == [
        ("client.py", "_send", "request")
    ]
    method = sites[0][3].args[0]
    assert isinstance(method, ast.Constant) and method.value == "GET"
    assert not any(
        keyword.arg in ("json", "data", "content", "files")
        for keyword in sites[0][3].keywords
    )


def test_client_exposes_no_generic_request_method():
    public = [name for name in vars(Trading212Client) if not name.startswith("_")]
    assert public
    for name in public:
        assert name == "close" or name.startswith(("get_", "search_")), name
