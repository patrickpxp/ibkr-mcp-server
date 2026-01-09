You are implementing a **viable** (robust, maintainable, production-grade patterns) HYBRID integration for my OpenAI Codex agent.

This repo root is empty. Create everything from scratch.

**Additional viability-grade specs (REQUIRED):**

* **Python:** Target **Python 3.12.x** (use `python:3.12-slim` for Docker).
* **License:** Add a **MIT** `LICENSE` file at repo root.

You will deliver:

1. A network-accessible **MCP server** running in Docker, using **FastMCP v2.14.2** with **Streamable HTTP** transport.
2. A **Codex Skill** checked into this repo under `.codex/skills/` that teaches Codex how to use the MCP tool to retrieve my current IBKR portfolio (positions + best-effort P&L).

You are running under **Codex CLI on my computer**, so you may assume:

* You can run docker/docker compose locally.
* You can run tests locally.
* You can use Context7 to consult docs for FastMCP v2.14.2 and ib_async v2.0.1.

---

## CORE REQUIREMENTS (DO NOT VIOLATE)

* MCP transport: **Streamable HTTP** (networked), served at **/mcp**.
* Health endpoint at **/health**.
* Framework: **FastMCP v2.14.2** (NOT official Python MCP SDK).
* Language: **Python**.
* IBKR library: **ib_async v2.0.1 only** (do NOT use ibapi, do NOT use ib_insync).
* Dependency pinning: **exact versions** in `pyproject.toml`.
* Logging: structured logs to **stderr**; NEVER log protocol payloads to stdout.
* Provide clear run instructions and examples.

Future (NOT IMPLEMENTING YET):

* We may restrict access using FastMCP built-in OAuth providers.
  Add a short README section “Future: Auth” describing intended path, but implement **no auth now**.

---

## NETWORKING ASSUMPTIONS

* TWS is running on the Docker host and has API enabled.
* Container must connect to host TWS API.
* Default host for container-to-host on Docker Desktop (macOS/Windows): `host.docker.internal`.
* On Linux, support `extra_hosts: ["host.docker.internal:host-gateway"]` in docker compose.

---

## CONFIGURATION (ENV VARS)

Use env vars for MCP server config:

IBKR:

* `IBKR_HOST` default: `host.docker.internal`
* `IBKR_PORT` default: `7497`
* `IBKR_CLIENT_ID` default: `123`
* `IBKR_ACCOUNT` optional; if set, prefer this account for P&L
* `IBKR_TIMEOUT_SECONDS` default: `10`

Server:

* `MCP_BIND_HOST` default: `0.0.0.0`
* `MCP_PORT` default: `8000`

Time:

* `TZ` default: `Europe/Madrid` for timestamps

---

## DELIVERABLES: REPO STRUCTURE (MUST CREATE)

/LICENSE                         # MIT (repo root)
/mcp-ibkr/
README.md
Dockerfile
docker-compose.yml
pyproject.toml
src/
mcp_ibkr/
**init**.py
server.py              # FastMCP server entrypoint (Streamable HTTP mounted at /mcp, /health)
ibkr_client.py         # ib_async connection + wrappers
models.py              # typed models (pydantic/dataclasses)
logging_utils.py       # stderr structured logger helper
tests/
test_schema.py           # validate JSON shape
test_offline_mode.py     # graceful behavior when TWS unreachable
conftest.py

/.codex/skills/
ibkr-portfolio/
SKILL.md
references/
IBKR_TOOL_SCHEMA.md
assets/
portfolio_output_example.json

---

## MCP SERVER REQUIREMENTS (FAST-MCP)

* Use FastMCP to define tools.
* Run over **Streamable HTTP** and expose it at **/mcp**.
* Expose **/health** endpoint that returns 200 + small JSON body (e.g. {"status":"ok"}).
* Bind host/port from env (`MCP_BIND_HOST`, `MCP_PORT`).
* IMPORTANT: If wrapping FastMCP into an ASGI app (Starlette/FastAPI), ensure FastMCP lifespan is correctly wired so session manager initializes (follow FastMCP docs via Context7).
* Only implement ONE MCP tool for now:

  * `ibkr_get_portfolio`
    (Do not add other IBKR tools. A ping tool is unnecessary since /health exists.)

---

## MCP TOOL SPEC (ONLY ONE TOOL)

Tool name: `ibkr_get_portfolio`
Description: "Fetch current IBKR portfolio positions and P&L from local TWS via ib_async."

Input params:

* `account` (string, optional) – overrides env IBKR_ACCOUNT
* `include_pnl` (boolean, default true)
* `as_of` (string, optional) – reserved for future; echo back for now

Output JSON MUST be stable/deterministic:

{
"as_of": "2026-01-08T18:00:00+01:00",
"account": "U1234567",
"currency": "BASE",
"positions": [
{
"symbol": "AAPL",
"secType": "STK",
"exchange": "SMART",
"currency": "USD",
"conId": 265598,
"position": 10,
"avgCost": 150.12,
"marketPrice": 172.34,
"marketValue": 1723.40,
"unrealizedPnl": 222.20,
"realizedPnl": null
}
],
"totals": {
"unrealizedPnl": 222.20,
"realizedPnl": null,
"netLiquidation": null
},
"notes": [
"realizedPnl not available via current implementation"
]
}

Rules:

* If TWS is not reachable, return structured error object (do NOT crash):
  { "error": { "type": "TWS_CONNECTION_FAILED", "message": "...", "retryable": true } }
* Use timezone Europe/Madrid for `as_of` (from `TZ` env default Europe/Madrid).
* Prefer numeric values as JSON numbers, not strings.
* Always include `notes` array (possibly empty).
* P&L is best-effort using ib_async data available without extra market data subscriptions.
  If marketPrice/marketValue/unrealizedPnl/realizedPnl cannot be reliably obtained, return null and add a note explaining why.

Architecture requirements:

* Implement an `IBKRClient` wrapper in `ibkr_client.py` with clear methods:

  * connect(), disconnect()
  * get_positions()
  * get_pnl_best_effort()
* Keep it easy to add more tools later mapping to TWS API.
* Include an “offline mode” path so tool returns structured error when connection fails quickly (respect timeout).

---

## DOCKER / COMPOSE REQUIREMENTS

* Provide `Dockerfile` to build the FastMCP server image (base: `python:3.12-slim`).
* Provide `docker-compose.yml` with:

  * service name: `mcp-ibkr`
  * port mapping: `${MCP_PORT:-8000}:8000`
  * env vars: IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID, IBKR_ACCOUNT, IBKR_TIMEOUT_SECONDS, TZ, MCP_BIND_HOST, MCP_PORT
  * Linux: `extra_hosts: ["host.docker.internal:host-gateway"]`
  * restart policy: unless-stopped
* README must include:

  * `docker compose up -d --build`
  * Ensuring TWS API is enabled and listening on the configured port
  * How to confirm connectivity:

    * `curl http://localhost:${MCP_PORT:-8000}/health`
    * plus an example MCP tool invocation from a client

---

## CODEX SKILL REQUIREMENTS

Create `.codex/skills/ibkr-portfolio/SKILL.md` with YAML front matter:

* Required fields: `name` and `description` (single-line, non-empty).
* Optional: `metadata.short-description`.

Skill instructions must:

* Always call MCP tool `ibkr_get_portfolio` to retrieve portfolio.
* Present results:

  * a table of positions
  * totals summary
  * notes
* Handle errors (especially TWS_CONNECTION_FAILED) with troubleshooting steps:

  * TWS running?
  * API enabled?
  * IBKR_HOST/PORT correct?
  * Docker networking (Linux extra_hosts)?
  * firewall/permissions?
* Safety: read-only; no trading endpoints; do not place orders.

Also create:

* `references/IBKR_TOOL_SCHEMA.md` documenting tool input/output schema.
* `assets/portfolio_output_example.json` with realistic example output.

---

## TESTING REQUIREMENTS

Automated tests:

* Validate output JSON shape and required keys.
* Validate error object shape in offline mode (TWS unreachable).
* Do NOT require a live TWS connection in tests. Mock/stub `IBKRClient`.

Manual testing steps in README:

* How to run compose and then hit the MCP server from a client.
* Include an example of configuring Codex to use a streamable HTTP MCP server via:

  * `codex mcp add ...` (example command/config snippet)

---

## DELIVERY STYLE (COMMITS)

Work in small commits with clear messages:

1. scaffold python project + dependency pinning
2. Dockerfile + compose + baseline run path
3. FastMCP server skeleton + HTTP transport mounted at /mcp + /health
4. ib_async client wrapper + mocks
5. portfolio tool implementation
6. tests
7. skill folder + docs + sample asset
8. README polish

At the end, print:

* “What I built”
* How to run via docker compose
* How to register MCP server in Codex
* Example output (from sample asset or mocked run)

DO IT NOW in this repo.
