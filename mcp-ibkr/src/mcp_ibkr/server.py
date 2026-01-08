import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastmcp import FastMCP
from starlette.responses import JSONResponse
import uvicorn

from .ibkr_client import IBKRClient, IBKRConnectionError
from .logging_utils import configure_logging
from .models import ErrorDetails, ErrorResponse, PortfolioResponse, PositionModel, TotalsModel

logger = logging.getLogger(__name__)

mcp = FastMCP("IBKR MCP")


def create_app() -> FastAPI:
    mcp_app = mcp.http_app()
    app = FastAPI(lifespan=mcp_app.lifespan)

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.mount("/mcp", mcp_app)
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


@mcp.tool
def ibkr_get_portfolio(
    account: str | None = None,
    include_pnl: bool = True,
    as_of: str | None = None,
) -> dict:
    client = create_client()
    try:
        client.connect()
    except IBKRConnectionError as exc:
        logger.warning("tws connection failed", exc_info=True)
        error = ErrorResponse(
            error=ErrorDetails(
                type="TWS_CONNECTION_FAILED",
                message=str(exc),
                retryable=True,
            )
        )
        return error.model_dump()

    try:
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
    except Exception as exc:
        logger.exception("ibkr_get_portfolio failed")
        error = ErrorResponse(
            error=ErrorDetails(
                type="INTERNAL_ERROR",
                message=str(exc),
                retryable=False,
            )
        )
        return error.model_dump()
    finally:
        client.disconnect()


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
