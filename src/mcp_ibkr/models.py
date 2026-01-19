from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


@dataclass
class PositionSnapshot:
    symbol: str
    sec_type: str
    exchange: str
    currency: str
    con_id: int
    position: float
    avg_cost: Optional[float]
    contract: Any


class PositionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    secType: str
    exchange: str
    currency: str
    conId: int
    position: float
    avgCost: Optional[float] = None
    marketPrice: Optional[float] = None
    marketValue: Optional[float] = None
    unrealizedPnl: Optional[float] = None
    realizedPnl: Optional[float] = None


class TotalsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unrealizedPnl: Optional[float] = None
    realizedPnl: Optional[float] = None
    netLiquidation: Optional[float] = None


class PortfolioResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: str
    account: str
    currency: str
    positions: list[PositionModel]
    totals: TotalsModel
    notes: list[str]


class ErrorDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    message: str
    retryable: bool


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorDetails


class PnlResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positions: list[PositionModel]
    totals: TotalsModel
    notes: list[str]
    currency: Optional[str] = None


class AccountSummaryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str
    value: str
    currency: Optional[str] = None
    account: Optional[str] = None


class AccountSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str
    items: list[AccountSummaryItem]
    notes: list[str]


class AccountValueItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str
    value: str
    currency: Optional[str] = None
    account: Optional[str] = None
    modelCode: Optional[str] = None


class AccountValuesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str
    items: list[AccountValueItem]
    notes: list[str]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conId: Optional[int] = None
    symbol: Optional[str] = None
    secType: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None
    primaryExchange: Optional[str] = None


class OpenOrderModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orderId: Optional[int] = None
    permId: Optional[int] = None
    action: Optional[str] = None
    totalQuantity: Optional[float] = None
    orderType: Optional[str] = None
    lmtPrice: Optional[float] = None
    auxPrice: Optional[float] = None
    tif: Optional[str] = None
    status: Optional[str] = None
    account: Optional[str] = None
    contract: ContractModel


class OpenOrdersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orders: list[OpenOrderModel]
    notes: list[str]


class ExecutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execId: Optional[str] = None
    orderId: Optional[int] = None
    permId: Optional[int] = None
    side: Optional[str] = None
    shares: Optional[float] = None
    price: Optional[float] = None
    time: Optional[str] = None
    exchange: Optional[str] = None
    account: Optional[str] = None
    contract: ContractModel


class ExecutionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executions: list[ExecutionModel]
    notes: list[str]


class SymbolMatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conId: Optional[int] = None
    symbol: Optional[str] = None
    secType: Optional[str] = None
    exchange: Optional[str] = None
    primaryExchange: Optional[str] = None
    currency: Optional[str] = None
    description: Optional[str] = None
    derivativeSecTypes: list[str]


class SymbolMatchesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[SymbolMatchModel]
    notes: list[str]


class ContractDetailsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conId: Optional[int] = None
    symbol: Optional[str] = None
    secType: Optional[str] = None
    exchange: Optional[str] = None
    primaryExchange: Optional[str] = None
    currency: Optional[str] = None
    longName: Optional[str] = None
    marketName: Optional[str] = None
    minTick: Optional[float] = None
    orderTypes: Optional[str] = None
    validExchanges: Optional[str] = None
    timeZoneId: Optional[str] = None
    tradingHours: Optional[str] = None
    liquidHours: Optional[str] = None
    industry: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    underConId: Optional[int] = None
    underSymbol: Optional[str] = None
    underSecType: Optional[str] = None


class ContractDetailsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    details: list[ContractDetailsModel]
    notes: list[str]


class MarketDataSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conId: Optional[int] = None
    symbol: Optional[str] = None
    secType: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    close: Optional[float] = None
    marketPrice: Optional[float] = None


class MarketDataSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshots: list[MarketDataSnapshotModel]
    notes: list[str]
