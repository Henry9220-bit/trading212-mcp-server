"""Endpoint contracts: every client method is a GET to an allowlisted path."""

import httpx
import pytest

from trading212_mcp import models
from trading212_mcp.client import Trading212Client
from trading212_mcp.errors import (
    AuthenticationError,
    InvalidResponseError,
    NotFoundError,
    RateLimitError,
    RequestError,
    UpstreamError,
)
from trading212_mcp.models import HistoryTransactionItem, Order

CASES = [
    ("get_account_summary", (), "GET", "/equity/account/summary", {"currency": "GBP"}),
    (
        "get_account_cash",
        (),
        "GET",
        "/equity/account/summary",
        {"cash": {"availableToTrade": 12}},
    ),
    ("get_account_positions", (), "GET", "/equity/positions", []),
    ("get_positions", ("AAPL_US_EQ",), "GET", "/equity/positions", []),
    (
        "get_position_by_ticker",
        ("AAPL_US_EQ",),
        "GET",
        "/equity/positions",
        [{"quantity": 2}],
    ),
    (
        "get_account_position_by_ticker",
        ("AAPL_US_EQ",),
        "GET",
        "/equity/positions",
        [{"quantity": 2}],
    ),
    (
        "search_position_by_ticker",
        ("AAPL_US_EQ",),
        "GET",
        "/equity/positions",
        [{"quantity": 2}],
    ),
    (
        "get_dividends",
        (100, "AAPL_US_EQ", 500),
        "GET",
        "/equity/history/dividends",
        {"items": [], "nextPagePath": "/api/v0/equity/history/dividends?cursor=200"},
    ),
    ("get_orders", (), "GET", "/equity/orders", []),
    ("get_order_by_id", (12,), "GET", "/equity/orders/12", {"id": 12}),
    (
        "get_historical_order_data",
        (100, "AAPL_US_EQ", 50),
        "GET",
        "/equity/history/orders",
        {"items": []},
    ),
    (
        "get_history_transactions",
        ("abc", "2025-01-01T00:00:00Z", 20),
        "GET",
        "/equity/history/transactions",
        {"items": []},
    ),
    (
        "get_instruments",
        (),
        "GET",
        "/equity/metadata/instruments",
        [{"ticker": "AAPL_US_EQ", "name": "Apple"}],
    ),
    (
        "get_exchanges",
        (),
        "GET",
        "/equity/metadata/exchanges",
        [{"id": 1, "name": "Test", "workingSchedules": []}],
    ),
    ("get_reports", (), "GET", "/equity/history/exports", []),
]

# Every write the upstream server could issue, as (verb, path).
FORMER_WRITES = [
    ("POST", "/equity/orders/market"),
    ("POST", "/equity/orders/limit"),
    ("POST", "/equity/orders/stop"),
    ("POST", "/equity/orders/stop_limit"),
    ("DELETE", "/equity/orders/12"),
    ("POST", "/equity/pies"),
    ("POST", "/equity/pies/12"),
    ("POST", "/equity/pies/12/duplicate"),
    ("DELETE", "/equity/pies/12"),
    ("POST", "/equity/history/exports"),
]
REMOVED_CLIENT_METHODS = [
    "place_market_order",
    "place_limit_order",
    "place_stop_order",
    "place_stop_limit_order",
    "cancel_order",
    "request_export",
    "get_pies",
    "get_pie_by_id",
    "create_pie",
    "update_pie",
    "duplicate_pie",
    "delete_pie",
]
REMOVED_MODELS = [
    "MarketRequest",
    "LimitRequest",
    "StopRequest",
    "StopLimitRequest",
    "PlaceOrderError",
    "PieRequest",
    "DuplicateBucketRequest",
    "PublicReportRequest",
    "EnqueuedReportResponse",
    "AccountBucketResultResponse",
    "AccountBucketInstrumentsDetailedResponse",
]


@pytest.mark.parametrize("method,args,verb,path,response", CASES)
def test_endpoint(settings, method, args, verb, path, response):
    calls = []

    def respond(req):
        calls.append(req)
        return (
            httpx.Response(204)
            if response is None
            else httpx.Response(200, json=response)
        )

    with Trading212Client(settings, transport=httpx.MockTransport(respond)) as c:
        result = getattr(c, method)(*args)
    req = calls[0]
    assert verb == "GET"
    assert req.method == verb and req.url.path == "/api/v0" + path
    assert len(calls) == 1
    assert req.content == b""
    if method in (
        "get_positions",
        "get_position_by_ticker",
        "get_account_position_by_ticker",
        "search_position_by_ticker",
    ):
        assert req.url.params["ticker"] == "AAPL_US_EQ"
    if method == "get_dividends":
        assert req.url.params == httpx.QueryParams(
            cursor=100, ticker="AAPL_US_EQ", limit=50
        )
        assert result.nextPagePath.endswith("cursor=200")


@pytest.mark.parametrize("verb,path", FORMER_WRITES)
def test_former_write_endpoints_are_rejected(settings, verb, path):
    calls = []
    with Trading212Client(
        settings,
        transport=httpx.MockTransport(
            lambda req: calls.append(req) or httpx.Response(200, json={})
        ),
    ) as c:
        with pytest.raises(RequestError, match="Read-only server"):
            c._make_request(verb, path)
    assert calls == []


@pytest.mark.parametrize("name", REMOVED_CLIENT_METHODS)
def test_write_and_pie_methods_do_not_exist(name):
    assert not hasattr(Trading212Client, name)


@pytest.mark.parametrize("name", REMOVED_MODELS)
def test_write_request_models_do_not_exist(name):
    assert not hasattr(models, name)


@pytest.mark.parametrize(
    "status,error",
    [
        (401, AuthenticationError),
        (403, AuthenticationError),
        (429, RateLimitError),
        (500, UpstreamError),
        (302, UpstreamError),
        (400, UpstreamError),
        (404, NotFoundError),
    ],
)
def test_errors_and_retry_limit(settings, status, error):
    calls = []
    sleeps = []

    def respond(req):
        calls.append(req)
        return httpx.Response(
            status,
            headers={"location": "https://evil.invalid/"},
            text="synthetic-account-private-data",
        )

    with Trading212Client(
        settings, transport=httpx.MockTransport(respond), sleep=sleeps.append
    ) as c:
        with pytest.raises(error) as exc:
            c.get_orders()
    assert "synthetic-account-private-data" not in str(exc.value)
    assert len(calls) == (3 if status in (429, 500) else 1)


def test_retry_then_success_and_reset_budget(settings):
    calls = []
    sleeps = []

    def respond(req):
        calls.append(req)
        return httpx.Response(
            503 if len(calls) < 3 else 200, headers={"retry-after": "2"}, json=[]
        )

    with Trading212Client(
        settings, transport=httpx.MockTransport(respond), sleep=sleeps.append
    ) as c:
        assert c.get_orders() == []
    assert sleeps == [2, 2]
    with Trading212Client(
        settings.model_copy(update={"account_ttl": 0}),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(429, headers={"retry-after": "60"})
        ),
        sleep=sleeps.append,
    ) as c:
        with pytest.raises(RateLimitError):
            c.get_orders()
    assert sleeps == [2, 2]


def test_invalid_json_and_missing_position(settings):
    with Trading212Client(
        settings,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text="not-json")),
    ) as c:
        with pytest.raises(InvalidResponseError):
            c.get_orders()
    with Trading212Client(
        settings,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[])),
    ) as c:
        with pytest.raises(RequestError, match="No open position"):
            c.get_position_by_ticker("AAPL_US_EQ")


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.invalid/",
        "//evil.invalid/",
        "/equity/../secret",
        "/equity/%2e%2e/secret",
        "/api/v1/equity/orders",
        "/equity/a\\b",
        "/equity/orders#fragment",
        "/equity/orders\n",
        "/other",
    ],
)
def test_rejects_unsafe_paths(settings, path):
    with Trading212Client(settings) as c:
        with pytest.raises(RequestError):
            c._make_request("GET", path)


def test_not_found_is_explained_where_the_cause_is_known(settings):
    calls = []

    def respond(req):
        calls.append(req)
        return httpx.Response(404, text="synthetic-account-private-data")

    with Trading212Client(settings, transport=httpx.MockTransport(respond)) as c:
        # A ticker Trading 212 does not know, such as a bare symbol.
        for lookup in (c.get_positions, c.get_position_by_ticker):
            with pytest.raises(
                RequestError, match="does not recognise that ticker"
            ) as e:
                lookup("SYNTHETIC-TICKER")
            assert "SYNTHETIC-TICKER" not in str(e.value)
            assert "synthetic-account-private-data" not in str(e.value)
        with pytest.raises(RequestError, match="No pending order was found") as e:
            c.get_order_by_id(12)
        assert "12" not in str(e.value)
        # Without a ticker there is no better explanation than the status.
        with pytest.raises(NotFoundError, match="HTTP 404"):
            c.get_positions()
        with pytest.raises(NotFoundError, match="HTTP 404"):
            c.get_historical_order_data(ticker="SYNTHETIC-TICKER")
    # A 404 is a definite answer and is never retried.
    assert len(calls) == 5


def test_empty_success_body_is_none(settings):
    with Trading212Client(
        settings, transport=httpx.MockTransport(lambda req: httpx.Response(200))
    ) as c:
        assert c._make_request("GET", "/equity/orders") is None


def test_invalid_api_version_is_rejected(settings):
    with pytest.raises(ValueError, match="Invalid API version"):
        Trading212Client(settings, version="v0/../x")


def test_pagination_path_and_auth(settings):
    calls = []
    with Trading212Client(
        settings,
        transport=httpx.MockTransport(
            lambda req: calls.append(req) or httpx.Response(200, json={})
        ),
    ) as c:
        c._make_request("GET", "/api/v0/equity/history/orders?cursor=123&limit=2")
    assert calls[0].url.params["cursor"] == "123"
    assert calls[0].headers["Authorization"].startswith("Basic ")
    with Trading212Client(
        settings.model_copy(update={"api_secret": None}),
        transport=httpx.MockTransport(
            lambda req: calls.append(req) or httpx.Response(200, json={})
        ),
    ) as c:
        c._make_request("GET", "/equity/orders")
    assert calls[1].headers["Authorization"] == "synthetic-key"


def test_current_upstream_enum_values():
    free_cash = HistoryTransactionItem(type="INTEREST_ON_FREE_CASH")
    lending = HistoryTransactionItem(type="LENDING_INTEREST")
    autoinvest = Order(initiatedFrom="INSTRUMENT_AUTOINVEST")
    assert free_cash.type.value == "INTEREST_ON_FREE_CASH"
    assert lending.type.value == "LENDING_INTEREST"
    assert autoinvest.initiatedFrom.value == "INSTRUMENT_AUTOINVEST"


def test_wrong_response_shape_is_typed_error(settings):
    with Trading212Client(
        settings,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})),
    ) as c:
        with pytest.raises(InvalidResponseError):
            c.get_orders()


def test_tax_fields_survive_history_response(settings):
    data = {
        "items": [
            {
                "fill": {
                    "walletImpact": {
                        "taxes": [
                            {
                                "chargedAt": "2026-10-23T10:00:00Z",
                                "currency": "GBP",
                                "name": "STAMP_DUTY",
                                "quantity": 1.2,
                            }
                        ]
                    }
                }
            }
        ],
        "nextPagePath": None,
    }
    with Trading212Client(
        settings,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data)),
    ) as client:
        tax = client.get_historical_order_data().items[0].fill.walletImpact.taxes[0]
        assert tax.currency == "GBP"
        assert tax.chargedAt.isoformat() == "2026-10-23T10:00:00+00:00"


def test_export_response_allows_nested_additions(settings):
    data = [
        {"reportId": 1, "dataIncluded": {"includeOrders": True, "futureFlag": True}}
    ]
    with Trading212Client(
        settings,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data)),
    ) as client:
        assert client.get_reports()[0].dataIncluded.includeOrders is True


@pytest.mark.parametrize(
    "method,bad,good",
    [
        ("get_orders", {}, []),
        (
            "get_historical_order_data",
            {"items": [{}], "nextPagePath": 123},
            {"items": []},
        ),
        ("get_exchanges", [{}], []),
    ],
)
def test_invalid_response_is_not_cached(settings, method, bad, good):
    calls = []

    def respond(req):
        calls.append(req)
        return httpx.Response(200, json=bad if len(calls) == 1 else good)

    with Trading212Client(settings, transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(InvalidResponseError):
            getattr(client, method)()
        recovered = getattr(client, method)()
        assert getattr(client, method)() == recovered
    assert len(calls) == 2
