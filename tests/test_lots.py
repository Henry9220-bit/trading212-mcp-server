"""FIFO open lots: pure computation, pagination, pacing and the MCP tool.

Everything is offline: fixtures are synthetic and HTTP uses a mock transport.
"""

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from mcp import Client

from trading212_mcp import client as client_module
from trading212_mcp.client import Trading212Client
from trading212_mcp.errors import InvalidResponseError
from trading212_mcp.lots import FEES_NOTE, build_open_lots
from trading212_mcp.models import HistoricalOrder, Position
from trading212_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)
AAPL, VOD = "AAPL_US_EQ", "VODl_EQ"


def record(
    ticker,
    side,
    quantity,
    at,
    fill_id,
    *,
    price=10.0,
    net_value=None,
    status="FILLED",
    fill_type="TRADE",
    currency="USD",
    with_fill=True,
    order_ticker=True,
):
    order = {
        "id": 9000 + fill_id,
        "side": side,
        "status": status,
        "instrument": {"ticker": ticker, "currency": currency},
    }
    if order_ticker:
        order["ticker"] = ticker
    item = {"order": order}
    if with_fill:
        item["fill"] = {
            "id": fill_id,
            "filledAt": at,
            "price": price,
            "quantity": quantity,
            "type": fill_type,
            "walletImpact": {
                "currency": "GBP",
                "fxRate": 1.25,
                "netValue": (
                    net_value
                    if net_value is not None
                    else round(price * abs(quantity) / 1.25, 6)
                ),
            },
        }
    return item


def position(ticker, quantity, currency="USD"):
    return {
        "quantity": quantity,
        "instrument": {"ticker": ticker, "currency": currency},
    }


def report(history, positions, *, truncated=False, pages=1, max_pages=40):
    return build_open_lots(
        [HistoricalOrder.model_validate(item) for item in history],
        [Position.model_validate(item) for item in positions],
        now=NOW,
        truncated=truncated,
        pages_fetched=pages,
        max_pages=max_pages,
    )


def test_single_buy_is_one_lot():
    result = report(
        [record(AAPL, "BUY", 2, "2026-01-01T12:00:00Z", 1, price=150.0)],
        [position(AAPL, 2)],
    )
    (lot,) = result.lots
    assert lot.ticker == AAPL
    assert lot.filled_at == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert lot.quantity_remaining == lot.quantity_original == 2
    assert lot.price_native == 150.0 and lot.instrument_currency == "USD"
    assert lot.cost_native == 300.0
    assert lot.cost_account_currency == 240.0 and lot.account_currency == "GBP"
    assert lot.fx_rate == 1.25
    assert (lot.fill_id, lot.order_id, lot.holding_days) == (1, 9001, 30)
    (summary,) = result.summaries
    assert summary.reconciled is True and summary.difference == 0
    assert summary.total_quantity_from_lots == summary.position_quantity == 2
    assert summary.weighted_avg_price_native == 150.0
    assert summary.oldest_lot_at == summary.newest_lot_at == lot.filled_at
    assert summary.notes == []
    assert json.loads(result.model_dump_json())["lots"][0]["filled_at"] == (
        "2026-01-01T12:00:00Z"
    )


@pytest.mark.parametrize("sell_quantity", [-3, 3])
def test_partial_sell_reduces_the_oldest_lot_first(sell_quantity):
    """Direction comes from order.side, so the sign of a sell fill is irrelevant."""
    result = report(
        [
            record(AAPL, "BUY", 5, "2026-01-01T10:00:00Z", 1, price=100.0),
            record(AAPL, "BUY", 4, "2026-01-02T10:00:00Z", 2, price=110.0),
            record(AAPL, "SELL", sell_quantity, "2026-01-03T10:00:00Z", 3),
        ],
        [position(AAPL, 6)],
    )
    first, second = result.lots
    assert (first.fill_id, first.quantity_remaining, first.quantity_original) == (
        1,
        2,
        5,
    )
    assert (second.fill_id, second.quantity_remaining, second.quantity_original) == (
        2,
        4,
        4,
    )
    (summary,) = result.summaries
    assert summary.reconciled is True
    assert summary.weighted_avg_price_native == pytest.approx((2 * 100 + 4 * 110) / 6)
    assert summary.oldest_lot_at < summary.newest_lot_at


def test_sell_spanning_lots_and_selling_out_leaves_only_the_new_lot():
    result = report(
        [
            record(AAPL, "BUY", 1, "2026-01-01T10:00:00Z", 1),
            record(AAPL, "BUY", 2, "2026-01-02T10:00:00Z", 2),
            record(AAPL, "SELL", -3, "2026-01-03T10:00:00Z", 3),
            record(AAPL, "BUY", 7, "2026-01-04T10:00:00Z", 4, price=20.0),
        ],
        [position(AAPL, 7)],
    )
    (lot,) = result.lots
    assert (lot.fill_id, lot.quantity_remaining, lot.price_native) == (4, 7, 20.0)
    assert result.summaries[0].reconciled is True


def test_fractional_shares_keep_decimal_precision():
    result = report(
        [
            record(AAPL, "BUY", 0.123456, "2026-01-01T10:00:00Z", 1),
            record(AAPL, "BUY", 0.2, "2026-01-02T10:00:00Z", 2),
            record(AAPL, "BUY", 0.1, "2026-01-03T10:00:00Z", 3),
            record(AAPL, "SELL", -0.1, "2026-01-04T10:00:00Z", 4),
        ],
        [position(AAPL, 0.323456)],
    )
    assert [lot.quantity_remaining for lot in result.lots] == [0.023456, 0.2, 0.1]
    (summary,) = result.summaries
    # Binary floats would give 0.32345599999999997 and a non-zero difference.
    assert summary.total_quantity_from_lots == 0.323456
    assert summary.difference == 0 and summary.reconciled is True


def test_mixed_tickers_out_of_order_are_replayed_by_fill_time():
    history = [
        record(VOD, "BUY", 10, "2026-01-05T09:00:00Z", 6, price=70.0, currency="GBX"),
        record(AAPL, "SELL", -1, "2026-01-04T10:00:00Z", 5),
        record(AAPL, "BUY", 1, "2026-01-02T10:00:00+02:00", 3),
        record(VOD, "SELL", -4, "2026-01-06T09:00:00Z", 7, currency="GBX"),
        # Same instant: fill id decides, so the sell of 2 hits fill 1 first.
        record(AAPL, "SELL", -2, "2026-01-03T10:00:00Z", 4),
        record(AAPL, "BUY", 2, "2026-01-01T10:00:00Z", 1),
        record(AAPL, "BUY", 5, "2026-01-01T10:00:00Z", 2),
    ]
    result = report(history, [position(VOD, 6, "GBX"), position(AAPL, 5)])
    assert [
        (lot.ticker, lot.fill_id, lot.quantity_remaining) for lot in result.lots
    ] == [
        (AAPL, 2, 4),
        (AAPL, 3, 1),
        (VOD, 6, 6),
    ]
    assert result.lots[1].filled_at == datetime(2026, 1, 2, 8, 0, tzinfo=UTC)
    assert result.lots[2].instrument_currency == "GBX"
    assert [s.ticker for s in result.summaries] == [AAPL, VOD]
    assert all(s.reconciled for s in result.summaries)


def test_records_that_are_not_filled_or_have_no_fill_are_ignored():
    history = [
        record(AAPL, "BUY", 3, "2026-01-01T10:00:00Z", 1),
        record(AAPL, "BUY", 50, "2026-01-02T10:00:00Z", 2, status="CANCELLED"),
        record(AAPL, "BUY", 60, "2026-01-02T11:00:00Z", 3, status="REJECTED"),
        record(AAPL, "SELL", -70, "2026-01-02T12:00:00Z", 4, status="NEW"),
        record(AAPL, "BUY", 80, "2026-01-02T13:00:00Z", 5, with_fill=False),
        # Cancelled before anything executed: nothing to report.
        record(AAPL, "BUY", 0, "2026-01-02T13:30:00Z", 7, status="CANCELLED"),
        {"fill": {"id": 6, "quantity": 90, "filledAt": "2026-01-02T14:00:00Z"}},
        {},
    ]
    result = report(history, [position(AAPL, 3)])
    assert [(lot.fill_id, lot.quantity_remaining) for lot in result.lots] == [(1, 3)]
    (summary,) = result.summaries
    assert summary.reconciled is True
    # Executed quantity on a non-FILLED order is ignored but not hidden.
    assert any("CANCELLED x1" in note and "NEW x1" in note for note in summary.notes)


def test_mismatch_is_reported_and_lots_are_returned_unchanged():
    history = [
        record(AAPL, "BUY", 2, "2026-01-01T10:00:00Z", 1, price=100.0),
        record(AAPL, "BUY", 3, "2026-01-02T10:00:00Z", 2, price=120.0),
    ]
    matching = report(history, [position(AAPL, 5)])
    result = report(history, [position(AAPL, 10)])
    assert result.lots == matching.lots
    assert sum(lot.quantity_remaining for lot in result.lots) == 5
    (summary,) = result.summaries
    assert summary.reconciled is False
    assert (summary.total_quantity_from_lots, summary.position_quantity) == (5, 10)
    assert summary.difference == -5
    assert any("stock split" in note for note in summary.notes)
    assert "truncated: false" in summary.notes
    assert "truncated: false. The full order history was fetched." in result.notes


def test_tolerance_is_one_millionth_of_a_share():
    history = [record(AAPL, "BUY", 1, "2026-01-01T10:00:00Z", 1)]
    assert report(history, [position(AAPL, 1.000001)]).summaries[0].reconciled is True
    assert report(history, [position(AAPL, 1.000002)]).summaries[0].reconciled is False


def test_cost_in_account_currency_follows_the_remaining_share():
    result = report(
        [
            record(AAPL, "BUY", 4, "2026-01-01T10:00:00Z", 1, net_value=-400.4),
            record(AAPL, "SELL", -1, "2026-01-02T10:00:00Z", 2),
            record(AAPL, "BUY", 3, "2026-01-03T10:00:00Z", 3, net_value=90.0),
        ],
        [position(AAPL, 6)],
    )
    first, second = result.lots
    assert first.quantity_remaining == 3
    # abs(netValue) * remaining / original: sign and sold part are both removed.
    assert first.cost_account_currency == pytest.approx(400.4 * 3 / 4)
    assert first.cost_native == 30.0
    assert second.cost_account_currency == 90.0
    assert FEES_NOTE in result.notes and "Not confirmed" in FEES_NOTE


def test_corporate_action_and_transfer_fills_are_flagged_not_replayed():
    history = [
        record(AAPL, "BUY", 1, "2026-01-01T10:00:00Z", 1),
        record(AAPL, "BUY", 3, "2026-01-02T10:00:00Z", 2, fill_type="STOCK_SPLIT"),
        record(AAPL, "BUY", 5, "2026-01-03T10:00:00Z", 3, fill_type="FOP"),
    ]
    result = report(history, [position(AAPL, 9)])
    assert [(lot.fill_id, lot.quantity_remaining) for lot in result.lots] == [(1, 1)]
    (summary,) = result.summaries
    assert summary.reconciled is False and summary.difference == -8
    assert any("FOP x1, STOCK_SPLIT x1" in note for note in summary.notes)


def test_sells_without_known_buys_are_reported():
    history = [
        record(AAPL, "SELL", -2, "2026-01-01T10:00:00Z", 1),
        record(AAPL, "BUY", 1, "2026-01-02T10:00:00Z", 2),
        record(AAPL, "SELL", -4, "2026-01-03T10:00:00Z", 3),
        record(AAPL, "BUY", 6, "2026-01-04T10:00:00Z", 4),
    ]
    result = report(history, [position(AAPL, 11)], truncated=True, pages=2, max_pages=2)
    assert [lot.quantity_remaining for lot in result.lots] == [6]
    (summary,) = result.summaries
    assert summary.reconciled is False
    assert any("exceed the known lots by 5.0" in note for note in summary.notes)
    assert "truncated: true" in summary.notes
    assert result.truncated is True
    assert any(
        note.startswith("truncated: true. History stopped at max_pages=2")
        for note in result.notes
    )


def test_position_without_history_and_lots_without_position():
    history = [record(AAPL, "BUY", 2, "2026-01-01T10:00:00Z", 1)]
    result = report(history, [position(VOD, 12, "GBX")])
    by_ticker = {summary.ticker: summary for summary in result.summaries}
    assert by_ticker[AAPL].position_quantity == 0
    assert by_ticker[AAPL].reconciled is False
    assert "Lots remain but there is no open position." in by_ticker[AAPL].notes
    transferred = by_ticker[VOD]
    assert transferred.total_quantity_from_lots == 0
    assert transferred.position_quantity == 12 and transferred.difference == -12
    assert transferred.weighted_avg_price_native is None
    assert transferred.oldest_lot_at is None and transferred.newest_lot_at is None
    assert "No buy fills were found for this open position." in transferred.notes


def test_sold_out_tickers_are_omitted():
    history = [
        record(AAPL, "BUY", 2, "2026-01-01T10:00:00Z", 1),
        record(AAPL, "SELL", -2, "2026-01-02T10:00:00Z", 2),
    ]
    result = report(history, [])
    assert result.lots == [] and result.summaries == []


def test_incomplete_and_odd_records_are_skipped_and_counted():
    no_side = record(AAPL, "BUY", 1, "2026-01-02T10:00:00Z", 2)
    del no_side["order"]["side"]
    no_time = record(AAPL, "BUY", 1, "2026-01-02T10:00:00Z", 3)
    del no_time["fill"]["filledAt"]
    zero = record(AAPL, "BUY", 0, "2026-01-02T10:00:00Z", 4)
    no_ticker = record(AAPL, "BUY", 1, "2026-01-02T10:00:00Z", 5, order_ticker=False)
    del no_ticker["order"]["instrument"]
    instrument_ticker = record(
        AAPL, "BUY", 2, "2026-01-01T10:00:00", 1, order_ticker=False
    )
    bare = record(AAPL, "BUY", 1, "2026-01-03T10:00:00Z", 6)
    del bare["fill"]["walletImpact"], bare["fill"]["price"], bare["fill"]["type"]
    del bare["order"]["instrument"]
    unknown = {"quantity": None, "instrument": {"ticker": AAPL, "currency": "USD"}}
    result = report(
        [no_side, no_time, zero, no_ticker, instrument_ticker, bare],
        [unknown, {"quantity": 1}],
    )
    first, second = result.lots
    # A timestamp without an offset is read as UTC; ticker falls back to the
    # instrument; a fill without a type is treated as a trade.
    assert first.filled_at == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert (first.fill_id, first.quantity_remaining) == (1, 2)
    assert second.price_native is None and second.cost_native is None
    assert second.cost_account_currency is None and second.account_currency is None
    assert second.fx_rate is None
    # Currency falls back to the position's instrument when the order has none.
    assert second.instrument_currency == "USD"
    (summary,) = result.summaries
    assert summary.position_quantity is None and summary.difference is None
    assert summary.reconciled is False
    assert summary.weighted_avg_price_native is None
    assert any(note.startswith("3 FILLED record(s) lacked") for note in summary.notes)
    assert "The position has no quantity in the API response." in summary.notes
    assert "1 FILLED record(s) had no ticker and were skipped." in result.notes


def test_cached_history_is_named_as_the_cause_of_a_mismatch():
    history = [
        HistoricalOrder.model_validate(
            record(AAPL, "BUY", 1, "2026-01-01T10:00:00Z", 1)
        ),
        HistoricalOrder.model_validate(
            record(VOD, "BUY", 5, "2026-01-02T10:00:00Z", 2, currency="GBX")
        ),
    ]
    positions = [
        Position.model_validate(position(AAPL, 2)),
        Position.model_validate(position(VOD, 5, "GBX")),
    ]
    options = {"now": NOW, "truncated": False, "pages_fetched": 1, "max_pages": 40}
    fresh = build_open_lots(history, positions, **options)
    stale = build_open_lots(
        history,
        positions,
        history_cached_at=datetime(2026, 1, 31, 11, 58),
        **options,
    )
    assert fresh.history_cached_at is None
    assert not any("cache" in note for note in fresh.notes)
    assert not any("cache" in note for note in fresh.summaries[0].notes)
    assert stale.lots == fresh.lots
    assert stale.history_cached_at == datetime(2026, 1, 31, 11, 58, tzinfo=UTC)
    assert any("retrieved at 2026-01-31T11:58:00Z" in note for note in stale.notes)
    mismatched, matched = stale.summaries
    assert mismatched.reconciled is False
    assert any(
        "came from the cache, retrieved at 2026-01-31T11:58:00Z" in note
        for note in mismatched.notes
    )
    # A ticker that reconciles needs no explanation.
    assert matched.reconciled is True and matched.notes == []


def test_holding_days_never_negative_and_accepts_naive_now():
    result = build_open_lots(
        [
            HistoricalOrder.model_validate(
                record(AAPL, "BUY", 1, "2026-03-01T10:00:00Z", 1)
            )
        ],
        [],
        now=datetime(2026, 1, 31, 12, 0),
        truncated=False,
        pages_fetched=1,
        max_pages=40,
    )
    assert result.lots[0].holding_days == 0


# --------------------------------------------------------------------------
# Pagination, pacing and the MCP tool


class Clock:
    """Deterministic monotonic clock; sleeping advances it."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


PAGES = {
    None: (
        [record(AAPL, "SELL", -1, "2026-01-05T10:00:00Z", 5)],
        "/api/v0/equity/history/orders?cursor=300&limit=50",
    ),
    "300": (
        [
            record(AAPL, "BUY", 2, "2026-01-04T10:00:00Z", 4, price=12.0),
            record(AAPL, "BUY", 3, "2026-01-03T10:00:00Z", 3, price=11.0),
        ],
        "/api/v0/equity/history/orders?cursor=200&limit=50",
    ),
    "200": ([record(AAPL, "BUY", 4, "2026-01-01T10:00:00Z", 1, price=10.0)], None),
}


def paged_api(calls, pages=PAGES, position_quantity=8):
    def respond(req):
        calls.append(req)
        if req.url.path.endswith("/positions"):
            return httpx.Response(200, json=[position(AAPL, position_quantity)])
        items, following = pages[req.url.params.get("cursor")]
        return httpx.Response(200, json={"items": items, "nextPagePath": following})

    return httpx.MockTransport(respond)


def history_calls(calls):
    return [req for req in calls if req.url.path.endswith("/history/orders")]


def test_rate_limit_constant_matches_the_api_snapshot():
    spec = json.loads((ROOT / "docs/api.json").read_text(encoding="utf-8"))
    operation = spec["paths"]["/api/v0" + client_module.HISTORY_ORDERS_PATH]["get"]
    limit = re.search(
        r"Rate limit:\*\* (\d+) req / (\d+)m(\d+)s", operation["description"]
    )
    documented = (int(limit[1]), int(limit[2]) * 60.0 + int(limit[3]))
    assert client_module.HISTORY_ORDERS_RATE_LIMIT == documented == (20, 60.0)
    page_size = next(p for p in operation["parameters"] if p["name"] == "limit")
    assert page_size["description"] == f"Max items: {client_module.HISTORY_PAGE_SIZE}"


def test_three_pages_are_fetched_in_full_and_paced(settings):
    calls, clock = [], Clock()
    with Trading212Client(
        settings,
        transport=paged_api(calls),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    ) as c:
        history = c.get_order_history(ticker=AAPL)
        assert (history.pages, history.truncated) == (3, False)
        assert [item.fill.id for item in history.items] == [5, 4, 3, 1]
        requests = history_calls(calls)
        assert [req.url.params.get("cursor") for req in requests] == [
            None,
            "300",
            "200",
        ]
        assert all(req.url.params["limit"] == "50" for req in requests)
        assert all(req.url.params["ticker"] == AAPL for req in requests)
        # 20 requests per minute: three seconds between network requests, by
        # waiting up front instead of provoking HTTP 429.
        assert clock.sleeps == [3.0, 3.0]
        # Cached pages cost no requests and, once the window has passed, no wait.
        clock.now += 10
        assert c.get_order_history(ticker=AAPL) == history
        assert len(history_calls(calls)) == 3 and clock.sleeps == [3.0, 3.0]


def test_max_pages_truncates(settings):
    calls, clock = [], Clock()
    with Trading212Client(
        settings,
        transport=paged_api(calls),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    ) as c:
        history = c.get_order_history(max_pages=2)
        assert (history.pages, history.truncated) == (2, True)
        assert [item.fill.id for item in history.items] == [5, 4, 3]
        assert len(history_calls(calls)) == 2 and clock.sleeps == [3.0]
        assert "ticker" not in history_calls(calls)[0].url.params


@pytest.mark.parametrize("requested,expected", [(0, 1), (-5, 1), (1, 1), (10**6, 200)])
def test_max_pages_is_clamped(settings, requested, expected):
    endless = {
        cursor: ([], f"/api/v0/equity/history/orders?cursor={cursor_after}")
        for cursor, cursor_after in zip(
            [None, *map(str, range(1, 300))], range(1, 301), strict=True
        )
    }
    calls, clock = [], Clock()
    with Trading212Client(
        settings.model_copy(update={"history_ttl": 0}),
        transport=paged_api(calls, endless),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    ) as c:
        history = c.get_order_history(max_pages=requested)
    assert (history.pages, history.truncated) == (expected, True)
    assert len(calls) == expected and len(clock.sleeps) == expected - 1


@pytest.mark.parametrize(
    "following",
    [
        "/api/v0/equity/history/orders?limit=50",
        "/api/v0/equity/history/orders?cursor=abc",
        "/api/v0/equity/history/orders?cursor=7",
    ],
)
def test_unusable_pagination_paths_are_errors_not_silent_truncation(
    settings, following
):
    pages = {None: ([], "/api/v0/equity/history/orders?cursor=7"), "7": ([], following)}
    if following.endswith("cursor=7"):
        pages = {None: ([], following), "7": ([], following)}
    with Trading212Client(
        settings, transport=paged_api([], pages), sleep=lambda _: None
    ) as c:
        with pytest.raises(InvalidResponseError, match="pagination"):
            c.get_order_history()


def call_lots(settings, arguments, *, position_quantity=8):
    calls, clock = [], Clock()

    def factory(settings):
        return Trading212Client(
            settings,
            transport=paged_api(calls, position_quantity=position_quantity),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

    async def run():
        async with Client(create_server(settings, factory)) as session:
            return await session.call_tool("fetch_open_lots", arguments)

    return asyncio.run(run()), calls, clock


def test_tool_returns_reconciled_lots_over_all_pages(settings):
    result, calls, clock = call_lots(settings, {"ticker": AAPL})
    assert not result.is_error
    data = result.structured_content
    assert [(lot["fill_id"], lot["quantity_remaining"]) for lot in data["lots"]] == [
        (1, 3.0),
        (3, 3.0),
        (4, 2.0),
    ]
    assert data["lots"][0]["filled_at"] == "2026-01-01T10:00:00Z"
    assert data["lots"][0]["holding_days"] >= 0
    (summary,) = data["summaries"]
    assert summary["reconciled"] is True and summary["position_quantity"] == 8.0
    assert (data["truncated"], data["pages_fetched"], data["max_pages"]) == (
        False,
        3,
        40,
    )
    assert any(
        "Not confirmed whether netValue includes fees" in n for n in data["notes"]
    )
    assert {req.method for req in calls} == {"GET"}
    assert all(req.url.params.get("ticker") == AAPL for req in calls)
    assert clock.sleeps == [3.0, 3.0]


def test_tool_truncates_and_reports_instead_of_balancing(settings):
    result, calls, _ = call_lots(settings, {"max_pages": 2})
    data = result.structured_content
    assert data["truncated"] is True and data["pages_fetched"] == 2
    assert len(history_calls(calls)) == 2
    # Without the oldest page the sell consumes a later lot: 5 - 1 = 4 != 8.
    assert [lot["quantity_remaining"] for lot in data["lots"]] == [2.0, 2.0]
    (summary,) = data["summaries"]
    assert summary["reconciled"] is False and summary["difference"] == -4.0
    assert "truncated: true" in summary["notes"]
    assert all("ticker" not in req.url.params for req in calls)


def test_tool_reports_a_trade_made_after_the_history_was_cached(settings):
    """Positions refresh in seconds, history in minutes: say so, do not guess."""
    held = {"quantity": 8}
    calls = []

    def respond(req):
        calls.append(req)
        if req.url.path.endswith("/positions"):
            return httpx.Response(200, json=[position(AAPL, held["quantity"])])
        items, following = PAGES[req.url.params.get("cursor")]
        return httpx.Response(200, json={"items": items, "nextPagePath": following})

    def factory(settings):
        return Trading212Client(
            settings, transport=httpx.MockTransport(respond), sleep=lambda _: None
        )

    async def run():
        # Positions are never cached here; history keeps its five-minute TTL.
        config = settings.model_copy(update={"account_ttl": 0})
        async with Client(create_server(config, factory)) as session:
            first = await session.call_tool("fetch_open_lots", {})
            held["quantity"] = 9  # a buy lands after the history was cached
            second = await session.call_tool("fetch_open_lots", {})
            return first.structured_content, second.structured_content

    first, second = asyncio.run(run())
    assert first["history_cached_at"] is None
    assert first["summaries"][0]["reconciled"] is True
    assert not any("cache" in note for note in first["notes"])
    assert len(history_calls(calls)) == 3  # the second call reused all three pages
    assert second["history_cached_at"] is not None
    assert second["lots"] == first["lots"]
    (summary,) = second["summaries"]
    assert summary["reconciled"] is False and summary["difference"] == -1.0
    assert any("came from the cache" in note for note in summary["notes"])
    assert any("served from the cache" in note for note in second["notes"])


def test_tool_clamps_max_pages_and_treats_blank_ticker_as_all(settings):
    result, calls, _ = call_lots(settings, {"ticker": "", "max_pages": 100000})
    assert result.structured_content["max_pages"] == 200
    assert all("ticker" not in req.url.params for req in calls)
    # Same cache directory: the first page is reused, so this adds no requests.
    result, calls, _ = call_lots(settings, {"max_pages": 0})
    data = result.structured_content
    assert (data["max_pages"], data["pages_fetched"], data["truncated"]) == (1, 1, True)
    assert history_calls(calls) == []
