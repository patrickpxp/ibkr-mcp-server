---
name: ibkr-portfolio
description: Retrieve IBKR portfolio positions, P&L, and historical trades via the MCP tools and present a clear summary.
metadata:
  short-description: IBKR portfolio/trades via MCP
---

Use this skill when the user wants their current IBKR portfolio, P&L, or historical trade activity.

Instructions:
- For current positions/current portfolio P&L, call the MCP tool `ibkr_get_portfolio`; do not fabricate or estimate positions.
- For current-position P&L workflows, call `ibkr_get_portfolio` with `include_pnl=true` (or omit it, since default is `true`). Only set `include_pnl=false` when the user explicitly asks for positions-only output.
- Use input parameters only when explicitly requested:
  - `account` to override the default account.
  - `include_pnl` to omit or include P&L (`true` by default).
  - `as_of` to echo the caller-provided timestamp.
- Current P&L behavior:
  - Position-level `unrealizedPnl` and `realizedPnl` use IBKR portfolio rows when available (`ib.portfolio(...)` data).
  - If market data price is missing, `marketPrice`/`marketValue` can still be populated from portfolio rows.
  - If both market data and portfolio row values are unavailable for a position, related P&L fields may remain `null`; report this via returned `notes`.
Historical trades:
- For “what did I trade?”, yesterday’s trades, YTD trade lists, or trade-count questions, call `ibkr_get_trade_confirmations` first.
- Do **not** conclude “no trades” from `ibkr_get_transactions` or `ibkr_get_executions` alone; those can be session-limited/empty while the Flex statement has trades.
- If `TradeConfirm` rows are unavailable but the tool notes a fallback to Flex `Trade` rows, use those rows.
- Parse returned `dateTime` as `DD/MM/YYYY;HH:MM:SS TZ` first. Example: `28/04/2026;13:43:25 EDT` is 2026-04-28. Also tolerate ISO dates if present.
- When filtering by a calendar date, compare parsed dates, not string fragments like `04/28/2026`.
- For trade rows, present columns: dateTime, symbol, description, side, quantity, price, proceeds, commission, currency, tradeId, orderId.
- Make clear that proceeds/commissions are cash flow; exact realized profit needs realized P&L / cost-basis data.

Portfolio presentation:
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
