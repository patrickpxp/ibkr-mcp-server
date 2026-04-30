import logging
import os
import threading
from dataclasses import fields as dataclass_fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Iterable
from zoneinfo import ZoneInfo

import anyio
import fastmcp
from fastapi import FastAPI
from fastmcp import FastMCP
from ib_async.objects import ExecutionFilter
from ib_async.order import Order
from ib_async import util
import mcp.types as mcp_types
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse
import uvicorn
import xmltodict

from .ibkr_client import IBKRClient, IBKRConnectionError, IBKRMarketDataTimeoutError
from .logging_utils import configure_logging
from .models import (
    AccountSummaryItem,
    AccountSummaryResponse,
    AccountValueItem,
    AccountValuesResponse,
    CashActivityItem,
    CashActivityResponse,
    DividendItem,
    DividendsResponse,
    ContractDetailsModel,
    ContractDetailsResponse,
    ContractModel,
    ErrorDetails,
    ErrorResponse,
    ExerciseOptionsResponse,
    ExecutionModel,
    ExecutionsResponse,
    FlexStatementResponse,
    GlobalCancelResponse,
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
    OptionGreeksModel,
    OrderStateModel,
    OptionChainModel,
    OptionChainResponse,
    OcaGroupResponse,
    PlaceOrderResponse,
    PortfolioResponse,
    PositionModel,
    PreviewOrderResponse,
    BracketOrderResponse,
    CancelOrderResponse,
    ScannerDataResponse,
    ScannerParamsResponse,
    ScannerResultModel,
    StatementSummaryResponse,
    StatementTopicItem,
    StatementTopicsResponse,
    TradeConfirmationItem,
    TradeConfirmationsResponse,
    SymbolMatchModel,
    SymbolMatchesResponse,
    TradeSnapshotModel,
    TotalsModel,
    TransactionModel,
    TransactionsResponse,
)
from .statement_client import (
    CashActivityEntry,
    DividendEntry,
    StatementSummary,
    StatementTopicEntry,
    StatementClient,
    StatementConfigError,
    StatementRequestError,
    TradeConfirmationEntry,
)

logger = logging.getLogger(__name__)
_CLIENT_SESSION_LOCK = threading.Lock()


def _ibkr_connection_identity() -> dict[str, object]:
    ibkr_port = int(os.getenv("IBKR_PORT", "7497"))
    trading_mode = {7496: "live", 7497: "paper"}.get(ibkr_port, "custom")
    return {
        "ibkr_host": os.getenv("IBKR_HOST", "host.docker.internal"),
        "ibkr_port": ibkr_port,
        "ibkr_client_id": int(os.getenv("IBKR_CLIENT_ID", "100")),
        "ibkr_account_configured": bool(os.getenv("IBKR_ACCOUNT", "").strip()),
        "ibkr_trading_enabled": os.getenv("IBKR_ENABLE_TRADING", "false").lower()
        in {"1", "true", "yes", "on"},
        "ibkr_trading_mode": trading_mode,
    }


def _startup_log_context() -> dict[str, object]:
    context = _ibkr_connection_identity()
    context.update(
        {
            "mcp_bind_host": os.getenv("MCP_BIND_HOST", "0.0.0.0"),
            "mcp_port": int(os.getenv("MCP_PORT", "8000")),
        }
    )
    return context

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
        timeout_seconds = int(os.getenv("IBKR_TIMEOUT_SECONDS", "10"))
        return JSONResponse(
            {"status": "ok", "ibkrTimeoutSeconds": timeout_seconds}
        )

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
QueryId = Annotated[
    str | None,
    Field(description="IBKR Flex query identifier. Defaults to IBKR_FLEX_QUERY_ID."),
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
FromTime = Annotated[
    str | None, Field(description="Start timestamp filter for transactions (ISO-8601 preferred).")
]
ToTime = Annotated[
    str | None, Field(description="End timestamp filter for transactions (ISO-8601 preferred).")
]
ResultLimit = Annotated[
    int,
    Field(description="Maximum number of transactions to return."),
]
OrderInput = Annotated[
    dict,
    Field(
        description=(
            "Order fields (action, totalQuantity, orderType, lmtPrice, auxPrice, tif, account, etc.)."
        )
    ),
]
Confirm = Annotated[
    bool,
    Field(description="Must be true to execute a live trading action."),
]
DryRun = Annotated[
    bool,
    Field(description="When true, return notes only and do not place orders."),
]
Transmit = Annotated[
    bool,
    Field(description="Whether to transmit placed orders to IBKR (default false)."),
]
OrderId = Annotated[
    int,
    Field(description="IBKR orderId to cancel."),
]
OrderAction = Annotated[
    str,
    Field(description="Order action: BUY or SELL."),
]
OrderQuantity = Annotated[
    float,
    Field(description="Order quantity."),
]
LimitPrice = Annotated[
    float,
    Field(description="Limit price."),
]
TakeProfitPrice = Annotated[
    float,
    Field(description="Take-profit limit price."),
]
StopLossPrice = Annotated[
    float,
    Field(description="Stop-loss trigger price."),
]
OrderOptionsInput = Annotated[
    dict,
    Field(description="Optional order fields applied to bracket legs."),
]
OcaOrdersInput = Annotated[
    list[dict],
    Field(description="List of objects with `contract` and `order` for OCA placement."),
]
OcaGroup = Annotated[
    str,
    Field(description="OCA group identifier."),
]
OcaType = Annotated[
    int,
    Field(description="OCA type value."),
]
ExerciseAction = Annotated[
    int,
    Field(description="1 to exercise, 2 to lapse."),
]
ExerciseQuantity = Annotated[
    int,
    Field(description="Number of option contracts."),
]
Override = Annotated[
    int,
    Field(description="0 no override, 1 override natural action."),
]

_ORDER_FIELD_NAMES = {field.name for field in dataclass_fields(Order)}

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


def _parse_time_filter(value: str | None) -> datetime | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    normalized = candidate.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        pass
    for fmt in ("%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    raise ValueError(f"invalid timestamp '{value}'")


def _execution_side_multiplier(side: object) -> int | None:
    normalized = str(side or "").strip().upper()
    if normalized in {"BOT", "BUY"}:
        return -1
    if normalized in {"SLD", "SELL"}:
        return 1
    return None


def _transaction_model(fill: object) -> TransactionModel:
    execution = getattr(fill, "execution", None)
    commission_report = getattr(fill, "commissionReport", None)
    side = getattr(execution, "side", None)
    quantity = _optional_float(getattr(execution, "shares", None))
    price = _optional_float(getattr(execution, "price", None))
    gross_amount = (
        abs(quantity * price) if quantity is not None and price is not None else None
    )
    commission = _optional_float(getattr(commission_report, "commission", None))
    realized_pnl = _optional_float(getattr(commission_report, "realizedPNL", None))
    multiplier = _execution_side_multiplier(side)
    net_amount = None
    if gross_amount is not None and multiplier is not None:
        net_amount = (gross_amount * multiplier) - (commission or 0.0)
    return TransactionModel(
        execId=getattr(execution, "execId", None),
        orderId=getattr(execution, "orderId", None),
        permId=getattr(execution, "permId", None),
        account=getattr(execution, "acctNumber", None),
        time=_format_time(getattr(fill, "time", None)),
        side=side,
        quantity=quantity,
        price=price,
        grossAmount=gross_amount,
        commission=commission,
        commissionCurrency=getattr(commission_report, "currency", None),
        realizedPnl=realized_pnl,
        netAmount=net_amount,
        contract=_contract_model(getattr(fill, "contract", None)),
    )


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


def _greeks_model(value: object | None) -> OptionGreeksModel | None:
    if value is None:
        return None
    model = OptionGreeksModel(
        impliedVol=_optional_float(getattr(value, "impliedVol", None)),
        delta=_optional_float(getattr(value, "delta", None)),
        optPrice=_optional_float(getattr(value, "optPrice", None)),
        pvDividend=_optional_float(getattr(value, "pvDividend", None)),
        gamma=_optional_float(getattr(value, "gamma", None)),
        vega=_optional_float(getattr(value, "vega", None)),
        theta=_optional_float(getattr(value, "theta", None)),
        undPrice=_optional_float(getattr(value, "undPrice", None)),
    )
    if all(
        getattr(model, field) is None
        for field in (
            "impliedVol",
            "delta",
            "optPrice",
            "pvDividend",
            "gamma",
            "vega",
            "theta",
            "undPrice",
        )
    ):
        return None
    return model


def _select_preferred_greeks(
    model_greeks: OptionGreeksModel | None,
    last_greeks: OptionGreeksModel | None,
    bid_greeks: OptionGreeksModel | None,
    ask_greeks: OptionGreeksModel | None,
) -> tuple[OptionGreeksModel | None, str | None]:
    ordered = [
        ("model", model_greeks),
        ("last", last_greeks),
        ("bid", bid_greeks),
        ("ask", ask_greeks),
    ]
    for source, greeks in ordered:
        if greeks is not None and greeks.delta is not None:
            return greeks, source
    for source, greeks in ordered:
        if greeks is not None:
            return greeks, source
    return None, None


def _snapshot_from_ticker(ticker: object) -> MarketDataSnapshotModel:
    contract = getattr(ticker, "contract", None)
    market_price_value = getattr(ticker, "marketPrice", None)
    if callable(market_price_value):
        market_price_value = market_price_value()

    model_greeks = _greeks_model(getattr(ticker, "modelGreeks", None))
    bid_greeks = _greeks_model(getattr(ticker, "bidGreeks", None))
    ask_greeks = _greeks_model(getattr(ticker, "askGreeks", None))
    last_greeks = _greeks_model(getattr(ticker, "lastGreeks", None))
    preferred_greeks, source = _select_preferred_greeks(
        model_greeks,
        last_greeks,
        bid_greeks,
        ask_greeks,
    )

    return MarketDataSnapshotModel(
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
        delta=getattr(preferred_greeks, "delta", None),
        gamma=getattr(preferred_greeks, "gamma", None),
        vega=getattr(preferred_greeks, "vega", None),
        theta=getattr(preferred_greeks, "theta", None),
        impliedVol=getattr(preferred_greeks, "impliedVol", None),
        optPrice=getattr(preferred_greeks, "optPrice", None),
        undPrice=getattr(preferred_greeks, "undPrice", None),
        greeksSource=source,
        modelGreeks=model_greeks,
        bidGreeks=bid_greeks,
        askGreeks=ask_greeks,
        lastGreeks=last_greeks,
    )


def _append_option_greeks_note(
    snapshots: list[MarketDataSnapshotModel],
    notes: list[str],
) -> None:
    option_snapshots = [
        snapshot
        for snapshot in snapshots
        if (snapshot.secType or "").upper() in {"OPT", "FOP"}
    ]
    if option_snapshots and all(snapshot.delta is None for snapshot in option_snapshots):
        notes.append(
            "option greeks unavailable in snapshot; ensure market data subscriptions for options and underlying"
        )


def _trading_enabled() -> bool:
    return _env_bool("IBKR_ENABLE_TRADING", False)


def _ensure_trading_allowed(confirm: bool) -> dict | None:
    if not _trading_enabled():
        return _error_response(
            "TRADING_DISABLED",
            "live trading is disabled; set IBKR_ENABLE_TRADING=true to enable mutating tools",
            False,
        )
    if not confirm:
        return _error_response(
            "CONFIRM_REQUIRED",
            "confirm must be true for this trading action",
            False,
        )
    return None


def _jsonify(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {
            field.name: _jsonify(getattr(value, field.name))
            for field in dataclass_fields(value)
        }
    if isinstance(value, dict):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonify(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            str(key): _jsonify(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return str(value)


def _trade_snapshot_model(trade: object | None) -> TradeSnapshotModel | None:
    if trade is None:
        return None
    is_done_value = getattr(trade, "isDone", None)
    is_done = is_done_value() if callable(is_done_value) else None
    return TradeSnapshotModel(
        contract=_jsonify(getattr(trade, "contract", None)) or {},
        order=_jsonify(getattr(trade, "order", None)) or {},
        orderStatus=_jsonify(getattr(trade, "orderStatus", None)) or {},
        fills=_jsonify(list(getattr(trade, "fills", []) or [])) or [],
        log=_jsonify(list(getattr(trade, "log", []) or [])) or [],
        advancedError=getattr(trade, "advancedError", None),
        isDone=is_done if isinstance(is_done, bool) else None,
    )


def _order_state_model(order_state: object | None) -> OrderStateModel | None:
    if order_state is None:
        return None
    return OrderStateModel(
        status=getattr(order_state, "status", None),
        initMarginBefore=getattr(order_state, "initMarginBefore", None),
        maintMarginBefore=getattr(order_state, "maintMarginBefore", None),
        equityWithLoanBefore=getattr(order_state, "equityWithLoanBefore", None),
        initMarginChange=getattr(order_state, "initMarginChange", None),
        maintMarginChange=getattr(order_state, "maintMarginChange", None),
        equityWithLoanChange=getattr(order_state, "equityWithLoanChange", None),
        initMarginAfter=getattr(order_state, "initMarginAfter", None),
        maintMarginAfter=getattr(order_state, "maintMarginAfter", None),
        equityWithLoanAfter=getattr(order_state, "equityWithLoanAfter", None),
        commission=_optional_float(getattr(order_state, "commission", None)),
        minCommission=_optional_float(getattr(order_state, "minCommission", None)),
        maxCommission=_optional_float(getattr(order_state, "maxCommission", None)),
        commissionCurrency=getattr(order_state, "commissionCurrency", None),
        warningText=getattr(order_state, "warningText", None),
        completedTime=getattr(order_state, "completedTime", None),
        completedStatus=getattr(order_state, "completedStatus", None),
    )


def _run_with_client(action) -> dict:
    with _CLIENT_SESSION_LOCK:
        client = create_client()
        try:
            client.connect()
            return action(client)
        except IBKRConnectionError as exc:
            logger.warning("tws connection failed", exc_info=True)
            return _error_response("TWS_CONNECTION_FAILED", str(exc), True)
        except IBKRMarketDataTimeoutError as exc:
            logger.warning("market data snapshot timed out", exc_info=True)
            return _error_response("MARKET_DATA_TIMEOUT", str(exc), True)
        except Exception as exc:
            logger.exception("ibkr tool failed")
            return _error_response("INTERNAL_ERROR", str(exc), False)
        finally:
            client.disconnect()


def create_statement_client() -> StatementClient:
    return StatementClient.from_env()


def _run_with_statement_client(action) -> dict:
    try:
        client = create_statement_client()
        return action(client)
    except StatementConfigError as exc:
        return _error_response("STATEMENT_CONFIG_ERROR", str(exc), False)
    except StatementRequestError as exc:
        return _error_response("STATEMENT_REQUEST_FAILED", str(exc), exc.retryable)
    except Exception as exc:
        logger.exception("statement tool failed")
        return _error_response("INTERNAL_ERROR", str(exc), False)


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


def _ibkr_get_transactions_sync(
    account: str | None = None,
    symbol: str | None = None,
    secType: str | None = None,
    exchange: str | None = None,
    side: str | None = None,
    fromTime: str | None = None,
    toTime: str | None = None,
    limit: int = 100,
) -> dict:
    def action(client: IBKRClient) -> dict:
        try:
            start_dt = _parse_time_filter(fromTime)
            end_dt = _parse_time_filter(toTime)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)
        if start_dt and end_dt and start_dt > end_dt:
            return _error_response("INVALID_ARGUMENT", "fromTime must be <= toTime", False)
        normalized_limit = _optional_int(limit)
        if normalized_limit is None or normalized_limit <= 0:
            return _error_response("INVALID_ARGUMENT", "limit must be a positive integer", False)

        resolved_account = _resolve_optional_account(account)
        exec_filter = ExecutionFilter(
            acctCode=resolved_account or "",
            symbol=symbol or "",
            secType=secType or "",
            exchange=exchange or "",
            side=side or "",
            time="",
        )
        notes: list[str] = []
        transactions: list[TransactionModel] = []
        skipped_missing_time = 0
        for fill in client.get_executions(exec_filter):
            transaction = _transaction_model(fill)
            if not transaction.time:
                if start_dt or end_dt:
                    skipped_missing_time += 1
                    continue
                transactions.append(transaction)
                continue
            try:
                execution_dt = _parse_time_filter(transaction.time)
            except ValueError:
                notes.append(f"unparseable transaction time skipped: {transaction.time}")
                continue
            if start_dt and execution_dt and execution_dt < start_dt:
                continue
            if end_dt and execution_dt and execution_dt > end_dt:
                continue
            transactions.append(transaction)

        transactions.sort(key=lambda item: item.time or "", reverse=True)
        if len(transactions) > normalized_limit:
            notes.append(f"results truncated to limit={normalized_limit}")
            transactions = transactions[:normalized_limit]
        if skipped_missing_time:
            notes.append(
                f"skipped {skipped_missing_time} transaction(s) without timestamps for date filtering"
            )
        if not transactions:
            notes.append("no transactions returned")
        response = TransactionsResponse(transactions=transactions, notes=notes)
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool(
    title="Get Transactions",
    description="Return past transaction history from IBKR executions, enriched with commissions and net cash flow when available.",
    output_schema=_combined_output_schema(TransactionsResponse),
)
async def ibkr_get_transactions(
    account: OptionalAccountId = None,
    symbol: Symbol = None,
    secType: OptionalSecType = None,
    exchange: ExecutionExchange = None,
    side: ExecutionSide = None,
    fromTime: FromTime = None,
    toTime: ToTime = None,
    limit: ResultLimit = 100,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_transactions_sync,
        account,
        symbol,
        secType,
        exchange,
        side,
        fromTime,
        toTime,
        limit,
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
    market_data_type: int | None = None,
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
        if market_data_type is not None:
            try:
                client.ib.reqMarketDataType(int(market_data_type))
                notes.append(f"market data type set to {int(market_data_type)}")
            except Exception as exc:
                notes.append(f"market data type set failed: {exc}")
        snapshots = []
        tickers, qualification_notes = client.get_market_data_snapshot(
            contract_list,
            regulatory_snapshot=regulatory_snapshot,
        )
        notes.extend(qualification_notes)
        for ticker in tickers:
            snapshots.append(_snapshot_from_ticker(ticker))
        _append_option_greeks_note(snapshots, notes)
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
    market_data_type: MarketDataType = None,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_market_data_snapshot_sync,
        contracts,
        regulatory_snapshot,
        market_data_type,
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
        notes.append(
            "option chain returns metadata only (expirations/strikes); use ibkr_get_market_data_snapshot on option contracts for greeks like delta"
        )
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


def _ibkr_get_flex_statement_sync(
    queryId: str | None = None,
    format: str = "json",
) -> dict:
    def action(client: StatementClient) -> dict:
        normalized_format = str(format or "json").strip().lower()
        if normalized_format not in {"json", "xml"}:
            return _error_response("INVALID_ARGUMENT", "format must be 'json' or 'xml'", False)
        result = client.get_flex_statement(query_id=queryId, format=normalized_format)
        response = FlexStatementResponse(
            queryId=(queryId or client.query_id or ""),
            referenceCode=result.reference_code,
            format=normalized_format,
            url=result.url,
            statement=result.statement,
            notes=result.notes,
        )
        return response.model_dump()

    return _run_with_statement_client(action)


@mcp.tool(
    title="Get Flex Statement",
    description=(
        "Fetch an IBKR Flex Web Service statement/report for a configured query id. "
        "This uses Flex reporting, not the live TWS socket session."
    ),
    output_schema=_combined_output_schema(FlexStatementResponse),
)
async def ibkr_get_flex_statement(
    queryId: QueryId = None,
    format: ResponseFormat = "json",
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_flex_statement_sync,
        queryId,
        format,
    )


def _cash_activity_item(entry: CashActivityEntry) -> CashActivityItem:
    return CashActivityItem(
        date=entry.date,
        type=entry.type,
        description=entry.description,
        amount=entry.amount,
        currency=entry.currency,
        symbol=entry.symbol,
        accountId=entry.account_id,
        sourceTopic=entry.source_topic,
    )


def _ibkr_get_cash_activity_sync(
    queryId: str | None = None,
) -> dict:
    def action(client: StatementClient) -> dict:
        items, resolved_query_id, notes = client.get_cash_activity(queryId)
        response = CashActivityResponse(
            queryId=resolved_query_id,
            items=[_cash_activity_item(item) for item in items],
            notes=notes,
        )
        return response.model_dump()

    return _run_with_statement_client(action)


@mcp.tool(
    title="Get Cash Activity",
    description=(
        "Extract normalized cash activity from an IBKR Flex statement, "
        "including dividends and other cash movements when present."
    ),
    output_schema=_combined_output_schema(CashActivityResponse),
)
async def ibkr_get_cash_activity(
    queryId: QueryId = None,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_cash_activity_sync,
        queryId,
    )


def _statement_summary_response(summary: StatementSummary) -> StatementSummaryResponse:
    return StatementSummaryResponse(
        queryId=summary.query_id,
        period=summary.period,
        currency=summary.currency,
        startingNav=summary.starting_nav,
        endingNav=summary.ending_nav,
        netDeposits=summary.net_deposits,
        withdrawals=summary.withdrawals,
        dividends=summary.dividends,
        withholdingTax=summary.withholding_tax,
        interest=summary.interest,
        fees=summary.fees,
        tradeCount=summary.trade_count,
        notes=summary.notes,
    )


def _ibkr_get_statement_summary_sync(
    queryId: str | None = None,
) -> dict:
    def action(client: StatementClient) -> dict:
        summary = client.get_statement_summary(queryId)
        return _statement_summary_response(summary).model_dump()

    return _run_with_statement_client(action)


@mcp.tool(
    title="Get Statement Summary",
    description=(
        "Return a compact summary of an IBKR Flex statement, including available NAV, "
        "cash movements, dividends, fees, and trade count."
    ),
    output_schema=_combined_output_schema(StatementSummaryResponse),
)
async def ibkr_get_statement_summary(
    queryId: QueryId = None,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_statement_summary_sync,
        queryId,
    )


def _dividend_item(entry: DividendEntry) -> DividendItem:
    return DividendItem(
        date=entry.date,
        description=entry.description,
        symbol=entry.symbol,
        amount=entry.amount,
        withholdingTax=entry.withholding_tax,
        currency=entry.currency,
        accountId=entry.account_id,
        sourceTopic=entry.source_topic,
    )


def _ibkr_get_dividends_sync(
    queryId: str | None = None,
) -> dict:
    def action(client: StatementClient) -> dict:
        items, resolved_query_id, notes = client.get_dividends(queryId)
        total_dividends = sum(
            item.amount for item in items if item.amount is not None
        ) if items else None
        total_withholding_tax = sum(
            item.withholding_tax for item in items if item.withholding_tax is not None
        ) if items else None
        response = DividendsResponse(
            queryId=resolved_query_id,
            items=[_dividend_item(item) for item in items],
            totalDividends=total_dividends,
            totalWithholdingTax=total_withholding_tax,
            notes=notes,
        )
        return response.model_dump()

    return _run_with_statement_client(action)


@mcp.tool(
    title="Get Dividends",
    description=(
        "Extract dividend activity from an IBKR Flex statement, including withholding tax "
        "when present."
    ),
    output_schema=_combined_output_schema(DividendsResponse),
)
async def ibkr_get_dividends(
    queryId: QueryId = None,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_dividends_sync,
        queryId,
    )


def _trade_confirmation_item(entry: TradeConfirmationEntry) -> TradeConfirmationItem:
    return TradeConfirmationItem(
        dateTime=entry.date_time,
        symbol=entry.symbol,
        description=entry.description,
        side=entry.side,
        quantity=entry.quantity,
        price=entry.price,
        proceeds=entry.proceeds,
        commission=entry.commission,
        currency=entry.currency,
        accountId=entry.account_id,
        tradeId=entry.trade_id,
        orderId=entry.order_id,
        sourceTopic=entry.source_topic,
    )


def _ibkr_get_trade_confirmations_sync(
    queryId: str | None = None,
) -> dict:
    def action(client: StatementClient) -> dict:
        items, resolved_query_id, notes = client.get_trade_confirmations(queryId)
        response = TradeConfirmationsResponse(
            queryId=resolved_query_id,
            items=[_trade_confirmation_item(item) for item in items],
            notes=notes,
        )
        return response.model_dump()

    return _run_with_statement_client(action)


@mcp.tool(
    title="Get Trade Confirmations",
    description=(
        "Extract historical trade confirmations from an IBKR Flex statement, using "
        "TradeConfirm rows when available."
    ),
    output_schema=_combined_output_schema(TradeConfirmationsResponse),
)
async def ibkr_get_trade_confirmations(
    queryId: QueryId = None,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_trade_confirmations_sync,
        queryId,
    )


def _statement_topic_item(entry: StatementTopicEntry) -> StatementTopicItem:
    return StatementTopicItem(
        topic=entry.topic,
        count=entry.count,
    )


def _ibkr_get_statement_topics_sync(
    queryId: str | None = None,
) -> dict:
    def action(client: StatementClient) -> dict:
        items, resolved_query_id, notes = client.get_statement_topics(queryId)
        response = StatementTopicsResponse(
            queryId=resolved_query_id,
            topics=[_statement_topic_item(item) for item in items],
            notes=notes,
        )
        return response.model_dump()

    return _run_with_statement_client(action)


@mcp.tool(
    title="Get Statement Topics",
    description=(
        "Inspect the extractable topic names present in an IBKR Flex statement, with row counts "
        "to help validate query layouts."
    ),
    output_schema=_combined_output_schema(StatementTopicsResponse),
)
async def ibkr_get_statement_topics(
    queryId: QueryId = None,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_get_statement_topics_sync,
        queryId,
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


def _normalize_bracket_order_options(
    options: dict | None,
) -> tuple[dict[str, object], list[str]] | tuple[None, None]:
    if options is None:
        return {}, []
    if not isinstance(options, dict):
        return None, None
    notes: list[str] = []
    forbidden = {
        "action",
        "totalQuantity",
        "orderType",
        "lmtPrice",
        "auxPrice",
        "orderId",
        "parentId",
        "transmit",
    }
    normalized: dict[str, object] = {}
    for key, value in options.items():
        if key not in _ORDER_FIELD_NAMES:
            notes.append(f"ignored unknown bracket order option '{key}'")
            continue
        if key in forbidden:
            notes.append(f"ignored bracket order option '{key}'")
            continue
        normalized[str(key)] = value
    return normalized, notes


def _ibkr_preview_order_sync(
    contract: dict,
    order: dict,
) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        if not isinstance(contract, dict):
            return _error_response("INVALID_ARGUMENT", "contract must be an object", False)
        if not isinstance(order, dict):
            return _error_response("INVALID_ARGUMENT", "order must be an object", False)
        try:
            contract_obj = IBKRClient.contract_from_input(contract)
            order_obj = IBKRClient.order_from_input(order, default_transmit=False)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)
        order_state, qualification_notes = client.preview_order(contract_obj, order_obj)
        notes.extend(qualification_notes)
        response = PreviewOrderResponse(
            orderState=_order_state_model(order_state),
            notes=notes,
        )
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool(
    title="Preview Order",
    description="Run IBKR what-if analysis for an order and return margin/commission impact.",
    output_schema=_combined_output_schema(PreviewOrderResponse),
)
async def ibkr_preview_order(
    contract: ContractInput,
    order: OrderInput,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_preview_order_sync,
        contract,
        order,
    )


def _ibkr_place_order_sync(
    contract: dict,
    order: dict,
    confirm: bool = False,
    dry_run: bool = True,
    transmit: bool = False,
) -> dict:
    def action(client: IBKRClient) -> dict:
        notes: list[str] = []
        if not isinstance(contract, dict):
            return _error_response("INVALID_ARGUMENT", "contract must be an object", False)
        if not isinstance(order, dict):
            return _error_response("INVALID_ARGUMENT", "order must be an object", False)
        try:
            contract_obj = IBKRClient.contract_from_input(contract)
            order_obj = IBKRClient.order_from_input(order, default_transmit=transmit)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)
        order_obj.transmit = transmit
        if dry_run:
            notes.append("dry_run=true; order not sent")
            notes.append(f"order transmit={bool(order_obj.transmit)}")
            response = PlaceOrderResponse(trade=None, notes=notes)
            return response.model_dump()
        trading_error = _ensure_trading_allowed(confirm)
        if trading_error:
            return trading_error

        trade, qualification_notes = client.place_order(contract_obj, order_obj)
        notes.extend(qualification_notes)
        if trade is None:
            notes.append("order not placed")
        response = PlaceOrderResponse(
            trade=_trade_snapshot_model(trade),
            notes=notes,
        )
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool(
    title="Place Order",
    description="Place an IBKR order. Defaults to dry_run=true and transmit=false for safety.",
    output_schema=_combined_output_schema(PlaceOrderResponse),
)
async def ibkr_place_order(
    contract: ContractInput,
    order: OrderInput,
    confirm: Confirm = False,
    dry_run: DryRun = True,
    transmit: Transmit = False,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_place_order_sync,
        contract,
        order,
        confirm,
        dry_run,
        transmit,
    )


def _ibkr_cancel_order_sync(
    orderId: int,
    confirm: bool = False,
) -> dict:
    def action(client: IBKRClient) -> dict:
        trading_error = _ensure_trading_allowed(confirm)
        if trading_error:
            return trading_error
        order_id = _optional_int(orderId)
        if order_id is None:
            return _error_response("INVALID_ARGUMENT", "orderId must be an integer", False)
        trade, notes = client.cancel_order_by_id(order_id)
        response = CancelOrderResponse(
            orderId=order_id,
            trade=_trade_snapshot_model(trade),
            notes=notes,
        )
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool(
    title="Cancel Order",
    description="Cancel a single IBKR order by orderId. Requires confirm=true.",
    output_schema=_combined_output_schema(CancelOrderResponse),
)
async def ibkr_cancel_order(
    orderId: OrderId,
    confirm: Confirm = False,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_cancel_order_sync,
        orderId,
        confirm,
    )


def _ibkr_global_cancel_sync(confirm: bool = False) -> dict:
    def action(client: IBKRClient) -> dict:
        trading_error = _ensure_trading_allowed(confirm)
        if trading_error:
            return trading_error
        client.global_cancel()
        response = GlobalCancelResponse(status="submitted", notes=[])
        return response.model_dump()

    return _run_with_client(action)


@mcp.tool(
    title="Global Cancel",
    description="Cancel all active IBKR orders. Requires confirm=true.",
    output_schema=_combined_output_schema(GlobalCancelResponse),
)
async def ibkr_global_cancel(confirm: Confirm = False) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_global_cancel_sync,
        confirm,
    )


def _ibkr_bracket_order_sync(
    contract: dict,
    action: str,
    quantity: float,
    limitPrice: float,
    takeProfitPrice: float,
    stopLossPrice: float,
    confirm: bool = False,
    dry_run: bool = True,
    transmit: bool = False,
    orderOptions: dict | None = None,
) -> dict:
    def action_fn(client: IBKRClient) -> dict:
        notes: list[str] = []
        if not isinstance(contract, dict):
            return _error_response("INVALID_ARGUMENT", "contract must be an object", False)
        try:
            contract_obj = IBKRClient.contract_from_input(contract)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)

        normalized_options, option_notes = _normalize_bracket_order_options(orderOptions)
        if normalized_options is None:
            return _error_response("INVALID_ARGUMENT", "orderOptions must be an object", False)
        notes.extend(option_notes or [])
        normalized_action = str(action or "").strip().upper()
        if normalized_action not in {"BUY", "SELL"}:
            return _error_response("INVALID_ARGUMENT", "action must be BUY or SELL", False)

        if dry_run:
            notes.append("dry_run=true; bracket order not sent")
            notes.append(f"order transmit={bool(transmit)}")
            response = BracketOrderResponse(orderIds=[], trades=[], notes=notes)
            return response.model_dump()

        trading_error = _ensure_trading_allowed(confirm)
        if trading_error:
            return trading_error

        trades, qualification_notes = client.place_bracket_order(
            contract=contract_obj,
            action=normalized_action,
            quantity=float(quantity),
            limit_price=float(limitPrice),
            take_profit_price=float(takeProfitPrice),
            stop_loss_price=float(stopLossPrice),
            transmit=transmit,
            order_kwargs=normalized_options,
        )
        notes.extend(qualification_notes)
        snapshots = [snapshot for trade in trades if (snapshot := _trade_snapshot_model(trade))]
        order_ids = [
            _optional_int(getattr(getattr(trade, "order", None), "orderId", None))
            for trade in trades
        ]
        response = BracketOrderResponse(
            orderIds=[order_id for order_id in order_ids if order_id is not None],
            trades=snapshots,
            notes=notes,
        )
        return response.model_dump()

    return _run_with_client(action_fn)


@mcp.tool(
    title="Bracket Order",
    description=(
        "Create and place a bracket order (entry, take-profit, stop-loss). "
        "Defaults to dry_run=true and transmit=false."
    ),
    output_schema=_combined_output_schema(BracketOrderResponse),
)
async def ibkr_bracket_order(
    contract: ContractInput,
    action: OrderAction,
    quantity: OrderQuantity,
    limitPrice: LimitPrice,
    takeProfitPrice: TakeProfitPrice,
    stopLossPrice: StopLossPrice,
    confirm: Confirm = False,
    dry_run: DryRun = True,
    transmit: Transmit = False,
    orderOptions: OrderOptionsInput | None = None,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_bracket_order_sync,
        contract,
        action,
        quantity,
        limitPrice,
        takeProfitPrice,
        stopLossPrice,
        confirm,
        dry_run,
        transmit,
        orderOptions,
    )


def _ibkr_oca_group_sync(
    orders: list[dict],
    ocaGroup: str,
    ocaType: int,
    confirm: bool = False,
    dry_run: bool = True,
    transmit: bool = False,
) -> dict:
    def action_fn(client: IBKRClient) -> dict:
        notes: list[str] = []
        if not isinstance(orders, list):
            return _error_response("INVALID_ARGUMENT", "orders must be a list", False)
        parsed_entries: list[tuple[object, object]] = []
        for index, item in enumerate(orders):
            if not isinstance(item, dict):
                return _error_response("INVALID_ARGUMENT", f"orders[{index}] must be an object", False)
            contract_input = item.get("contract")
            order_input = item.get("order")
            if not isinstance(contract_input, dict) or not isinstance(order_input, dict):
                return _error_response(
                    "INVALID_ARGUMENT",
                    f"orders[{index}] requires object fields: contract and order",
                    False,
                )
            try:
                contract_obj = IBKRClient.contract_from_input(contract_input)
                order_obj = IBKRClient.order_from_input(order_input, default_transmit=transmit)
            except ValueError as exc:
                return _error_response("INVALID_ARGUMENT", f"orders[{index}] {exc}", False)
            order_obj.transmit = transmit
            parsed_entries.append((contract_obj, order_obj))
        if not parsed_entries:
            return _error_response("INVALID_ARGUMENT", "orders must not be empty", False)

        normalized_oca_type = _optional_int(ocaType)
        if normalized_oca_type is None:
            return _error_response("INVALID_ARGUMENT", "ocaType must be an integer", False)

        if dry_run:
            notes.append("dry_run=true; OCA orders not sent")
            notes.append(f"order transmit={bool(transmit)}")
            response = OcaGroupResponse(
                ocaGroup=ocaGroup,
                ocaType=normalized_oca_type,
                orderIds=[],
                trades=[],
                notes=notes,
            )
            return response.model_dump()

        trading_error = _ensure_trading_allowed(confirm)
        if trading_error:
            return trading_error

        trades, qualification_notes = client.apply_oca_group_and_place(
            entries=parsed_entries,
            oca_group=ocaGroup,
            oca_type=normalized_oca_type,
            transmit=transmit,
        )
        notes.extend(qualification_notes)
        snapshots = [snapshot for trade in trades if (snapshot := _trade_snapshot_model(trade))]
        order_ids = [
            _optional_int(getattr(getattr(trade, "order", None), "orderId", None))
            for trade in trades
        ]
        response = OcaGroupResponse(
            ocaGroup=ocaGroup,
            ocaType=normalized_oca_type,
            orderIds=[order_id for order_id in order_ids if order_id is not None],
            trades=snapshots,
            notes=notes,
        )
        return response.model_dump()

    return _run_with_client(action_fn)


@mcp.tool(
    title="Place OCA Group",
    description=(
        "Apply an OCA group to multiple orders and place them. "
        "Defaults to dry_run=true and transmit=false."
    ),
    output_schema=_combined_output_schema(OcaGroupResponse),
)
async def ibkr_oca_group(
    orders: OcaOrdersInput,
    ocaGroup: OcaGroup,
    ocaType: OcaType,
    confirm: Confirm = False,
    dry_run: DryRun = True,
    transmit: Transmit = False,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_oca_group_sync,
        orders,
        ocaGroup,
        ocaType,
        confirm,
        dry_run,
        transmit,
    )


def _ibkr_exercise_options_sync(
    contract: dict,
    exerciseAction: int,
    exerciseQuantity: int,
    account: str | None,
    override: int,
    confirm: bool = False,
) -> dict:
    def action_fn(client: IBKRClient) -> dict:
        trading_error = _ensure_trading_allowed(confirm)
        if trading_error:
            return trading_error
        if not isinstance(contract, dict):
            return _error_response("INVALID_ARGUMENT", "contract must be an object", False)
        try:
            contract_obj = IBKRClient.contract_from_input(contract)
        except ValueError as exc:
            return _error_response("INVALID_ARGUMENT", str(exc), False)
        resolved_account, account_notes = _resolve_account(account, client)
        status, notes = client.exercise_options(
            contract_obj,
            int(exerciseAction),
            int(exerciseQuantity),
            resolved_account,
            int(override),
        )
        response = ExerciseOptionsResponse(status=status, notes=[*account_notes, *notes])
        return response.model_dump()

    return _run_with_client(action_fn)


@mcp.tool(
    title="Exercise Options",
    description="Exercise or lapse an options contract. Requires confirm=true.",
    output_schema=_combined_output_schema(ExerciseOptionsResponse),
)
async def ibkr_exercise_options(
    contract: ContractInput,
    exerciseAction: ExerciseAction,
    exerciseQuantity: ExerciseQuantity,
    account: AccountId = None,
    override: Override = 0,
    confirm: Confirm = False,
) -> dict:
    return await anyio.to_thread.run_sync(
        _ibkr_exercise_options_sync,
        contract,
        exerciseAction,
        exerciseQuantity,
        account,
        override,
        confirm,
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
                snapshots.append(_snapshot_from_ticker(ticker))
            _append_option_greeks_note(snapshots, attempt_notes)
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
    startup_context = _startup_log_context()

    logger.info("starting mcp server", extra=startup_context)
    uvicorn.run(
        "mcp_ibkr.server:app",
        host=str(startup_context["mcp_bind_host"]),
        port=int(startup_context["mcp_port"]),
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
