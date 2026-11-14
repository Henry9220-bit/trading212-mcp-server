# Trading 212 MCP Server (read-only fork)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Fork of [RohanAnandPandit/trading212-mcp-server](https://github.com/RohanAnandPandit/trading212-mcp-server), modified to be read-only.

A [Model Context Protocol](https://modelcontextprotocol.io/) server for the
[Trading 212 public API](https://docs.trading212.com/api). Any MCP client
(Claude Desktop, Claude Code, Cursor) can use it to read a Trading 212 Invest
or Stocks ISA account: positions, average price paid, profit and loss, pending
orders, order history, dividends, transactions, and each individual buy that is
still held.

**This server cannot change your account.** It never places, modifies or
cancels an order, never creates, edits or deletes a pie, and never requests an
export. The write tools of the upstream project, and the whole pies feature,
have been removed.

The safety boundary is in the API client, not in the tool annotations:

- `Trading212Client._make_request` refuses every HTTP method except `GET`, and
  every path that is not on a fixed allowlist of ten read endpoints. Refusal
  happens before the cache or the network is touched.
- `Trading212Client._send`, the only place that calls the HTTP library, checks
  the method again, and the HTTP client itself carries a hook that rejects
  anything but `GET`.
- Tests fail if a write method name appears anywhere in the source, if a second
  HTTP call site is added, or if a tool is not annotated read-only.

MCP tool annotations (`readOnlyHint`) are set on every tool, but they are hints
for the client and enforce nothing. For a second, independent layer, give the
server an API key that has read permissions only (see below).

It does not provide independent market feeds, investment recommendations, or
real-time streaming. Python 3.11–3.14 is supported. The package version is
`0.3.0+readonly`; see [the changelog](CHANGELOG.md). The project uses the
official MCP Python SDK 2.x.

## Install and run

### Setup

1. **Install prerequisites and dependencies.** You need Git,
   [uv](https://docs.astral.sh/uv/getting-started/installation/), and Python
   3.11–3.14. Choose a stable installation folder, then run:

   ```sh
   git clone https://github.com/Henry9220-bit/trading212-mcp-server.git
   cd trading212-mcp-server
   uv sync --frozen
   ```

   Clone this fork, not the upstream repository: upstream includes trading
   tools. If you already have a checkout, reuse it without overwriting local
   changes.

2. **Create a read-only API key.** See
   [Creating an API key](#creating-an-api-key).

3. **Configure credentials locally.** Create an ignored `.env` file from
   `.env.example` if one does not already exist:

   ```sh
   cp .env.example .env
   ```

   Edit `.env` yourself to set `TRADING212_API_KEY` and
   `TRADING212_API_SECRET`. API-key-only authentication remains available where
   Trading 212 accepts it; remove the example secret value if using it. Start
   with `ENVIRONMENT=demo`. Set `ENVIRONMENT=live` to read your real account.
   Demo and live accounts use different keys.

   Keep credentials out of chat, commands, logs, and MCP client configuration.
   If an agent is helping, enter the values yourself; the agent must wait for
   you and must not read, display, log, or repeat them.

   The server reads `.env` in its working directory at startup. Existing
   process environment variables take precedence. Started without credentials,
   it exits with a message naming the variable to set and prints no values.

4. **Connect your MCP client.** See [Claude Desktop](#claude-desktop) and
   [Claude Code](#claude-code).

5. **Verify the connection.** Restart your MCP client if needed and check that
   it lists 17 tools, including `fetch_open_lots`. Imports and tool discovery
   do not make Trading 212 requests.

### Creating an API key

In the Trading 212 app or web platform, open the menu, then **Settings → API
(Beta) → Generate API key**. Trading 212 describes the process in
[Trading 212 API key](https://helpcentre.trading212.com/hc/en-us/articles/14584770928157-Trading-212-API-key).

- **Grant read permissions only.** The API reference names the permissions
  (scopes) behind this server's endpoints:

  | Scope | Used for |
  |---|---|
  | `account` | Account summary and cash |
  | `portfolio` | Open positions |
  | `orders:read` | Pending orders |
  | `history:orders` | Order history and `fetch_open_lots` |
  | `history:dividends` | Dividend history |
  | `history:transactions` | Deposits, withdrawals and other transactions |
  | `metadata` | Instruments and exchanges |

- **Do not grant** `orders:execute` (placing and cancelling orders) or
  `pies:write`. `pies:read` is not needed either.
- The app may word these differently from the scope names above. If a label is
  unclear, tick only the permissions that say read or view, and nothing that
  mentions placing orders, executing, trading, or writing.
- **Restrict the key to your IP addresses** if you can. Trading 212 recommends
  "Restrict access to trusted IPs".
- The API secret is shown once. Put both values straight into `.env`.

A key without write permissions means Trading 212 itself would reject a write,
even if this server had a bug.

### Claude Desktop

Add the server to `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "trading212": {
      "command": "uv",
      "args": ["run", "--frozen", "--directory", "/absolute/path/to/trading212-mcp-server", "trading212-mcp-server"],
      "env": {
        "ENVIRONMENT": "demo"
      }
    }
  }
}
```

Replace the path with your checkout's absolute path. There are no credentials
in this file: `uv run --directory` starts the server inside the checkout, and
the server reads the `.env` file there. If Claude Desktop cannot find `uv`, use
its full path (run `which uv`) as `command`. Restart Claude Desktop afterwards.

### Claude Code

```sh
claude mcp add --transport stdio --scope user trading212 -- uv run --frozen --directory /absolute/path/to/trading212-mcp-server trading212-mcp-server
```

Use `--scope local` to register it for the current project only. As above,
credentials stay in the checkout's `.env`, not in the command. Check it with
`claude mcp list`.

Other clients that accept `mcpServers` JSON, such as Cursor, can use the Claude
Desktop snippet.

### Running it directly

```sh
uv run --frozen trading212-mcp-server
```

Existing configurations pointing to `src/server.py` continue to work after
`uv sync --frozen`. The script also remains the Inspector entry point:

```sh
uv run --frozen mcp dev src/server.py
```

Inspector requires Node.js and `npx`. Tool calls in Inspector read the
configured account.

### Docker

```sh
docker build -t trading212-mcp-server .
docker run --rm -i --env-file .env trading212-mcp-server
```

For a desktop client, pass both credential variables through Docker:

```json
{
  "mcpServers": {
    "trading212": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "-e", "TRADING212_API_KEY", "-e", "TRADING212_API_SECRET", "-e", "ENVIRONMENT", "trading212-mcp-server"],
      "env": {
        "TRADING212_API_KEY": "YOUR_API_KEY",
        "TRADING212_API_SECRET": "YOUR_API_SECRET",
        "ENVIRONMENT": "demo"
      }
    }
  }
}
```

The image runs as UID/GID 10001. Credentials, local caches, and development
files are excluded from the image. The default container cache is ephemeral;
mount a private directory writable by UID 10001 at `/home/app/.cache` if you
want persistence. Stdio does not require publishing a port.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TRADING212_API_KEY` | Required | Account API key |
| `TRADING212_API_SECRET` | Unset | Secret for Basic authentication |
| `ENVIRONMENT` | `demo` | `demo` or `live` |
| `TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `TRADING212_CACHE_DIR` | `.cache/trading212-v3` | Cache root; relative to working directory |
| `TRADING212_ACCOUNT_TTL` | `15` | Account, positions, orders, export-status cache seconds |
| `TRADING212_HISTORY_TTL` | `300` | History cache seconds |
| `TRADING212_METADATA_TTL` | `3600` | Instrument and exchange cache seconds |

A TTL of zero disables caching for that category. Values must be finite and
non-negative. `streamable-http` binds only to `127.0.0.1:8000`, with explicit
Host/Origin restrictions; the `sse` transport has been removed. This is a local
single-account server; hosted access, public HTTP binding, and multi-user
authentication are not provided. Do not expose it through a public proxy or
tunnel.

## Tools

All 17 tools are read-only.

| Area | Tools |
|---|---|
| Account | `fetch_account_summary`, `fetch_account_cash`, `fetch_account_info` |
| Positions | `fetch_positions`, `fetch_position_by_ticker`, `fetch_all_open_positions`, `fetch_open_position_by_ticker`, `search_specific_position_by_ticker` |
| Lots | `fetch_open_lots` |
| Orders | `fetch_all_orders`, `fetch_order` (pending orders) |
| Metadata | `search_instrument`, `search_exchange` |
| History | `fetch_historical_order_data`, `fetch_paid_out_dividends`, `fetch_transaction_list`, `fetch_exports_list` |

Removed from upstream: `place_market_order`, `place_limit_order`,
`place_stop_order`, `place_stop_limit_order`, `cancel_order`,
`request_csv_export`, and the pie tools `fetch_pies`, `fetch_a_pie`,
`create_pie`, `update_pie`, `duplicate_pie`, `delete_pie`.

`fetch_account_info` aliases `fetch_account_summary`. The older position tools
remain compatibility aliases. Positions report quantity, `averagePricePaid` and
`currentPrice` in the instrument currency, and cost, value and profit or loss
in the account currency under `walletImpact`. The history tools other than
`fetch_open_lots` return a single page with `nextPagePath`; pass its cursor and
other query parameters to fetch the next page. Limits are clamped to 1–50.
`fetch_exports_list` lists exports that already exist; it cannot request one
and does not follow download links.

### `fetch_open_lots`: each buy still held

A position shows one average price. `fetch_open_lots` rebuilds the separate
buys behind it, so you can see when each lot was bought and what it cost.

| Argument | Default | Meaning |
|---|---|---|
| `ticker` | all positions | Limit to one instrument, e.g. `AAPL_US_EQ` |
| `max_pages` | `40` | History pages of 50 orders to read, clamped to 1–200 |

**Method.** The tool reads `history/orders` page by page, keeps records whose
order status is `FILLED` and whose fill is a trade, sorts them by fill time
(then fill ID), and replays them: each buy opens a lot, and each sell reduces
the oldest lots first (FIFO), possibly partially. Direction comes from
`order.side`; quantity is the absolute value of `fill.quantity`. FIFO is this
tool's convention for analysis. It is not a broker statement or a tax record.

**Fields of each lot.**

| Field | Meaning |
|---|---|
| `ticker` | Instrument ticker |
| `filled_at` | When the buy was filled, ISO 8601 in UTC |
| `quantity_remaining` | Shares of this buy still held |
| `quantity_original` | Shares originally bought |
| `price_native` | `fill.price`, per share, in the instrument currency |
| `instrument_currency` | Currency of `price_native` and `cost_native` |
| `cost_native` | `price_native × quantity_remaining` |
| `cost_account_currency` | Share of the buy's `walletImpact.netValue` still held, as a positive number |
| `account_currency` | Currency of `cost_account_currency` (`walletImpact.currency`) |
| `fx_rate` | `walletImpact.fxRate` of the fill |
| `fill_id`, `order_id` | Identifiers from Trading 212 |
| `holding_days` | Whole days from `filled_at` to now |

**Fields of each ticker summary.** `total_quantity_from_lots`,
`position_quantity` (from the positions endpoint), `difference` (lots minus
position), `reconciled` (true when the difference is at most 0.000001 shares),
`weighted_avg_price_native`, `oldest_lot_at`, `newest_lot_at`, and `notes`.
The report also has `truncated`, `pages_fetched`, `max_pages`,
`history_cached_at` and `notes`.

**Currencies.** `price_native` and `cost_native` are in the instrument
currency. For London-listed shares that is often `GBX`, which is pence: 100 GBX
is 1 GBP. `cost_account_currency` is in your account currency and is the only
cost figure that can be added up across instruments.

**Fees.** `cost_account_currency` is taken from `fill.walletImpact.netValue`.
The API schema gives that field no description, so it is **not confirmed
whether it includes fees and taxes** such as stamp duty or currency conversion
fees. Likewise the schema does not describe `fill.price`; it is treated as
being in the instrument currency, which is how the schema documents
`position.averagePricePaid`.

**Time.** Trading 212 allows 20 history requests per minute. The tool waits
three seconds between pages rather than running into the limit, so an uncached
40-page history takes about two minutes and 200 pages about ten. Pages are
cached for `TRADING212_HISTORY_TTL` seconds, so a repeat call is fast. Use
`ticker` to keep it short.

**Recent trades.** Positions are cached for 15 seconds and order history for
300, so right after a trade the position can be ahead of the history and the
ticker shows `reconciled: false`. When any history page came from the cache,
the report sets `history_cached_at` to the time it was retrieved and the notes
say so. Call again after the history cache has expired, or set
`TRADING212_HISTORY_TTL=0` to always read fresh history.

**Known limitations.** This version detects the following cases but does not
correct for them. They show up as `reconciled: false`:

- stock splits and consolidations;
- ticker changes;
- positions transferred in, which have no buy order;
- other corporate actions (spin-offs, stock dividends, acquisitions);
- history older than `max_pages`, reported as `truncated: true`;
- pie trades, if they are missing from `history/orders`.

When a ticker does not reconcile, its lots are still returned exactly as
computed, and `notes` lists what was found (for example non-trade fills, or
sells with no matching buy) with the likely causes and whether the history was
truncated. Lots are never invented, dropped or rescaled to match the position.

## Resources and prompt

- `trading212://account/summary`, `trading212://account/cash`
- `trading212://positions`, `trading212://positions/{ticker}`
- `trading212://orders`, `trading212://orders/{order_id}`
- `trading212://instruments`, `trading212://exchanges`
- `trading212://history/exports`

Compatibility URIs: `trading212://account/info`,
`trading212://account/portfolio`, `trading212://account/positions`,
`trading212://account/portfolio/{ticker}`, and
`trading212://account/positions/{ticker}`. The `trading212://pies` resources
have been removed.

The `analyse_trading212_data` prompt includes account currency when available
and explains GBX/GBP units. If account retrieval fails, it still returns the
base prompt and writes a generic diagnostic to stderr.

## Cache and request guarantees

Private data remains cached. Credential/base-URL fingerprints isolate SQLite
storage across accounts, secret rotations, environments, and API versions.
Authorization and cookies never enter the cache layer. Cache responses contain
financial data in plaintext; private filesystem permissions are not encryption.
Use a private local disk, not a shared or network-mounted cache directory.

Successful responses are validated against their endpoint models before caching.
Expired responses are never served. A cross-process file lock covers each
request and its body consumption. Because this server sends no writes, it never
invalidates the cache itself: a trade made in the Trading 212 app becomes
visible when the TTL expires (15 seconds for positions by default).

MCP tool results and resource contents carry freshness details under
`_meta["io.github.RohanAnandPandit.trading212/cache"]`: an array of `cacheHit`,
`retrievedAt` (UTC), and `ttlSeconds` records. Existing data fields remain intact.

Transient failures may be retried twice within a 30-second retry budget,
respecting `Retry-After` and rate-reset headers. Individual HTTP operations have
explicit timeouts. Waiting for another process's cache lock is bounded separately
at 30 seconds. Error messages never include response bodies or credentials.

### Upgrading from upstream

Stop older server processes, switch the checkout to this fork, and run
`uv sync --frozen`. MCP clients that called the removed tools will find them
gone. The cache directory is compatible. Caches from 0.1.x (`.cache/hishel`,
`.cache/trading212-v2`) are never read or migrated; they may contain private
responses and Authorization headers, so remove them yourself after confirming
the paths. Rebuild Docker images.

## Development and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture and verification
commands. The read-only rules for contributors are short: no HTTP method other
than `GET`, no endpoint outside the allowlist in `client.py`, and no real API
keys or live requests in tests.

Report problems with this fork in this fork's repository, not upstream.

### Keeping the Trading 212 schema in sync

Trading 212's live
[OpenAPI description](https://docs.trading212.com/_bundle/api.yaml) is the
authority. `docs/api.json` is the reviewable checked-in snapshot. Before making
API-facing changes or preparing a release, check it without account credentials:

```sh
uv run --with PyYAML python .agents/skills/trading212-api-sync/scripts/sync_api_schema.py --check
```

If the command reports drift, refresh the snapshot:

```sh
uv run --with PyYAML python .agents/skills/trading212-api-sync/scripts/sync_api_schema.py --update
```

Review the resulting diff rather than treating regeneration as sufficient.
The snapshot still documents Trading 212's write endpoints; this server does
not implement them, and tests check that it carries no models for them. The
schema check only downloads public documentation; it must never make
authenticated account requests.

This project is independently maintained and is not affiliated with or endorsed by Trading 212.
Consult the provider's current documentation and terms. Licensed under [MIT](LICENSE).
