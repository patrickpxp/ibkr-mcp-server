---
name: ibkr-portfolio
description: Retrieve IBKR portfolio positions and P&L via the MCP tool and present a clear summary.
metadata:
  short-description: IBKR portfolio via MCP
---

Use this skill when the user wants their current IBKR portfolio or P&L.

Instructions:
- Always call the MCP tool `ibkr_get_portfolio` to retrieve portfolio data; do not fabricate or estimate positions.
- For P&L workflows, call with `include_pnl=true` (or omit it, since default is `true`). Only set `include_pnl=false` when the user explicitly asks for positions-only output.
- Use input parameters only when explicitly requested:
  - `account` to override the default account.
  - `include_pnl` to omit or include P&L (`true` by default).
  - `as_of` to echo the caller-provided timestamp.
- Current P&L behavior:
  - Position-level `unrealizedPnl` and `realizedPnl` use IBKR portfolio rows when available (`ib.portfolio(...)` data).
  - If market data price is missing, `marketPrice`/`marketValue` can still be populated from portfolio rows.
  - If both market data and portfolio row values are unavailable for a position, related P&L fields may remain `null`; report this via returned `notes`.
- Present results in this order:
  1) A table of positions with columns: symbol, secType, exchange, currency, position, avgCost, marketPrice, marketValue, unrealizedPnl, realizedPnl.
  2) Totals summary: unrealizedPnl, realizedPnl, netLiquidation.
  3) Notes list (bullet points).
- If the tool returns an error object:
  - Explain the error clearly.
  - Provide troubleshooting steps:
    - Confirm TWS/IB Gateway is running.
    - Verify API access is enabled in TWS.
    - Check `IBKR_HOST`/`IBKR_PORT` values.
    - On Linux, ensure Docker uses `extra_hosts: host.docker.internal:host-gateway`.
    - Check firewall or local permissions.
- Safety: read-only. Do not place orders or perform any trading action.
