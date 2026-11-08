"""Error vocabulary and HTTP mapping for the wrapper rail application.

The wrapper reads the board and the sessions dashboard and serves them as one rail. A failing
source, an unknown project or a body the client mangled has to reach the client as a defined
status code with a JSON body that names the failing upstream; this module holds that vocabulary
and installs the handlers that produce those answers.

Mapping installed by `install_error_handlers`:

- `UnknownProjectError` answers `400` with a JSON body carrying the message it was raised with.
- `RequestValidationError` — a body the client mangled, including one that is not valid JSON —
  answers `400` with a JSON body naming the reason.
- `UpstreamError` answers `502` with a JSON body carrying the upstream's text and an `upstream`
  field holding `"board"` or `"dashboard"`; the source is a JSON field, never a substring.
"""

from __future__ import annotations

from typing import Final, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

__all__ = ["UnknownProjectError", "UpstreamError", "install_error_handlers"]

SOURCES: Final[tuple[str, str]] = ("board", "dashboard")


class UnknownProjectError(Exception):
    """The requested project is not one the wrapper knows about.

    Answers 400: the client asked for something the configuration does not contain.

    Arguments:
        *args: The message and any further context carried by ``Exception``.
    """

    def __init__(self, *args: object) -> None:
        super().__init__(*args)


class UpstreamError(Exception):
    """A source the wrapper depends on answered with a failure.

    Answers 502 with a JSON body whose ``upstream`` field names the source that failed.

    Arguments:
        source: The upstream that failed, ``"board"`` or ``"dashboard"``.
        message: The upstream's own text; a sentence naming the source when omitted.

    Raises:
        ValueError: If ``source`` is not one of the two upstreams the wrapper reads.
    """

    def __init__(self, source: Literal["board", "dashboard"], message: str | None = None) -> None:
        if source not in SOURCES:
            raise ValueError(f"unknown upstream {source!r}: expected one of {SOURCES}")
        super().__init__(message if message else f"the {source} is unavailable")
        self.source: Literal["board", "dashboard"] = source


async def _unknown_project(_: Request, error: Exception) -> JSONResponse:
    """Answer an unknown project with 400 and the message the raiser carried."""
    return JSONResponse(status_code=400, content={"error": str(error) or "unknown project"})


async def _invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
    """Answer a body that failed validation — malformed JSON included — with 400."""
    return JSONResponse(status_code=400, content={"error": _reason(error)})


async def _upstream_failure(_: Request, error: UpstreamError) -> JSONResponse:
    """Answer a failing upstream with 502 and the source that failed."""
    return JSONResponse(status_code=502, content={"error": str(error), "upstream": error.source})


def _reason(error: RequestValidationError) -> str:
    """Summarise a validation failure as one line the client can act on."""
    return "; ".join(
        f"{'.'.join(str(part) for part in problem.get('loc', ())) or 'body'}: {problem.get('msg') or 'invalid'}"
        for problem in error.errors()
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register the wrapper's exception handlers on an application.

    After the call an unknown project answers 400, a body that fails validation answers 400, and
    a failing upstream answers 502 with a JSON body whose ``upstream`` field names the source.

    Arguments:
        app: The FastAPI application to install the handlers on.
    """
    app.add_exception_handler(UnknownProjectError, _unknown_project)
    app.add_exception_handler(RequestValidationError, _invalid_request)
    app.add_exception_handler(UpstreamError, _upstream_failure)
