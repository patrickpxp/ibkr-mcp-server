import datetime

from mcp_ibkr import server


class StubContract:
    def __init__(self):
        self.conId = 265598
        self.symbol = "AAPL"
        self.secType = "STK"
        self.exchange = "SMART"
        self.currency = "USD"
        self.primaryExchange = "NASDAQ"


class StubBar:
    def __init__(self):
        self.date = datetime.datetime(2024, 1, 2, 15, 0, 0)
        self.open = 170.0
        self.high = 172.0
        self.low = 169.5
        self.close = 171.5
        self.volume = 1000
        self.average = 171.0
        self.barCount = 12


class StubHistoricalTick:
    def __init__(self):
        self.time = datetime.datetime(2024, 1, 2, 15, 0, 1)
        self.price = 171.55
        self.size = 10


class StubHistoricalTickBidAsk:
    def __init__(self):
        self.time = datetime.datetime(2024, 1, 2, 15, 0, 2)
        self.priceBid = 171.5
        self.priceAsk = 171.6
        self.sizeBid = 5
        self.sizeAsk = 7


class StubNewsProvider:
    code = "BZ"
    name = "Benzinga"


class StubNewsItem:
    def __init__(self):
        self.time = datetime.datetime(2024, 1, 2, 15, 10, 0)
        self.providerCode = "BZ"
        self.articleId = "123"
        self.headline = "Apple headlines"


class StubOptionChain:
    def __init__(self):
        self.exchange = "SMART"
        self.underlyingConId = 265598
        self.tradingClass = "AAPL"
        self.multiplier = "100"
        self.expirations = ["20250117"]
        self.strikes = [150.0, 160.0]


class StubDOMLevel:
    def __init__(self, price, size, maker):
        self.price = price
        self.size = size
        self.marketMaker = maker


class StubContractDetails:
    def __init__(self):
        self.contract = StubContract()
        self.marketName = "NMS"
        self.longName = "Apple Inc"


class StubScanData:
    def __init__(self):
        self.rank = 1
        self.contractDetails = StubContractDetails()
        self.distance = "1"
        self.benchmark = "2"
        self.projection = "3"
        self.legsStr = ""


class StubQualifiedContract:
    conId = 265598


class StubClient:
    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def get_historical_bars(self, contract, end_date_time, duration_str, bar_size_setting, what_to_show, use_rth, format_date):
        return [StubBar()], []

    def get_historical_ticks(self, contract, start_date_time, end_date_time, number_of_ticks, what_to_show, use_rth, ignore_size=False):
        return [StubHistoricalTick(), StubHistoricalTickBidAsk()], []

    def get_head_timestamp(self, contract, what_to_show, use_rth, format_date):
        return datetime.datetime(2020, 1, 1, 0, 0, 0), []

    def get_market_depth_snapshot(self, contract, num_rows=5, is_smart_depth=False):
        return [StubDOMLevel(171.4, 10, "MM1")], [StubDOMLevel(171.6, 12, "MM2")], []

    def get_option_chain(self, underlying_symbol, exchange, sec_type, underlying_con_id):
        return [StubOptionChain()]

    def get_news_providers(self):
        return [StubNewsProvider()]

    def qualify_contract(self, contract):
        return StubQualifiedContract(), []

    def get_historical_news(self, con_id, provider_codes, start_time, end_time, total_results):
        return [StubNewsItem()]

    def get_fundamental_data(self, contract, report_type):
        return "<Fundamentals></Fundamentals>", []

    def get_wsh_metadata(self):
        return '{"event_types":[{"code":"wshe_ed","name":"Earnings Dates"}],"filters":[{"code":"watchlist"}]}'

    def get_wsh_event_data(self, request):
        return '{"data":[{"event_type":"wshe_ed","title":"Apple earnings","event_date":"2024-05-02","event_time":"AMC"}]}'

    def get_scanner_params(self):
        return "<scanner></scanner>"

    def get_news_article(self, provider_code, article_id):
        class _Article:
            articleType = 1
            articleText = "Body text"

        return _Article()

    def run_scanner(self, subscription):
        return [StubScanData()]


def _use_stub(monkeypatch):
    monkeypatch.setattr(server, "create_client", lambda: StubClient())


def _run_tool_sync(tool_fn, *args):
    sync_fn = getattr(server, f"_{tool_fn.__name__}_sync")
    return sync_fn(*args)


def test_historical_bars_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_historical_bars.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        "20240102 16:00:00",
        "1 D",
        "1 hour",
        "TRADES",
        True,
        1,
    )

    assert response["bars"]
    assert response["bars"][0]["open"] == 170.0


def test_historical_ticks_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_historical_ticks.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        "20240102 15:00:00",
        "",
        2,
        "TRADES",
        True,
        False,
    )

    assert response["ticks"]
    assert response["ticks"][0]["price"] == 171.55
    assert response["ticks"][1]["priceBid"] == 171.5


def test_head_timestamp_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_head_timestamp.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        "TRADES",
        True,
        1,
    )

    assert response["headTimestamp"]


def test_market_depth_snapshot_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_market_depth_snapshot.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        5,
        False,
    )

    assert response["bids"]
    assert response["asks"]
    assert response["bids"][0]["price"] == 171.4


def test_option_chain_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_option_chain.fn,
        "AAPL",
        "SMART",
        "STK",
        265598,
    )

    assert response["chains"]
    assert response["chains"][0]["exchange"] == "SMART"
    assert any("metadata only" in note for note in response["notes"])


def test_news_providers_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(server.ibkr_get_news_providers.fn)

    assert response["providers"]
    assert response["providers"][0]["code"] == "BZ"


def test_historical_news_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_historical_news.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        "BZ",
        "20240101 00:00:00",
        "20240102 00:00:00",
        10,
    )

    assert response["items"]
    assert response["items"][0]["headline"] == "Apple headlines"


def test_historical_news_tool_missing_entitlement(monkeypatch):
    class NoProviderClient(StubClient):
        def get_news_providers(self):
            return []

    monkeypatch.setattr(server, "create_client", lambda: NoProviderClient())

    response = _run_tool_sync(
        server.ibkr_get_historical_news.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        "BZ",
        "20240101 00:00:00",
        "20240102 00:00:00",
        10,
    )

    assert response["items"] == []
    assert any("not subscribed" in note for note in response["notes"])


def test_fundamental_data_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_fundamental_data.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        "ReportSnapshot",
    )

    assert response["report"]
    assert "Fundamentals" in response["report"]


def test_wsh_metadata_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(server.ibkr_get_wsh_metadata.fn)

    assert response["metadata"]
    assert response["metadata"]["event_types"][0]["code"] == "wshe_ed"



def test_wsh_earnings_calendar_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_wsh_earnings_calendar.fn,
        {"symbol": "AAPL", "secType": "STK", "exchange": "SMART", "currency": "USD"},
        "20240501",
        "20240531",
        5,
    )

    assert response["conId"] == 265598
    assert response["eventTypeCode"] == "wshe_ed"
    assert response["events"]["data"][0]["event_date"] == "2024-05-02"



def test_scanner_params_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(server.ibkr_get_scanner_params.fn)

    assert response["params"]
    assert "scanner" in response["params"]


def test_run_scanner_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_run_scanner.fn,
        {"instrument": "STK", "locationCode": "STK.US", "scanCode": "TOP_PERC_GAIN"},
    )

    assert response["results"]
    assert response["results"][0]["rank"] == 1


def test_news_article_tool(monkeypatch):
    _use_stub(monkeypatch)

    response = _run_tool_sync(
        server.ibkr_get_news_article.fn,
        "BZ",
        "123",
    )

    assert response["articleType"] == 1
    assert response["articleText"] == "Body text"
