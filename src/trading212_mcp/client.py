"""Trading 212 API client: one validated, read-only HTTP boundary.

This module is the server's security boundary. Every request goes through
``Trading212Client._make_request``, which admits only the HTTP GET method and an
explicit allowlist of read endpoints before the cache or the network is
touched. ``_send`` and an HTTPX request hook check the method again as defense
in depth. MCP tool annotations describe this behaviour; they do not enforce it.
"""

import base64
import json
import re
import time
from collections.abc import Callable, Mapping
from email.utils import parsedate_to_datetime
from typing import Any, NamedTuple, TypeVar
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from hishel import Headers, Request, Response
from pydantic import TypeAdapter, ValidationError

from .cache import ResponseCache
from .errors import (
    AuthenticationError,
    InvalidResponseError,
    RateLimitError,
    RequestError,
    UpstreamError,
)
from .models import (
    AccountSummary,
    Cash,
    Exchange,
    HistoricalOrder,
    Order,
    PaginatedResponseHistoricalOrder,
    PaginatedResponseHistoryDividendItem,
    PaginatedResponseHistoryTransactionItem,
    Position,
    ReportResponse,
    TradeableInstrument,
)
from .settings import Settings

T = TypeVar("T")

READ_ONLY_ERROR = "Read-only server: only GET is allowed."
NOT_ALLOWED_ERROR = "Read-only server: this endpoint is not on the allowlist."

# Paths, relative to /api/<version>, that this server may request. Everything
# else is refused, including every order-placement, pie and export-request
# endpoint. Order IDs are positive int64 values, hence at most 19 digits.
ALLOWED_PATHS: tuple[str, ...] = (
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

HISTORY_ORDERS_PATH = "/equity/history/orders"
# docs/api.json documents "Rate limit: 20 req / 1m0s" for GET
# /equity/history/orders; tests/test_lots.py checks this pair against the
# snapshot. Multi-page reads wait period / requests between network requests
# instead of relying on HTTP 429 responses.
HISTORY_ORDERS_RATE_LIMIT: tuple[int, float] = (20, 60.0)
HISTORY_PAGE_SIZE = 50
MAX_HISTORY_PAGES = 200


class OrderHistory(NamedTuple):
    """Historical orders gathered across pages, newest page first."""

    items: list[HistoricalOrder]
    pages: int
    truncated: bool


def parse_response(schema: type[T], data: Any) -> T:
    try:
        return TypeAdapter(schema).validate_python(data)
    except ValidationError:
        raise InvalidResponseError(
            "Trading 212 returned an unexpected response format."
        ) from None


def _reject_writes(request: httpx.Request) -> None:
    """HTTPX request hook: nothing but GET may leave the HTTP client."""
    if request.method != "GET":
        raise RequestError(READ_ONLY_ERROR)


class Trading212Client:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        version: str = "v0",
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not re.fullmatch(r"v[0-9]+", version):
            raise ValueError("Invalid API version")
        self.base_url = f"https://{settings.environment}.trading212.com/api/{version}"
        self.version = version
        key = settings.api_key.get_secret_value()
        secret = settings.api_secret.get_secret_value() if settings.api_secret else None
        authorization = (
            ("Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode())
            if secret
            else key
        )
        self.cache = ResponseCache(settings, self.base_url, authorization)
        self.http = httpx.Client(
            headers={"Authorization": authorization},
            timeout=httpx.Timeout(10, connect=5),
            follow_redirects=False,
            transport=transport,
            trust_env=False,
            event_hooks={"request": [_reject_writes]},
        )
        self._sleep = sleep
        self._monotonic = monotonic
        # Completion time of the latest network request per endpoint URL.
        self._last_sent: dict[str, float] = {}

    def close(self) -> None:
        try:
            self.cache.close()
        finally:
            self.http.close()

    def __enter__(self) -> "Trading212Client":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _normalise_path(self, path: str) -> str:
        parsed = urlsplit(path)
        decoded = unquote(parsed.path)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or not path.startswith("/")
            or "\\" in decoded
            or any(part in ("..", ".") for part in decoded.split("/"))
            or any(ord(char) < 32 for char in path)
        ):
            raise RequestError("Invalid API path.")
        if parsed.path.startswith("/api/"):
            prefix = f"/api/{self.version}/"
            if not parsed.path.startswith(prefix):
                raise RequestError("API path uses a different version.")
            path = path[len(prefix) - 1 :]
        if not path.startswith("/equity/"):
            raise RequestError("Invalid API endpoint.")
        return path

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return max(0, float(retry_after))
            except ValueError:
                try:
                    return max(
                        0, parsedate_to_datetime(retry_after).timestamp() - time.time()
                    )
                except (ValueError, TypeError, OverflowError):
                    pass
        reset = response.headers.get("x-ratelimit-reset")
        if reset:
            try:
                return max(0, float(reset) - time.time())
            except ValueError:
                pass
        return float(2**attempt)

    def _send(self, request: Request) -> Response:
        # Second line of defense: _make_request has already refused other methods.
        if request.method != "GET":
            raise RequestError(READ_ONLY_ERROR)
        endpoint = request.url.split("?", 1)[0]
        deadline = self._monotonic() + 30
        for attempt in range(3):
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise UpstreamError("Trading 212 request timed out.")
            try:
                # Cookies are unnecessary for API-key authentication.
                self.http.cookies.clear()
                try:
                    response = self.http.request(
                        "GET",
                        request.url,
                        timeout=httpx.Timeout(
                            min(10, remaining), connect=min(5, remaining)
                        ),
                    )
                finally:
                    self._last_sent[endpoint] = self._monotonic()
            except httpx.TransportError:
                if attempt == 2:
                    raise UpstreamError("Trading 212 connection failed.") from None
                delay = float(2**attempt)
            else:
                status = response.status_code
                if status in (401, 403):
                    raise AuthenticationError(
                        "Trading 212 authentication or permission denied."
                    )
                if 200 <= status < 300:
                    body = response.content
                    if body:
                        try:
                            json.loads(body)
                        except (ValueError, UnicodeError):
                            raise InvalidResponseError(
                                "Trading 212 returned invalid JSON."
                            ) from None
                    return Response(
                        status_code=status,
                        headers=Headers(
                            {
                                "content-type": "application/json",
                                "x-trading212-retrieved-at": str(time.time()),
                            }
                        ),
                        stream=iter([body]),
                    )
                if status not in (429, 500, 502, 503, 504) or attempt == 2:
                    if status == 429:
                        raise RateLimitError(
                            "Trading 212 rate limit reached; retry later."
                        )
                    raise UpstreamError(f"Trading 212 request failed (HTTP {status}).")
                delay = self._retry_delay(response, attempt)
            if delay >= deadline - self._monotonic():
                raise RateLimitError(
                    "Trading 212 retry exceeds the request budget; retry later."
                )
            self._sleep(delay)
        raise UpstreamError("Trading 212 request failed.")

    def _make_request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, int | str] | None = None,
        response_schema: Any = None,
    ) -> Any:
        """Send one allowlisted GET request; the only path to the network.

        Refusals happen before the cache layer runs, so a rejected request
        makes no network call and leaves cached data untouched.
        """
        if method != "GET":
            raise RequestError(READ_ONLY_ERROR)
        path = self._normalise_path(url)
        endpoint = urlsplit(path).path
        if not any(re.fullmatch(allowed, endpoint) for allowed in ALLOWED_PATHS):
            raise RequestError(NOT_ALLOWED_ERROR)
        target = httpx.URL(self.base_url + path)
        if params:
            target = target.copy_merge_params(params)
        schema = response_schema
        body = self.cache.request(
            str(target),
            self._send,
            validate=(
                lambda body: parse_response(schema, json.loads(body) if body else None)
            )
            if schema is not None
            else None,
        )
        return json.loads(body) if body else None

    def _pace(self, path: str, interval: float) -> None:
        """Keep network requests to one endpoint at least ``interval`` apart.

        Cached pages never reach ``_send``, so they do not start a new wait.
        """
        last = self._last_sent.get(self.base_url + path)
        if last is None:
            return
        wait = interval - (self._monotonic() - last)
        if wait > 0:
            self._sleep(wait)

    def get_account_summary(self) -> AccountSummary:
        """Fetch the account summary."""
        data = self._make_request(
            "GET", "/equity/account/summary", response_schema=AccountSummary
        )
        return parse_response(AccountSummary, data)

    def get_account_cash(self) -> Cash:
        """Fetch account cash from the account summary endpoint."""
        account_summary = self.get_account_summary()
        return parse_response(
            Cash, account_summary.cash.model_dump() if account_summary.cash else {}
        )

    def get_account_positions(self) -> list[Position]:
        """Fetch all open positions."""
        data = self._make_request(
            "GET", "/equity/positions", response_schema=list[Position]
        )
        return parse_response(list[Position], data)

    def get_positions(self, ticker: str | None = None) -> list[Position]:
        """Fetch positions, optionally filtered by ticker."""
        params = {"ticker": ticker} if ticker else None
        data = self._make_request(
            "GET", "/equity/positions", params=params, response_schema=list[Position]
        )
        return parse_response(list[Position], data)

    def get_position_by_ticker(self, ticker: str) -> Position:
        """Fetch a single open position by ticker via the current filter API."""
        positions = self.get_positions(ticker=ticker)
        if not positions:
            raise RequestError("No open position found for that ticker.")
        return positions[0]

    def get_account_position_by_ticker(self, ticker: str) -> Position:
        """Deprecated alias for fetching a single position by ticker."""
        return self.get_position_by_ticker(ticker)

    def search_position_by_ticker(self, ticker: str) -> Position:
        """Deprecated alias for fetching a single position by ticker."""
        return self.get_position_by_ticker(ticker)

    @staticmethod
    def _history_params(
        cursor: int | str | None,
        limit: int,
        *,
        ticker: str | None = None,
        time_from: str | None = None,
    ) -> dict[str, int | str]:
        params: dict[str, int | str] = {"limit": min(HISTORY_PAGE_SIZE, max(1, limit))}
        if cursor is not None:
            params["cursor"] = cursor
        if ticker is not None:
            params["ticker"] = ticker
        if time_from is not None:
            params["time"] = time_from
        return params

    def get_dividends(
        self,
        cursor: int | None = None,
        ticker: str | None = None,
        limit: int = 20,
    ) -> PaginatedResponseHistoryDividendItem:
        """Fetch dividend history with optional pagination."""
        params = self._history_params(cursor, limit, ticker=ticker)

        data = self._make_request(
            "GET",
            "/equity/history/dividends",
            params=params,
            response_schema=PaginatedResponseHistoryDividendItem,
        )
        return parse_response(PaginatedResponseHistoryDividendItem, data)

    def get_orders(self) -> list[Order]:
        """Fetch current orders."""
        data = self._make_request("GET", "/equity/orders", response_schema=list[Order])
        return parse_response(list[Order], data)

    def get_order_by_id(self, order_id: int) -> Order:
        """Fetch a specific order by ID."""
        data = self._make_request(
            "GET", f"/equity/orders/{order_id}", response_schema=Order
        )
        return parse_response(Order, data)

    def get_historical_order_data(
        self,
        cursor: int | None = None,
        ticker: str | None = None,
        limit: int = 20,
    ) -> PaginatedResponseHistoricalOrder:
        """Fetch historical order data with pagination."""
        params = self._history_params(cursor, limit, ticker=ticker)

        data = self._make_request(
            "GET",
            HISTORY_ORDERS_PATH,
            params=params,
            response_schema=PaginatedResponseHistoricalOrder,
        )
        return parse_response(PaginatedResponseHistoricalOrder, data)

    @staticmethod
    def _next_cursor(next_page_path: str) -> int:
        values = parse_qs(urlsplit(next_page_path).query).get("cursor", [])
        try:
            return int(values[0])
        except (IndexError, ValueError):
            raise InvalidResponseError(
                "Trading 212 returned an unexpected pagination path."
            ) from None

    def get_order_history(
        self, ticker: str | None = None, max_pages: int = 40
    ) -> OrderHistory:
        """Follow ``nextPagePath`` cursors through historical orders.

        Stops when the API reports no further page or after ``max_pages``
        pages (clamped to 1-200), in which case ``truncated`` is true. Pages
        use the normal cache, retry and validation path, and network requests
        are spaced to the documented rate limit using the injectable sleep.
        """
        max_pages = min(MAX_HISTORY_PAGES, max(1, max_pages))
        requests, period = HISTORY_ORDERS_RATE_LIMIT
        interval = period / requests
        items: list[HistoricalOrder] = []
        cursor: int | None = None
        pages = 0
        while True:
            self._pace(HISTORY_ORDERS_PATH, interval)
            page = self.get_historical_order_data(
                cursor=cursor, ticker=ticker, limit=HISTORY_PAGE_SIZE
            )
            pages += 1
            items.extend(page.items)
            if not page.nextPagePath:
                return OrderHistory(items, pages, truncated=False)
            if pages >= max_pages:
                return OrderHistory(items, pages, truncated=True)
            following = self._next_cursor(page.nextPagePath)
            if following == cursor:
                raise InvalidResponseError(
                    "Trading 212 returned a repeated pagination cursor."
                )
            cursor = following

    def get_history_transactions(
        self,
        cursor: str | None = None,
        time_from: str | None = None,
        limit: int = 20,
    ) -> PaginatedResponseHistoryTransactionItem:
        """Fetch movements to and from the account."""
        params = self._history_params(cursor, limit, time_from=time_from)

        data = self._make_request(
            "GET",
            "/equity/history/transactions",
            params=params,
            response_schema=PaginatedResponseHistoryTransactionItem,
        )
        return parse_response(PaginatedResponseHistoryTransactionItem, data)

    def get_instruments(self) -> list[TradeableInstrument]:
        """Fetch all tradeable instruments."""
        data = self._make_request(
            "GET",
            "/equity/metadata/instruments",
            response_schema=list[TradeableInstrument],
        )
        return parse_response(list[TradeableInstrument], data)

    def get_exchanges(self) -> list[Exchange]:
        """Fetch all exchanges and their working schedules."""
        data = self._make_request(
            "GET", "/equity/metadata/exchanges", response_schema=list[Exchange]
        )
        return parse_response(list[Exchange], data)

    def get_reports(self) -> list[ReportResponse]:
        """List existing account export reports. This never requests a new one."""
        data = self._make_request(
            "GET", "/equity/history/exports", response_schema=list[ReportResponse]
        )
        return parse_response(list[ReportResponse], data)
