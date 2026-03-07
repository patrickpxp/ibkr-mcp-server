import os
import xml.etree.ElementTree as et
from dataclasses import dataclass
from typing import Any

import xmltodict
from ib_async.flexreport import FlexError, FlexReport


class StatementConfigError(Exception):
    pass


class StatementRequestError(Exception):
    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class FlexStatementResult:
    reference_code: str
    url: str
    statement: Any
    notes: list[str]


@dataclass
class CashActivityEntry:
    date: str | None
    type: str
    description: str | None
    amount: float | None
    currency: str | None
    symbol: str | None
    account_id: str | None
    source_topic: str


@dataclass
class StatementSummary:
    query_id: str
    period: str | None
    currency: str | None
    starting_nav: float | None
    ending_nav: float | None
    net_deposits: float | None
    withdrawals: float | None
    dividends: float | None
    withholding_tax: float | None
    interest: float | None
    fees: float | None
    trade_count: int | None
    notes: list[str]


@dataclass
class DividendEntry:
    date: str | None
    description: str | None
    symbol: str | None
    amount: float | None
    withholding_tax: float | None
    currency: str | None
    account_id: str | None
    source_topic: str


@dataclass
class TradeConfirmationEntry:
    date_time: str | None
    symbol: str | None
    description: str | None
    side: str | None
    quantity: float | None
    price: float | None
    proceeds: float | None
    commission: float | None
    currency: str | None
    account_id: str | None
    trade_id: str | None
    order_id: str | None
    source_topic: str


@dataclass
class StatementTopicEntry:
    topic: str
    count: int


class StatementClient:
    def __init__(
        self,
        token: str,
        query_id: str | None,
    ) -> None:
        self.token = token
        self.query_id = query_id

    @classmethod
    def from_env(cls) -> "StatementClient":
        token = os.getenv("IBKR_FLEX_TOKEN", "").strip()
        if not token:
            raise StatementConfigError("IBKR_FLEX_TOKEN is required for statement tools")
        return cls(
            token=token,
            query_id=os.getenv("IBKR_FLEX_QUERY_ID", "").strip() or None,
        )

    def get_flex_statement(
        self,
        query_id: str | None = None,
        format: str = "json",
    ) -> FlexStatementResult:
        report, resolved_query_id = self._load_flex_report(query_id)
        statement_xml = report.data.decode("utf-8")
        statement: Any
        normalized_format = format.strip().lower()
        if normalized_format == "xml":
            statement = statement_xml
        else:
            try:
                statement = xmltodict.parse(statement_xml)
            except Exception as exc:
                raise StatementRequestError(
                    f"unable to parse statement payload XML: {exc}",
                    False,
                ) from exc

        reference_code = self._reference_code_from_root(report.root)
        url = self._statement_url(reference_code)
        return FlexStatementResult(
            reference_code=reference_code,
            url=url,
            statement=statement,
            notes=[f"statement retrieved for query {resolved_query_id} via ib_async FlexReport"],
        )

    def get_cash_activity(
        self,
        query_id: str | None = None,
    ) -> tuple[list[CashActivityEntry], str, list[str]]:
        report, resolved_query_id = self._load_flex_report(query_id)
        items, notes = self._extract_cash_activity(report, resolved_query_id)
        return items, resolved_query_id, notes

    def get_statement_summary(
        self,
        query_id: str | None = None,
    ) -> StatementSummary:
        report, resolved_query_id = self._load_flex_report(query_id)
        notes: list[str] = [f"statement summary extracted from query {resolved_query_id}"]
        items, cash_notes = self._extract_cash_activity(report, resolved_query_id)
        notes.extend(cash_notes)
        dividend_items, dividend_notes = self._extract_dividends(report, resolved_query_id)
        if dividend_items:
            notes.append("dividend totals derived from ChangeInDividendAccrual")
        else:
            notes.extend(dividend_notes)

        dividends = self._sum_values(
            item.amount for item in dividend_items if item.amount is not None
        )
        withholding_tax = self._sum_values(
            item.withholding_tax for item in dividend_items if item.withholding_tax is not None
        )
        interest = self._sum_amounts(items, "interest")
        fees = self._sum_amounts(items, "fee")
        net_deposits = self._sum_amounts(items, "deposit")
        withdrawals = self._sum_amounts(items, "withdrawal")

        funds_summary = self._extract_statement_of_funds_summary(report)
        if funds_summary["deposits"] is not None:
            net_deposits = funds_summary["deposits"]
            notes.append("deposit totals derived from StatementOfFundsLine")
        if funds_summary["withdrawals"] is not None:
            withdrawals = funds_summary["withdrawals"]
            notes.append("withdrawal totals derived from StatementOfFundsLine")
        if funds_summary["fees"] is not None:
            fees = funds_summary["fees"]
            notes.append("fee totals derived from StatementOfFundsLine")

        trade_count = None
        if "TradeConfirm" in report.topics():
            trade_count = len(report.extract("TradeConfirm", parseNumbers=False))
        else:
            notes.append("TradeConfirm topic unavailable; tradeCount omitted")

        starting_nav, ending_nav, currency, period = self._extract_equity_summary(report, notes)
        return StatementSummary(
            query_id=resolved_query_id,
            period=period,
            currency=currency,
            starting_nav=starting_nav,
            ending_nav=ending_nav,
            net_deposits=net_deposits,
            withdrawals=withdrawals,
            dividends=dividends,
            withholding_tax=withholding_tax,
            interest=interest,
            fees=fees,
            trade_count=trade_count,
            notes=notes,
        )

    def get_dividends(
        self,
        query_id: str | None = None,
    ) -> tuple[list[DividendEntry], str, list[str]]:
        report, resolved_query_id = self._load_flex_report(query_id)
        items, notes = self._extract_dividends(report, resolved_query_id)
        return items, resolved_query_id, notes

    def get_trade_confirmations(
        self,
        query_id: str | None = None,
    ) -> tuple[list[TradeConfirmationEntry], str, list[str]]:
        report, resolved_query_id = self._load_flex_report(query_id)
        items, notes = self._extract_trade_confirmations(report, resolved_query_id)
        return items, resolved_query_id, notes

    def get_statement_topics(
        self,
        query_id: str | None = None,
    ) -> tuple[list[StatementTopicEntry], str, list[str]]:
        report, resolved_query_id = self._load_flex_report(query_id)
        notes = [f"statement topics extracted from query {resolved_query_id}"]
        items: list[StatementTopicEntry] = []
        for topic in sorted(report.topics()):
            try:
                count = len(report.extract(topic, parseNumbers=False))
            except Exception:
                count = 0
                notes.append(f"topic count unavailable for {topic}")
            items.append(StatementTopicEntry(topic=topic, count=count))
        if not items:
            notes.append("no extractable topics found in Flex report")
        return items, resolved_query_id, notes

    def _load_flex_report(self, query_id: str | None) -> tuple[FlexReport, str]:
        resolved_query_id = (query_id or self.query_id or "").strip()
        if not resolved_query_id:
            raise StatementConfigError(
                "queryId is required; set IBKR_FLEX_QUERY_ID or pass queryId explicitly"
            )

        try:
            report = FlexReport(token=self.token, queryId=resolved_query_id)
        except FlexError as exc:
            message = str(exc)
            retryable = "generation in progress" in message.lower() or "timeout" in message.lower()
            raise StatementRequestError(message, retryable) from exc
        except Exception as exc:
            raise StatementRequestError(str(exc), False) from exc
        return report, resolved_query_id

    def _statement_url(self, reference_code: str) -> str:
        return (
            "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"
            f"?q={reference_code}&t={self.token}"
        )

    @staticmethod
    def _reference_code_from_root(root: et.Element) -> str:
        reference_code = root.attrib.get("queryName") or root.attrib.get("referenceCode") or ""
        if reference_code:
            return reference_code
        # ib_async's FlexReport only retains the final statement XML, so a true reference
        # code may not be available. Return a stable placeholder instead of fabricating one.
        return "unavailable"

    def _extract_cash_activity(
        self,
        report: FlexReport,
        resolved_query_id: str,
    ) -> tuple[list[CashActivityEntry], list[str]]:
        topics = report.topics()
        notes = [f"cash activity extracted from query {resolved_query_id}"]
        candidate_topics = [
            "CashTransaction",
            "ChangeInDividendAccrual",
        ]
        items: list[CashActivityEntry] = []
        for topic in candidate_topics:
            if topic not in topics:
                continue
            for record in report.extract(topic, parseNumbers=False):
                items.append(self._cash_activity_entry(topic, record))
        if not items:
            notes.append("no cash activity topics found in Flex report")
        items.sort(key=lambda item: item.date or "", reverse=True)
        return items, notes

    def _extract_dividends(
        self,
        report: FlexReport,
        resolved_query_id: str,
    ) -> tuple[list[DividendEntry], list[str]]:
        notes = [f"dividends extracted from query {resolved_query_id}"]
        if "ChangeInDividendAccrual" not in report.topics():
            notes.append("ChangeInDividendAccrual topic unavailable; no dividends returned")
            return [], notes

        grouped: dict[tuple[str | None, str | None, str | None, str | None], DividendEntry] = {}
        for record in report.extract("ChangeInDividendAccrual", parseNumbers=False):
            data = self._record_to_dict(record)
            description = self._first_value(
                data,
                "description",
                "activityDescription",
                "type",
                "code",
            )
            symbol = self._first_value(data, "symbol", "underlyingSymbol")
            date = self._first_value(data, "payDate", "reportDate", "date")
            currency = self._first_value(data, "currency", "currencyPrimary", "assetCurrency")
            account_id = self._first_value(data, "accountId", "accountAlias", "account")
            key = (date, symbol, currency, account_id)
            entry = grouped.get(key)
            if entry is None:
                entry = DividendEntry(
                    date=date,
                    description=description,
                    symbol=symbol,
                    amount=None,
                    withholding_tax=None,
                    currency=currency,
                    account_id=account_id,
                    source_topic="ChangeInDividendAccrual",
                )
                grouped[key] = entry
            gross_amount = self._extract_value_by_keys(data, "grossAmount", "amount")
            net_amount = self._extract_value_by_keys(data, "netAmount")
            tax_amount = self._extract_value_by_keys(data, "tax")
            activity_type = self._classify_cash_activity("ChangeInDividendAccrual", description)
            if tax_amount is not None:
                entry.withholding_tax = (entry.withholding_tax or 0.0) - abs(tax_amount)
            elif activity_type == "withholding_tax":
                inferred_amount = self._extract_amount(data)
                entry.withholding_tax = (entry.withholding_tax or 0.0) + (inferred_amount or 0.0)

            if gross_amount is not None:
                entry.amount = (entry.amount or 0.0) + gross_amount
            elif net_amount is not None and entry.withholding_tax is not None:
                entry.amount = (entry.amount or 0.0) + (net_amount - entry.withholding_tax)
            else:
                inferred_amount = self._extract_amount(data)
                if activity_type != "withholding_tax":
                    entry.amount = (entry.amount or 0.0) + (inferred_amount or 0.0)
            if entry.description is None:
                entry.description = description

        items = sorted(grouped.values(), key=lambda item: item.date or "", reverse=True)
        if not items:
            notes.append("no dividend rows found in Flex report")
        return items, notes

    def _extract_trade_confirmations(
        self,
        report: FlexReport,
        resolved_query_id: str,
    ) -> tuple[list[TradeConfirmationEntry], list[str]]:
        notes = [f"trade confirmations extracted from query {resolved_query_id}"]
        source_topic = None
        if "TradeConfirm" in report.topics():
            source_topic = "TradeConfirm"
        elif "Trade" in report.topics():
            source_topic = "Trade"
            notes.append("TradeConfirm topic unavailable; using Trade rows instead")
        else:
            notes.append("TradeConfirm and Trade topics unavailable; no trade confirmations returned")
            return [], notes

        items = [
            self._trade_confirmation_entry(source_topic, record)
            for record in report.extract(source_topic, parseNumbers=False)
        ]
        items.sort(key=lambda item: item.date_time or "", reverse=True)
        if not items:
            notes.append("no trade confirmation rows found in Flex report")
        return items, notes

    @staticmethod
    def _cash_activity_entry(topic: str, record: object) -> CashActivityEntry:
        data = StatementClient._record_to_dict(record)
        description = StatementClient._first_value(
            data,
            "description",
            "activityDescription",
            "type",
            "code",
        )
        amount = StatementClient._extract_amount(data)
        return CashActivityEntry(
            date=StatementClient._first_value(
                data,
                "date",
                "reportDate",
                "payDate",
                "settleDate",
                "transactionDate",
            ),
            type=StatementClient._classify_cash_activity(topic, description),
            description=description,
            amount=amount,
            currency=StatementClient._first_value(
                data,
                "currency",
                "currencyPrimary",
                "assetCurrency",
            ),
            symbol=StatementClient._first_value(
                data,
                "symbol",
                "underlyingSymbol",
            ),
            account_id=StatementClient._first_value(
                data,
                "accountId",
                "accountAlias",
                "account",
            ),
            source_topic=topic,
        )

    @staticmethod
    def _first_value(data: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    @staticmethod
    def _record_to_dict(record: object) -> dict[str, Any]:
        if hasattr(record, "__dict__") and vars(record):
            return dict(vars(record))
        data: dict[str, Any] = {}
        for key in dir(record):
            if key.startswith("_"):
                continue
            value = getattr(record, key)
            if callable(value):
                continue
            data[key] = value
        return data

    @staticmethod
    def _extract_amount(data: dict[str, Any]) -> float | None:
        for key in (
            "amount",
            "netAmount",
            "amountInBase",
            "proceeds",
            "value",
        ):
            value = StatementClient._to_float(data.get(key))
            if value is not None:
                return value
        credit = StatementClient._to_float(data.get("credit"))
        if credit is not None:
            return credit
        debit = StatementClient._to_float(data.get("debit"))
        if debit is not None:
            return -abs(debit)
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _classify_cash_activity(topic: str, description: str | None) -> str:
        normalized_description = (description or "").lower()
        normalized_topic = topic.lower()
        if "dividend" in normalized_topic or "dividend" in normalized_description:
            if "withholding" in normalized_description or "tax" in normalized_description:
                return "withholding_tax"
            return "dividend"
        if "withholding" in normalized_description and "int" in normalized_description:
            return "withholding_tax"
        if "interest" in normalized_description:
            return "interest"
        if "credit int" in normalized_description:
            return "interest"
        if "fee" in normalized_description or "commission" in normalized_description:
            return "fee"
        if "deposit" in normalized_description:
            return "deposit"
        if "withdraw" in normalized_description:
            return "withdrawal"
        return "cash"

    def _trade_confirmation_entry(
        self,
        topic: str,
        record: object,
    ) -> TradeConfirmationEntry:
        data = self._record_to_dict(record)
        return TradeConfirmationEntry(
            date_time=self._first_value(
                data,
                "dateTime",
                "tradeDate",
                "date",
            ),
            symbol=self._first_value(data, "symbol", "underlyingSymbol"),
            description=self._first_value(
                data,
                "description",
                "description1",
                "companyName",
            ),
            side=self._first_value(data, "buySell", "side"),
            quantity=self._extract_value_by_keys(
                data,
                "quantity",
                "shares",
            ),
            price=self._extract_value_by_keys(
                data,
                "tradePrice",
                "price",
            ),
            proceeds=self._extract_value_by_keys(
                data,
                "proceeds",
                "netCash",
                "netAmount",
            ),
            commission=self._extract_value_by_keys(
                data,
                "ibCommission",
                "commission",
            ),
            currency=self._first_value(data, "currency", "currencyPrimary"),
            account_id=self._first_value(data, "accountId", "accountAlias", "account"),
            trade_id=self._first_value(data, "tradeID", "tradeId", "transactionID"),
            order_id=self._first_value(data, "ibOrderID", "orderID", "orderId"),
            source_topic=topic,
        )

    @staticmethod
    def _sum_amounts(items: list[CashActivityEntry], activity_type: str) -> float | None:
        values = [item.amount for item in items if item.type == activity_type and item.amount is not None]
        if not values:
            return None
        return sum(values)

    @staticmethod
    def _sum_values(values) -> float | None:
        collected = [value for value in values if value is not None]
        if not collected:
            return None
        return sum(collected)

    def _extract_equity_summary(
        self,
        report: FlexReport,
        notes: list[str],
    ) -> tuple[float | None, float | None, str | None, str | None]:
        topics = report.topics()
        for topic in ("EquitySummaryInBase", "EquitySummaryByReportDateInBase"):
            if topic not in topics:
                continue
            rows = report.extract(topic, parseNumbers=False)
            if not rows:
                continue
            sorted_rows = sorted(
                (self._record_to_dict(row) for row in rows),
                key=lambda row: self._first_value(row, "reportDate", "date") or "",
            )
            first_row = sorted_rows[0]
            last_row = sorted_rows[-1]
            starting_nav = self._extract_value_by_keys(
                first_row,
                "total",
                "nav",
                "netAssetValue",
                "endingValue",
                "equity",
            )
            ending_nav = self._extract_value_by_keys(
                last_row,
                "total",
                "nav",
                "netAssetValue",
                "endingValue",
                "equity",
            )
            currency = self._first_value(first_row, "currency", "reportCurrency")
            start_date = self._first_value(first_row, "reportDate", "date")
            end_date = self._first_value(last_row, "reportDate", "date")
            period = f"{start_date} to {end_date}" if start_date and end_date else start_date or end_date
            return starting_nav, ending_nav, currency, period
        notes.append("equity summary topic unavailable; NAV fields omitted")
        return None, None, None, None

    def _extract_statement_of_funds_summary(
        self,
        report: FlexReport,
    ) -> dict[str, float | None]:
        if "StatementOfFundsLine" not in report.topics():
            return {"deposits": None, "withdrawals": None, "fees": None}

        deposits: list[float] = []
        withdrawals: list[float] = []
        fees: list[float] = []
        for row in report.extract("StatementOfFundsLine", parseNumbers=False):
            data = self._record_to_dict(row)
            amount = self._extract_value_by_keys(data, "amount")
            if amount is None:
                continue
            activity_code = (self._first_value(data, "activityCode") or "").upper()
            description = " ".join(
                filter(
                    None,
                    [
                        self._first_value(data, "activityDescription"),
                        self._first_value(data, "description"),
                        self._first_value(data, "subCategory"),
                    ],
                )
            ).lower()
            if activity_code in {"DEP"} or "fund transfer" in description or "cash receipts" in description:
                if amount > 0:
                    deposits.append(amount)
                elif amount < 0:
                    withdrawals.append(amount)
                continue
            if activity_code in {"WDR"} or "withdraw" in description:
                if amount < 0:
                    withdrawals.append(amount)
                elif amount > 0:
                    deposits.append(amount)
                continue
            if "fee" in description or "commission" in description or "charge" in description:
                fees.append(amount)

        return {
            "deposits": sum(deposits) if deposits else None,
            "withdrawals": sum(withdrawals) if withdrawals else None,
            "fees": sum(fees) if fees else None,
        }

    @staticmethod
    def _extract_value_by_keys(data: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = StatementClient._to_float(data.get(key))
            if value is not None:
                return value
        return None
