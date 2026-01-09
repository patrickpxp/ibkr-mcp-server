from mcp_ibkr import server
from mcp_ibkr.ibkr_client import IBKRConnectionError


class FailingClient:
    def connect(self) -> None:
        raise IBKRConnectionError("tws not reachable")

    def disconnect(self) -> None:
        return None


def test_offline_mode(monkeypatch):
    monkeypatch.setattr(server, "create_client", lambda: FailingClient())

    response = server.ibkr_get_portfolio.fn()

    assert "error" in response
    error = response["error"]
    assert error["type"] == "TWS_CONNECTION_FAILED"
    assert error["retryable"] is True
    assert "tws not reachable" in error["message"]
