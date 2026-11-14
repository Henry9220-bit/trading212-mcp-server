# Contributing

## Read-only rules

This fork must never change a Trading 212 account. Three rules follow, and
`tests/test_readonly.py` enforces them:

- The only HTTP method is `GET`. Do not add the name of any other method to the
  source, even in an error message; compare against `"GET"` instead.
- Requests go only to the allowlist `ALLOWED_PATHS` in `client.py`. Adding a
  path is a security decision: it needs a matching entry in the reviewed list
  in `tests/test_readonly.py` and a note in the changelog.
- `Trading212Client._send` is the only caller of the HTTP library. Do not add a
  second call site or a public method that takes an arbitrary method or path.

Do not add tools that place, modify or cancel orders, change pies, or request
exports. Tool annotations are hints; they are not the boundary.

## Setup and checks

Use Python 3.11–3.14 and uv 0.12.9 or newer:

```sh
uv sync --frozen
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest --cov --cov-report=term-missing
uv run --frozen pip-audit --local
uv build --no-build-isolation
```

Tests use synthetic credentials, temporary directories, and mock HTTP transports.
They do not need a Trading 212 account. Stdio smoke tests launch local subprocesses
and perform discovery only. Cross-process cache tests use spawned processes so
connections and locks are not inherited. CI tests every supported Python version,
plus Windows and macOS, package installation, and Docker startup.

Use `feature/`, `fix/`, `chore/`, `docs/`, or `refactor/` branches as appropriate;
do not use the `codex/` prefix in this repository.

## Architecture

`src/trading212_mcp/` is the installed package. `server.create_server()` registers
handlers without reading credentials or opening an API connection. Lifespan owns
a single client and closes it on shutdown. Each server gets its own client
provider; there is no module-global account client. This also supports static MCP
resources, for which the SDK does not support injected Context arguments.

- `settings.py`: validated environment configuration; dotenv only at startup.
- `client.py`: the read-only boundary (GET plus path allowlist), endpoint
  mapping, bounded retries, paced multi-page history reads, safe errors.
- `cache.py`: Hishel adapter for GET requests, credential isolation, private storage.
- `lots.py`: FIFO open-lot reconstruction; pure computation with no I/O.
- `models/`: response types, grouped by API domain. There are no request models.
- `tools/`: typed MCP handlers by domain; resources and prompts are separate modules.
- `registration.py`: read-only annotations and safe exception mapping.

The MCP SDK runs synchronous handlers on worker threads. Keep client methods
synchronous; use the server's lifetime and shared cache coordination rather than
adding untracked background tasks. Keep the compatibility `src/server.py` launcher.
Use explicit imports and type annotations. Ruff handles formatting and imports;
mypy checks the complete installed package in strict mode.

## Public contracts

Keep tool names, argument names, resource URIs, and response fields compatible.
`tests/fixtures/mcp_v1.json` captures the read-only discovery surface: the
upstream surface minus the write and pie entries, plus `fetch_open_lots`.
`model_fields_v1.json` captures model fields and requiredness. Additive optional
fields are allowed while existing fields and requiredness remain protected.
`test_schema_contract.py` checks documented fields and enum values against the
reviewed API snapshot for every schema reachable from an allowlisted endpoint,
and checks that schemas used only by write or pie endpoints have no model.
Avoid rewriting compatibility snapshots just to make a test pass.

Response models tolerate extra upstream fields. The checked-in OpenAPI schema
is a reference: verify upstream changes before altering endpoint paths or
types. Tests never use real credentials or contact Trading 212.

`lots.py` must stay honest about what it cannot know. It replays only FILLED
trade fills, reports everything else in `notes`, and never creates, drops or
rescales a lot to make a ticker reconcile. Undocumented API fields are labelled
as unconfirmed rather than interpreted.

## Cache invariants

Credentials are attached only by the live HTTP client. Hishel receives sanitized
requests and an allowlisted response header set; never pass Authorization,
Set-Cookie, or arbitrary request/response headers into storage. Namespaces hash
both the full outgoing authorization identity and API base URL.

Each client owns its storage connection and closes it. A per-namespace file lock
serializes complete requests across processes, including streamed body consumption.
This server never advances the private generation, because it sends no writes.
It still reads the generation under the lock, so a write-capable process that
shares the cache directory can invalidate its entries. The metadata generation
is stable. Old entries expire through Hishel cleanup. Do not release the lock
before body consumption.
SQLite transaction context managers do not close their connections; use explicit
closing as well. Keep warnings as errors in tests.

Cache payloads contain private financial data, although credentials are excluded.
Use private local directories. No automatic deletion or migration of legacy caches.
Tests must cover credential/secret/environment/version isolation, persistence,
expiration, concurrency, write rejection, and absent credential bytes.

## Dependency changes

`pyproject.toml` defines supported ranges and `uv.lock` pins resolutions. Upgrade
with the helper below, which resolves the lockfile, regenerates the export and
synchronizes the environment together:

```sh
bash scripts/update-dependencies.sh             # refresh all packages
bash scripts/update-dependencies.sh pydantic    # refresh selected packages
```

Review both generated files. To regenerate just the export:

```sh
uv export --frozen --no-dev --no-emit-project --output-file requirements.txt
```

Dependabot excludes the generated requirements manifest from its update scan;
after a lockfile update, use the helper to regenerate the export before merging.
CI checks lockfile freshness and export consistency; security auditing runs in
an independent job so quality failures cannot suppress it.

Do not edit generated requirements or independently pin transitive dependencies.
Do not admit prereleases without an explicit compatibility decision. Run the audit,
protocol tests, and packaging checks after upgrades; Hishel and MCP major updates
need behavior verification. CI actions are pinned to commits; Dependabot proposes
updates for uv, actions, and Docker.

## Reporting issues

Do not report problems with this fork to the upstream project. Report security
issues privately as described in [.github/SECURITY.md](.github/SECURITY.md);
never include credentials or account data in any report. Use synthetic examples
to reproduce bugs. Contributions use the MIT license.
