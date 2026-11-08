"""Mount a directory of static files on a FastAPI application with path containment."""

from pathlib import Path

from fastapi import FastAPI

__all__ = ["mount_static"]


def mount_static(app: FastAPI, directory: Path) -> None:
    """Mount ``directory`` under ``/static`` on ``app``.

    Every request is resolved against the resolved form of ``directory``. A request whose
    resolved target lies outside that directory, whether through a plain ``..`` segment,
    a percent-encoded traversal, or a symbolic link, is answered with 404. Files inside the
    directory, including files in nested subdirectories, are served with their bytes.

    Arguments:
        app: The FastAPI application to mount the static route on.
        directory: The directory whose files are served.

    Returns:
        None.

    Raises:
        NotImplementedError: Always; the implementation is supplied by the executor.
    """
    raise NotImplementedError
