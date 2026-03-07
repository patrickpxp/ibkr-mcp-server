import xml.etree.ElementTree as et

from ib_async.flexreport import FlexError
from mcp_ibkr import server
from mcp_ibkr.statement_client import StatementClient, StatementRequestError


class StubStatementClient:
    def __init__(self, query_id="12345"):
        self.query_id = query_id

    def get_flex_statement(self, query_id=None, format="json"):
        return type(
            "Result",
            (),
            {
                "reference_code": "REF-123",
                "url": "https://example.test/GetStatement?t=token&q=REF-123&v=3",
                "statement": {"FlexQueryResponse": {"QueryName": "DailyActivity"}},
                "notes": ["reference code acquired for query 12345"],
            },
        )()


def test_get_flex_statement_tool(monkeypatch):
    monkeypatch.setattr(server, "create_statement_client", lambda: StubStatementClient())

    response = server._ibkr_get_flex_statement_sync(None, "json")

    assert response["referenceCode"] == "REF-123"
    assert response["queryId"] == "12345"
    assert response["format"] == "json"
    assert response["statement"]["FlexQueryResponse"]["QueryName"] == "DailyActivity"


def test_get_flex_statement_requires_valid_format(monkeypatch):
    monkeypatch.setattr(server, "create_statement_client", lambda: StubStatementClient())

    response = server._ibkr_get_flex_statement_sync(None, "csv")

    assert response["error"]["type"] == "INVALID_ARGUMENT"


def test_get_flex_statement_surfaces_config_errors(monkeypatch):
    monkeypatch.setattr(
        server,
        "create_statement_client",
        lambda: (_ for _ in ()).throw(server.StatementConfigError("missing token")),
    )

    response = server._ibkr_get_flex_statement_sync(None, "json")

    assert response["error"]["type"] == "STATEMENT_CONFIG_ERROR"


def test_statement_client_uses_ib_async_flexreport(monkeypatch):
    class StubFlexReport:
        def __init__(self, token=None, queryId=None, path=None):
            assert token == "token-123"
            assert queryId == "query-123"
            self.data = b"<FlexQueryResponse queryName='DailyActivity'><Trades /></FlexQueryResponse>"
            self.root = et.fromstring(self.data)

    monkeypatch.setattr("mcp_ibkr.statement_client.FlexReport", StubFlexReport)

    client = StatementClient(token="token-123", query_id="query-123")
    result = client.get_flex_statement(format="json")

    assert result.statement["FlexQueryResponse"]["@queryName"] == "DailyActivity"
    assert "ib_async FlexReport" in result.notes[0]


def test_statement_client_maps_flex_errors(monkeypatch):
    class StubFlexReport:
        def __init__(self, token=None, queryId=None, path=None):
            raise FlexError("statement generation in progress")

    monkeypatch.setattr("mcp_ibkr.statement_client.FlexReport", StubFlexReport)

    client = StatementClient(token="token-123", query_id="query-123")

    try:
        client.get_flex_statement(format="json")
    except StatementRequestError as exc:
        assert "statement generation in progress" in str(exc)
        assert exc.retryable is True
    else:
        raise AssertionError("StatementRequestError expected")


def test_statement_client_extracts_cash_activity(monkeypatch):
    class StubFlexReport:
        def __init__(self, token=None, queryId=None, path=None):
            self.data = (
                b"<FlexQueryResponse queryName='DailyActivity'>"
                b"<CashTransactions>"
                b"<CashTransaction reportDate='2024-01-03' description='Deposit' amount='1000.50' currency='USD' accountId='U123' />"
                b"</CashTransactions>"
                b"<ChangeInDividendAccruals>"
                b"<ChangeInDividendAccrual payDate='2024-01-02' description='Dividend accrual' amount='12.34' currency='USD' symbol='AAPL' accountId='U123' />"
                b"</ChangeInDividendAccruals>"
                b"</FlexQueryResponse>"
            )
            self.root = et.fromstring(self.data)

        def topics(self):
            return {"CashTransaction", "ChangeInDividendAccrual"}

        def extract(self, topic, parseNumbers=False):
            if topic == "CashTransaction":
                return [
                    type(
                        "CashTransaction",
                        (),
                        {
                            "reportDate": "2024-01-03",
                            "description": "Deposit",
                            "amount": "1000.50",
                            "currency": "USD",
                            "accountId": "U123",
                        },
                    )()
                ]
            if topic == "ChangeInDividendAccrual":
                return [
                    type(
                        "ChangeInDividendAccrual",
                        (),
                        {
                            "payDate": "2024-01-02",
                            "description": "Dividend accrual",
                            "amount": "12.34",
                            "currency": "USD",
                            "symbol": "AAPL",
                            "accountId": "U123",
                        },
                    )()
                ]
            return []

    monkeypatch.setattr("mcp_ibkr.statement_client.FlexReport", StubFlexReport)

    client = StatementClient(token="token-123", query_id="query-123")
    items, query_id, notes = client.get_cash_activity()

    assert query_id == "query-123"
    assert len(items) == 2
    assert items[0].type == "deposit"
    assert items[0].amount == 1000.50
    assert items[1].type == "dividend"
    assert items[1].symbol == "AAPL"
    assert "cash activity extracted" in notes[0]


def test_get_cash_activity_tool(monkeypatch):
    class StubStatementClient:
        def get_cash_activity(self, query_id=None):
            return (
                [
                    type(
                        "Entry",
                        (),
                        {
                            "date": "2024-01-03",
                            "type": "deposit",
                            "description": "Deposit",
                            "amount": 1000.5,
                            "currency": "USD",
                            "symbol": None,
                            "account_id": "U123",
                            "source_topic": "CashTransaction",
                        },
                    )()
                ],
                "12345",
                ["cash activity extracted from query 12345"],
            )

    monkeypatch.setattr(server, "create_statement_client", lambda: StubStatementClient())

    response = server._ibkr_get_cash_activity_sync(None)

    assert response["queryId"] == "12345"
    assert response["items"][0]["type"] == "deposit"
    assert response["items"][0]["sourceTopic"] == "CashTransaction"


def test_statement_client_extracts_statement_summary(monkeypatch):
    class StubFlexReport:
        def __init__(self, token=None, queryId=None, path=None):
            self.data = b"<FlexQueryResponse queryName='DailyActivity' />"
            self.root = et.fromstring(self.data)

        def topics(self):
            return {
                "CashTransaction",
                "ChangeInDividendAccrual",
                "TradeConfirm",
                "EquitySummaryByReportDateInBase",
                "StatementOfFundsLine",
            }

        def extract(self, topic, parseNumbers=False):
            if topic == "CashTransaction":
                return [
                    type(
                        "CashTransaction",
                        (),
                        {"reportDate": "2024-01-03", "description": "Deposit", "amount": "1000", "currency": "USD"},
                    )(),
                    type(
                        "CashTransaction",
                        (),
                        {"reportDate": "2024-01-04", "description": "Withdrawal", "amount": "-200", "currency": "USD"},
                    )(),
                    type(
                        "CashTransaction",
                        (),
                        {"reportDate": "2024-01-05", "description": "Interest paid", "amount": "4.5", "currency": "USD"},
                    )(),
                    type(
                        "CashTransaction",
                        (),
                        {"reportDate": "2024-01-06", "description": "Monthly fee", "amount": "-3.25", "currency": "USD"},
                    )(),
                ]
            if topic == "ChangeInDividendAccrual":
                return [
                    type(
                        "ChangeInDividendAccrual",
                        (),
                        {
                            "payDate": "2024-01-02",
                            "description": "Dividend accrual",
                            "grossAmount": "12.34",
                            "netAmount": "11.11",
                            "tax": "1.23",
                            "currency": "USD",
                        },
                    )(),
                ]
            if topic == "TradeConfirm":
                return [object(), object(), object()]
            if topic == "StatementOfFundsLine":
                return [
                    type(
                        "StatementOfFundsLine",
                        (),
                        {
                            "activityCode": "DEP",
                            "activityDescription": "Electronic Fund Transfer",
                            "amount": "1000",
                            "reportDate": "2024-01-03",
                        },
                    )(),
                    type(
                        "StatementOfFundsLine",
                        (),
                        {
                            "activityCode": "WDR",
                            "activityDescription": "Withdrawal",
                            "amount": "-200",
                            "reportDate": "2024-01-04",
                        },
                    )(),
                    type(
                        "StatementOfFundsLine",
                        (),
                        {
                            "activityCode": "",
                            "activityDescription": "Monthly Fee",
                            "amount": "-3.25",
                            "reportDate": "2024-01-05",
                        },
                    )(),
                ]
            if topic == "EquitySummaryByReportDateInBase":
                return [
                    type(
                        "EquitySummaryByReportDateInBase",
                        (),
                        {"reportDate": "2024-01-01", "total": "50000", "currency": "USD"},
                    )(),
                    type(
                        "EquitySummaryByReportDateInBase",
                        (),
                        {"reportDate": "2024-01-31", "total": "52000", "currency": "USD"},
                    )(),
                ]
            return []

    monkeypatch.setattr("mcp_ibkr.statement_client.FlexReport", StubFlexReport)

    client = StatementClient(token="token-123", query_id="query-123")
    summary = client.get_statement_summary()

    assert summary.query_id == "query-123"
    assert summary.currency == "USD"
    assert summary.starting_nav == 50000.0
    assert summary.ending_nav == 52000.0
    assert summary.net_deposits == 1000.0
    assert summary.withdrawals == -200.0
    assert summary.dividends == 12.34
    assert summary.withholding_tax == -1.23
    assert summary.interest == 4.5
    assert summary.fees == -3.25
    assert summary.trade_count == 3


def test_get_statement_summary_tool(monkeypatch):
    class StubStatementClient:
        def get_statement_summary(self, query_id=None):
            return type(
                "Summary",
                (),
                {
                    "query_id": "12345",
                    "period": "2024-01-01 to 2024-01-31",
                    "currency": "USD",
                    "starting_nav": 50000.0,
                    "ending_nav": 52000.0,
                    "net_deposits": 1000.0,
                    "withdrawals": -200.0,
                    "dividends": 12.34,
                    "withholding_tax": -1.23,
                    "interest": 4.5,
                    "fees": -3.25,
                    "trade_count": 3,
                    "notes": ["statement summary extracted from query 12345"],
                },
            )()

    monkeypatch.setattr(server, "create_statement_client", lambda: StubStatementClient())

    response = server._ibkr_get_statement_summary_sync(None)

    assert response["queryId"] == "12345"
    assert response["endingNav"] == 52000.0
    assert response["tradeCount"] == 3


def test_statement_client_extracts_dividends(monkeypatch):
    class StubFlexReport:
        def __init__(self, token=None, queryId=None, path=None):
            self.data = b"<FlexQueryResponse queryName='DailyActivity' />"
            self.root = et.fromstring(self.data)

        def topics(self):
            return {"ChangeInDividendAccrual"}

        def extract(self, topic, parseNumbers=False):
            if topic != "ChangeInDividendAccrual":
                return []
            return [
                type(
                    "ChangeInDividendAccrual",
                    (),
                    {
                        "payDate": "2024-01-02",
                        "description": "APPLE INC",
                        "grossAmount": "12.34",
                        "netAmount": "11.11",
                        "tax": "1.23",
                        "currency": "USD",
                        "symbol": "AAPL",
                        "accountId": "U123",
                    },
                )(),
            ]

    monkeypatch.setattr("mcp_ibkr.statement_client.FlexReport", StubFlexReport)

    client = StatementClient(token="token-123", query_id="query-123")
    items, query_id, notes = client.get_dividends()

    assert query_id == "query-123"
    assert len(items) == 1
    assert items[0].symbol == "AAPL"
    assert items[0].amount == 12.34
    assert items[0].withholding_tax == -1.23
    assert "dividends extracted" in notes[0]


def test_get_dividends_tool(monkeypatch):
    class StubStatementClient:
        def get_dividends(self, query_id=None):
            return (
                [
                    type(
                        "DividendEntry",
                        (),
                        {
                            "date": "2024-01-02",
                            "description": "Dividend accrual",
                            "symbol": "AAPL",
                            "amount": 12.34,
                            "withholding_tax": -1.23,
                            "currency": "USD",
                            "account_id": "U123",
                            "source_topic": "ChangeInDividendAccrual",
                        },
                    )()
                ],
                "12345",
                ["dividends extracted from query 12345"],
            )

    monkeypatch.setattr(server, "create_statement_client", lambda: StubStatementClient())

    response = server._ibkr_get_dividends_sync(None)

    assert response["queryId"] == "12345"
    assert response["items"][0]["symbol"] == "AAPL"
    assert response["totalDividends"] == 12.34
    assert response["totalWithholdingTax"] == -1.23


def test_statement_client_extracts_trade_confirmations(monkeypatch):
    class StubFlexReport:
        def __init__(self, token=None, queryId=None, path=None):
            self.data = b"<FlexQueryResponse queryName='DailyActivity' />"
            self.root = et.fromstring(self.data)

        def topics(self):
            return {"TradeConfirm"}

        def extract(self, topic, parseNumbers=False):
            if topic != "TradeConfirm":
                return []
            return [
                type(
                    "TradeConfirm",
                    (),
                    {
                        "dateTime": "2024-01-03T15:00:00",
                        "symbol": "AAPL",
                        "description": "APPLE INC",
                        "buySell": "BUY",
                        "quantity": "10",
                        "tradePrice": "172.34",
                        "proceeds": "-1723.40",
                        "ibCommission": "1.25",
                        "currency": "USD",
                        "accountId": "U123",
                        "tradeID": "T1",
                        "ibOrderID": "O1",
                    },
                )()
            ]

    monkeypatch.setattr("mcp_ibkr.statement_client.FlexReport", StubFlexReport)

    client = StatementClient(token="token-123", query_id="query-123")
    items, query_id, notes = client.get_trade_confirmations()

    assert query_id == "query-123"
    assert len(items) == 1
    assert items[0].symbol == "AAPL"
    assert items[0].side == "BUY"
    assert items[0].quantity == 10.0
    assert items[0].price == 172.34
    assert items[0].commission == 1.25
    assert items[0].trade_id == "T1"
    assert "trade confirmations extracted" in notes[0]


def test_statement_client_trade_confirmations_falls_back_to_trade(monkeypatch):
    class StubFlexReport:
        def __init__(self, token=None, queryId=None, path=None):
            self.data = b"<FlexQueryResponse queryName='DailyActivity' />"
            self.root = et.fromstring(self.data)

        def topics(self):
            return {"Trade"}

        def extract(self, topic, parseNumbers=False):
            if topic != "Trade":
                return []
            return [
                type(
                    "Trade",
                    (),
                    {
                        "tradeDate": "2024-01-03",
                        "symbol": "MSFT",
                        "buySell": "SELL",
                        "quantity": "5",
                        "tradePrice": "400.0",
                        "proceeds": "2000.0",
                        "commission": "1.0",
                        "currency": "USD",
                    },
                )()
            ]

    monkeypatch.setattr("mcp_ibkr.statement_client.FlexReport", StubFlexReport)

    client = StatementClient(token="token-123", query_id="query-123")
    items, _, notes = client.get_trade_confirmations()

    assert len(items) == 1
    assert items[0].source_topic == "Trade"
    assert any("using Trade rows instead" in note for note in notes)


def test_get_trade_confirmations_tool(monkeypatch):
    class StubStatementClient:
        def get_trade_confirmations(self, query_id=None):
            return (
                [
                    type(
                        "TradeConfirmationEntry",
                        (),
                        {
                            "date_time": "2024-01-03T15:00:00",
                            "symbol": "AAPL",
                            "description": "APPLE INC",
                            "side": "BUY",
                            "quantity": 10.0,
                            "price": 172.34,
                            "proceeds": -1723.40,
                            "commission": 1.25,
                            "currency": "USD",
                            "account_id": "U123",
                            "trade_id": "T1",
                            "order_id": "O1",
                            "source_topic": "TradeConfirm",
                        },
                    )()
                ],
                "12345",
                ["trade confirmations extracted from query 12345"],
            )

    monkeypatch.setattr(server, "create_statement_client", lambda: StubStatementClient())

    response = server._ibkr_get_trade_confirmations_sync(None)

    assert response["queryId"] == "12345"
    assert response["items"][0]["symbol"] == "AAPL"
    assert response["items"][0]["sourceTopic"] == "TradeConfirm"


def test_statement_client_extracts_statement_topics(monkeypatch):
    class StubFlexReport:
        def __init__(self, token=None, queryId=None, path=None):
            self.data = b"<FlexQueryResponse queryName='DailyActivity' />"
            self.root = et.fromstring(self.data)

        def topics(self):
            return {"TradeConfirm", "CashTransaction"}

        def extract(self, topic, parseNumbers=False):
            if topic == "TradeConfirm":
                return [object(), object()]
            if topic == "CashTransaction":
                return [object()]
            return []

    monkeypatch.setattr("mcp_ibkr.statement_client.FlexReport", StubFlexReport)

    client = StatementClient(token="token-123", query_id="query-123")
    items, query_id, notes = client.get_statement_topics()

    assert query_id == "query-123"
    assert [(item.topic, item.count) for item in items] == [
        ("CashTransaction", 1),
        ("TradeConfirm", 2),
    ]
    assert "statement topics extracted" in notes[0]


def test_get_statement_topics_tool(monkeypatch):
    class StubStatementClient:
        def get_statement_topics(self, query_id=None):
            return (
                [
                    type("StatementTopicEntry", (), {"topic": "CashTransaction", "count": 1})(),
                    type("StatementTopicEntry", (), {"topic": "TradeConfirm", "count": 2})(),
                ],
                "12345",
                ["statement topics extracted from query 12345"],
            )

    monkeypatch.setattr(server, "create_statement_client", lambda: StubStatementClient())

    response = server._ibkr_get_statement_topics_sync(None)

    assert response["queryId"] == "12345"
    assert response["topics"][0]["topic"] == "CashTransaction"
    assert response["topics"][1]["count"] == 2
