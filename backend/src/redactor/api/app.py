"""FastAPI application (plan §4.10). Local only: binds 127.0.0.1, refuses non-loopback clients and
foreign Host headers, ships no CDN assets (Swagger/ReDoc UIs are disabled; the OpenAPI JSON is served
for type generation), installs the network guard, and logs nothing that carries document text."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..dataset.store import StoreError
from ..security import netguard

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def create_app() -> FastAPI:
    netguard.install()
    app = FastAPI(title="redactor local API", version="0.1.0", docs_url=None, redoc_url=None, openapi_url="/openapi.json")

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        client = request.client.host if request.client else ""
        host = (request.headers.get("host") or "").split(":")[0].strip("[]")
        if client not in LOOPBACK or host not in LOOPBACK:
            return JSONResponse({"error": "local_only"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(StoreError)
    async def store_error(_: Request, exc: StoreError) -> JSONResponse:
        body: dict[str, Any] = {"error": exc.code}
        if exc.detail is not None:
            body["detail"] = exc.detail
        return JSONResponse(body, status_code=exc.status)

    from .routes import annotations, documents, runs
    app.include_router(documents.router)
    app.include_router(annotations.router)
    app.include_router(runs.router)
    try:
        from .routes import review  # G1 onwards
        app.include_router(review.router)
    except ImportError:
        pass
    try:
        from .routes import outputs  # X1 onwards
        app.include_router(outputs.router)
    except ImportError:
        pass
    try:
        from .routes import intake  # U1 onwards
        app.include_router(intake.router)
    except ImportError:
        pass

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "network_guard": netguard.is_installed()}

    return app


def serve(port: int = 8765) -> None:
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning", access_log=False, server_header=False)
