from fastapi.testclient import TestClient
import anyio

import mcp.types as mcp_types
from mcp_ibkr import server
from mcp_ibkr.ibkr_client import IBKRConnectionError


class StubClient:
    def __init__(self, positions, pnl_result) -> None:
        self._positions = positions
        self._pnl_result = pnl_result
        self.disconnected = False

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        self.disconnected = True

    def get_positions(self):
        return self._positions

    def get_pnl_best_effort(self, account, positions):
        return self._pnl_result

    def get_managed_accounts(self):
        return ["U1234567"]


class PnlForbiddenClient:
    def __init__(self, positions) -> None:
        self._positions = positions

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def get_positions(self):
        return self._positions

    def get_pnl_best_effort(self, account, positions):
        raise AssertionError("get_pnl_best_effort should not be called")

    def get_managed_accounts(self):
        return ["U1234567"]


def _post_mcp(client, payload):
    return client.post(
        "/mcp",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json=payload,
    )


def test_tools_list_accepts_json_only():
    app = server.create_app()
    with TestClient(app) as client:
        response = _post_mcp(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["tools"]
    tool_names = {tool["name"] for tool in payload["result"]["tools"]}
    expected = {
        "ibkr_get_portfolio",
        "ibkr_get_account_summary",
        "ibkr_get_account_values",
        "ibkr_get_open_orders",
        "ibkr_get_executions",
        "ibkr_search_symbols",
        "ibkr_get_contract_details",
        "ibkr_get_market_data_snapshot",
        "ibkr_get_historical_bars",
        "ibkr_get_historical_ticks",
        "ibkr_get_head_timestamp",
        "ibkr_get_market_depth_snapshot",
        "ibkr_get_option_chain",
        "ibkr_get_news_providers",
        "ibkr_get_historical_news",
        "ibkr_get_news_article",
        "ibkr_get_fundamental_data",
        "ibkr_get_scanner_params",
        "ibkr_run_scanner",
    }
    assert expected.issubset(tool_names)


def test_tools_list_includes_metadata_and_schemas():
    app = server.create_app()
    with TestClient(app) as client:
        response = _post_mcp(
            client,
            {"jsonrpc": "2.0", "id": 10, "method": "tools/list", "params": {}},
        )

    assert response.status_code == 200
    payload = response.json()
    mcp_types.ListToolsResult.model_validate(payload["result"])
    tools = payload["result"]["tools"]
    portfolio_tool = next(tool for tool in tools if tool["name"] == "ibkr_get_portfolio")

    assert portfolio_tool["title"]
    assert portfolio_tool["description"]
    assert portfolio_tool["inputSchema"]["properties"]["account"]["description"]
    assert portfolio_tool["outputSchema"]["type"] == "object"


def test_tools_call_returns_structured_content(monkeypatch, sample_positions, sample_pnl_result):
    stub = StubClient(sample_positions, sample_pnl_result)
    monkeypatch.setattr(server, "create_client", lambda: stub)

    app = server.create_app()
    with TestClient(app) as client:
        response = _post_mcp(
            client,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "ibkr_get_portfolio", "arguments": {}},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    result = payload["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["account"] == "U1234567"
    assert result["structuredContent"]["positions"]


def test_tools_call_error_sets_is_error(monkeypatch):
    class FailingClient:
        def connect(self) -> None:
            raise IBKRConnectionError("tws not reachable")

        def disconnect(self) -> None:
            return None

    monkeypatch.setattr(server, "create_client", lambda: FailingClient())

    app = server.create_app()
    with TestClient(app) as client:
        response = _post_mcp(
            client,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "ibkr_get_portfolio", "arguments": {}},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    mcp_types.CallToolResult.model_validate(payload["result"])
    result = payload["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["type"] == "TWS_CONNECTION_FAILED"

def test_tools_call_include_pnl_false_skips_pnl(monkeypatch, sample_positions):
    stub = PnlForbiddenClient(sample_positions)
    monkeypatch.setattr(server, "create_client", lambda: stub)

    app = server.create_app()
    with TestClient(app) as client:
        response = _post_mcp(
            client,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "ibkr_get_portfolio",
                    "arguments": {"include_pnl": False},
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    result = payload["result"]["structuredContent"]
    assert result["positions"]


def test_disconnect_called_on_connect_error(monkeypatch):
    class FailingClient:
        def __init__(self) -> None:
            self.disconnected = False

        def connect(self) -> None:
            raise IBKRConnectionError("tws not reachable")

        def disconnect(self) -> None:
            self.disconnected = True

    client = FailingClient()
    monkeypatch.setattr(server, "create_client", lambda: client)

    response = anyio.run(server.ibkr_get_portfolio.fn)

    assert response["error"]["type"] == "TWS_CONNECTION_FAILED"
    assert client.disconnected is True
