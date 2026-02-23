# Next Steps: Batch 4 Planning

Status as of 2026-02-23:
- Batch 2: implemented.
- Batch 3: implemented and validated against paper TWS (`192.168.1.76:7497`).

Batch 3 validation notes:
- Safety model is active:
  - `IBKR_ENABLE_TRADING=false` by default.
  - Mutating tools require `confirm=true`.
  - Placement flows default to `dry_run=true` and `transmit=false`.
- Live staged placement checks (`transmit=false`) passed for:
  - `ibkr_place_order`
  - `ibkr_bracket_order`
  - `ibkr_oca_group`
- `ibkr_preview_order` was corrected to force `transmit=true` internally for what-if requests.
- `ibkr_exercise_options` safety gate verified with `confirm=false` -> `CONFIRM_REQUIRED`.

See detailed execution notes in `docs/batch3_validation.md`.

## Batch 4: Order Lifecycle and Operational Hardening

Focus: close practical lifecycle gaps after initial trading tool rollout.

### Proposed scope

1. Add order/trade lookup tools
- `ibkr_get_order_status`
  - Inputs: `orderId` and/or `permId`
  - Output: current status + full trade snapshot
- `ibkr_get_recent_trades`
  - Inputs: optional filters (`account`, `symbol`, `limit`)
  - Output: recent in-session trades

2. Improve cancel reliability for stateless usage
- Track submitted orders in a lightweight server-side cache keyed by `orderId`/`permId`.
- Use cached identifiers to improve cancel behavior across separate tool calls.

3. Normalize output values for API ergonomics
- Convert IBKR unset sentinels (e.g. `1.7976931348623157e308`, `2147483647`) to `null`
  in trade snapshots where appropriate.

4. Strengthen safety controls
- Optional allowlists/limits:
  - `IBKR_ALLOWED_ACCOUNTS`
  - `IBKR_ALLOWED_SYMBOLS`
  - `IBKR_MAX_ORDER_QTY`
- Reject requests early with explicit `INVALID_ARGUMENT` or `POLICY_BLOCKED` errors.

5. Expand tests and docs
- Add integration-like tests for order lifecycle paths (place -> lookup -> cancel).
- Document expected behavior for staged (`transmit=false`) versus transmitted orders.

### Done criteria for Batch 4
- Reliable status retrieval for order ids returned by placement tools.
- Cancel success rates improved for stateless call patterns.
- Trade snapshot payloads no longer expose IBKR unset sentinels to clients.
