import logging
import math
import os
from typing import Iterable, Optional

from ib_async import IB, util

from .models import PnlResult, PositionModel, PositionSnapshot, TotalsModel

logger = logging.getLogger(__name__)


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
            util.startLoop()
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

    def get_managed_accounts(self) -> list[str]:
        return list(self.ib.managedAccounts())

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
                tickers = util.run(
                    self.ib.reqTickersAsync(
                        *[pos.contract for pos in positions_list]
                    ),
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
