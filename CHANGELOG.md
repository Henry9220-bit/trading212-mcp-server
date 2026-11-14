# Changelog

## 0.3.0-readonly — Unreleased

Read-only fork of RohanAnandPandit/trading212-mcp-server. The package version is
`0.3.0+readonly`, the PEP 440 spelling of this entry.

Removed:

- Order tools `place_market_order`, `place_limit_order`, `place_stop_order`,
  `place_stop_limit_order` and `cancel_order`, with their client methods and
  request models.
- `request_csv_export` and the client's export request. `fetch_exports_list`
  remains.
- The whole pies feature: six pie tools, the `trading212://pies` and
  `trading212://pies/{pie_id}` resources, six client methods and the pie models.
- The `sse` transport. `stdio` (default) and `streamable-http` on `127.0.0.1`
  remain.
- The `write` argument of `annotations()`; every tool is annotated read-only.
- Mutation handling in the cache and retry code, which nothing can reach now.

Added:

- A read-only boundary in the client: `_make_request` accepts only `GET` and an
  allowlist of ten read endpoints, checked before the cache and the network.
  `_send` and an HTTP client hook re-check the method.
- `fetch_open_lots`: rebuilds the buys still held, using FIFO over the paged
  order history, and reconciles them with open positions. Mismatches are
  reported as `reconciled: false`; lots are never adjusted to fit.
- Paced multi-page history reads that respect the documented limit of 20
  requests per minute.
- `tests/test_readonly.py`, `tests/test_lots.py` and
  `tests/test_credentials.py`, including static checks that the source holds no
  write method names and a single HTTP call site.
- A startup error that names the misconfigured variables and prints no values.
- README sections for read-only API keys, Claude Desktop and Claude Code.

Counts: 17 tools (was 28), 10 static resources (was 11), 4 resource templates
(was 5), 1 prompt.

Migration: MCP clients that relied on the removed tools must stop calling them.
`TRANSPORT=sse` is now a configuration error; use `streamable-http`. The cache
directory format is unchanged.

## 0.2.0 — Unreleased

- Preserve tax charge dates and currencies; tolerate additional nested export response fields.
- Validate endpoint responses before caching and test model coverage against the API snapshot.
- Separate security auditing from quality checks; add a dependency lock/export update helper.

- Migrate to MCP SDK 2.x and current supported dependencies; add a typed installable package.
- Preserve 28 tools, 16 resource registrations, the prompt, and the script launcher.
- Create API clients during server lifespan and close them on shutdown.
- Replace legacy file caching with credential-free SQLite caching and cross-process invalidation.
- Add category TTLs, freshness metadata, bounded GET retries, request validation, and safe errors.
- Harden local HTTP transport and non-root Docker builds; exclude local secrets and caches.
- Add offline endpoint, protocol, package, and concurrency tests, static checks and dependency auditing.
- Refresh the Trading 212 schema snapshot and accept newly documented transaction and order-origin values.

Migration: install the new lockfile and rebuild Docker images. Legacy cache paths
are ignored and remain for explicit user cleanup. Some previously accepted invalid
requests now fail validation. Package version does not imply a published release.
