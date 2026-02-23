# Batch 3 Validation (Paper TWS)

Date: 2026-02-23  
Environment:
- MCP server started locally with:
  - `IBKR_HOST=192.168.1.76`
  - `IBKR_PORT=7497`
  - `IBKR_ENABLE_TRADING=true`
  - `MCP_BIND_HOST=127.0.0.1`
- Transport: JSON-RPC over `/mcp`

## Summary

Validated against a live IBKR paper TWS session:
- Tool discovery includes all Batch 3 tools.
- Safety defaults and gates work as designed.
- Placement flows default to safe behavior (`dry_run=true`, `transmit=false`).
- Live placement checks were executed with `transmit=false` (staged only).

## Tool Results

1. `ibkr_preview_order`: PASS
- Returned a structured success payload (`isError=false`).
- `orderState` fields may be null depending on IBKR response/entitlements.

2. `ibkr_place_order`: PASS
- Dry-run path: `isError=false`, no trade submitted.
- Live staged path (`confirm=true`, `dry_run=false`, `transmit=false`): `isError=false`, returned full trade snapshot.

3. `ibkr_cancel_order`: PASS (with expected note)
- Returned success payload.
- For a staged untransmitted order in a stateless request, note can be:
  - `order not found in local trade cache; sending cancel by orderId only`

4. `ibkr_global_cancel`: PASS
- Returned `status=submitted`.

5. `ibkr_bracket_order`: PASS
- Dry-run: no orderIds, clear dry-run notes.
- Live staged: returned 3 orderIds and full trade snapshots.
- All legs had `transmit=false` when requested.

6. `ibkr_oca_group`: PASS
- Dry-run: no orderIds, clear dry-run notes.
- Live staged: returned orderIds and full trade snapshots.
- All legs had `transmit=false` when requested.

7. `ibkr_exercise_options`: PASS (safety gate)
- `confirm=false` returns `CONFIRM_REQUIRED` with `isError=true`.
- No live exercise was executed in validation.

## Fix validated during run

`whatIf` preview initially timed out because IBKR requires `transmit=true` for what-if requests.  
Implementation now sets `transmit=true` internally for preview-only calls while leaving user order placement defaults unchanged (`transmit=false`).

