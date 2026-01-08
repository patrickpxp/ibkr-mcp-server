import pytest

from mcp_ibkr.models import PnlResult, PositionModel, PositionSnapshot, TotalsModel


@pytest.fixture()
def sample_positions() -> list[PositionSnapshot]:
    return [
        PositionSnapshot(
            symbol="AAPL",
            sec_type="STK",
            exchange="SMART",
            currency="USD",
            con_id=265598,
            position=10.0,
            avg_cost=150.12,
            contract=object(),
        )
    ]


@pytest.fixture()
def sample_pnl_result() -> PnlResult:
    position = PositionModel(
        symbol="AAPL",
        secType="STK",
        exchange="SMART",
        currency="USD",
        conId=265598,
        position=10.0,
        avgCost=150.12,
        marketPrice=172.34,
        marketValue=1723.4,
        unrealizedPnl=222.2,
        realizedPnl=None,
    )
    totals = TotalsModel(
        unrealizedPnl=222.2,
        realizedPnl=None,
        netLiquidation=100000.0,
    )
    return PnlResult(
        positions=[position],
        totals=totals,
        notes=["realizedPnl not available via current implementation"],
        currency="BASE",
    )
