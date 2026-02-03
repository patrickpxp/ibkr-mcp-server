import logging
import os
from datetime import datetime
from typing import Annotated, Any, Iterable
from zoneinfo import ZoneInfo

import anyio
import fastmcp
from fastapi import FastAPI
from fastmcp import FastMCP
from ib_async.objects import ExecutionFilter
from ib_async import util
import mcp.types as mcp_types
from pydantic import BaseModel, Field
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

def _merge_schema_definitions(*schemas: dict[str, Any]) -> dict[str, Any]:
    definitions: dict[str, Any] = {}
    for schema in schemas:
        definitions.update(schema.get("$defs", {}))
    return definitions


def _combined_output_schema(model: type[BaseModel]) -> dict[str, Any]:
    success_schema = model.model_json_schema()
    error_schema = ErrorResponse.model_json_schema()
    definitions = _merge_schema_definitions(success_schema, error_schema)
    success_schema.pop("$defs", None)
    error_schema.pop("$defs", None)
    combined: dict[str, Any] = {
        "type": "object",
        "anyOf": [success_schema, error_schema],
    }
    if definitions:
        combined["$defs"] = definitions
    return combined


class IbkrMCP(FastMCP):
    async def _call_tool_mcp(
        self, key: str, arguments: dict[str, Any]
    ) -> (
        list[mcp_types.ContentBlock]
        | tuple[list[mcp_types.ContentBlock], dict[str, Any]]
        | mcp_types.CallToolResult
    ):
        result = await super()._call_tool_mcp(key, arguments)
        if isinstance(result, mcp_types.CallToolResult):
            if isinstance(result.structuredContent, dict) and "error" in result.structuredContent:
                return result.model_copy(update={"isError": True})
            return result
        if isinstance(result, tuple) and len(result) == 2:
            content, structured_content = result
            if isinstance(structured_content, dict) and "error" in structured_content:
                return mcp_types.CallToolResult(
                    content=list(content),
                    structuredContent=structured_content,
                    isError=True,
                )
        return result

    async def _list_tools_mcp(
        self, request: mcp_types.ListToolsRequest | None = None
    ) -> mcp_types.ListToolsResult:
        logger.debug("[%s] Handler called: list_tools", self.name)
        async with fastmcp.server.context.Context(fastmcp=self):
            cursor = None
            if request and request.params:
                cursor = request.params.cursor
            if cursor not in (None, "", "0"):
                return mcp_types.ListToolsResult(tools=[], nextCursor=None)

            tools = await self._list_tools_middleware()
            mcp_tools = [
                tool.to_mcp_tool(
                    name=tool.key,
                    include_fastmcp_meta=self.include_fastmcp_meta,
                )
                for tool in tools
            ]
            return mcp_types.ListToolsResult(tools=mcp_tools, nextCursor=None)


mcp = IbkrMCP("IBKR MCP")


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


AccountId = Annotated[
    str | None,
    Field(
        description=(
            "IBKR account ID. Defaults to IBKR_ACCOUNT or the first managed account."
        )
    ),
]
OptionalAccountId = Annotated[
    str | None,
    Field(description="Optional IBKR account ID filter."),
]
IncludePnl = Annotated[
    bool,
    Field(description="Whether to compute best-effort P&L and totals."),
]
AsOfTimestamp = Annotated[
    str | None,
    Field(description="ISO-8601 timestamp override for the response."),
]
IncludeAll = Annotated[
    bool,
    Field(description="Whether to include open orders from all accounts."),
]
QueryText = Annotated[str, Field(description="Symbol or name to search for.")]
ContractInput = Annotated[
    dict,
    Field(
        description=(
            "Contract fields such as symbol, secType, exchange, currency, conId, "
            "and primaryExchange."
        )
    ),
]
ContractsInput = Annotated[
    list[dict],
    Field(description="List of contract objects."),
]
RegulatorySnapshot = Annotated[
    bool,
    Field(description="Request a regulatory snapshot (may incur fees)."),
]
EndDateTime = Annotated[
    str | None,
    Field(description="IB-formatted end date/time (e.g., '20240102 16:00:00')."),
]
DurationStr = Annotated[
    str,
    Field(description="IB duration string (e.g., '1 D', '2 W')."),
]
BarSizeSetting = Annotated[
    str,
    Field(description="IB bar size setting (e.g., '1 hour', '1 day')."),
]
WhatToShow = Annotated[
    str,
    Field(description="IB data type (e.g., TRADES, MIDPOINT, BID_ASK)."),
]
UseRth = Annotated[
    bool,
    Field(description="Whether to use regular trading hours only."),
]
FormatDate = Annotated[
    int,
    Field(description="IB formatDate flag (1 for human-readable timestamps)."),
]
StartDateTime = Annotated[
    str | None,
    Field(description="IB-formatted start date/time."),
]
NumberOfTicks = Annotated[
    int,
    Field(description="Number of ticks to request."),
]
IgnoreSize = Annotated[
    bool,
    Field(description="Ignore size in historical tick request."),
]
NumRows = Annotated[
    int,
    Field(description="Number of market depth rows to request."),
]
IsSmartDepth = Annotated[
    bool,
    Field(description="Whether to request SMART market depth."),
]
UnderlyingSymbol = Annotated[
    str,
    Field(description="Underlying symbol for the option chain."),
]
Exchange = Annotated[
    str,
    Field(description="Exchange for the request (ignored for non-FUT option chains)."),
]
ExecutionExchange = Annotated[
    str | None,
    Field(description="Execution exchange filter."),
]
SecType = Annotated[
    str,
    Field(description="IB security type (e.g., STK, OPT, FUT)."),
]
OptionalSecType = Annotated[
    str | None,
    Field(description="IB security type filter (e.g., STK, OPT, FUT)."),
]
UnderlyingConId = Annotated[
    int,
    Field(description="Underlying conId for the option chain."),
]
ProviderCodes = Annotated[
    str,
    Field(description="Plus-delimited provider codes (e.g., 'BZ+DJ')."),
]
ProviderCode = Annotated[
    str,
    Field(description="News provider code (e.g., 'BZ')."),
]
StartTime = Annotated[
    str,
    Field(description="IB-formatted start time (e.g., '20240101 00:00:00')."),
]
EndTime = Annotated[
    str,
    Field(description="IB-formatted end time (e.g., '20240102 00:00:00')."),
]
TotalResults = Annotated[
    int,
    Field(description="Maximum number of results to return."),
]
ReportType = Annotated[
    str,
    Field(description="IB fundamentals report type (e.g., ReportSnapshot)."),
]
ResponseFormat = Annotated[
    str,
    Field(description="Output format: json (default) or xml."),
]
MarketDataType = Annotated[
    int | None,
    Field(description="IB market data type override."),
]
ForceSmart = Annotated[
    bool,
    Field(description="Force a SMART+primaryExchange retry in debug snapshot."),
]
ScannerSubscription = Annotated[
    dict,
    Field(
        description=(
            "Scanner subscription object (instrument, locationCode, scanCode, etc.)."
        )
    ),
]
Symbol = Annotated[str | None, Field(description="Symbol filter for executions.")]
ExecutionSide = Annotated[
    str | None, Field(description="Execution side filter (BOT/SLD).")
]
ExecutionTime = Annotated[
    str | None, Field(description="Execution time filter string.")
]

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


@mcp.tool(
    title="Get Portfolio",
    description="Return positions and best-effort P&L for an IBKR account.",
    output_schema=_combined_output_schema(PortfolioResponse),
)
async def ibkr_get_portfolio(
    account: AccountId = None,
    include_pnl: IncludePnl = True,
    as_of: AsOfTimestamp = None,
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


@mcp.tool(
    title="Get Account Summary",
    description="Return account summary values such as NetLiquidation and BuyingPower.",
    output_schema=_combined_output_schema(AccountSummaryResponse),
)
async def ibkr_get_account_summary(account: AccountId = None) -> dict:
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


@mcp.tool(
    title="Get Account Values",
    description="Return account values snapshot for the specified account.",
    output_schema=_combined_output_schema(AccountValuesResponse),
)
async def ibkr_get_account_values(account: AccountId = None) -> dict:
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


@mcp.tool(
    title="Get Open Orders",
    description="Return open orders with contract details and status.",
    output_schema=_combined_output_schema(OpenOrdersResponse),
)
async def ibkr_get_open_orders(
    account: OptionalAccountId = None,
    include_all: IncludeAll = True,
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


@mcp.tool(
    title="Get Executions",
    description="Return executions/fills matching the provided filters.",
    output_schema=_combined_output_schema(ExecutionsResponse),
)
async def ibkr_get_executions(
    account: OptionalAccountId = None,
    symbol: Symbol = None,
    secType: OptionalSecType = None,
    exchange: ExecutionExchange = None,
    side: ExecutionSide = None,
    time: ExecutionTime = None,
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


@mcp.tool(
    title="Search Symbols",
    description="Search for symbols and matching contracts.",
    output_schema=_combined_output_schema(SymbolMatchesResponse),
)
async def ibkr_search_symbols(query: QueryText) -> dict:
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


@mcp.tool(
    title="Get Contract Details",
    description="Return contract details for a given contract input.",
    output_schema=_combined_output_schema(ContractDetailsResponse),
)
async def ibkr_get_contract_details(contract: ContractInput) -> dict:
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


@mcp.tool(
    title="Get Market Data Snapshot",
    description="Return a one-shot market data snapshot for contracts.",
    output_schema=_combined_output_schema(MarketDataSnapshotResponse),
)
async def ibkr_get_market_data_snapshot(
    contracts: ContractsInput,
    regulatory_snapshot: RegulatorySnapshot = False,
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


@mcp.tool(
    title="Get Historical Bars",
    description="Return historical OHLCV bars for a contract.",
    output_schema=_combined_output_schema(HistoricalBarsResponse),
)
async def ibkr_get_historical_bars(
    contract: ContractInput,
    endDateTime: EndDateTime,
    durationStr: DurationStr,
    barSizeSetting: BarSizeSetting,
    whatToShow: WhatToShow,
    useRTH: UseRth,
    formatDate: FormatDate = 1,
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


@mcp.tool(
    title="Get Historical Ticks",
    description="Return historical ticks for a contract.",
    output_schema=_combined_output_schema(HistoricalTicksResponse),
)
async def ibkr_get_historical_ticks(
    contract: ContractInput,
    startDateTime: StartDateTime,
    endDateTime: EndDateTime,
    numberOfTicks: NumberOfTicks,
    whatToShow: WhatToShow,
    useRTH: UseRth,
    ignoreSize: IgnoreSize = False,
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


@mcp.tool(
    title="Get Head Timestamp",
    description="Return the earliest available historical data timestamp.",
    output_schema=_combined_output_schema(HeadTimestampResponse),
)
async def ibkr_get_head_timestamp(
    contract: ContractInput,
    whatToShow: WhatToShow,
    useRTH: UseRth,
    formatDate: FormatDate = 1,
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


@mcp.tool(
    title="Get Market Depth Snapshot",
    description="Return a one-shot market depth (L2) snapshot.",
    output_schema=_combined_output_schema(MarketDepthSnapshotResponse),
)
async def ibkr_get_market_depth_snapshot(
    contract: ContractInput,
    numRows: NumRows = 5,
    isSmartDepth: IsSmartDepth = False,
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


@mcp.tool(
    title="Get Option Chain",
    description="Return option chain metadata for an underlying.",
    output_schema=_combined_output_schema(OptionChainResponse),
)
async def ibkr_get_option_chain(
    underlyingSymbol: UnderlyingSymbol,
    exchange: Exchange,
    secType: SecType,
    underlyingConId: UnderlyingConId,
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


@mcp.tool(
    title="Get News Providers",
    description="Return available news provider codes and names.",
    output_schema=_combined_output_schema(NewsProvidersResponse),
)
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


@mcp.tool(
    title="Get Historical News",
    description="Return historical news headlines for a contract.",
    output_schema=_combined_output_schema(HistoricalNewsResponse),
)
async def ibkr_get_historical_news(
    contract: ContractInput,
    providerCodes: ProviderCodes,
    startTime: StartTime,
    endTime: EndTime,
    totalResults: TotalResults,
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


@mcp.tool(
    title="Get Fundamental Data",
    description="Return a fundamentals report for a contract.",
    output_schema=_combined_output_schema(FundamentalDataResponse),
)
async def ibkr_get_fundamental_data(
    contract: ContractInput,
    reportType: ReportType,
    format: ResponseFormat = "json",
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


@mcp.tool(
    title="Get Scanner Params",
    description="Return market scanner parameter definitions.",
    output_schema=_combined_output_schema(ScannerParamsResponse),
)
async def ibkr_get_scanner_params(format: ResponseFormat = "json") -> dict:
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


@mcp.tool(
    title="Get News Article",
    description="Return a news article body for a provider/article id.",
    output_schema=_combined_output_schema(NewsArticleResponse),
)
async def ibkr_get_news_article(
    providerCode: ProviderCode,
    articleId: Annotated[str, Field(description="News article identifier.")],
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


@mcp.tool(
    title="Run Scanner",
    description="Run a market scanner subscription and return ranked results.",
    output_schema=_combined_output_schema(ScannerDataResponse),
)
async def ibkr_run_scanner(subscription: ScannerSubscription) -> dict:
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


@mcp.tool(
    title="Debug Market Data Snapshot",
    description="Return diagnostic market data snapshots for a contract.",
    output_schema=_combined_output_schema(MarketDataSnapshotDebugResponse),
)
async def ibkr_debug_market_data_snapshot(
    contract: ContractInput,
    regulatory_snapshot: RegulatorySnapshot = False,
    market_data_type: MarketDataType = None,
    force_smart: ForceSmart = True,
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
