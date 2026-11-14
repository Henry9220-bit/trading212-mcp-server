from collections.abc import Callable

from mcp.server import MCPServer

from ..client import Trading212Client
from ..models.history import (
    PaginatedResponseHistoricalOrder,
    PaginatedResponseHistoryDividendItem,
    PaginatedResponseHistoryTransactionItem,
    ReportResponse,
)
from ..registration import annotations, safe_handler


def register(mcp: MCPServer, get_client: Callable[[], Trading212Client]) -> None:
    @mcp.tool("fetch_historical_order_data", annotations=annotations())
    @safe_handler
    def fetch_historical_order_data(
        cursor: int | None = None,
        ticker: str | None = None,
        limit: int = 20,
    ) -> PaginatedResponseHistoricalOrder:
        """Fetch historical order data with pagination."""
        return get_client().get_historical_order_data(
            cursor=cursor, ticker=ticker, limit=limit
        )

    @mcp.tool("fetch_paid_out_dividends", annotations=annotations())
    @safe_handler
    def fetch_paid_out_dividends(
        cursor: int | None = None,
        ticker: str | None = None,
        limit: int = 20,
    ) -> PaginatedResponseHistoryDividendItem:
        """Fetch historical dividend data with pagination."""
        return get_client().get_dividends(cursor=cursor, ticker=ticker, limit=limit)

    @mcp.tool("fetch_exports_list", annotations=annotations())
    @safe_handler
    def fetch_exports_list() -> list[ReportResponse]:
        """List existing CSV account exports. This cannot request a new export."""
        return get_client().get_reports()

    @mcp.tool("fetch_transaction_list", annotations=annotations())
    @safe_handler
    def fetch_transaction_list(
        cursor: str | None = None,
        time: str | None = None,
        limit: int = 20,
    ) -> PaginatedResponseHistoryTransactionItem:
        """Fetch superficial information about movements to and from your
        account."""
        return get_client().get_history_transactions(
            cursor=cursor, time_from=time, limit=limit
        )
