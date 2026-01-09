# IBKR MCP Server

Expose IBKR portfolio positions (and best-effort P&L) via MCP to Claude or Codex.
Tested with Interactive Brokers Trader Workstation.
⚠️ Be careful !! You can lose real money if you don't understand what you're doing here !!

## Requirements
- Docker
- TWS or IB Gateway running locally with API access enabled

## Configure TWS
In TWS, enable API access and allow local connections.

![TWS API Settings](https://interactivebrokers.github.io/tws-api/tws_allow_connections.png)

## Installation
```
git clone https://github.com/patrickpxp/ibkr-mcp-server
cd ibkr-mcp-server
```

## Configure
Create `mcp-ibkr/.env` (ignored by git) as needed:
```
IBKR_HOST=host.docker.internal
IBKR_PORT=7497 # paper trading port , use 7496 for live trading
IBKR_CLIENT_ID=123
IBKR_ACCOUNT=
IBKR_TIMEOUT_SECONDS=10
MCP_PORT=8000
TZ=Europe/Madrid
```

## Run
```
cd mcp-ibkr
docker compose up -d --build
```

## Verify
```
curl http://localhost:8000/health
```

## Register MCP Server with Codex
```
codex mcp add ibkr-portfolio \
  --transport http \
  --url http://localhost:8000/mcp
```

## Install the Skill
Copy the provided skill into your Codex skills directory:
```
mkdir -p ~/.codex/skills
cp -R .codex/skills/ibkr-portfolio ~/.codex/skills/
```

## Use
Ask Codex: "get my ibkr portfolio".
