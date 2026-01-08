---
name: ibkr-portfolio
description: Retrieve IBKR portfolio positions and P&L via the MCP tool and present a clear summary.
metadata:
  short-description: IBKR portfolio via MCP
---

Use this skill when the user wants their current IBKR portfolio or P&L.

Instructions:
- Always call the MCP tool `ibkr_get_portfolio` to retrieve portfolio data; do not fabricate or estimate positions.
- Use input parameters only when explicitly requested:
  - `account` to override the default account.
  - `include_pnl` to omit or include P&L.
  - `as_of` to echo the caller-provided timestamp.
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
