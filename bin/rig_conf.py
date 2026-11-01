#!/usr/bin/env python3
"""rig_conf.py — where this rig's environment comes from: the project config, then a variable, never code.

A checkout is not a deployment. An address, a proxy or a repository path written into a script is a
default that only works on the machine it was typed on, and it hides the fact that the machine was never
configured. So each helper here resolves in one order and stops:

  1. the project's own config (`~/.config/cascade/projects/<slug>.yaml`, or keys.env for variables),
  2. an environment variable,
  3. a failure that says what to set.

A missing proxy means *no proxy* — never a guess. `keys.env` is read because it is where this machine's
variables already live; it is not in any repository.

Resolution is cached per process; nothing here writes.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cascade"
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "cascade"
KEYS = CONFIG / "keys.env"

GATEWAY_ENV = "CASCADE_GATEWAY"
PROXY_ENV = "CASCADE_PROXY"
PRICES_PROXY_ENV = "CASCADE_PRICES_PROXY"
TELEGRAM_PROXY_ENV = "HERMES_TELEGRAM_PROXY"
REPOS_ENV = "CASCADE_REPOS"
RIG_ENV = "CASCADE_RIG"


@lru_cache(maxsize=1)
def keys() -> dict[str, str]:
    """Variables from keys.env (0600), stripped of quotes; empty when the file does not exist."""
    values: dict[str, str] = {}
    if not KEYS.exists():
        return values
    for line in KEYS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def value(name: str) -> str | None:
    """An environment variable, falling back to keys.env. None when neither has it."""
    return os.environ.get(name) or keys().get(name) or None


def gateway(project: dict, *, script: str = "this script") -> str:
    """The model gateway base URL: project `base_url`, then $CASCADE_GATEWAY, else fail."""
    url = ((project.get("_") or {}).get("base_url") or value(GATEWAY_ENV) or "").rstrip("/")
    if not url:
        raise SystemExit(
            f"ERROR: {script} has no gateway base_url. Set `base_url:` in "
            f"{CONFIG}/projects/<slug>.yaml (or export {GATEWAY_ENV}); no address is assumed."
        )
    return url


def proxy(name: str = PROXY_ENV) -> str | None:
    """A proxy URL from the variable or keys.env; None means connect directly."""
    return value(name)


def repriced_proxy() -> str | None:
    return proxy(PRICES_PROXY_ENV)


def project_config(slug: str) -> dict:
    """The top-level keys of one project config, as `gateway()` expects them.

    Deliberately minimal: only flat `key: value` lines are read, which is all a default-resolution
    helper needs. Scripts that need the full document parse it themselves.
    """
    path = CONFIG / "projects" / f"{slug}.yaml"
    flat: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith((" ", "\t", "#")) or ":" not in line:
                continue
            name, _, rest = line.partition(":")
            if rest.strip():
                flat[name.strip()] = rest.split("#", 1)[0].strip().strip('"').strip("'")
    return {"_": flat}


def repos() -> list[Path]:
    """Repository checkouts: $CASCADE_REPOS, then the `repo:` key of every project config."""
    raw = value(REPOS_ENV)
    if raw:
        return [Path(part).expanduser() for part in raw.replace(":", " ").split() if part]
    found: list[Path] = []
    own = value(RIG_ENV)
    if own:
        found.append(Path(own).expanduser())
    for config in sorted((CONFIG / "projects").glob("*.yaml")):
        for line in config.read_text(encoding="utf-8").splitlines():
            name, _, rest = line.partition(":")
            if name.strip() == "repo" and rest.strip():
                path = Path(rest.strip().strip('"').strip("'")).expanduser()
                if path not in found:
                    found.append(path)
    return found


def rig_repo(explicit: str | None = None) -> Path:
    """This rig's own checkout: the argument, then $CASCADE_RIG, then the current directory."""
    if explicit:
        return Path(explicit).expanduser()
    configured = value(RIG_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.cwd()
