from mcp_ibkr import server


def test_ibkr_connection_identity_from_env(monkeypatch):
    monkeypatch.setenv("IBKR_HOST", "host.docker.internal")
    monkeypatch.setenv("IBKR_PORT", "7497")
    monkeypatch.setenv("IBKR_CLIENT_ID", "300")
    monkeypatch.setenv("IBKR_ACCOUNT", "DU123")
    monkeypatch.setenv("IBKR_ENABLE_TRADING", "true")
    monkeypatch.setenv("MCP_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "18000")

    connection_identity = server._ibkr_connection_identity()
    startup_context = server._startup_log_context()

    assert connection_identity == {
        "ibkr_host": "host.docker.internal",
        "ibkr_port": 7497,
        "ibkr_client_id": 300,
        "ibkr_account_configured": True,
        "ibkr_trading_enabled": True,
        "ibkr_trading_mode": "paper",
    }
    assert startup_context["mcp_bind_host"] == "0.0.0.0"
    assert startup_context["mcp_port"] == 18000
