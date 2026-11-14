"""Check implementation coverage against the reviewed public schema snapshot."""

import json
import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from trading212_mcp import models
from trading212_mcp.client import ALLOWED_PATHS

SPEC = json.loads((Path(__file__).resolve().parents[1] / "docs/api.json").read_text())
SCHEMAS = SPEC["components"]["schemas"]


def model_for(name):
    # Preserve the public Python spelling while covering the upstream model.
    return getattr(
        models, "TradeableInstrument" if name == "TradableInstrument" else name, None
    )


def readable_schemas():
    """Schemas reachable from the GET operations this server may call."""
    seen = set()

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref:
                name = ref.rsplit("/", 1)[-1]
                if name not in seen:
                    seen.add(name)
                    walk(SCHEMAS[name])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path, operations in SPEC["paths"].items():
        endpoint = path.removeprefix("/api/v0").replace("{id}", "1")
        if "get" in operations and any(
            re.fullmatch(allowed, endpoint) for allowed in ALLOWED_PATHS
        ):
            walk(operations["get"]["responses"])
    return seen


READABLE = readable_schemas()
OBJECTS = [
    (name, schema) for name, schema in SCHEMAS.items() if schema.get("properties")
]


@pytest.mark.parametrize("name,schema", OBJECTS, ids=[n for n, _ in OBJECTS])
def test_schema_fields_and_enums_are_represented(name, schema):
    model = model_for(name)
    if name not in READABLE:
        # Request bodies and responses of write or pie endpoints: the read-only
        # server must not carry models for them.
        assert model is None
        return
    assert model is not None
    assert set(schema["properties"]) <= set(model.model_fields)
    for field, definition in schema["properties"].items():
        if "$ref" in definition:
            definition = SCHEMAS[definition["$ref"].rsplit("/", 1)[-1]]
        for value in definition.get("enum", []):
            parsed = TypeAdapter(model.model_fields[field].annotation).validate_python(
                value
            )
            assert parsed == value


def test_allowlist_matches_the_documented_read_endpoints():
    documented = {
        path.removeprefix("/api/v0").replace("{id}", "1")
        for path, operations in SPEC["paths"].items()
        if "get" in operations and "/pies" not in path
    }
    assert documented
    for endpoint in documented:
        assert any(re.fullmatch(allowed, endpoint) for allowed in ALLOWED_PATHS)
    assert "AccountSummary" in READABLE and "HistoricalOrder" in READABLE
    assert not READABLE & {"MarketRequest", "PieRequest", "PublicReportRequest"}
