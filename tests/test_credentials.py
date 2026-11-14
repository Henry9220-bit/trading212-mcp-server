"""Credentials never appear in errors, logs, stdout/stderr or MCP results."""

import asyncio
import base64
import logging
import subprocess
import sys

import httpx
import pytest
from mcp import Client
from pydantic import ValidationError

from trading212_mcp.client import Trading212Client
from trading212_mcp.errors import (
    AuthenticationError,
    InvalidResponseError,
    RateLimitError,
)
from trading212_mcp.server import create_server, main
from trading212_mcp.settings import Settings

FAKE_KEY = "TEST-KEY-123"
FAKE_SECRET = "TEST-SECRET-456"  # noqa: S105 - intentionally synthetic
ENCODED = base64.b64encode(f"{FAKE_KEY}:{FAKE_SECRET}".encode()).decode()
SECRETS = (FAKE_KEY, FAKE_SECRET, ENCODED)

RESPONSES = {
    "unauthorized": (httpx.Response(401, text="bad key"), AuthenticationError),
    "rate-limited": (httpx.Response(429, text="slow down"), RateLimitError),
    "wrong-shape": (httpx.Response(200, json={"not": "a list"}), InvalidResponseError),
}


@pytest.fixture
def fake_settings(tmp_path):
    return Settings(api_key=FAKE_KEY, api_secret=FAKE_SECRET, cache_dir=tmp_path / "c")


def assert_clean(*texts):
    for text in texts:
        for secret in SECRETS:
            assert secret not in text


def echoing(response):
    """An upstream that reflects the Authorization header back in its body."""

    def respond(req):
        assert req.headers["Authorization"] == "Basic " + ENCODED
        if response.status_code == 200:
            return httpx.Response(200, json={"echo": req.headers["Authorization"]})
        return httpx.Response(
            response.status_code,
            text=f"{response.text} {req.headers['Authorization']} {FAKE_KEY}",
            headers={"retry-after": "0"},
        )

    return httpx.MockTransport(respond)


@pytest.mark.parametrize("case", RESPONSES)
def test_client_errors_do_not_leak_credentials(fake_settings, case, caplog, capsys):
    response, error = RESPONSES[case]
    caplog.set_level(logging.DEBUG)
    with Trading212Client(
        fake_settings, transport=echoing(response), sleep=lambda _: None
    ) as client:
        with pytest.raises(error) as raised:
            client.get_orders()
        assert_clean(repr(client.__dict__), repr(fake_settings))
    captured = capsys.readouterr()
    exc = raised.value
    assert_clean(
        str(exc),
        repr(exc),
        repr(exc.__cause__),
        repr(exc.__context__),
        caplog.text,
        captured.out,
        captured.err,
    )


@pytest.mark.parametrize("case", RESPONSES)
def test_mcp_results_do_not_leak_credentials(fake_settings, case, caplog, capsys):
    response, _ = RESPONSES[case]
    caplog.set_level(logging.DEBUG)

    def factory(settings):
        return Trading212Client(
            settings, transport=echoing(response), sleep=lambda _: None
        )

    async def run():
        async with Client(create_server(fake_settings, factory)) as session:
            tool = await session.call_tool("fetch_all_orders", {})
            lots = await session.call_tool("fetch_open_lots", {})
            prompt = await session.get_prompt("analyse_trading212_data", {})
            try:
                resource = await session.read_resource("trading212://orders")
            except Exception as exc:  # noqa: BLE001 - only its text matters here
                resource = exc
            return tool, lots, prompt, resource

    tool, lots, prompt, resource = asyncio.run(run())
    assert tool.is_error and lots.is_error
    captured = capsys.readouterr()
    assert_clean(
        str(tool),
        str(lots),
        str(prompt),
        str(resource),
        repr(resource),
        caplog.text,
        captured.out,
        captured.err,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "evil"},
        {"transport": "sse"},
        {"account_ttl": -1},
        {"api_secret": FAKE_SECRET, "metadata_ttl": "not-a-number"},
    ],
)
def test_settings_validation_errors_hide_inputs(overrides):
    with pytest.raises(ValidationError) as raised:
        Settings.model_validate({"api_key": FAKE_KEY, **overrides})
    assert_clean(str(raised.value), repr(raised.value))
    for value in overrides.values():
        if isinstance(value, str) and value != FAKE_SECRET:
            assert value not in str(raised.value)


def test_startup_error_names_variables_but_never_values(monkeypatch, capsys):
    monkeypatch.setenv("TRADING212_API_KEY", FAKE_KEY)
    monkeypatch.setenv("TRADING212_API_SECRET", FAKE_SECRET)
    monkeypatch.setenv("ENVIRONMENT", "private-environment-value")
    monkeypatch.setenv("TRADING212_HISTORY_TTL", "private-ttl-value")
    with pytest.raises(SystemExit) as raised:
        main()
    message = str(raised.value)
    assert "ENVIRONMENT" in message and "TRADING212_HISTORY_TTL" in message
    captured = capsys.readouterr()
    for text in (message, captured.out, captured.err):
        assert_clean(text)
        assert "private-environment-value" not in text
        assert "private-ttl-value" not in text


def test_server_without_credentials_exits_with_a_clear_error(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "trading212_mcp"],
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "UNRELATED_PRIVATE": "must-not-be-printed"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "TRADING212_API_KEY" in result.stderr
    assert "Traceback" not in result.stderr
    assert "must-not-be-printed" not in result.stdout + result.stderr
    assert result.stdout == ""
    assert not (tmp_path / ".cache").exists()
