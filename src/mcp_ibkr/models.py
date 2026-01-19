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
