# Finding 002 - Blocking Calls on the Event Loop

## Context
`IBKRClient.get_pnl_best_effort()` uses `util.run(...)` to wait for
`reqTickersAsync()` and `accountSummaryAsync()` with a timeout.

## Why It Matters
`util.run(...)` blocks the current event loop. As more tools are added, this
can serialize tool calls and reduce throughput under concurrent usage. It also
makes it harder to introduce truly async tools later.

## Impact
- Concurrent tool calls may queue behind blocking operations.
- Increased latency under load.
- Harder to add streaming or long-running tools that expect async behavior.

## Options
1) Offload blocking calls to a worker thread (`anyio.to_thread.run_sync`).
2) Convert the tool handlers to async and await IBKR operations directly.
3) Use a background task queue for IBKR requests and return task handles.

## Suggested Next Steps
- Decide on a concurrency model for future tools.
- If keeping sync handlers, wrap IBKR calls in `to_thread.run_sync`.
- Add a concurrency test that runs multiple tool calls in parallel.
