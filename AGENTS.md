# Repository instructions

## Read-only fork

This fork must never change a Trading 212 account. Do not add an HTTP method
other than `GET`, an endpoint outside `ALLOWED_PATHS` in
`src/trading212_mcp/client.py`, or a tool that places or cancels orders, edits
pies, or requests exports. Never use real API keys or make live Trading 212
requests; tests use mock transports. Never read, print or commit `.env`.

## Branch names

Do not use a `codex/` prefix when creating branches in this repository.
Use a category prefix that describes the work, such as `feature/`, `fix/`,
`chore/`, `docs/`, or `refactor/`.

Examples:

- `feature/portfolio-alerts`
- `fix/credential-cache-isolation`
- `chore/update-dependencies`

## Trading 212 API schema

Treat `https://docs.trading212.com/_bundle/api.yaml` as the authority for the
current public API. `docs/api.json` is a checked-in snapshot, not proof that the
implementation is still current.

Before changing API endpoints, request or response models, authentication,
pagination, or release compatibility—and before claiming that the server is in
sync—use the repository skill at
`.agents/skills/trading212-api-sync/SKILL.md`. Run its live schema check and
resolve relevant drift in the snapshot, implementation, tests, and user-facing
documentation.

Make response parsing resilient to additive upstream fields and audit every
changed enum. The snapshot documents write endpoints; this fork does not
implement them. Schema checks must not use account credentials or call live
trading endpoints.
