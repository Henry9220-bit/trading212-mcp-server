from collections.abc import Callable
from datetime import UTC, datetime

from mcp.server import MCPServer

from ..client import MAX_HISTORY_PAGES, Trading212Client
from ..lots import OpenLotsReport, build_open_lots
from ..registration import annotations, safe_handler


def register(mcp: MCPServer, get_client: Callable[[], Trading212Client]) -> None:
    @mcp.tool("fetch_open_lots", annotations=annotations())
    @safe_handler
    def fetch_open_lots(
        ticker: str | None = None, max_pages: int = 40
    ) -> OpenLotsReport:
        """
        Rebuild the individual buys (lots) still held, using FIFO.

        Reads historical orders page by page, replays FILLED trade fills oldest
        first, matches sells against the oldest lots, and reconciles the result
        with the open positions. Read-only: it only issues GET requests.

        Each lot reports when it was bought, shares remaining, price in the
        instrument currency (GBX is pence) and cost in the account currency.
        Each ticker summary has ``reconciled``; when false, the lots are still
        returned unchanged and ``notes`` explains likely causes, such as a
        stock split, a transferred-in position, or truncated history.

        Pages are spaced to the documented rate limit (20 requests per
        minute), so a long uncached history can take minutes: roughly three
        seconds per page after the first.

        Args:
            ticker: Limit to one instrument (e.g. 'AAPL_US_EQ'). Omit for all.
            max_pages: Maximum history pages of 50 orders to read, clamped to
                1-200. If more history exists, ``truncated`` is true.
        """
        client = get_client()
        ticker = ticker or None
        pages = min(MAX_HISTORY_PAGES, max(1, max_pages))
        # Positions first: a missing permission fails before the slow part.
        positions = client.get_positions(ticker)
        history = client.get_order_history(ticker=ticker, max_pages=pages)
        return build_open_lots(
            history.items,
            positions,
            now=datetime.now(UTC),
            truncated=history.truncated,
            pages_fetched=history.pages,
            max_pages=pages,
        )
