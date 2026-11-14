from collections.abc import Callable

from mcp.server import MCPServer

from ..client import Trading212Client
from . import account, history, lots, metadata, orders


def register(mcp: MCPServer, get_client: Callable[[], Trading212Client]) -> None:
    for module in (account, history, lots, metadata, orders):
        module.register(mcp, get_client)
