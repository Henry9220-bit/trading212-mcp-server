from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    # Trading 212's public API is still evolving. Ignoring unknown fields keeps
    # additive upstream changes from breaking the MCP server.
    model_config = ConfigDict(extra="ignore")


class Environment(StrEnum):
    DEMO = "demo"
    LIVE = "live"
