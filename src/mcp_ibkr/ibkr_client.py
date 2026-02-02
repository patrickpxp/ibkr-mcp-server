import copy
import logging
import math
import os
import threading
from typing import Iterable, Optional

from ib_async import IB, util
from ib_async.contract import Contract, ContractDescription, ContractDetails
from ib_async.objects import AccountValue, ExecutionFilter, Fill
from ib_async.order import Trade

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

    def get_market_data_snapshot(
        self,
        contracts: Iterable[Contract],
        regulatory_snapshot: bool = False,
    ) -> tuple[list[object], list[str]]:
        contracts_list = list(contracts)
        if not contracts_list:
            return [], []
        qualified = util.run(
            self.ib.qualifyContractsAsync(*contracts_list),
            timeout=self.timeout_seconds,
        )
        notes: list[str] = []
        qualified_contracts: list[Contract] = []
        for contract, result in zip(contracts_list, qualified):
            if isinstance(result, Contract):
                qualified_contracts.append(result)
            else:
                notes.append(
                    f"contract not qualified: {getattr(contract, 'symbol', '')} {getattr(contract, 'secType', '')}"
                )
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

    def get_pnl_best_effort(
        self,
        account: Optional[str],
        positions: Iterable[PositionSnapshot],
    ) -> PnlResult:
        notes: list[str] = []
        positions_list = list(positions)
        output_positions = [_position_model(snapshot) for snapshot in positions_list]

        market_prices: dict[int, float] = {}
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

        for position in output_positions:
            price = market_prices.get(position.conId)
            if price is None:
                market_data_missing = True
                continue
            position.marketPrice = price
            position.marketValue = price * position.position
            if position.avgCost is not None:
                position.unrealizedPnl = (price - position.avgCost) * position.position

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
        notes.append("realizedPnl not available via current implementation")

        totals = TotalsModel(
            unrealizedPnl=total_unrealized,
            realizedPnl=None,
            netLiquidation=net_liquidation,
        )

        return PnlResult(
            positions=output_positions,
            totals=totals,
            notes=notes,
            currency=currency,
        )
