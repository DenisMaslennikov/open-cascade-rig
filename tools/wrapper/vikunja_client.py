"""Read-only Vikunja client for the wrapper: projects by slug, the cards of `Plan`, blockers.

The wrapper never writes to the board: there is no call here that creates, moves, comments on or
edits a card. The pipeline owns the board; the wrapper only reads what it can start.

Addresses and tokens are never guessed. A client takes them explicitly, from the environment
(`VIKUNJA_URL`, `VIKUNJA_TOKEN`) or from the local credentials file, and says what to set when
nothing is configured.

The transport is injectable so every rule above is provable without a network: anything with
`request(method, path, body=None, token=None) -> (status, payload)` works, and the default is
plain `urllib` from the standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

CREDS_PATH = Path("~/.config/vikunja/creds.json")
DASH_SPLIT = re.compile(r" — | - ")
PLAN_COLUMN = "Plan"
DEFAULT_SLUG = "project"
LOGIN_PATH = "/login"


class VikunjaError(Exception):
    """A request the board answered with something unusable, or a configuration gap."""


class VikunjaConfigError(VikunjaError):
    """No address or no token anywhere: the message names what to set."""


class VikunjaHTTPError(VikunjaError):
    """The board answered with an error status; method, path and status travel on the error."""

    def __init__(self, method: str, path: str, status: int) -> None:
        self.method = method
        self.path = path
        self.status = status
        super().__init__(f"{method} {path} -> HTTP {status}")


class UnknownProject(VikunjaError):
    """No project carries the requested slug or id; the message names the slugs that exist."""


class Transport(Protocol):
    """The one call a transport answers: a request in, a status and a decoded payload out."""

    def request(self, method: str, path: str, body: Any = None, token: str | None = None) -> tuple[int, Any]: ...


class UrllibTransport:
    """The default transport: one JSON request over urllib, standard library only."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def request(self, method: str, path: str, body: Any = None, token: str | None = None) -> tuple[int, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return response.status, _decode(raw)
        except urllib.error.HTTPError as error:
            return error.code, _decode(error.read().decode("utf-8", errors="replace"))
        except urllib.error.URLError as error:
            raise VikunjaError(f"{method} {path} failed: {error.reason}") from error


def _decode(raw: str) -> Any:
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


@dataclass(frozen=True, slots=True)
class Label:
    """A label as the board shows it."""

    title: str
    colour: str


@dataclass(frozen=True, slots=True)
class Blocker:
    """A card this one waits for; `done` says whether the wait is over."""

    card_id: int
    title: str
    done: bool


@dataclass(frozen=True, slots=True)
class Card:
    """One card of the `Plan` column, with everything the wrapper needs to judge it."""

    id: int
    title: str
    project_id: int
    labels: tuple[Label, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    url: str = ""

    @property
    def blocked(self) -> bool:
        """True when a `blocked` label is set or any blocker is still unfinished."""
        if any(label.title.strip().casefold() == "blocked" for label in self.labels):
            return True
        return any(not blocker.done for blocker in self.blockers)


@dataclass(frozen=True, slots=True)
class Project:
    """A board project with the slug the wrapper addresses it by."""

    id: int
    title: str
    slug: str


def project_slug(title: str) -> str:
    """Derive the slug of a project from its title.

    The slug is the part of the title before the first ` — ` or ` - ` separator, lowercased, with
    non-ASCII characters dropped and every run of other characters replaced by one dash; leading
    and trailing dashes are removed. A title with nothing usable yields the fallback `project`.

    Args:
        title: The project title as the board shows it, e.g. `cascade-rig — отладка`.

    Returns:
        A lowercase ASCII slug matching `[a-z0-9]([a-z0-9-]*[a-z0-9])?`, never empty.
    """
    head = DASH_SPLIT.split(title, maxsplit=1)[0]
    slug = re.sub(r"[^a-z0-9]+", "-", head.casefold()).strip("-")
    return slug or DEFAULT_SLUG


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    return value is True


def _narrow_labels(payload: Any) -> tuple[Label, ...]:
    if not isinstance(payload, list):
        return ()
    return tuple(
        Label(_text(label.get("title")), _text(label.get("hex_color")))
        for label in payload
        if isinstance(label, dict)
    )


def _narrow_blockers(payload: Any) -> tuple[Blocker, ...]:
    if not isinstance(payload, dict):
        return ()
    related = payload.get("blocked")
    if not isinstance(related, list):
        return ()
    return tuple(
        Blocker(card_id=_int(entry.get("id")) or 0, title=_text(entry.get("title")), done=_bool(entry.get("done")))
        for entry in related
        if isinstance(entry, dict)
    )


class VikunjaClient:
    """Reads projects and the cards of the `Plan` column from one Vikunja instance.

    Args:
        base_url: The API address, e.g. `http://127.0.0.1:3457/api/v1`. Falls back to
            `VIKUNJA_URL`, then to the `base` field of the credentials file.
        token: A bearer token. Falls back to `VIKUNJA_TOKEN`, then to a username/password login
            against the credentials file.
        creds_path: The credentials file to fall back to; the default is `~/.config/vikunja/creds.json`.
        transport: The transport to send requests through; the default speaks plain urllib.

    Raises:
        VikunjaConfigError: When no address is configured anywhere, or a token is needed and no
            token, no login and no credentials file exists. The message names what to set.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        creds_path: Path | str | None = None,
        transport: Transport | None = None,
    ) -> None:
        creds = _read_creds(Path(creds_path) if creds_path else CREDS_PATH)
        self.base_url = (base_url or os.environ.get("VIKUNJA_URL") or creds.get("base") or "").rstrip("/")
        if not self.base_url:
            raise VikunjaConfigError(
                "no Vikunja address: set VIKUNJA_URL, pass base_url, or add `base` to "
                f"{creds_path or CREDS_PATH}"
            )
        self._transport = transport or UrllibTransport()
        self._token = token or os.environ.get("VIKUNJA_TOKEN") or ""
        if not self._token:
            username, password = creds.get("username", ""), creds.get("password", "")
            if username and password:
                self._token = self._login(username, password)
            else:
                raise VikunjaConfigError(
                    f"no Vikunja token: set VIKUNJA_TOKEN, pass token, or put username/password into "
                    f"{creds_path or CREDS_PATH} for a login"
                )

    def _login(self, username: str, password: str) -> str:
        _, payload = self._transport.request(
            "POST", LOGIN_PATH, {"username": username, "password": password}, token=None
        )
        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise VikunjaConfigError(f"login at {LOGIN_PATH} returned no token; check the credentials file")
        return token

    def request(self, method: str, path: str, body: Any = None) -> Any:
        """Send one request and return the decoded payload, or raise `VikunjaHTTPError`.

        Args:
            method: The HTTP method, e.g. `GET`.
            path: The API path beginning with `/`.
            body: The JSON body, or `None` for a bodyless request.

        Returns:
            Whatever the board answered with, decoded from JSON (an empty `{}` when it is empty).

        Raises:
            VikunjaHTTPError: When the board answers with an error status.
        """
        status, payload = self._transport.request(method, path, body=body, token=self._token)
        if status >= 400:
            raise VikunjaHTTPError(method, path, status)
        return payload

    def projects(self) -> list[Project]:
        """List the non-archived projects with slugs derived from their titles.

        Returns:
            The projects in board order. A duplicate slug gets the project id appended (`project-9`).

        Raises:
            VikunjaHTTPError: When the board answers with an error status.
        """
        seen: set[str] = set()
        out: list[Project] = []
        for payload in self.request("GET", "/projects") or []:
            if not isinstance(payload, dict) or payload.get("is_archived"):
                continue
            pid = _int(payload.get("id"))
            if pid is None:
                continue
            title = _text(payload.get("title"))
            slug = project_slug(title)
            if slug in seen:
                slug = f"{slug}-{pid}"
            seen.add(slug)
            out.append(Project(pid, title, slug))
        return out

    def project(self, ref: str | int) -> Project:
        """Find one project by slug (case-insensitive) or by id.

        Args:
            ref: The slug (`cascade-rig`), the id (`7`) or the id as text (`"7"`).

        Returns:
            The matching project.

        Raises:
            UnknownProject: When no project matches; the message names the slugs that exist.
        """
        projects = self.projects()
        if isinstance(ref, str) and not ref.isdigit():
            wanted = ref.casefold()
            for project in projects:
                if project.slug.casefold() == wanted:
                    return project
        else:
            wanted_id = int(ref)
            for project in projects:
                if project.id == wanted_id:
                    return project
        known = ", ".join(sorted(project.slug for project in projects)) or "none"
        raise UnknownProject(f"no project {ref!r}; projects on the board: {known}")

    def plan_cards(self, project: str | int) -> list[Card]:
        """Read the cards of the `Plan` column of one project, in board order.

        The board is trusted to be configured; a missing kanban view or a missing `Plan` column is
        an error that names what the board has, never an empty list.

        Args:
            project: The project slug or id, as for `project`.

        Returns:
            The cards of the `Plan` column in the order the board keeps them.

        Raises:
            UnknownProject: When no project matches.
            VikunjaHTTPError: When the board answers with an error status.
            VikunjaError: When the project has no kanban view or the view has no `Plan` column.
        """
        pid = self.project(project).id
        views = self.request("GET", f"/projects/{pid}/views") or []
        kanban = next(
            (view for view in views if isinstance(view, dict) and view.get("view_kind") == "kanban"), None
        )
        if kanban is None:
            names = ", ".join(sorted(_text(view.get("title")) for view in views if isinstance(view, dict)))
            raise VikunjaError(f"project {pid} has no kanban view; views on the board: {names or 'none'}")
        view_id = _int(kanban.get("id"))
        if view_id is None:
            raise VikunjaError(f"the kanban view of project {pid} has no id")
        return self._collect_plan_cards(pid, view_id)

    def _collect_plan_cards(self, pid: int, view_id: int) -> list[Card]:
        web_base = re.sub(r"/api/v[0-9]+$", "", self.base_url)
        seen: set[int] = set()
        columns: list[str] = []
        plan_exists = False
        out: list[Card] = []
        for page in range(1, 101):
            buckets = self.request("GET", f"/projects/{pid}/views/{view_id}/tasks?per_page=100&page={page}")
            fresh = 0
            for bucket in buckets if isinstance(buckets, list) else []:
                if not isinstance(bucket, dict):
                    continue
                title = _text(bucket.get("title"))
                if title and title not in columns:
                    columns.append(title)
                if title != PLAN_COLUMN:
                    continue
                plan_exists = True
                for entry in bucket.get("tasks") or []:
                    if not isinstance(entry, dict):
                        continue
                    cid = _int(entry.get("id"))
                    if cid is None or cid in seen:
                        continue
                    seen.add(cid)
                    fresh += 1
                    out.append(self._card(pid, entry, web_base))
            if not fresh:
                break
        if not plan_exists:
            raise VikunjaError(f"project {pid} has no {PLAN_COLUMN!r} column; columns on the board: {', '.join(columns) or 'none'}")
        return out

    def _card(self, pid: int, payload: dict[str, Any], web_base: str) -> Card:
        cid = _int(payload.get("id")) or 0
        return Card(
            id=cid,
            title=_text(payload.get("title")),
            project_id=_int(payload.get("project_id")) or pid,
            labels=_narrow_labels(payload.get("labels")),
            blockers=_narrow_blockers(payload.get("related_tasks")),
            url=f"{web_base}/projects/{pid}/tasks/{cid}",
        )


def _read_creds(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise VikunjaConfigError(f"{path} is unreadable: {error}") from error
    return payload if isinstance(payload, dict) else {}


def main(argv: list[str] | None = None) -> int:
    """Print the plan cards of one project; the operator's smoke check of the configuration."""
    parser = argparse.ArgumentParser(description="Show the cards of the Plan column of one project.")
    parser.add_argument("--project", default="cascade-rig", help="project slug or id (default: cascade-rig)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of one line per card")
    args = parser.parse_args(argv)
    client = VikunjaClient()
    cards = client.plan_cards(args.project)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": card.id,
                        "title": card.title,
                        "labels": [{"title": label.title, "hex_color": label.colour} for label in card.labels],
                        "blockers": [{"id": b.card_id, "done": b.done} for b in card.blockers],
                        "blocked": card.blocked,
                        "url": card.url,
                    }
                    for card in cards
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    for card in cards:
        names = ", ".join(label.title for label in card.labels)
        state = "blocked" if card.blocked else "free"
        print(f"#{card.id} [{state}] {card.title} ({names})" if names else f"#{card.id} [{state}] {card.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
