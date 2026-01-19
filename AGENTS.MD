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
