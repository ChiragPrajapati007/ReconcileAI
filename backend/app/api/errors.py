"""
Centralized error handling for the ReconcileAI API.

All routes raise AppError for predictable HTTP errors.
A registered exception handler converts it to the standard error envelope:

    {
        "error": {
            "code": "INVOICE_NOT_FOUND",
            "message": "Invoice abc123 not found."
        }
    }

Unexpected exceptions (500) are caught separately to avoid leaking stack traces.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Raised from routes to produce a structured error response."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def register_error_handlers(app: FastAPI) -> None:
    """Attach exception handlers to the FastAPI app."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # Do NOT expose internal details
        return JSONResponse(
            status_code=500,
            content=_error_body("INTERNAL_SERVER_ERROR", "An unexpected error occurred."),
        )
