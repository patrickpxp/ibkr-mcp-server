import logging
import os

from fastapi import FastAPI
from fastmcp import FastMCP
from starlette.responses import JSONResponse
import uvicorn

from .logging_utils import configure_logging

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
