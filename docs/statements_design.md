# Statements Design

## Scope split

This repository now distinguishes between two reporting classes:

- Transaction history:
  - Backed by the live TWS / IB Gateway API session.
  - Suitable for fills, commissions, and basic realized trade activity.
- Statements and account reports:
  - Should be backed by a dedicated IBKR reporting integration.
  - Suitable for official statements, cash activity, dividends, fees, transfers, and tax-style reports.

## Why keep them separate

The current `IBKRClient` in `src/mcp_ibkr/ibkr_client.py` is tightly coupled to the live TWS session and `ib_async`.
That is the right surface for:

- executions
- open orders
- market data
- ad hoc account snapshots

It is not the right surface for statement retrieval if that requires:

- different authentication
- asynchronous report generation
- document metadata and downloads
- historical statement archives outside the active TWS session cache

## Recommended implementation

Add a separate reporting client and MCP tool set:

- `src/mcp_ibkr/statement_client.py`
- `ibkr_get_flex_statement` for a configured Flex query id
  - Implementation should prefer `ib_async.flexreport.FlexReport` instead of reimplementing Flex polling manually.
- `ibkr_get_statement_list` if a future reporting API supports query/report discovery
- `ibkr_get_statement`
- `ibkr_get_cash_activity`
- `ibkr_get_dividends`

## MCP behavior

Statement tools should:

- return structured JSON summaries by default
- optionally expose raw source payloads where useful
- clearly label report period, account, currency, and source system
- use distinct error types from TWS connectivity errors when the reporting backend fails
