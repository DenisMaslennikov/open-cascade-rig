"""Error vocabulary and HTTP mapping for the wrapper rail application.

The wrapper reads the board and the sessions dashboard and serves them as one rail. A failing
source, an unknown project or a body the client mangled has to reach the client as a defined
status code with a JSON body that names the failing upstream; this module holds that vocabulary
and installs the handlers that produce those answers.
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

__all__ = ["UpstreamError", "UnknownProjectError", "install_error_handlers"]


class UnknownProjectError(Exception):
    """The requested project is not one the wrapper knows about.

    Answers 400: the client asked for something the configuration does not contain.
    """

    def __init__(self, *args: object) -> None:
        """Record the failure.

        Arguments:
            *args: The message and any further context carried by ``Exception``.

        Raises:
            NotImplementedError: Always, until this module is implemented.
        """
        raise NotImplementedError


class UpstreamError(Exception):
    """A source the wrapper depends on answered with a failure.

    Arguments:
        source: The upstream that failed, ``"board"`` or ``"dashboard"``.
    """

    def __init__(self, source: Literal["board", "dashboard"]) -> None:
        """Record which upstream failed.

        Arguments:
            source: The upstream that failed, ``"board"`` or ``"dashboard"``.

        Raises:
            NotImplementedError: Always, until this module is implemented.
        """
        raise NotImplementedError


def install_error_handlers(app: FastAPI) -> None:
    """Register the wrapper's exception handlers on an application.

    After the call an unknown project answers 400, a body that fails validation answers 400, and
a failing upstream answers 502 with a JSON body whose ``upstream`` field names the source.

    Arguments:
        app: The FastAPI application to install the handlers on.

    Raises:
        NotImplementedError: Always, until this module is implemented.
    """
    raise NotImplementedError
