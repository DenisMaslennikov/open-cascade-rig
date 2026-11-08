"""Mount a directory of static files on a FastAPI application with path containment."""

import urllib.parse
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

__all__ = ["mount_static"]

PREFIX = "/static"


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
    """
    root = directory.resolve()

    @app.get(f"{PREFIX}/{{file_path:path}}", include_in_schema=False)
    def serve_static(file_path: str) -> FileResponse:
        target = _resolve_inside(root, file_path)
        if target is None:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(target)


def _resolve_inside(root: Path, file_path: str) -> Path | None:
    """Resolve a request path against ``root`` and return it only if it is a file inside ``root``.

    The request path is percent-decoded first, because a client may send an encoded traversal,
    and the result is resolved so a symbolic link is followed before containment is checked.
    """
    candidate = (root / urllib.parse.unquote(file_path)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None
