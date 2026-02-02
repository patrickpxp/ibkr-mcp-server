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
    MarketDataSnapshotModel,
    MarketDataSnapshotAttempt,
    MarketDataSnapshotDebugResponse,
    MarketDataSnapshotResponse,
    OpenOrderModel,
    OpenOrdersResponse,
    PortfolioResponse,
    PositionModel,
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
