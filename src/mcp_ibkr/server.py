import logging
import os
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

import anyio
from fastapi import FastAPI
from fastmcp import FastMCP
from ib_async.objects import ExecutionFilter
from ib_async import util
from starlette.responses import JSONResponse
import uvicorn
import xmltodict

from .ibkr_client import IBKRClient, IBKRConnectionError
from .logging_utils import configure_logging
from .models import (
    AccountSummaryItem,
    AccountSummaryResponse,
    AccountValueItem,
    AccountValuesResponse,
    ContractDetailsModel,
    ContractDetailsResponse,
    ContractModel,
    ErrorDetails,
    ErrorResponse,
    ExecutionModel,
    ExecutionsResponse,
    FundamentalDataResponse,
    HeadTimestampResponse,
    HistoricalBarModel,
    HistoricalBarsResponse,
    HistoricalNewsItemModel,
    HistoricalNewsResponse,
    HistoricalTickModel,
    HistoricalTicksResponse,
    NewsArticleResponse,
    MarketDataSnapshotModel,
    MarketDataSnapshotAttempt,
    MarketDataSnapshotDebugResponse,
    MarketDataSnapshotResponse,
    MarketDepthLevelModel,
    MarketDepthSnapshotResponse,
    NewsProviderModel,
    NewsProvidersResponse,
    OpenOrderModel,
    OpenOrdersResponse,
    OptionChainModel,
    OptionChainResponse,
    PortfolioResponse,
    PositionModel,
    ScannerDataResponse,
    ScannerParamsResponse,
    ScannerResultModel,
    SymbolMatchModel,
    SymbolMatchesResponse,
    TotalsModel,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("IBKR MCP")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def create_app() -> FastAPI:
    json_response = _env_bool("MCP_JSON_RESPONSE", True)
    stateless_http = _env_bool("MCP_STATELESS_HTTP", True)
    mcp_app = mcp.http_app(
        path="/mcp",
        json_response=json_response,
        stateless_http=stateless_http,
    )
    app = FastAPI(lifespan=mcp_app.lifespan)

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.mount("/", mcp_app)
    return app


app = create_app()


def _safe_tz_name() -> str:
    return os.getenv("TZ", "Europe/Madrid")


def _as_of_timestamp(value: str | None) -> str:
    if value:
        return value
    tz_name = _safe_tz_name()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        logger.warning("invalid TZ; falling back", extra={"tz": tz_name})
        tz = ZoneInfo("Europe/Madrid")
    return datetime.now(tz=tz).isoformat()


def _resolve_account(account_override: str | None, client: IBKRClient) -> tuple[str, list[str]]:
    notes: list[str] = []
    if account_override:
        return account_override, notes
    env_account = os.getenv("IBKR_ACCOUNT")
    if env_account:
        return env_account, notes
    try:
        accounts = client.get_managed_accounts()
        if accounts:
            return accounts[0], notes
    except Exception:
        logger.warning("managed accounts lookup failed", exc_info=True)
    notes.append("account not resolved from input or IBKR_ACCOUNT; using UNKNOWN")
    return "UNKNOWN", notes


def _resolve_optional_account(account_override: str | None) -> str | None:
    if account_override:
        return account_override
    env_account = os.getenv("IBKR_ACCOUNT")
    if env_account:
        return env_account
    return None


def _position_from_snapshot(snapshot: object) -> PositionModel:
    return PositionModel(
        symbol=snapshot.symbol,
        secType=snapshot.sec_type,
        exchange=snapshot.exchange,
        currency=snapshot.currency,
        conId=snapshot.con_id,
        position=snapshot.position,
        avgCost=snapshot.avg_cost,
    )


def create_client() -> IBKRClient:
    return IBKRClient.from_env()


def _error_response(
    error_type: str,
    message: str,
    retryable: bool,
) -> dict:
    return ErrorResponse(
        error=ErrorDetails(
            type=error_type,
            message=message,
            retryable=retryable,
        )
    ).model_dump()


def _format_time(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if value in {util.UNSET_DOUBLE, util.UNSET_INTEGER}:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if value in {util.UNSET_DOUBLE, util.UNSET_INTEGER}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _contract_model(contract: object | None) -> ContractModel:
    if contract is None:
        return ContractModel()
    return ContractModel(
        conId=getattr(contract, "conId", None),
        symbol=getattr(contract, "symbol", None),
        secType=getattr(contract, "secType", None),
        exchange=getattr(contract, "exchange", None),
        currency=getattr(contract, "currency", None),
        primaryExchange=getattr(contract, "primaryExchange", None),
    )


def _run_with_client(action) -> dict:
    client = create_client()
    try:
        client.connect()
        return action(client)
    except IBKRConnectionError as exc:
        logger.warning("tws connection failed", exc_info=True)
        return _error_response("TWS_CONNECTION_FAILED", str(exc), True)
    except Exception as exc:
        logger.exception("ibkr tool failed")
        return _error_response("INTERNAL_ERROR", str(exc), False)
    finally:
        client.disconnect()


def _ibkr_get_portfolio_sync(
    account: str | None = None,
    include_pnl: bool = True,
    as_of: str | None = None,
) -> dict:
    def action(client: IBKRClient) -> dict:
        resolved_account, notes = _resolve_account(account, client)
        raw_positions = client.get_positions()

        currency = "BASE"
        if include_pnl:
            pnl_result = client.get_pnl_best_effort(resolved_account, raw_positions)
            positions = pnl_result.positions
            totals = pnl_result.totals
            notes.extend(pnl_result.notes)
            if pnl_result.currency:
                currency = pnl_result.currency
        else:
            positions = [_position_from_snapshot(pos) for pos in raw_positions]
            totals = TotalsModel()

        response = PortfolioResponse(
            as_of=_as_of_timestamp(as_of),
            account=resolved_account,
            currency=currency,
            positions=positions,
            totals=totals,
            notes=notes,
        )
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_portfolio(
    account: str | None = None,
    include_pnl: bool = True,
    as_of: str | None = None,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_portfolio_sync,
        account,
        include_pnl,
        as_of,
    )


def _ibkr_get_account_summary_sync(account: str | None = None) -> dict:
    def action(client: IBKRClient) -> dict:
        resolved_account, notes = _resolve_account(account, client)
        items = [
            AccountSummaryItem(
                tag=item.tag,
                value=item.value,
                currency=getattr(item, "currency", None),
                account=getattr(item, "account", None),
            )
            for item in client.get_account_summary(resolved_account)
        ]
        response = AccountSummaryResponse(
            account=resolved_account,
            items=items,
            notes=notes,
        )
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_account_summary(account: str | None = None) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_account_summary_sync,
        account,
    )


def _ibkr_get_account_values_sync(account: str | None = None) -> dict:
    def action(client: IBKRClient) -> dict:
        resolved_account, notes = _resolve_account(account, client)
        items = [
            AccountValueItem(
                tag=item.tag,
                value=item.value,
                currency=getattr(item, "currency", None),
                account=getattr(item, "account", None),
                modelCode=getattr(item, "modelCode", None),
            )
            for item in client.get_account_values(resolved_account)
        ]
        response = AccountValuesResponse(
            account=resolved_account,
            items=items,
            notes=notes,
        )
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_account_values(account: str | None = None) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_account_values_sync,
        account,
    )


def _ibkr_get_open_orders_sync(
    account: str | None = None,
    include_all: bool = True,
) -> dict:
    def action(client: IBKRClient) -> dict:
        resolved_account = _resolve_optional_account(account)
        notes: list[str] = []
        orders = []
        for trade in client.get_open_orders(include_all=include_all):
            order_account = getattr(trade.order, "account", None)
            if resolved_account and order_account and order_account != resolved_account:
                continue
            orders.append(
                OpenOrderModel(
                    orderId=getattr(trade.order, "orderId", None),
                    permId=getattr(trade.order, "permId", None),
                    action=getattr(trade.order, "action", None),
                    totalQuantity=_optional_float(getattr(trade.order, "totalQuantity", None)),
                    orderType=getattr(trade.order, "orderType", None),
                    lmtPrice=_optional_float(getattr(trade.order, "lmtPrice", None)),
                    auxPrice=_optional_float(getattr(trade.order, "auxPrice", None)),
                    tif=getattr(trade.order, "tif", None),
                    status=getattr(trade.orderStatus, "status", None),
                    account=order_account,
                    contract=_contract_model(getattr(trade, "contract", None)),
                )
            )
        response = OpenOrdersResponse(orders=orders, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_open_orders(
    account: str | None = None,
    include_all: bool = True,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_open_orders_sync,
        account,
        include_all,
    )


def _ibkr_get_executions_sync(
    account: str | None = None,
    symbol: str | None = None,
    secType: str | None = None,
    exchange: str | None = None,
    side: str | None = None,
    time: str | None = None,
) -> dict:
    def action(client: IBKRClient) -> dict:
        resolved_account = _resolve_optional_account(account)
        exec_filter = ExecutionFilter(
            acctCode=resolved_account or "",
            symbol=symbol or "",
            secType=secType or "",
            exchange=exchange or "",
            side=side or "",
            time=time or "",
        )
        notes: list[str] = []
        executions = []
        for fill in client.get_executions(exec_filter):
            execution = getattr(fill, "execution", None)
            executions.append(
                ExecutionModel(
                    execId=getattr(execution, "execId", None),
                    orderId=getattr(execution, "orderId", None),
                    permId=getattr(execution, "permId", None),
                    side=getattr(execution, "side", None),
                    shares=_optional_float(getattr(execution, "shares", None)),
                    price=_optional_float(getattr(execution, "price", None)),
                    time=_format_time(getattr(fill, "time", None)),
                    exchange=getattr(execution, "exchange", None),
                    account=getattr(execution, "acctNumber", None),
                    contract=_contract_model(getattr(fill, "contract", None)),
                )
            )
        response = ExecutionsResponse(executions=executions, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_executions(
    account: str | None = None,
    symbol: str | None = None,
    secType: str | None = None,
    exchange: str | None = None,
    side: str | None = None,
    time: str | None = None,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_executions_sync,
        account,
        symbol,
        secType,
        exchange,
        side,
        time,
    )


def _ibkr_search_symbols_sync(query: str) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        matches = []
        for item in client.search_symbols(query):
            contract = getattr(item, "contract", None)
            matches.append(
                SymbolMatchModel(
                    conId=getattr(contract, "conId", None),
                    symbol=getattr(contract, "symbol", None),
                    secType=getattr(contract, "secType", None),
                    exchange=getattr(contract, "exchange", None),
                    primaryExchange=getattr(contract, "primaryExchange", None),
                    currency=getattr(contract, "currency", None),
                    description=getattr(contract, "description", None),
                    derivativeSecTypes=list(getattr(item, "derivativeSecTypes", []) or []),
                )
            )
        response = SymbolMatchesResponse(matches=matches, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_search_symbols(query: str) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_search_symbols_sync,
        query,
    )


def _ibkr_get_contract_details_sync(contract: dict) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        try:
            contract_obj = IBKRClient.contract_from_input(contract)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)
        details = []
        for item in client.get_contract_details(contract_obj):
            contract_value = getattr(item, "contract", None)
            details.append(
                ContractDetailsModel(
                    conId=getattr(contract_value, "conId", None),
                    symbol=getattr(contract_value, "symbol", None),
                    secType=getattr(contract_value, "secType", None),
                    exchange=getattr(contract_value, "exchange", None),
                    primaryExchange=getattr(contract_value, "primaryExchange", None),
                    currency=getattr(contract_value, "currency", None),
                    longName=getattr(item, "longName", None),
                    marketName=getattr(item, "marketName", None),
                    minTick=_optional_float(getattr(item, "minTick", None)),
                    orderTypes=getattr(item, "orderTypes", None),
                    validExchanges=getattr(item, "validExchanges", None),
                    timeZoneId=getattr(item, "timeZoneId", None),
                    tradingHours=getattr(item, "tradingHours", None),
                    liquidHours=getattr(item, "liquidHours", None),
                    industry=getattr(item, "industry", None),
                    category=getattr(item, "category", None),
                    subcategory=getattr(item, "subcategory", None),
                    underConId=getattr(item, "underConId", None),
                    underSymbol=getattr(item, "underSymbol", None),
                    underSecType=getattr(item, "underSecType", None),
                )
            )
        response = ContractDetailsResponse(details=details, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_contract_details(contract: dict) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_contract_details_sync,
        contract,
    )


def _ibkr_get_market_data_snapshot_sync(
    contracts: Iterable[dict],
    regulatory_snapshot: bool = False,
) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        if not isinstance(contracts, Iterable):
            return _error_response("INVALID_ARGUMENT", "contracts must be a list", False)
        contract_list = []
        for item in contracts:
            if not isinstance(item, dict):
                return _error_response("INVALID_ARGUMENT", "contracts must be objects", False)
            try:
                contract_list.append(IBKRClient.contract_from_input(item))
            except ValueError as exc:
                return _error_response("INVALID_ARGUMENT", str(exc), False)
        snapshots = []
        tickers, qualification_notes = client.get_market_data_snapshot(
            contract_list,
            regulatory_snapshot=regulatory_snapshot,
        )
        notes.extend(qualification_notes)
        for ticker in tickers:
            contract = getattr(ticker, "contract", None)
            market_price_value = getattr(ticker, "marketPrice", None)
            if callable(market_price_value):
                market_price_value = market_price_value()
            snapshots.append(
                MarketDataSnapshotModel(
                    conId=getattr(contract, "conId", None),
                    symbol=getattr(contract, "symbol", None),
                    secType=getattr(contract, "secType", None),
                    exchange=getattr(contract, "exchange", None),
                    currency=getattr(contract, "currency", None),
                    bid=_optional_float(getattr(ticker, "bid", None)),
                    ask=_optional_float(getattr(ticker, "ask", None)),
                    last=_optional_float(getattr(ticker, "last", None)),
                    close=_optional_float(getattr(ticker, "close", None)),
                    marketPrice=_optional_float(market_price_value),
                )
            )
        response = MarketDataSnapshotResponse(snapshots=snapshots, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_market_data_snapshot(
    contracts: list[dict],
    regulatory_snapshot: bool = False,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_market_data_snapshot_sync,
        contracts,
        regulatory_snapshot,
    )


def _ibkr_get_historical_bars_sync(
    contract: dict,
    endDateTime: str | None,
    durationStr: str,
    barSizeSetting: str,
    whatToShow: str,
    useRTH: bool,
    formatDate: int = 1,
) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        if not isinstance(contract, dict):
            return _error_response("INVALID_ARGUMENT", "contract must be an object", False)
        try:
            contract_obj = IBKRClient.contract_from_input(contract)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)
        bars, qualification_notes = client.get_historical_bars(
            contract_obj,
            endDateTime or "",
            durationStr,
            barSizeSetting,
            whatToShow,
            useRTH,
            formatDate,
        )
        notes.extend(qualification_notes)
        output_bars = [
            HistoricalBarModel(
                time=_format_time(getattr(bar, "date", None)),
                open=_optional_float(getattr(bar, "open", None)),
                high=_optional_float(getattr(bar, "high", None)),
                low=_optional_float(getattr(bar, "low", None)),
                close=_optional_float(getattr(bar, "close", None)),
                volume=_optional_float(getattr(bar, "volume", None)),
                average=_optional_float(getattr(bar, "average", None)),
                barCount=_optional_int(getattr(bar, "barCount", None)),
            )
            for bar in bars
        ]
        if not output_bars:
            notes.append("no historical bars returned")
        response = HistoricalBarsResponse(bars=output_bars, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_historical_bars(
    contract: dict,
    endDateTime: str | None,
    durationStr: str,
    barSizeSetting: str,
    whatToShow: str,
    useRTH: bool,
    formatDate: int = 1,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_historical_bars_sync,
        contract,
        endDateTime,
        durationStr,
        barSizeSetting,
        whatToShow,
        useRTH,
        formatDate,
    )


def _ibkr_get_historical_ticks_sync(
    contract: dict,
    startDateTime: str | None,
    endDateTime: str | None,
    numberOfTicks: int,
    whatToShow: str,
    useRTH: bool,
    ignoreSize: bool = False,
) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        if whatToShow.strip().upper() != "BID_ASK":
            notes.append("bid/ask fields are only populated for whatToShow=BID_ASK")
        if not isinstance(contract, dict):
            return _error_response("INVALID_ARGUMENT", "contract must be an object", False)
        try:
            contract_obj = IBKRClient.contract_from_input(contract)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)
        ticks, qualification_notes = client.get_historical_ticks(
            contract_obj,
            startDateTime or "",
            endDateTime or "",
            numberOfTicks,
            whatToShow,
            useRTH,
            ignoreSize,
        )
        notes.extend(qualification_notes)
        output_ticks: list[HistoricalTickModel] = []
        for tick in ticks:
            time_value = _format_time(getattr(tick, "time", None))
            if hasattr(tick, "priceBid") or hasattr(tick, "priceAsk"):
                output_ticks.append(
                    HistoricalTickModel(
                        time=time_value,
                        priceBid=_optional_float(getattr(tick, "priceBid", None)),
                        priceAsk=_optional_float(getattr(tick, "priceAsk", None)),
                        sizeBid=_optional_float(getattr(tick, "sizeBid", None)),
                        sizeAsk=_optional_float(getattr(tick, "sizeAsk", None)),
                    )
                )
                continue
            output_ticks.append(
                HistoricalTickModel(
                    time=time_value,
                    price=_optional_float(getattr(tick, "price", None)),
                    size=_optional_float(getattr(tick, "size", None)),
                    exchange=getattr(tick, "exchange", None),
                    specialConditions=getattr(tick, "specialConditions", None),
                )
            )
        if not output_ticks:
            notes.append("no historical ticks returned")
        response = HistoricalTicksResponse(ticks=output_ticks, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_historical_ticks(
    contract: dict,
    startDateTime: str | None,
    endDateTime: str | None,
    numberOfTicks: int,
    whatToShow: str,
    useRTH: bool,
    ignoreSize: bool = False,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_historical_ticks_sync,
        contract,
        startDateTime,
        endDateTime,
        numberOfTicks,
        whatToShow,
        useRTH,
        ignoreSize,
    )


def _ibkr_get_head_timestamp_sync(
    contract: dict,
    whatToShow: str,
    useRTH: bool,
    formatDate: int = 1,
) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        if not isinstance(contract, dict):
            return _error_response("INVALID_ARGUMENT", "contract must be an object", False)
        try:
            contract_obj = IBKRClient.contract_from_input(contract)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)
        timestamp, qualification_notes = client.get_head_timestamp(
            contract_obj,
            whatToShow,
            useRTH,
            formatDate,
        )
        notes.extend(qualification_notes)
        formatted = _format_time(timestamp)
        if not formatted:
            notes.append("head timestamp unavailable")
        response = HeadTimestampResponse(headTimestamp=formatted, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_head_timestamp(
    contract: dict,
    whatToShow: str,
    useRTH: bool,
    formatDate: int = 1,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_head_timestamp_sync,
        contract,
        whatToShow,
        useRTH,
        formatDate,
    )


def _ibkr_get_market_depth_snapshot_sync(
    contract: dict,
    numRows: int = 5,
    isSmartDepth: bool = False,
) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        if not isinstance(contract, dict):
            return _error_response("INVALID_ARGUMENT", "contract must be an object", False)
        try:
            contract_obj = IBKRClient.contract_from_input(contract)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)
        bids, asks, qualification_notes = client.get_market_depth_snapshot(
            contract_obj,
            numRows,
            isSmartDepth,
        )
        notes.extend(qualification_notes)
        bid_levels = [
            MarketDepthLevelModel(
                price=_optional_float(getattr(level, "price", None)),
                size=_optional_float(getattr(level, "size", None)),
                marketMaker=getattr(level, "marketMaker", None),
            )
            for level in bids
        ]
        ask_levels = [
            MarketDepthLevelModel(
                price=_optional_float(getattr(level, "price", None)),
                size=_optional_float(getattr(level, "size", None)),
                marketMaker=getattr(level, "marketMaker", None),
            )
            for level in asks
        ]
        if not bid_levels and not ask_levels:
            notes.append("market depth snapshot empty")
        response = MarketDepthSnapshotResponse(bids=bid_levels, asks=ask_levels, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_market_depth_snapshot(
    contract: dict,
    numRows: int = 5,
    isSmartDepth: bool = False,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_market_depth_snapshot_sync,
        contract,
        numRows,
        isSmartDepth,
    )


def _ibkr_get_option_chain_sync(
    underlyingSymbol: str,
    exchange: str,
    secType: str,
    underlyingConId: int,
) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        normalized_exchange = exchange
        if secType.strip().upper() != "FUT" and exchange:
            normalized_exchange = ""
            notes.append("exchange ignored for non-FUT option chain requests")
        con_id = _optional_int(underlyingConId)
        if con_id is None:
            return _error_response("INVALID_ARGUMENT", "underlyingConId must be an integer", False)
        chains = [
            OptionChainModel(
                exchange=getattr(chain, "exchange", None),
                underlyingConId=_optional_int(getattr(chain, "underlyingConId", None)),
                tradingClass=getattr(chain, "tradingClass", None),
                multiplier=getattr(chain, "multiplier", None),
                expirations=list(getattr(chain, "expirations", []) or []),
                strikes=list(getattr(chain, "strikes", []) or []),
            )
            for chain in client.get_option_chain(
                underlyingSymbol,
                normalized_exchange,
                secType,
                con_id,
            )
        ]
        if not chains:
            notes.append("no option chain entries returned")
        response = OptionChainResponse(chains=chains, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_option_chain(
    underlyingSymbol: str,
    exchange: str,
    secType: str,
    underlyingConId: int,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_option_chain_sync,
        underlyingSymbol,
        exchange,
        secType,
        underlyingConId,
    )


def _ibkr_get_news_providers_sync() -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        providers = [
            NewsProviderModel(
                code=getattr(provider, "code", None),
                name=getattr(provider, "name", None),
            )
            for provider in client.get_news_providers()
        ]
        if not providers:
            notes.append("no news providers returned")
        response = NewsProvidersResponse(providers=providers, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_news_providers() -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_news_providers_sync,
    )


def _ibkr_get_historical_news_sync(
    contract: dict,
    providerCodes: str,
    startTime: str,
    endTime: str,
    totalResults: int,
) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        requested_providers = {
            code.strip()
            for code in (providerCodes or "").split("+")
            if code.strip()
        }
        if requested_providers:
            try:
                available = {getattr(p, "code", None) for p in client.get_news_providers()}
                missing = sorted(code for code in requested_providers if code not in available)
                if missing:
                    notes.append(f"not subscribed for providers: {', '.join(missing)}")
                    response = HistoricalNewsResponse(items=[], notes=notes)
                    return response.model_dump()
            except Exception:
                notes.append("news provider entitlement check failed; proceeding with request")
        if not isinstance(contract, dict):
            return _error_response("INVALID_ARGUMENT", "contract must be an object", False)
        try:
            contract_obj = IBKRClient.contract_from_input(contract)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)
        qualified_contract, qualification_notes = client.qualify_contract(contract_obj)
        notes.extend(qualification_notes)
        con_id = getattr(qualified_contract, "conId", None) or getattr(contract_obj, "conId", None)
        if con_id is None:
            notes.append("contract conId unavailable; historical news not requested")
            response = HistoricalNewsResponse(items=[], notes=notes)
            return response.model_dump()
        items = [
            HistoricalNewsItemModel(
                time=_format_time(getattr(item, "time", None)),
                providerCode=getattr(item, "providerCode", None),
                articleId=getattr(item, "articleId", None),
                headline=getattr(item, "headline", None),
            )
            for item in client.get_historical_news(
                con_id,
                providerCodes,
                startTime,
                endTime,
                totalResults,
            )
        ]
        if not items:
            notes.append("no historical news returned")
        response = HistoricalNewsResponse(items=items, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_historical_news(
    contract: dict,
    providerCodes: str,
    startTime: str,
    endTime: str,
    totalResults: int,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_historical_news_sync,
        contract,
        providerCodes,
        startTime,
        endTime,
        totalResults,
    )


def _ibkr_get_fundamental_data_sync(
    contract: dict,
    reportType: str,
    format: str = "json",
) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        if not isinstance(contract, dict):
            return _error_response("INVALID_ARGUMENT", "contract must be an object", False)
        try:
            contract_obj = IBKRClient.contract_from_input(contract)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)
        data, qualification_notes = client.get_fundamental_data(contract_obj, reportType)
        notes.extend(qualification_notes)
        output: object | None = data
        if data and format.strip().lower() == "json":
            try:
                output = xmltodict.parse(data)
            except Exception as exc:
                notes.append(f"xml parse failed; returning raw xml ({exc})")
        if not data:
            notes.append("fundamental data unavailable")
        response = FundamentalDataResponse(report=output, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_fundamental_data(
    contract: dict,
    reportType: str,
    format: str = "json",
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_fundamental_data_sync,
        contract,
        reportType,
        format,
    )


def _ibkr_get_scanner_params_sync(format: str = "json") -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        params = client.get_scanner_params()
        output: object | None = params
        if params and format.strip().lower() == "json":
            try:
                output = xmltodict.parse(params)
            except Exception as exc:
                notes.append(f"xml parse failed; returning raw xml ({exc})")
        if not params:
            notes.append("scanner parameters empty")
        response = ScannerParamsResponse(params=output, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_scanner_params(format: str = "json") -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_scanner_params_sync,
        format,
    )


def _ibkr_get_news_article_sync(
    providerCode: str,
    articleId: str,
) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        article = client.get_news_article(providerCode, articleId)
        response = NewsArticleResponse(
            articleType=_optional_int(getattr(article, "articleType", None)),
            articleText=getattr(article, "articleText", None),
            notes=notes,
        )
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_get_news_article(
    providerCode: str,
    articleId: str,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_news_article_sync,
        providerCode,
        articleId,
    )


def _ibkr_run_scanner_sync(subscription: dict) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        try:
            subscription_obj = IBKRClient.scanner_subscription_from_input(subscription)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)
        results = []
        for item in client.run_scanner(subscription_obj):
            details = getattr(item, "contractDetails", None)
            contract = _contract_model(getattr(details, "contract", None))
            results.append(
                ScannerResultModel(
                    rank=_optional_int(getattr(item, "rank", None)),
                    contract=contract,
                    distance=getattr(item, "distance", None),
                    benchmark=getattr(item, "benchmark", None),
                    projection=getattr(item, "projection", None),
                    legsStr=getattr(item, "legsStr", None),
                    marketName=getattr(details, "marketName", None),
                    longName=getattr(details, "longName", None),
                )
            )
        if not results:
            notes.append("scanner returned no results")
        response = ScannerDataResponse(results=results, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_run_scanner(subscription: dict) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_run_scanner_sync,
        subscription,
    )


def _ibkr_debug_market_data_snapshot_sync(
    contract: dict,
    regulatory_snapshot: bool = False,
    market_data_type: int | None = None,
    force_smart: bool = True,
) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        if not isinstance(contract, dict):
            return _error_response("INVALID_ARGUMENT", "contract must be an object", False)
        try:
            base_contract = IBKRClient.contract_from_input(contract)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)

        if market_data_type is not None:
            try:
                client.ib.reqMarketDataType(int(market_data_type))
                notes.append(f"market data type set to {int(market_data_type)}")
            except Exception as exc:
                notes.append(f"market data type set failed: {exc}")

        def build_attempt(contract_obj: object) -> MarketDataSnapshotAttempt:
            snapshots: list[MarketDataSnapshotModel] = []
            tickers, attempt_notes = client.get_market_data_snapshot(
                [contract_obj],
                regulatory_snapshot=regulatory_snapshot,
            )
            for ticker in tickers:
                contract_value = getattr(ticker, "contract", None)
                market_price_value = getattr(ticker, "marketPrice", None)
                if callable(market_price_value):
                    market_price_value = market_price_value()
                snapshots.append(
                    MarketDataSnapshotModel(
                        conId=getattr(contract_value, "conId", None),
                        symbol=getattr(contract_value, "symbol", None),
                        secType=getattr(contract_value, "secType", None),
                        exchange=getattr(contract_value, "exchange", None),
                        currency=getattr(contract_value, "currency", None),
                        bid=_optional_float(getattr(ticker, "bid", None)),
                        ask=_optional_float(getattr(ticker, "ask", None)),
                        last=_optional_float(getattr(ticker, "last", None)),
                        close=_optional_float(getattr(ticker, "close", None)),
                        marketPrice=_optional_float(market_price_value),
                    )
                )
            return MarketDataSnapshotAttempt(
                request=_contract_model(contract_obj),
                snapshots=snapshots,
                notes=attempt_notes,
            )

        attempts = [build_attempt(base_contract)]

        if force_smart:
            exchange = getattr(base_contract, "exchange", None)
            if exchange and exchange.upper() != "SMART":
                modified = dict(contract)
                modified["exchange"] = "SMART"
                if not modified.get("primaryExchange"):
                    modified["primaryExchange"] = exchange
                try:
                    smart_contract = IBKRClient.contract_from_input(modified)
                    attempts.append(build_attempt(smart_contract))
                except ValueError as exc:
                    notes.append(f"smart override failed: {exc}")

        response = MarketDataSnapshotDebugResponse(attempts=attempts, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool
async def ibkr_debug_market_data_snapshot(
    contract: dict,
    regulatory_snapshot: bool = False,
    market_data_type: int | None = None,
    force_smart: bool = True,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_debug_market_data_snapshot_sync,
        contract,
        regulatory_snapshot,
        market_data_type,
        force_smart,
    )


def main() -> None:
    configure_logging()
    host = os.getenv("MCP_BIND_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))

    logger.info("starting mcp server", extra={"host": host, "port": port})
    uvicorn.run(
        "mcp_ibkr.server:app",
        host=host,
        port=port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
