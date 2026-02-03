# Agent Notes

## Run Tests

If the virtual environment does not exist yet, create it first:

```
python -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e .[test]
```

If the virtual environment already exists, use it directly from the repo root:

```
PYTHONPATH=src .venv/bin/python -m pytest -q
```

Alternatively, install the package in editable mode and run tests normally:

```
.venv/bin/python -m pip install -e .[test]
.venv/bin/python -m pytest -q
```

## Debug MCP Errors

If a tool call returns an INTERNAL_ERROR, check container logs first:

```
docker logs ibkr-mcp-server-mcp-ibkr-1 --tail 200
```

## Quick Tool Call Test

Use this curl command to call MCP tools with the required headers:

```
curl -s http://localhost:8000/mcp \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ibkr_get_open_orders","arguments":{}}}'
```

## MCP Tool Compliance

When adding or updating tools, keep MCP spec alignment:
- Provide `title`, `description`, and `output_schema` in the `@mcp.tool(...)` decorator.
- Use `Annotated[..., Field(description="...")]` for every tool parameter so `inputSchema` includes descriptions.
- Use `_combined_output_schema(<ResponseModel>)` for output schemas (success + error payloads).
- Return structured responses that match the response models; use `ErrorResponse` for tool-level errors.
- Ensure `tools/call` errors set `isError=true` by returning an `error` object in structured content.
- Update tests in `tests/test_mcp_http.py` when adding tools to keep schema checks current.
