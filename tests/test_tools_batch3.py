import anyio

from mcp_ibkr import server


class StubOrderState:
    status = "PreSubmitted"
    initMarginBefore = "1000"
    maintMarginBefore = "900"
    equityWithLoanBefore = "5000"
    initMarginChange = "100"
    maintMarginChange = "80"
    equityWithLoanChange = "-100"
    initMarginAfter = "1100"
    maintMarginAfter = "980"
    equityWithLoanAfter = "4900"
    commission = 1.25
    minCommission = 1.0
    maxCommission = 1.5
    commissionCurrency = "USD"
    warningText = ""
    completedTime = ""
    completedStatus = ""


class StubContract:
    def __init__(self):
        self.conId = 265598
        self.symbol = "AAPL"
        self.secType = "STK"
        self.exchange = "SMART"
        self.currency = "USD"
        self.primaryExchange = "NASDAQ"


class StubFill:
    def __init__(self):
        self.time = "2024-01-02T15:00:00"
        self.execution = {"execId": "1"}


class StubLog:
    def __init__(self, status="Submitted"):
        self.time = "2024-01-02T15:00:00"
        self.status = status
        self.message = ""


class StubOrderStatus:
    def __init__(self, status="Submitted"):
        self.status = status
        self.orderId = 1
        self.filled = 0
        self.remaining = 10
        self.avgFillPrice = 0


class StubTrade:
    def __init__(self, order, status="Submitted"):
        self.contract = StubContract()
        self.order = order
        self.orderStatus = StubOrderStatus(status)
        self.fills = [StubFill()]
        self.log = [StubLog(status)]
        self.advancedError = ""

    def isDone(self):
        return False


class StubOrder:
    def __init__(
        self,
        order_id,
        action="BUY",
        total_quantity=10,
        order_type="LMT",
        transmit=False,
    ):
        self.orderId = order_id
        self.action = action
        self.totalQuantity = total_quantity
        self.orderType = order_type
        self.transmit = transmit


class StubClient:
    def __init__(self):
        self.place_calls = 0
        self.last_order = None
        self.last_bracket_transmit = None
        self.last_oca_transmit = None
        self.global_cancel_calls = 0

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def preview_order(self, contract, order):
        return StubOrderState(), []

    def place_order(self, contract, order):
        self.place_calls += 1
        self.last_order = order
        return StubTrade(order), []

    def cancel_order_by_id(self, order_id):
        order = StubOrder(order_id=order_id, transmit=False)
        return StubTrade(order, status="PendingCancel"), []

    def global_cancel(self):
        self.global_cancel_calls += 1

    def place_bracket_order(
        self,
        contract,
        action,
        quantity,
        limit_price,
        take_profit_price,
        stop_loss_price,
        transmit=False,
        order_kwargs=None,
    ):
        self.last_bracket_transmit = transmit
        orders = []
        for order_id in [101, 102, 103]:
            order = StubOrder(
                order_id=order_id,
                action=action,
                total_quantity=quantity,
                order_type="LMT",
                transmit=transmit,
            )
            orders.append(StubTrade(order))
        return orders, []

    def apply_oca_group_and_place(self, entries, oca_group, oca_type, transmit=False):
        self.last_oca_transmit = transmit
        trades = []
        for index, (_, order) in enumerate(entries, start=1):
            order.orderId = index
            trades.append(StubTrade(order))
        return trades, []

    def exercise_options(
        self,
        contract,
        exercise_action,
        exercise_quantity,
        account,
        override,
    ):
        return "submitted", []

    def get_managed_accounts(self):
        return ["U1234567"]


def _use_stub(monkeypatch):
    stub = StubClient()
    monkeypatch.setattr(server, "create_client", lambda: stub)
    return stub


def test_preview_order_tool(monkeypatch):
    _use_stub(monkeypatch)
    response = anyio.run(
        server.ibkr_preview_order.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        {"action": "BUY", "totalQuantity": 10, "orderType": "LMT", "lmtPrice": 170.0},
    )
    assert response["orderState"]["status"] == "PreSubmitted"


def test_place_order_defaults_to_dry_run(monkeypatch):
    stub = _use_stub(monkeypatch)
    response = anyio.run(
        server.ibkr_place_order.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        {"action": "BUY", "totalQuantity": 10, "orderType": "LMT", "lmtPrice": 170.0},
    )
    assert response["trade"] is None
    assert stub.place_calls == 0
    assert any("dry_run=true" in note for note in response["notes"])


def test_place_order_enforces_trading_gate(monkeypatch):
    _use_stub(monkeypatch)
    monkeypatch.setenv("IBKR_ENABLE_TRADING", "false")
    response = anyio.run(
        server.ibkr_place_order.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        {"action": "BUY", "totalQuantity": 10, "orderType": "LMT", "lmtPrice": 170.0},
        True,
        False,
        False,
    )
    assert response["error"]["type"] == "TRADING_DISABLED"


def test_place_order_execute_uses_transmit_default_false(monkeypatch):
    stub = _use_stub(monkeypatch)
    monkeypatch.setenv("IBKR_ENABLE_TRADING", "true")
    response = anyio.run(
        server.ibkr_place_order.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        {"action": "BUY", "totalQuantity": 10, "orderType": "LMT", "lmtPrice": 170.0},
        True,
        False,
        False,
    )
    assert response["trade"]["order"]["transmit"] is False
    assert stub.last_order.transmit is False


def test_cancel_order_requires_confirm(monkeypatch):
    _use_stub(monkeypatch)
    monkeypatch.setenv("IBKR_ENABLE_TRADING", "true")
    response = anyio.run(server.ibkr_cancel_order.fn, 12345, False)
    assert response["error"]["type"] == "CONFIRM_REQUIRED"


def test_bracket_order_execute_transmit_default_false(monkeypatch):
    stub = _use_stub(monkeypatch)
    monkeypatch.setenv("IBKR_ENABLE_TRADING", "true")
    response = anyio.run(
        server.ibkr_bracket_order.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        "BUY",
        10.0,
        170.0,
        175.0,
        165.0,
        True,
        False,
        False,
    )
    assert response["orderIds"] == [101, 102, 103]
    assert stub.last_bracket_transmit is False


def test_oca_group_execute_transmit_default_false(monkeypatch):
    stub = _use_stub(monkeypatch)
    monkeypatch.setenv("IBKR_ENABLE_TRADING", "true")
    response = anyio.run(
        server.ibkr_oca_group.fn,
        [
            {
                "contract": {
                    "symbol": "AAPL",
                    "secType": "STK",
                    "exchange": "SMART",
                    "currency": "USD",
                },
                "order": {
                    "action": "BUY",
                    "totalQuantity": 10,
                    "orderType": "LMT",
                    "lmtPrice": 170.0,
                },
            }
        ],
        "group-1",
        1,
        True,
        False,
        False,
    )
    assert response["ocaGroup"] == "group-1"
    assert response["orderIds"] == [1]
    assert stub.last_oca_transmit is False


def test_exercise_options_requires_confirm(monkeypatch):
    _use_stub(monkeypatch)
    monkeypatch.setenv("IBKR_ENABLE_TRADING", "true")
    response = anyio.run(
        server.ibkr_exercise_options.fn,
        {"symbol": "AAPL", "secType": "OPT", "exchange": "SMART", "currency": "USD"},
        1,
        1,
        "U1234567",
        0,
        False,
    )
    assert response["error"]["type"] == "CONFIRM_REQUIRED"
