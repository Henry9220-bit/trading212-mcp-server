from collections.abc import Callable

from mcp.server import MCPServer

from ..client import Trading212Client
from ..models.orders import Order
from ..registration import annotations, safe_handler


def register(mcp: MCPServer, get_client: Callable[[], Trading212Client]) -> None:
    @mcp.tool("fetch_all_orders", annotations=annotations())
    @safe_handler
    def fetch_orders() -> list[Order]:
        """Fetch all pending equity orders. Orders cannot be placed or cancelled."""
        return get_client().get_orders()

    @mcp.tool("fetch_order", annotations=annotations())
    @safe_handler
    def fetch_order_by_id(order_id: int) -> Order:
        """Fetch a specific pending order by ID."""
        return get_client().get_order_by_id(order_id)
