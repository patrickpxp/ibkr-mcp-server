from fastapi.testclient import TestClient
import anyio

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
    assert any(
        tool["name"] == "ibkr_get_portfolio"
        for tool in payload["result"]["tools"]
    )


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
