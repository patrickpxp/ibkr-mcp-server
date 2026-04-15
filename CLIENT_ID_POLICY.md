# IBKR Client ID Policy

IBKR `clientId` values must be assigned deterministically.
Do not let multiple concurrent IBKR API clients discover or reuse IDs dynamically.

## Rules

- Concurrent IBKR API clients must never share the same `clientId`.
- Live and paper environments must never share the same `clientId` range.
- Treat each long-lived component as a separate IBKR client with explicit ownership.
- If you run multiple instances of the same component, each instance needs its own fixed ID.

## Recommended Allocation

- Live application clients: `10-49`
- Live MCP server instances: `100-119`
- Paper application clients: `210-249`
- Paper MCP server instances: `300-319`

## This Server

Recommended defaults for this repository:

- Live deployment: `IBKR_CLIENT_ID=100`, `IBKR_PORT=7496`
- Paper deployment: `IBKR_CLIENT_ID=300`, `IBKR_PORT=7497`

## Operational Notes

- The current MCP server serializes IBKR access intentionally. That prevents parallel requests from contending for the same `clientId`.
- Any other application using the same TWS/IB Gateway should use a non-overlapping `clientId`.
- If this server is later upgraded to a shared long-lived connection, keep the same dedicated ID policy.
