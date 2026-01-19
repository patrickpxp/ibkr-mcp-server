import logging

import pytest

from mcp_ibkr.ibkr_client import IBKRClient, IBKRConnectionError
from mcp_ibkr.models import PositionSnapshot


class _StubIB:
    def __init__(self) -> None:
        self._positions = []
        self._connected = False

    def connect(self, *args, **kwargs):
        return True

    def disconnect(self):
        raise RuntimeError("disconnect failed")

    def isConnected(self):
        return self._connected

    def positions(self):
        return self._positions

    def reqTickersAsync(self, *contracts):
        return ("tickers", contracts)

    def accountSummaryAsync(self, *args, **kwargs):
        return ("summary", args, kwargs)


def test_connect_raises_when_ib_connect_returns_false(monkeypatch):
    client = IBKRClient(host="127.0.0.1", port=7497, client_id=1, timeout_seconds=1)

    monkeypatch.setattr("mcp_ibkr.ibkr_client.util.startLoop", lambda: None)
    monkeypatch.setattr(client.ib, "connect", lambda *args, **kwargs: False)

    with pytest.raises(IBKRConnectionError):
        client.connect()


def test_connect_raises_when_ib_connect_raises(monkeypatch):
    client = IBKRClient(host="127.0.0.1", port=7497, client_id=1, timeout_seconds=1)

    def boom(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr("mcp_ibkr.ibkr_client.util.startLoop", lambda: None)
    monkeypatch.setattr(client.ib, "connect", boom)

    with pytest.raises(IBKRConnectionError):
        client.connect()


def test_disconnect_swallows_errors(monkeypatch, caplog):
    client = IBKRClient(host="127.0.0.1", port=7497, client_id=1, timeout_seconds=1)
    client.ib = _StubIB()
    client.ib._connected = True

    with caplog.at_level(logging.WARNING):
        client.disconnect()

    assert any("disconnect failed" in record.message for record in caplog.records)


def test_get_positions_maps_contract_fields():
    client = IBKRClient(host="127.0.0.1", port=7497, client_id=1, timeout_seconds=1)
    client.ib = _StubIB()

    class Contract:
        symbol = "AAPL"
        secType = "STK"
        exchange = ""
        primaryExchange = "NASDAQ"
        currency = "USD"
        conId = 123

    class Position:
        contract = Contract()
        position = 10
        avgCost = 150.25

    client.ib._positions = [Position()]

    results = client.get_positions()

    assert len(results) == 1
    snapshot = results[0]
    assert isinstance(snapshot, PositionSnapshot)
    assert snapshot.symbol == "AAPL"
    assert snapshot.sec_type == "STK"
    assert snapshot.exchange == "NASDAQ"
    assert snapshot.currency == "USD"
    assert snapshot.con_id == 123
    assert snapshot.position == 10.0
    assert snapshot.avg_cost == 150.25


def test_get_pnl_best_effort_notes_missing_market_data(monkeypatch):
    client = IBKRClient(host="127.0.0.1", port=7497, client_id=1, timeout_seconds=1)
    client.ib = _StubIB()

    class Contract:
        conId = 123
        symbol = "AAPL"
        secType = "STK"
        exchange = "SMART"
        currency = "USD"

    snapshot = PositionSnapshot(
        symbol="AAPL",
        sec_type="STK",
        exchange="SMART",
        currency="USD",
        con_id=123,
        position=10.0,
        avg_cost=150.0,
        contract=Contract(),
    )

    class Ticker:
        contract = Contract()
        last = None
        close = None

        def marketPrice(self):
            return None

    calls = {"count": 0}

    def fake_run(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return [Ticker()]
        return []

    monkeypatch.setattr("mcp_ibkr.ibkr_client.util.run", fake_run)

    result = client.get_pnl_best_effort("U123", [snapshot])

    assert any("market data unavailable" in note for note in result.notes)
    assert result.positions[0].marketPrice is None
    assert result.totals.unrealizedPnl is None


def test_get_pnl_best_effort_notes_account_summary_failure(monkeypatch):
    client = IBKRClient(host="127.0.0.1", port=7497, client_id=1, timeout_seconds=1)
    client.ib = _StubIB()

    class Contract:
        conId = 123
        symbol = "AAPL"
        secType = "STK"
        exchange = "SMART"
        currency = "USD"

    snapshot = PositionSnapshot(
        symbol="AAPL",
        sec_type="STK",
        exchange="SMART",
        currency="USD",
        con_id=123,
        position=10.0,
        avg_cost=150.0,
        contract=Contract(),
    )

    class Ticker:
        contract = Contract()
        last = 170.0
        close = 169.0

    calls = {"count": 0}

    def fake_run(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return [Ticker()]
        raise RuntimeError("summary down")

    monkeypatch.setattr("mcp_ibkr.ibkr_client.util.run", fake_run)

    result = client.get_pnl_best_effort("U123", [snapshot])

    assert any("account summary unavailable" in note for note in result.notes)
    assert result.totals.netLiquidation is None
