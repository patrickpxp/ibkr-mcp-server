import datetime

from mcp_ibkr import server


class StubAccountValue:
    def __init__(self, tag, value, currency, account, model_code=None):
        self.tag = tag
        self.value = value
        self.currency = currency
        self.account = account
        self.modelCode = model_code


class StubContract:
    def __init__(self):
        self.conId = 265598
        self.symbol = "AAPL"
        self.secType = "STK"
        self.exchange = "SMART"
        self.currency = "USD"
        self.primaryExchange = "NASDAQ"
        self.description = "APPLE INC"


class StubOrder:
    def __init__(self):
        self.orderId = 1
        self.permId = 2
        self.action = "BUY"
        self.totalQuantity = 10
        self.orderType = "LMT"
        self.lmtPrice = 150.25
        self.auxPrice = None
        self.tif = "DAY"
        self.account = "U123"


class StubOrderStatus:
    status = "Submitted"


class StubTrade:
    def __init__(self):
        self.contract = StubContract()
        self.order = StubOrder()
        self.orderStatus = StubOrderStatus()


class StubExecution:
    def __init__(self):
        self.execId = "1"
        self.orderId = 1
        self.permId = 2
        self.side = "BOT"
        self.shares = 10
        self.price = 172.34
        self.exchange = "NASDAQ"
        self.acctNumber = "U123"


class StubFill:
    def __init__(self):
        self.contract = StubContract()
        self.execution = StubExecution()
        self.time = datetime.datetime(2024, 1, 2, 15, 4, 5)


class StubContractDescription:
    def __init__(self):
        self.contract = StubContract()
        self.derivativeSecTypes = ["OPT"]


class StubContractDetails:
    def __init__(self):
        self.contract = StubContract()
        self.longName = "APPLE INC"
        self.marketName = "NMS"
        self.minTick = 0.01
        self.orderTypes = "LMT,MKT"
        self.validExchanges = "SMART,NASDAQ"
        self.timeZoneId = "US/Eastern"
        self.tradingHours = "20240102:0930-20240102:1600"
        self.liquidHours = "20240102:0930-20240102:1600"
        self.industry = "Technology"
        self.category = "Computers"
        self.subcategory = "Consumer"
        self.underConId = 0
        self.underSymbol = ""
        self.underSecType = ""


class StubTicker:
    def __init__(self):
        self.contract = StubContract()
        self.bid = 172.2
        self.ask = 172.4
        self.last = 172.3
        self.close = 171.8

    def marketPrice(self):
        return 172.3


class StubGreeks:
    def __init__(self):
        self.impliedVol = 0.22
        self.delta = 0.41
        self.optPrice = 4.5
        self.pvDividend = 0.0
        self.gamma = 0.03
        self.vega = 0.12
        self.theta = -0.08
        self.undPrice = 172.3


class StubOptionTicker:
    def __init__(self):
        self.contract = StubContract()
        self.contract.secType = "OPT"
        self.contract.symbol = "AAPL"
        self.contract.exchange = "SMART"
        self.contract.currency = "USD"
        self.bid = 4.4
        self.ask = 4.6
        self.last = 4.5
        self.close = 4.2
        self.modelGreeks = StubGreeks()
        self.bidGreeks = None
        self.askGreeks = None
        self.lastGreeks = None

    def marketPrice(self):
        return 4.5


class StubClient:
    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def get_managed_accounts(self):
        return ["U123"]

    def get_account_summary(self, account):
        return [StubAccountValue("NetLiquidation", "100000", "USD", account)]

    def get_account_values(self, account):
        return [StubAccountValue("BuyingPower", "50000", "USD", account, "")]

    def get_open_orders(self, include_all: bool = True):
        return [StubTrade()]

    def get_executions(self, exec_filter):
        return [StubFill()]

    def search_symbols(self, query):
        return [StubContractDescription()]

    def get_contract_details(self, contract):
        return [StubContractDetails()]

    def get_market_data_snapshot(self, contracts, regulatory_snapshot=False):
        return [StubTicker()], []


class StubOptionSnapshotClient(StubClient):
    def get_market_data_snapshot(self, contracts, regulatory_snapshot=False):
        return [StubOptionTicker()], []


class _StubMarketDataTypeIB:
    def __init__(self):
        self.last_market_data_type = None

    def reqMarketDataType(self, value):
        self.last_market_data_type = value


class StubMarketDataTypeClient(StubClient):
    def __init__(self):
        self.ib = _StubMarketDataTypeIB()


def _use_stub(monkeypatch):
    monkeypatch.setattr(server, "create_client", lambda: StubClient())


def _run_tool_sync(tool_fn, *args):
    sync_fn = getattr(server, f"_{tool_fn.__name__}_sync")
    return sync_fn(*args)


def test_account_summary_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(server.ibkr_get_account_summary.fn)

    assert response["account"] == "U123"
    assert response["items"]


def test_account_values_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(server.ibkr_get_account_values.fn)

    assert response["account"] == "U123"
    assert response["items"]


def test_open_orders_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(server.ibkr_get_open_orders.fn)

    assert response["orders"]
    assert response["orders"][0]["orderId"] == 1


def test_executions_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(server.ibkr_get_executions.fn)

    assert response["executions"]
    assert response["executions"][0]["execId"] == "1"


def test_search_symbols_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(server.ibkr_search_symbols.fn, "AAP")

    assert response["matches"]
    assert response["matches"][0]["symbol"] == "AAPL"


def test_contract_details_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_contract_details.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
    )

    assert response["details"]
    assert response["details"][0]["symbol"] == "AAPL"


def test_market_data_snapshot_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_market_data_snapshot.fn,
        [{"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"}],
        False,
    )

    assert response["snapshots"]
    assert response["snapshots"][0]["last"] == 172.3


def test_market_data_snapshot_option_includes_delta(monkeypatch):
    monkeypatch.setattr(server, "create_client", lambda: StubOptionSnapshotClient())

    response = _run_tool_sync(
        server.ibkr_get_market_data_snapshot.fn,
        [
            {
                "symbol": "AAPL",
                "secType": "OPT",
                "exchange": "SMART",
                "currency": "USD",
                "lastTradeDateOrContractMonth": "20250117",
                "strike": 170,
                "right": "C",
            }
        ],
        False,
    )

    assert response["snapshots"]
    snapshot = response["snapshots"][0]
    assert snapshot["secType"] == "OPT"
    assert snapshot["delta"] == 0.41
    assert snapshot["gamma"] == 0.03
    assert snapshot["vega"] == 0.12
    assert snapshot["theta"] == -0.08
    assert snapshot["impliedVol"] == 0.22
    assert snapshot["optPrice"] == 4.5
    assert snapshot["undPrice"] == 172.3
    assert snapshot["greeksSource"] == "model"


def test_market_data_snapshot_sets_market_data_type(monkeypatch):
    client = StubMarketDataTypeClient()
    monkeypatch.setattr(server, "create_client", lambda: client)

    response = _run_tool_sync(
        server.ibkr_get_market_data_snapshot.fn,
        [{"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"}],
        False,
        2,
    )

    assert client.ib.last_market_data_type == 2
    assert any("market data type set to 2" in note for note in response["notes"])
