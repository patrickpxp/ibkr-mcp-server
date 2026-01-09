# Finding 003 - Global AsyncIO Patch via util.startLoop()

## Context
`IBKRClient.connect()` calls `util.startLoop()` on every connection. This uses
`nest_asyncio.apply()` to patch the event loop globally.

## Why It Matters
This patch changes asyncio behavior globally and can mask event loop misuse.
As more tools are added, debugging async issues can become harder, especially
if some tools are truly async and others are sync wrappers.

## Impact
- Global event loop patching affects all async code in the process.
- Harder to reason about scheduling and cancellation behavior.
- Potential conflicts if future tools rely on standard asyncio semantics.

## Options
1) Call `util.startLoop()` once at startup instead of per-connection.
2) Guard the patch with a module-level flag to avoid repeated application.
3) Remove the patch once the codebase is fully async-safe.

## Suggested Next Steps
- Apply the patch in `main()` or app startup if still required.
- Add a small unit test to ensure `connect()` does not repeatedly patch.
