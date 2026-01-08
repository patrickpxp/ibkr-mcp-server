# IBKR MCP Tool Schema

Tool name: `ibkr_get_portfolio`

Description: Fetch current IBKR portfolio positions and P&L from local TWS via ib_async.

## Input

| Field | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| account | string | no | null | Overrides `IBKR_ACCOUNT` when provided. |
| include_pnl | boolean | no | true | Whether to include P&L fields and totals. |
| as_of | string | no | null | Reserved for future; echoed back if provided. |

## Output (success)

```json
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
      "marketValue": 1723.4,
      "unrealizedPnl": 222.2,
      "realizedPnl": null
    }
  ],
  "totals": {
    "unrealizedPnl": 222.2,
    "realizedPnl": null,
    "netLiquidation": 100000.0
  },
  "notes": [
    "realizedPnl not available via current implementation"
  ]
}
```

## Output (error)

```json
{
  "error": {
    "type": "TWS_CONNECTION_FAILED",
    "message": "failed to connect to TWS at host.docker.internal:7497",
    "retryable": true
  }
}
```
