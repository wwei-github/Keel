"""FastAPI 应用装配。

这个文件只做三件事：注册路由、挂异常处理器、挂中间件。
任何业务分支都不该出现在这里——应用装配一旦长出 ``if``，就再也没人敢删东西。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from keel import __version__
from keel.api.routers import backtests, health, market, ops, reports
from keel.errors import ErrorCode, KeelError
from keel.logging import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging("api")
    log.info("api started", extra={"version": __version__})
    yield
    log.info("api stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Keel",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # 前端开发时跑在 5173；生产由 web 容器同源反代，不需要 CORS。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(KeelError)
    async def handle_keel_error(_request, exc: KeelError) -> JSONResponse:
        # §6.1：失败响应统一形状，code 是稳定枚举，前端靠它分支。
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {
                    "code": exc.code.value,
                    "message": exc.message,
                    "details": exc.details,
                },
                "request_id": None,
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": ErrorCode.INTERNAL.value,
                    "message": "internal error",
                    "details": {},
                },
                "request_id": None,
            },
        )

    for router in (
        health.router,
        market.router,
        backtests.router,
        reports.router,
        ops.router,
    ):
        app.include_router(router)

    return app


app = create_app()


def main() -> None:
    """``keel-api`` 入口。生产用 uvicorn 直接跑，这个函数只服务本地开发。"""
    import uvicorn

    from keel.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "keel.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=(settings.env == "local"),
    )
