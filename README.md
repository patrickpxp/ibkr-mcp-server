# IBKR MCP Server

Expose IBKR account data, portfolio positions (best-effort P&L), orders, executions,
contracts, and market data snapshots via MCP to Claude or Codex.
Tested with Interactive Brokers Trader Workstation.
WARNING: You can lose real money if you do not understand what you are doing.

## Requirements
- Docker (and Docker Compose v2)
- TWS or IB Gateway running locally with API access enabled

## Configure TWS
In TWS, enable API access and allow local connections.

![TWS API Settings](https://interactivebrokers.github.io/tws-api/tws_allow_connections.png)

## Installation
```
git clone https://github.com/patrickpxp/ibkr-mcp-server
cd ibkr-mcp-server
```

## Configuration
Create `.env` (ignored by git) as needed:
```
IBKR_HOST=host.docker.internal
IBKR_PORT=7497 # paper trading port, use 7496 for live trading
IBKR_CLIENT_ID=123
IBKR_ACCOUNT=
IBKR_TIMEOUT_SECONDS=10
IBKR_ENABLE_TRADING=false
MCP_BIND_HOST=0.0.0.0
MCP_PORT=8000
MCP_JSON_RESPONSE=true
MCP_STATELESS_HTTP=true
TZ=Europe/Madrid
```

Set `MCP_JSON_RESPONSE=false` or `MCP_STATELESS_HTTP=false` to enable streamable
HTTP/session behavior when needed.

## Run
```
docker compose up -d --build
```

Ensure TWS or IB Gateway has API access enabled and is listening on the configured port.

## Health Check
```
curl http://localhost:${MCP_PORT:-8000}/health
```

Expected response:
```json
{"status":"ok"}
```

## MCP Tool Invocation Example
```
curl -s http://localhost:${MCP_PORT:-8000}/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ibkr_get_portfolio","arguments":{}}}'
```

## Tools
- `ibkr_get_portfolio`: Positions with best-effort P&L.
- `ibkr_get_account_summary`: Account summary values (NetLiquidation, BuyingPower, etc.).
- `ibkr_get_account_values`: Account values snapshot (uses a short refresh; may fall back to cached values).
- `ibkr_get_open_orders`: Open orders with contract details and status.
- `ibkr_get_executions`: Executions/fills with basic execution details.
- `ibkr_search_symbols`: Symbol lookup via matching symbols.
- `ibkr_get_contract_details`: Contract details for a given contract input.
- `ibkr_get_market_data_snapshot`: One-shot market data snapshot for contracts (IBIS requests are normalized to SMART + primaryExchange=IBIS).
- `ibkr_debug_market_data_snapshot`: Diagnostic snapshot that compares raw vs SMART+primaryExchange requests.
- `ibkr_get_historical_bars`: Historical OHLCV bars for a contract.
- `ibkr_get_historical_ticks`: Historical ticks (bid/ask/trades/midpoint) for a contract.
- `ibkr_get_head_timestamp`: Earliest available historical data timestamp for a contract.
- `ibkr_get_market_depth_snapshot`: One-shot market depth (L2) snapshot for a contract.
- `ibkr_get_option_chain`: Option chain metadata (expirations/strikes) for an underlying.
- `ibkr_get_news_providers`: Available news provider codes/names.
- `ibkr_get_historical_news`: Historical news headlines for a contract.
- `ibkr_get_news_article`: News article body for a provider/article id.
- `ibkr_get_fundamental_data`: Fundamental data report (JSON by default, XML optional).
- `ibkr_get_scanner_params`: Scanner parameters (JSON by default, XML optional).
- `ibkr_run_scanner`: Run a market scanner subscription and return ranked results.
- `ibkr_preview_order`: What-if margin/commission preview for an order.
- `ibkr_place_order`: Place one order (defaults: `dry_run=true`, `transmit=false`).
- `ibkr_cancel_order`: Cancel one order by `orderId` (requires `confirm=true`).
- `ibkr_global_cancel`: Cancel all active orders (requires `confirm=true`).
- `ibkr_bracket_order`: Place bracket entry/take-profit/stop-loss orders (defaults: `dry_run=true`, `transmit=false`).
- `ibkr_oca_group`: Place OCA grouped orders (defaults: `dry_run=true`, `transmit=false`).
- `ibkr_exercise_options`: Exercise/lapse options contract (requires `confirm=true`).

Batch 2 tool examples: `docs/batch2_examples.md`.

Batch 3 safety rules:
- Live mutating tools are disabled unless `IBKR_ENABLE_TRADING=true`.
- Mutating tools require `confirm=true`.
- Order placement flows default to `dry_run=true` and `transmit=false`.

## Schemas and Errors
`tools/list` includes `title`, `description`, `inputSchema`, and `outputSchema` for every tool. Input schemas include per-parameter descriptions, and output schemas describe the structured response payloads.

`tools/call` returns structured content that matches each tool's `outputSchema`. When a tool returns an error payload, the server sets `isError=true` and includes the structured `error` object in `structuredContent`.

## Register MCP Server with Codex
```
codex mcp add ibkr-portfolio \
  --transport http \
  --url http://localhost:${MCP_PORT:-8000}/mcp
```

Once registered, ask Codex for your IBKR portfolio to invoke the tool.

## Install the Skill
Copy the provided skill into your Codex skills directory:
```
mkdir -p ~/.codex/skills
cp -R .codex/skills/ibkr-portfolio ~/.codex/skills/
```

## Tests
```
python -m pytest -q
```

## Future: Auth
FastMCP includes built-in OAuth provider integrations. A future iteration can wrap the
existing `/mcp` endpoint with FastMCP OAuth configuration (e.g., GitHub or Google) and
add token validation middleware before exposing the server publicly. No authentication
is implemented yet.
