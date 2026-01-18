import anyio

from mcp_ibkr import server


class StubClient:
    def __init__(self, positions, pnl_result) -> None:
        self._positions = positions
        self._pnl_result = pnl_result

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def get_positions(self):
        return self._positions

    def get_pnl_best_effort(self, account, positions):
        return self._pnl_result

    def get_managed_accounts(self):
        return ["U1234567"]


def test_schema(monkeypatch, sample_positions, sample_pnl_result):
    stub = StubClient(sample_positions, sample_pnl_result)
    monkeypatch.setattr(server, "create_client", lambda: stub)

    response = anyio.run(server.ibkr_get_portfolio.fn)

    assert "error" not in response
    assert response["account"] == "U1234567"
    assert response["currency"] == "BASE"
    assert isinstance(response["positions"], list)
    assert isinstance(response["totals"], dict)
    assert isinstance(response["notes"], list)

    position = response["positions"][0]
    expected_keys = {
        "symbol",
        "secType",
        "exchange",
        "currency",
        "conId",
        "position",
        "avgCost",
        "marketPrice",
        "marketValue",
        "unrealizedPnl",
        "realizedPnl",
    }
    assert expected_keys.issubset(position.keys())

    totals = response["totals"]
    assert {"unrealizedPnl", "realizedPnl", "netLiquidation"}.issubset(totals.keys())
