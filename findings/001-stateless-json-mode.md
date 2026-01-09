# Finding 001 - Stateless JSON MCP Mode Limits Streaming and Sessions

## Context
The MCP server is configured with `json_response=True` and `stateless_http=True`
in `mcp-ibkr/src/mcp_ibkr/server.py`. This makes `/mcp` respond with JSON for
each request and avoids session IDs.

## Why It Matters
Stateless JSON responses are convenient for simple tool calls, but they disable
Streamable HTTP/SSE behavior and session state. If future tools need streaming
progress, resumable SSE, or session-scoped data (e.g., long-running tasks, tool
pipelines, or server-side caches), the current mode will block those features.

## Impact
- No SSE streaming or progress updates.
- No session-scoped state between tool calls.
- Harder to support task-based tools or interactive flows later.

## Options
1) Keep stateless JSON as the default and add an env switch to enable
   streamable HTTP when needed.
2) Make streamable HTTP the default and allow JSON/stateless for local/dev use.
3) Run two endpoints (one stateless JSON, one streamable) if both are needed.

## Suggested Next Steps
- Add env flags such as `MCP_JSON_RESPONSE` and `MCP_STATELESS_HTTP`.
- Document when to use each mode.
- Add a small integration test that verifies both modes work if enabled.
