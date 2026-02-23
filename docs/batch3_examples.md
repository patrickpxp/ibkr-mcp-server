# Batch 3 Tool Examples

## Preview an order

```bash
curl -s http://localhost:${MCP_PORT:-8000}/mcp \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"tools/call",
    "params":{
      "name":"ibkr_preview_order",
      "arguments":{
        "contract":{"symbol":"AAPL","secType":"STK","exchange":"SMART","currency":"USD"},
        "order":{"action":"BUY","totalQuantity":10,"orderType":"LMT","lmtPrice":170.0}
      }
    }
  }'
```

## Place one order (live)

```bash
curl -s http://localhost:${MCP_PORT:-8000}/mcp \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":2,
    "method":"tools/call",
    "params":{
      "name":"ibkr_place_order",
      "arguments":{
        "contract":{"symbol":"AAPL","secType":"STK","exchange":"SMART","currency":"USD"},
        "order":{"action":"BUY","totalQuantity":10,"orderType":"LMT","lmtPrice":170.0},
        "confirm":true,
        "dry_run":false,
        "transmit":false
      }
    }
  }'
```

## Cancel one order

```bash
curl -s http://localhost:${MCP_PORT:-8000}/mcp \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":3,
    "method":"tools/call",
    "params":{
      "name":"ibkr_cancel_order",
      "arguments":{"orderId":12345,"confirm":true}
    }
  }'
```

