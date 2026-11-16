"""The Savvy app server: one FastAPI app with a router per area (docs/app-api.md).

Each routes_*.py module belongs to one part of the build and exposes `router`.
Errors use {"error": code, "message": text}: raise ApiError from a route.
"""

import importlib

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ROUTE_MODULES = [
    "routes_core", "routes_google", "routes_today", "routes_plan", "routes_diary",
    "routes_chat", "routes_inbox", "routes_push",
]


class ApiError(Exception):
    """Raise from any route: becomes {"error": code, "message": message} with `status`."""

    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message or code


def create_app() -> FastAPI:
    app = FastAPI(title="Savvy", version="1", docs_url=None, redoc_url=None)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status)

    for name in ROUTE_MODULES:
        module = importlib.import_module(f"app_api.{name}")
        app.include_router(module.router)
    return app
