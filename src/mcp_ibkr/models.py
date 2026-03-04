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


class OptionGreeksModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    impliedVol: Optional[float] = None
    delta: Optional[float] = None
    optPrice: Optional[float] = None
    pvDividend: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    theta: Optional[float] = None
    undPrice: Optional[float] = None


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
    delta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    theta: Optional[float] = None
    impliedVol: Optional[float] = None
    optPrice: Optional[float] = None
    undPrice: Optional[float] = None
    greeksSource: Optional[str] = None
    modelGreeks: Optional[OptionGreeksModel] = None
    bidGreeks: Optional[OptionGreeksModel] = None
    askGreeks: Optional[OptionGreeksModel] = None
    lastGreeks: Optional[OptionGreeksModel] = None


class MarketDataSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshots: list[MarketDataSnapshotModel]
    notes: list[str]


class MarketDataSnapshotAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: ContractModel
    snapshots: list[MarketDataSnapshotModel]
    notes: list[str]


class MarketDataSnapshotDebugResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempts: list[MarketDataSnapshotAttempt]
    notes: list[str]


class HistoricalBarModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: Optional[str] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    average: Optional[float] = None
    barCount: Optional[int] = None


class HistoricalBarsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bars: list[HistoricalBarModel]
    notes: list[str]


class HistoricalTickModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: Optional[str] = None
    price: Optional[float] = None
    size: Optional[float] = None
    priceBid: Optional[float] = None
    priceAsk: Optional[float] = None
    sizeBid: Optional[float] = None
    sizeAsk: Optional[float] = None
    exchange: Optional[str] = None
    specialConditions: Optional[str] = None


class HistoricalTicksResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticks: list[HistoricalTickModel]
    notes: list[str]


class HeadTimestampResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headTimestamp: Optional[str] = None
    notes: list[str]


class MarketDepthLevelModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: Optional[float] = None
    size: Optional[float] = None
    marketMaker: Optional[str] = None


class MarketDepthSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bids: list[MarketDepthLevelModel]
    asks: list[MarketDepthLevelModel]
    notes: list[str]


class OptionChainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange: Optional[str] = None
    underlyingConId: Optional[int] = None
    tradingClass: Optional[str] = None
    multiplier: Optional[str] = None
    expirations: list[str]
    strikes: list[float]


class OptionChainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chains: list[OptionChainModel]
    notes: list[str]


class NewsProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Optional[str] = None
    name: Optional[str] = None


class NewsProvidersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[NewsProviderModel]
    notes: list[str]


class HistoricalNewsItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: Optional[str] = None
    providerCode: Optional[str] = None
    articleId: Optional[str] = None
    headline: Optional[str] = None


class HistoricalNewsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[HistoricalNewsItemModel]
    notes: list[str]


class FundamentalDataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: Optional[Any] = None
    notes: list[str]


class ScannerParamsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: Optional[Any] = None
    notes: list[str]


class ScannerResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: Optional[int] = None
    contract: ContractModel
    distance: Optional[str] = None
    benchmark: Optional[str] = None
    projection: Optional[str] = None
    legsStr: Optional[str] = None
    marketName: Optional[str] = None
    longName: Optional[str] = None


class ScannerDataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[ScannerResultModel]
    notes: list[str]


class NewsArticleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    articleType: Optional[int] = None
    articleText: Optional[str] = None
    notes: list[str]


class OrderStateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Optional[str] = None
    initMarginBefore: Optional[str] = None
    maintMarginBefore: Optional[str] = None
    equityWithLoanBefore: Optional[str] = None
    initMarginChange: Optional[str] = None
    maintMarginChange: Optional[str] = None
    equityWithLoanChange: Optional[str] = None
    initMarginAfter: Optional[str] = None
    maintMarginAfter: Optional[str] = None
    equityWithLoanAfter: Optional[str] = None
    commission: Optional[float] = None
    minCommission: Optional[float] = None
    maxCommission: Optional[float] = None
    commissionCurrency: Optional[str] = None
    warningText: Optional[str] = None
    completedTime: Optional[str] = None
    completedStatus: Optional[str] = None


class TradeSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: dict[str, Any]
    order: dict[str, Any]
    orderStatus: dict[str, Any]
    fills: list[dict[str, Any]]
    log: list[dict[str, Any]]
    advancedError: Optional[str] = None
    isDone: Optional[bool] = None


class PreviewOrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orderState: Optional[OrderStateModel] = None
    notes: list[str]


class PlaceOrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade: Optional[TradeSnapshotModel] = None
    notes: list[str]


class CancelOrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orderId: int
    trade: Optional[TradeSnapshotModel] = None
    notes: list[str]


class GlobalCancelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    notes: list[str]


class BracketOrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orderIds: list[int]
    trades: list[TradeSnapshotModel]
    notes: list[str]


class OcaGroupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ocaGroup: str
    ocaType: int
    orderIds: list[int]
    trades: list[TradeSnapshotModel]
    notes: list[str]


class ExerciseOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    notes: list[str]
