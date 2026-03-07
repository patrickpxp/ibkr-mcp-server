import datetime

from mcp_ibkr import server


class StubCommissionReport:
    def __init__(self, commission=1.25, currency="USD", realized_pnl=12.5):
        self.commission = commission
        self.currency = currency
        self.realizedPNL = realized_pnl


class StubContract:
    def __init__(self, symbol="AAPL"):
        self.conId = 265598
        self.symbol = symbol
        self.secType = "STK"
        self.exchange = "SMART"
        self.currency = "USD"
        self.primaryExchange = "NASDAQ"


class StubExecution:
    def __init__(
        self,
        exec_id,
        order_id,
        perm_id,
        side,
        shares,
        price,
        exchange="NASDAQ",
        account="U123",
    ):
        self.execId = exec_id
        self.orderId = order_id
        self.permId = perm_id
        self.side = side
        self.shares = shares
        self.price = price
        self.exchange = exchange
        self.acctNumber = account


class StubFill:
    def __init__(self, symbol, exec_id, timestamp, side, shares, price):
        self.contract = StubContract(symbol=symbol)
        self.execution = StubExecution(exec_id, 1, 2, side, shares, price)
        self.time = timestamp
        self.commissionReport = StubCommissionReport()


class StubClient:
    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def get_executions(self, exec_filter):
        return [
            StubFill("AAPL", "old", datetime.datetime(2024, 1, 2, 15, 4, 5), "BOT", 10, 172.34),
            StubFill("AAPL", "new", datetime.datetime(2024, 1, 3, 15, 4, 5), "SLD", 5, 180.0),
        ]


def _use_stub(monkeypatch):
    monkeypatch.setattr(server, "create_client", lambda: StubClient())


def _run_tool_sync(tool_fn, *args):
    sync_fn = getattr(server, f"_{tool_fn.__name__}_sync")
    return sync_fn(*args)


def test_transactions_tool_returns_enriched_history(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_transactions.fn,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        10,
    )

    assert response["transactions"]
    assert response["transactions"][0]["execId"] == "new"
    assert response["transactions"][0]["grossAmount"] == 900.0
    assert response["transactions"][0]["commission"] == 1.25
    assert response["transactions"][0]["netAmount"] == 898.75
    assert response["transactions"][1]["netAmount"] == -1724.65


def test_transactions_tool_filters_time_range_and_limit(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_transactions.fn,
        None,
        None,
        None,
        None,
        None,
        "2024-01-03T00:00:00",
        "2024-01-03T23:59:59",
        1,
    )

    assert [item["execId"] for item in response["transactions"]] == ["new"]
    assert all("truncated" not in note for note in response["notes"])


def test_transactions_tool_rejects_invalid_date_range(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_transactions.fn,
        None,
        None,
        None,
        None,
        None,
        "2024-01-04T00:00:00",
        "2024-01-03T00:00:00",
        10,
    )

    assert response["error"]["type"] == "INVALID_ARGUMENT"
