import base64
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import httpx
import pytest

from trading212_mcp.cache import observations
from trading212_mcp.client import Trading212Client
from trading212_mcp.errors import RequestError, UpstreamError
from trading212_mcp.settings import Settings

PATH = "/equity/orders"
WRITE_METHODS = ["POST", "PUT", "PATCH", "DELETE"]


def generation(c, bump=False):
    """Read the private-cache generation, as another process would see it."""
    with closing(sqlite3.connect(c.cache.path / "state.sqlite")) as state, state:
        if bump:
            state.execute("UPDATE generation SET value=value+1 WHERE id=1")
        return state.execute("SELECT value FROM generation WHERE id=1").fetchone()[0]


def client(settings, calls):
    def respond(req):
        calls.append(req)
        return httpx.Response(
            200,
            json={"count": len(calls)},
            headers={"set-cookie": "session=synthetic-cookie"},
        )

    return Trading212Client(settings, transport=httpx.MockTransport(respond))


@pytest.mark.parametrize(
    "keys",
    [
        [("key-a", "secret-a"), ("key-b", "secret-b")],
        [("key-a", "secret-a"), ("key-a", "secret-b")],
        [("key-a", None), ("key-b", None)],
        [("key-a", None), ("key-a", "secret-a")],
    ],
)
def test_credentials_never_share_responses(settings, keys):
    calls = []
    for key, secret in keys:
        cfg = Settings.model_validate(
            {**settings.model_dump(), "api_key": key, "api_secret": secret}
        )
        with client(cfg, calls) as c:
            first = c._make_request("GET", PATH)
            assert first == c._make_request("GET", PATH)
    assert len(calls) == 2


def test_restart_reuses_cache_and_close_is_independent(settings):
    calls = []
    first = client(settings, calls)
    second = client(settings, calls)
    original = first._make_request("GET", PATH)
    first.close()
    assert second._make_request("GET", PATH) == original
    second.close()
    with client(settings, calls) as restarted:
        assert restarted._make_request("GET", PATH) == original
    assert len(calls) == 1


def test_private_files_never_contain_credentials(settings):
    calls = []
    with client(settings, calls) as c:
        c._make_request("GET", PATH)
        path = c.cache.path
    assert (
        calls[0].headers["Authorization"]
        == "Basic " + base64.b64encode(b"synthetic-key:synthetic-secret").decode()
    )
    for file in path.rglob("*"):
        if file.is_file():
            data = file.read_bytes()
            for secret in (
                b"synthetic-key",
                b"synthetic-secret",
                b"Authorization",
                b"synthetic-cookie",
                calls[0].headers["Authorization"].encode(),
            ):
                assert secret not in data
            if os.name == "posix" and file.name != ".gitignore":
                assert stat.S_IMODE(file.stat().st_mode) == 0o600
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_environment_and_version_isolation(settings):
    calls = []
    for env, version in [("demo", "v0"), ("live", "v0"), ("demo", "v1")]:
        cfg = settings.model_copy(update={"environment": env})
        with Trading212Client(
            cfg,
            version=version,
            transport=httpx.MockTransport(
                lambda req: calls.append(req) or httpx.Response(200, json={})
            ),
        ) as c:
            c._make_request("GET", PATH)
    assert len(calls) == 3
    assert len({str(req.url) for req in calls}) == 3


def test_expiration_and_no_sliding_ttl(settings):
    calls = []
    with client(settings, calls) as c:
        c._make_request("GET", PATH)
        # Advance Hishel's clock, without sleeps or depending on its SQL schema.
        import time
        from unittest.mock import patch

        with patch(
            "hishel._core._storages._sync_sqlite.time.time",
            return_value=time.time() + 16,
        ):
            c._make_request("GET", PATH)
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("ttl_setting", "path"),
    [
        ("account_ttl", PATH),
        ("history_ttl", "/equity/history/orders"),
        ("metadata_ttl", "/equity/metadata/instruments"),
    ],
)
@pytest.mark.parametrize("restart", [False, True])
def test_reduced_ttl_applies_to_persisted_entries(
    settings, monkeypatch, ttl_setting, path, restart
):
    import time

    calls = []
    long_lived = settings.model_copy(update={ttl_setting: 3600})
    short_lived = settings.model_copy(update={ttl_setting: 15})
    with client(long_lived, calls) as original:
        assert original._make_request("GET", path) == {"count": 1}
        if restart:
            original.close()
        future = time.time() + 60
        monkeypatch.setattr("trading212_mcp.cache.time.time", lambda: future)
        events = []
        token = observations.set(events)
        try:
            with client(short_lived, calls) as current:
                assert current._make_request("GET", path) == {"count": 2}
        finally:
            observations.reset(token)
        assert events[0]["cacheHit"] is False
        assert events[0]["ttlSeconds"] == 15
    assert len(calls) == 2


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_rejected_writes_leave_cache_and_generation_untouched(settings, method):
    calls = []
    with client(settings, calls) as c:
        cached = c._make_request("GET", PATH)
        metadata = c._make_request("GET", "/equity/metadata/instruments")
        before = generation(c)
        with pytest.raises(RequestError, match="only GET is allowed"):
            c._make_request(method, PATH)
        assert generation(c) == before
        # Still served from the cache: the refused write invalidated nothing.
        assert c._make_request("GET", PATH) == cached
        assert c._make_request("GET", "/equity/metadata/instruments") == metadata
    assert [req.method for req in calls] == ["GET", "GET"]


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_write_is_refused_before_a_failing_network(settings, method):
    calls = []

    def respond(req):
        calls.append(req)
        raise httpx.ReadTimeout("SYNTHETIC SENSITIVE DETAIL")

    with Trading212Client(
        settings, transport=httpx.MockTransport(respond), sleep=lambda _: None
    ) as c:
        # A write is a request error, never an "outcome unknown" upstream error.
        with pytest.raises(RequestError) as refused:
            c._make_request(method, PATH)
        assert calls == []
        with pytest.raises(UpstreamError) as failed:
            c._make_request("GET", PATH)
    assert [req.method for req in calls] == ["GET"] * 3
    for error in (refused, failed):
        assert "SYNTHETIC SENSITIVE DETAIL" not in str(error.value)


def test_external_generation_bump_invalidates_private_cache(settings):
    """A write-capable process sharing the directory can still invalidate reads."""
    calls = []
    with client(settings, calls) as c:
        assert c._make_request("GET", PATH) == {"count": 1}
        metadata = c._make_request("GET", "/equity/metadata/instruments")
        before = generation(c)
        assert generation(c, bump=True) == before + 1
        assert c._make_request("GET", PATH) == {"count": 3}
        assert c._make_request("GET", "/equity/metadata/instruments") == metadata
    assert len(calls) == 3


def test_same_account_concurrent_clients_share_cache(settings):
    calls = []
    with ThreadPoolExecutor(8) as pool:

        def fetch(_):
            with client(settings, calls) as c:
                return c._make_request("GET", PATH)

        results = list(pool.map(fetch, range(8)))
    assert results == [{"count": 1}] * 8
    assert len(calls) == 1


def test_freshness_metadata(settings):
    calls = []
    events = []
    token = observations.set(events)
    try:
        with client(settings, calls) as c:
            c._make_request("GET", PATH)
            c._make_request("GET", PATH)
        assert [e["cacheHit"] for e in events] == [False, True]
        assert events[0]["retrievedAt"] == events[1]["retrievedAt"]
        assert events[0]["ttlSeconds"] == 15
    finally:
        observations.reset(token)


def test_zero_ttl_disables_cache(settings):
    calls = []
    with client(settings.model_copy(update={"account_ttl": 0}), calls) as c:
        c._make_request("GET", PATH)
        c._make_request("GET", PATH)
    assert len(calls) == 2


def test_legacy_cache_is_ignored(settings, tmp_path):
    legacy = tmp_path / ".cache/trading212-v2/legacy"
    legacy.mkdir(parents=True)
    (legacy / "data").write_text("synthetic-private-data")
    calls = []
    with client(settings, calls) as c:
        c._make_request("GET", PATH)
    assert len(calls) == 1 and (legacy / "data").exists()


def process_read(cache_dir, counter, barrier, results):
    """Spawn-safe worker; no inherited clients or database connections."""
    cfg = Settings(api_key="synthetic-process", cache_dir=cache_dir)

    def respond(req):
        with counter.get_lock():
            counter.value += 1
            value = counter.value
        return httpx.Response(200, json={"call": value})

    with Trading212Client(cfg, transport=httpx.MockTransport(respond)) as c:
        barrier.wait(timeout=10)
        results.put(c._make_request("GET", PATH))


def test_cross_process_cache_coordination(tmp_path):
    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    counter, barrier, results = ctx.Value("i", 0), ctx.Barrier(2), ctx.Queue()
    workers = [
        ctx.Process(target=process_read, args=(tmp_path, counter, barrier, results))
        for _ in range(2)
    ]
    try:
        for worker in workers:
            worker.start()
        assert [results.get(timeout=15) for _ in workers] == [{"call": 1}] * 2
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
        assert counter.value == 1
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()
        results.close()
        results.join_thread()


def test_connection_failure_never_uses_other_account(settings):
    calls = []
    with client(settings, calls) as first:
        first._make_request("GET", PATH)
    cfg = Settings.model_validate(
        {**settings.model_dump(), "api_key": "another-synthetic-key"}
    )

    def fail(req):
        raise httpx.ConnectError("synthetic-network-failure")

    with Trading212Client(
        cfg, transport=httpx.MockTransport(fail), sleep=lambda _: None
    ) as other:
        with pytest.raises(UpstreamError):
            other._make_request("GET", PATH)
