import copy
import logging
import math
import os
import threading
from dataclasses import fields as dataclass_fields
from typing import Iterable, Optional

from ib_async import IB, util
from ib_async.contract import Contract, ContractDescription, ContractDetails
from ib_async.objects import AccountValue, ExecutionFilter, Fill, ScannerSubscription
from ib_async.order import LimitOrder, Order, OrderState, StopOrder, Trade

from .models import PnlResult, PositionModel, PositionSnapshot, TotalsModel

logger = logging.getLogger(__name__)


def _normalize_market_data_contract(contract: Contract) -> Contract:
    exchange = getattr(contract, "exchange", None)
    if exchange and exchange.upper() == "IBIS":
        contract_copy = copy.copy(contract)
        contract_copy.exchange = "SMART"
        if not getattr(contract_copy, "primaryExchange", None):
            contract_copy.primaryExchange = "IBIS"
        return contract_copy
    return contract

_START_LOOP_LOCK = threading.Lock()
_START_LOOP_INITIALIZED = False


class IBKRConnectionError(Exception):
    pass


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _to_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: object) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _best_price(ticker: object) -> Optional[float]:
    price = None
    if hasattr(ticker, "marketPrice"):
        try:
            price = ticker.marketPrice()
        except Exception:
            price = None
    if price is None:
        price = getattr(ticker, "last", None) or getattr(ticker, "close", None)
    return _to_float(price)


def _position_model(snapshot: PositionSnapshot) -> PositionModel:
    return PositionModel(
        symbol=snapshot.symbol,
        secType=snapshot.sec_type,
        exchange=snapshot.exchange,
        currency=snapshot.currency,
        conId=snapshot.con_id,
        position=snapshot.position,
        avgCost=snapshot.avg_cost,
    )


_ORDER_INT_FIELDS = {
    "orderId",
    "clientId",
    "permId",
    "parentId",
    "ocaType",
    "displaySize",
    "triggerMethod",
    "minQty",
    "origin",
    "shortSaleSlot",
    "exemptCode",
    "referenceContractId",
}
_ORDER_FLOAT_FIELDS = {
    "totalQuantity",
    "lmtPrice",
    "auxPrice",
    "trailStopPrice",
    "trailingPercent",
    "cashQty",
    "discretionaryAmt",
}
_ORDER_BOOL_FIELDS = {
    "transmit",
    "outsideRth",
    "hidden",
    "allOrNone",
    "whatIf",
    "sweepToFill",
    "blockOrder",
}


class IBKRClient:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: int,
        timeout_seconds: int,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout_seconds = timeout_seconds
        self.ib = IB()

    @classmethod
    def from_env(cls) -> "IBKRClient":
        host = os.getenv("IBKR_HOST", "host.docker.internal")
        port = int(os.getenv("IBKR_PORT", "7497"))
        client_id = int(os.getenv("IBKR_CLIENT_ID", "123"))
        timeout_seconds = int(os.getenv("IBKR_TIMEOUT_SECONDS", "10"))
        return cls(host, port, client_id, timeout_seconds)

    def connect(self) -> None:
        try:
            global _START_LOOP_INITIALIZED
            if not _START_LOOP_INITIALIZED:
                with _START_LOOP_LOCK:
                    if not _START_LOOP_INITIALIZED:
                        util.startLoop()
                        _START_LOOP_INITIALIZED = True
            connected = self.ib.connect(
                self.host,
                self.port,
                clientId=self.client_id,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            raise IBKRConnectionError(
                f"failed to connect to TWS at {self.host}:{self.port}"
            ) from exc
        if not connected:
            raise IBKRConnectionError(
                f"failed to connect to TWS at {self.host}:{self.port}"
            )

    def disconnect(self) -> None:
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
        except Exception:
            logger.warning("disconnect failed", exc_info=True)

    def get_positions(self) -> list[PositionSnapshot]:
        positions = []
        for position in self.ib.positions():
            contract = position.contract
            exchange = contract.exchange or getattr(contract, "primaryExchange", "") or "SMART"
            positions.append(
                PositionSnapshot(
                    symbol=contract.symbol,
                    sec_type=contract.secType,
                    exchange=exchange,
                    currency=contract.currency or "BASE",
                    con_id=contract.conId,
                    position=float(position.position),
                    avg_cost=_to_float(position.avgCost),
                    contract=contract,
                )
            )
        return positions

    @staticmethod
    def contract_from_input(data: dict[str, object]) -> Contract:
        if not isinstance(data, dict):
            raise ValueError("contract must be an object")
        allowed_fields = {
            "conId",
            "symbol",
            "secType",
            "exchange",
            "currency",
            "primaryExchange",
            "lastTradeDateOrContractMonth",
            "strike",
            "right",
            "multiplier",
            "localSymbol",
            "tradingClass",
            "includeExpired",
            "secIdType",
            "secId",
        }
        kwargs: dict[str, object] = {}
        for key in allowed_fields:
            if key not in data:
                continue
            value = data.get(key)
            if value is None:
                continue
            if key in {"conId"}:
                coerced = _to_int(value)
                if coerced is not None:
                    kwargs[key] = coerced
            elif key in {"strike"}:
                coerced = _to_float(value)
                if coerced is not None:
                    kwargs[key] = coerced
            elif key in {"includeExpired"}:
                coerced = _coerce_bool(value)
                if coerced is not None:
                    kwargs[key] = coerced
            else:
                kwargs[key] = str(value)

        if not kwargs:
            raise ValueError("contract requires at least one field")
        return Contract(**kwargs)

    @staticmethod
    def order_from_input(
        data: dict[str, object],
        default_transmit: bool = False,
    ) -> Order:
        if not isinstance(data, dict):
            raise ValueError("order must be an object")
        allowed_fields = {field.name for field in dataclass_fields(Order)}
        kwargs: dict[str, object] = {}
        for key in allowed_fields:
            if key not in data:
                continue
            value = data.get(key)
            if value is None:
                continue
            if key in _ORDER_INT_FIELDS:
                coerced = _to_int(value)
                if coerced is not None:
                    kwargs[key] = coerced
            elif key in _ORDER_FLOAT_FIELDS:
                coerced = _to_float(value)
                if coerced is not None:
                    kwargs[key] = coerced
            elif key in _ORDER_BOOL_FIELDS:
                coerced = _coerce_bool(value)
                if coerced is not None:
                    kwargs[key] = coerced
            else:
                kwargs[key] = value

        if "transmit" not in kwargs:
            kwargs["transmit"] = default_transmit

        required_fields = {"action", "totalQuantity", "orderType"}
        missing = [
            name
            for name in required_fields
            if kwargs.get(name) in (None, "", 0, 0.0)
        ]
        if missing:
            raise ValueError(f"order missing required field(s): {', '.join(sorted(missing))}")

        return Order(**kwargs)

    def get_managed_accounts(self) -> list[str]:
        return list(self.ib.managedAccounts())

    def get_account_summary(self, account: str) -> list[AccountValue]:
        return list(
            util.run(
                self.ib.accountSummaryAsync(account),
                timeout=self.timeout_seconds,
            )
        )

    def get_account_values(self, account: str) -> list[AccountValue]:
        try:
            util.run(
                self.ib.reqAccountUpdatesAsync(account),
                timeout=min(self.timeout_seconds, 2),
            )
        except TimeoutError:
            logger.warning("account updates refresh timed out; using cached values")
        values = list(self.ib.accountValues(account))
        try:
            self.ib.client.reqAccountUpdates(False, account)
        except Exception:
            logger.warning("account updates unsubscribe failed", exc_info=True)
        return values

    def get_open_orders(self, include_all: bool = True) -> list[Trade]:
        request = self.ib.reqAllOpenOrdersAsync() if include_all else self.ib.reqOpenOrdersAsync()
        return list(
            util.run(
                request,
                timeout=self.timeout_seconds,
            )
        )

    def get_executions(self, exec_filter: Optional[ExecutionFilter]) -> list[Fill]:
        return list(
            util.run(
                self.ib.reqExecutionsAsync(exec_filter),
                timeout=self.timeout_seconds,
            )
        )

    def search_symbols(self, query: str) -> list[ContractDescription]:
        result = util.run(
            self.ib.reqMatchingSymbolsAsync(query),
            timeout=self.timeout_seconds,
        )
        return list(result or [])

    def get_contract_details(self, contract: Contract) -> list[ContractDetails]:
        return list(
            util.run(
                self.ib.reqContractDetailsAsync(contract),
                timeout=self.timeout_seconds,
            )
        )

    def _qualify_contracts(self, contracts: Iterable[Contract]) -> tuple[list[Contract], list[str]]:
        contracts_list = list(contracts)
        if not contracts_list:
            return [], []
        qualified = util.run(
            self.ib.qualifyContractsAsync(*contracts_list),
            timeout=self.timeout_seconds,
        )
        notes: list[str] = []
        qualified_contracts: list[Contract] = []
        for contract, result in zip(contracts_list, qualified or []):
            if isinstance(result, Contract):
                qualified_contracts.append(result)
            else:
                notes.append(
                    f"contract not qualified: {getattr(contract, 'symbol', '')} {getattr(contract, 'secType', '')}"
                )
        return qualified_contracts, notes

    def qualify_contract(self, contract: Contract) -> tuple[Contract | None, list[str]]:
        qualified, notes = self._qualify_contracts([contract])
        return (qualified[0] if qualified else None), notes

    def get_market_data_snapshot(
        self,
        contracts: Iterable[Contract],
        regulatory_snapshot: bool = False,
    ) -> tuple[list[object], list[str]]:
        qualified_contracts, notes = self._qualify_contracts(contracts)
        if not qualified_contracts:
            return [], notes
        normalized_contracts = [
            _normalize_market_data_contract(contract) for contract in qualified_contracts
        ]
        tickers = list(
            util.run(
                self.ib.reqTickersAsync(
                    *normalized_contracts,
                    regulatorySnapshot=regulatory_snapshot,
                ),
                timeout=self.timeout_seconds,
            )
        )
        return tickers, notes

    def get_historical_bars(
        self,
        contract: Contract,
        end_date_time: object,
        duration_str: str,
        bar_size_setting: str,
        what_to_show: str,
        use_rth: bool,
        format_date: int = 1,
    ) -> tuple[list[object], list[str]]:
        qualified_contracts, notes = self._qualify_contracts([contract])
        if not qualified_contracts:
            return [], notes
        bars = list(
            util.run(
                self.ib.reqHistoricalDataAsync(
                    qualified_contracts[0],
                    end_date_time,
                    duration_str,
                    bar_size_setting,
                    what_to_show,
                    use_rth,
                    format_date,
                ),
                timeout=self.timeout_seconds,
            )
        )
        return bars, notes

    def get_historical_ticks(
        self,
        contract: Contract,
        start_date_time: object,
        end_date_time: object,
        number_of_ticks: int,
        what_to_show: str,
        use_rth: bool,
        ignore_size: bool = False,
    ) -> tuple[list[object], list[str]]:
        qualified_contracts, notes = self._qualify_contracts([contract])
        if not qualified_contracts:
            return [], notes
        ticks = list(
            util.run(
                self.ib.reqHistoricalTicksAsync(
                    qualified_contracts[0],
                    start_date_time,
                    end_date_time,
                    number_of_ticks,
                    what_to_show,
                    use_rth,
                    ignore_size,
                ),
                timeout=self.timeout_seconds,
            )
        )
        return ticks, notes

    def get_head_timestamp(
        self,
        contract: Contract,
        what_to_show: str,
        use_rth: bool,
        format_date: int = 1,
    ) -> tuple[object | None, list[str]]:
        qualified_contracts, notes = self._qualify_contracts([contract])
        if not qualified_contracts:
            return None, notes
        timestamp = util.run(
            self.ib.reqHeadTimeStampAsync(
                qualified_contracts[0],
                what_to_show,
                use_rth,
                format_date,
            ),
            timeout=self.timeout_seconds,
        )
        return timestamp, notes

    def get_market_depth_snapshot(
        self,
        contract: Contract,
        num_rows: int = 5,
        is_smart_depth: bool = False,
    ) -> tuple[list[object], list[object], list[str]]:
        qualified_contracts, notes = self._qualify_contracts([contract])
        if not qualified_contracts:
            return [], [], notes
        normalized_contract = _normalize_market_data_contract(qualified_contracts[0])
        ticker = self.ib.reqMktDepth(
            normalized_contract,
            numRows=num_rows,
            isSmartDepth=is_smart_depth,
        )
        try:
            util.sleep(min(self.timeout_seconds, 1))
        finally:
            try:
                self.ib.cancelMktDepth(normalized_contract, isSmartDepth=is_smart_depth)
            except Exception:
                logger.warning("market depth cancel failed", exc_info=True)
        return list(getattr(ticker, "domBids", []) or []), list(
            getattr(ticker, "domAsks", []) or []
        ), notes

    def get_option_chain(
        self,
        underlying_symbol: str,
        exchange: str,
        sec_type: str,
        underlying_con_id: int,
    ) -> list[object]:
        return list(
            util.run(
                self.ib.reqSecDefOptParamsAsync(
                    underlying_symbol,
                    exchange,
                    sec_type,
                    underlying_con_id,
                ),
                timeout=self.timeout_seconds,
            )
        )

    def get_news_providers(self) -> list[object]:
        return list(
            util.run(
                self.ib.reqNewsProvidersAsync(),
                timeout=self.timeout_seconds,
            )
        )

    def get_historical_news(
        self,
        con_id: int,
        provider_codes: str,
        start_time: object,
        end_time: object,
        total_results: int,
    ) -> list[object]:
        result = util.run(
            self.ib.reqHistoricalNewsAsync(
                con_id,
                provider_codes,
                start_time,
                end_time,
                total_results,
            ),
            timeout=self.timeout_seconds,
        )
        if result is None:
            return []
        if isinstance(result, (list, tuple)):
            return list(result)
        return [result]

    def get_fundamental_data(
        self,
        contract: Contract,
        report_type: str,
    ) -> tuple[str | None, list[str]]:
        qualified_contracts, notes = self._qualify_contracts([contract])
        if not qualified_contracts:
            return None, notes
        data = util.run(
            self.ib.reqFundamentalDataAsync(
                qualified_contracts[0],
                report_type,
            ),
            timeout=self.timeout_seconds,
        )
        return data, notes

    def get_scanner_params(self) -> str:
        return util.run(
            self.ib.reqScannerParametersAsync(),
            timeout=self.timeout_seconds,
        )

    def get_news_article(self, provider_code: str, article_id: str) -> object:
        return util.run(
            self.ib.reqNewsArticleAsync(provider_code, article_id),
            timeout=self.timeout_seconds,
        )

    def preview_order(
        self,
        contract: Contract,
        order: Order,
    ) -> tuple[OrderState | None, list[str]]:
        qualified_contract, notes = self.qualify_contract(contract)
        if not qualified_contract:
            return None, notes
        # IBKR requires transmit=true for what-if requests.
        what_if_order = copy.copy(order)
        what_if_order.transmit = True
        state = util.run(
            self.ib.whatIfOrderAsync(qualified_contract, what_if_order),
            timeout=self.timeout_seconds,
        )
        return state, notes

    def place_order(
        self,
        contract: Contract,
        order: Order,
    ) -> tuple[Trade | None, list[str]]:
        qualified_contract, notes = self.qualify_contract(contract)
        if not qualified_contract:
            return None, notes
        trade = self.ib.placeOrder(qualified_contract, order)
        util.sleep(min(self.timeout_seconds, 1))
        return trade, notes

    def create_bracket_orders(
        self,
        action: str,
        quantity: float,
        limit_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        transmit: bool = False,
        order_kwargs: Optional[dict[str, object]] = None,
    ) -> list[Order]:
        if action not in {"BUY", "SELL"}:
            raise ValueError("action must be BUY or SELL")
        reverse_action = "BUY" if action == "SELL" else "SELL"
        common_kwargs = dict(order_kwargs or {})
        parent = LimitOrder(
            action,
            quantity,
            limit_price,
            orderId=self.ib.client.getReqId(),
            transmit=False,
            **common_kwargs,
        )
        take_profit = LimitOrder(
            reverse_action,
            quantity,
            take_profit_price,
            orderId=self.ib.client.getReqId(),
            parentId=parent.orderId,
            transmit=False,
            **common_kwargs,
        )
        stop_loss = StopOrder(
            reverse_action,
            quantity,
            stop_loss_price,
            orderId=self.ib.client.getReqId(),
            parentId=parent.orderId,
            transmit=transmit,
            **common_kwargs,
        )
        if not transmit:
            parent.transmit = False
            take_profit.transmit = False
            stop_loss.transmit = False
        return [parent, take_profit, stop_loss]

    def place_bracket_order(
        self,
        contract: Contract,
        action: str,
        quantity: float,
        limit_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        transmit: bool = False,
        order_kwargs: Optional[dict[str, object]] = None,
    ) -> tuple[list[Trade], list[str]]:
        qualified_contract, notes = self.qualify_contract(contract)
        if not qualified_contract:
            return [], notes
        orders = self.create_bracket_orders(
            action=action,
            quantity=quantity,
            limit_price=limit_price,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price,
            transmit=transmit,
            order_kwargs=order_kwargs,
        )
        trades = [self.ib.placeOrder(qualified_contract, order) for order in orders]
        util.sleep(min(self.timeout_seconds, 1))
        return trades, notes

    def apply_oca_group_and_place(
        self,
        entries: Iterable[tuple[Contract, Order]],
        oca_group: str,
        oca_type: int,
        transmit: bool = False,
    ) -> tuple[list[Trade], list[str]]:
        notes: list[str] = []
        normalized_entries: list[tuple[Contract, Order]] = []
        for contract, order in entries:
            qualified_contract, qualification_notes = self.qualify_contract(contract)
            notes.extend(qualification_notes)
            if not qualified_contract:
                continue
            normalized_entries.append((qualified_contract, order))

        if not normalized_entries:
            return [], notes

        orders = [order for _, order in normalized_entries]
        grouped_orders = self.ib.oneCancelsAll(orders, oca_group, oca_type)
        for order in grouped_orders:
            order.transmit = transmit
        trades = [
            self.ib.placeOrder(contract, order)
            for (contract, _), order in zip(normalized_entries, grouped_orders)
        ]
        util.sleep(min(self.timeout_seconds, 1))
        return trades, notes

    def _find_trade_by_order_id(self, order_id: int) -> Trade | None:
        for trade in self.get_open_orders(include_all=True):
            if _to_int(getattr(getattr(trade, "order", None), "orderId", None)) == order_id:
                return trade
        for trade in self.ib.trades():
            if _to_int(getattr(getattr(trade, "order", None), "orderId", None)) == order_id:
                return trade
        return None

    def cancel_order_by_id(self, order_id: int) -> tuple[Trade | None, list[str]]:
        notes: list[str] = []
        trade = self._find_trade_by_order_id(order_id)
        if trade is None:
            notes.append("order not found in local trade cache; sending cancel by orderId only")
            synthetic_order = Order(orderId=order_id, clientId=self.client_id)
            self.ib.cancelOrder(synthetic_order)
            util.sleep(min(self.timeout_seconds, 1))
            return None, notes

        cancelled_trade = self.ib.cancelOrder(getattr(trade, "order"))
        util.sleep(min(self.timeout_seconds, 1))
        return cancelled_trade or trade, notes

    def global_cancel(self) -> None:
        self.ib.reqGlobalCancel()
        util.sleep(min(self.timeout_seconds, 1))

    def exercise_options(
        self,
        contract: Contract,
        exercise_action: int,
        exercise_quantity: int,
        account: str,
        override: int,
    ) -> tuple[str, list[str]]:
        qualified_contract, notes = self.qualify_contract(contract)
        if not qualified_contract:
            return "not_sent", notes
        self.ib.exerciseOptions(
            qualified_contract,
            exercise_action,
            exercise_quantity,
            account,
            override,
        )
        util.sleep(min(self.timeout_seconds, 1))
        return "submitted", notes

    @staticmethod
    def scanner_subscription_from_input(data: dict[str, object]) -> ScannerSubscription:
        if not isinstance(data, dict):
            raise ValueError("scanner subscription must be an object")
        allowed_fields = {
            "numberOfRows",
            "instrument",
            "locationCode",
            "scanCode",
            "abovePrice",
            "belowPrice",
            "aboveVolume",
            "marketCapAbove",
            "marketCapBelow",
            "moodyRatingAbove",
            "moodyRatingBelow",
            "spRatingAbove",
            "spRatingBelow",
            "maturityDateAbove",
            "maturityDateBelow",
            "couponRateAbove",
            "couponRateBelow",
            "excludeConvertible",
            "averageOptionVolumeAbove",
            "scannerSettingPairs",
            "stockTypeFilter",
        }
        kwargs: dict[str, object] = {}
        for key in allowed_fields:
            if key not in data:
                continue
            value = data.get(key)
            if value is None:
                continue
            if key in {"numberOfRows", "aboveVolume", "averageOptionVolumeAbove"}:
                coerced = _to_int(value)
                if coerced is not None:
                    kwargs[key] = coerced
            elif key in {
                "abovePrice",
                "belowPrice",
                "marketCapAbove",
                "marketCapBelow",
                "couponRateAbove",
                "couponRateBelow",
            }:
                coerced = _to_float(value)
                if coerced is not None:
                    kwargs[key] = coerced
            elif key in {"excludeConvertible"}:
                coerced = _coerce_bool(value)
                if coerced is not None:
                    kwargs[key] = coerced
            else:
                kwargs[key] = str(value)
        if not kwargs:
            raise ValueError("scanner subscription requires at least one field")
        return ScannerSubscription(**kwargs)

    def run_scanner(
        self,
        subscription: ScannerSubscription,
    ) -> list[object]:
        return list(
            util.run(
                self.ib.reqScannerDataAsync(subscription),
                timeout=self.timeout_seconds,
            )
        )

    def get_pnl_best_effort(
        self,
        account: Optional[str],
        positions: Iterable[PositionSnapshot],
    ) -> PnlResult:
        notes: list[str] = []
        positions_list = list(positions)
        output_positions = [_position_model(snapshot) for snapshot in positions_list]

        market_prices: dict[int, float] = {}
        portfolio_items_by_con_id: dict[int, object] = {}
        market_data_missing = False
        if positions_list:
            try:
                normalized_contracts = [
                    _normalize_market_data_contract(pos.contract)
                    for pos in positions_list
                ]
                tickers = util.run(
                    self.ib.reqTickersAsync(*normalized_contracts),
                    timeout=self.timeout_seconds,
                )
                for ticker in tickers:
                    contract = getattr(ticker, "contract", None)
                    if not contract:
                        continue
                    price = _best_price(ticker)
                    if price is not None:
                        market_prices[contract.conId] = price
            except Exception:
                logger.warning("market data request failed", exc_info=True)

            def _collect_portfolio_items(target_account: str) -> list[object]:
                items = list(self.ib.portfolio(target_account))
                if items or not target_account:
                    return items
                # Fallback: some gateways don't return account-filtered rows reliably.
                return [
                    item
                    for item in self.ib.portfolio()
                    if getattr(item, "account", None) == target_account
                ]

            try:
                target_account = account or ""
                portfolio_items = _collect_portfolio_items(target_account)
                util.run(
                    self.ib.reqAccountUpdatesAsync(target_account),
                    timeout=min(self.timeout_seconds, 2),
                )
                refreshed_items = _collect_portfolio_items(target_account)
                if refreshed_items:
                    portfolio_items = refreshed_items
                for item in portfolio_items:
                    contract = getattr(item, "contract", None)
                    con_id = _to_int(getattr(contract, "conId", None)) if contract else None
                    if con_id is not None:
                        portfolio_items_by_con_id[con_id] = item
            except TimeoutError:
                logger.info("portfolio refresh timed out; using cached portfolio rows")
                target_account = account or ""
                for item in _collect_portfolio_items(target_account):
                    contract = getattr(item, "contract", None)
                    con_id = _to_int(getattr(contract, "conId", None)) if contract else None
                    if con_id is not None:
                        portfolio_items_by_con_id[con_id] = item
            except Exception:
                logger.warning("portfolio P&L request failed", exc_info=True)
            finally:
                try:
                    self.ib.client.reqAccountUpdates(False, account or "")
                except Exception:
                    logger.warning("account updates unsubscribe failed", exc_info=True)

        for position in output_positions:
            portfolio_item = portfolio_items_by_con_id.get(position.conId)
            price = market_prices.get(position.conId)
            if price is None and portfolio_item is not None:
                price = _to_float(getattr(portfolio_item, "marketPrice", None))
            if price is None:
                market_data_missing = True
            else:
                position.marketPrice = price
                portfolio_market_value = (
                    _to_float(getattr(portfolio_item, "marketValue", None))
                    if portfolio_item is not None
                    else None
                )
                position.marketValue = (
                    portfolio_market_value
                    if portfolio_market_value is not None
                    else price * position.position
                )
                if position.avgCost is not None:
                    position.unrealizedPnl = (price - position.avgCost) * position.position

            if portfolio_item is not None:
                portfolio_unrealized = _to_float(
                    getattr(portfolio_item, "unrealizedPNL", None)
                )
                if portfolio_unrealized is not None:
                    position.unrealizedPnl = portfolio_unrealized

                portfolio_realized = _to_float(getattr(portfolio_item, "realizedPNL", None))
                if portfolio_realized is not None:
                    position.realizedPnl = portfolio_realized

        total_unrealized = None
        unrealized_values = [
            pos.unrealizedPnl
            for pos in output_positions
            if pos.unrealizedPnl is not None
        ]
        if unrealized_values:
            total_unrealized = sum(unrealized_values)
        elif output_positions:
            market_data_missing = True

        total_realized = None
        realized_values = [
            pos.realizedPnl
            for pos in output_positions
            if pos.realizedPnl is not None
        ]
        if realized_values:
            total_realized = sum(realized_values)

        net_liquidation = None
        currency = None
        try:
            summary = util.run(
                self.ib.accountSummaryAsync(account) if account else self.ib.accountSummaryAsync(),
                timeout=self.timeout_seconds,
            )
            for item in summary:
                if item.tag == "NetLiquidation" and item.currency in ("", "BASE"):
                    net_liquidation = _to_float(item.value)
                if item.tag in ("BaseCurrency", "Currency") and item.value:
                    currency = item.value
        except Exception:
            logger.warning("account summary request failed", exc_info=True)
            notes.append("account summary unavailable; netLiquidation set to null")

        if market_data_missing:
            notes.append("market data unavailable for some positions; prices and P&L set to null where missing")
        if total_realized is None:
            notes.append("realizedPnl not available via current implementation")

        totals = TotalsModel(
            unrealizedPnl=total_unrealized,
            realizedPnl=total_realized,
            netLiquidation=net_liquidation,
        )

        return PnlResult(
            positions=output_positions,
            totals=totals,
            notes=notes,
            currency=currency,
        )
