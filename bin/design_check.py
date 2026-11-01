#!/usr/bin/env python3
"""design_check.py — deterministic acceptance for a design artefact (no AI).

Checks what a human would check first: the files exist, the page is self-contained (no external
fonts or scripts), the palette is the agreed light one, every promised state is rendered, every
component root is marked, and the page renders in a headless browser without console errors.
With --shots it also writes a PNG next to every HTML file, so a vision model can look at the
result and a human can see it without opening a browser.

CLI:
  design_check.py --dir docs/design/occc [--repo PATH] [--states loaded,loading,...]
                  [--nav] [--light] [--shots] [--min-components 5]

Exit 0 — accepted; 1 — rejected, with the reasons on stderr and a JSON verdict on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# playwright lives in the agent runtime, not in the system interpreter
RENDER_PY = Path.home() / "soft" / "openhands" / ".venv" / "bin" / "python"
RENDER_LIB = Path.home() / ".local" / "lib"  # libnss/libatk/... unpacked from .deb, no root here
LIGHT_TOKENS = ("#f4f8fd", "#ffffff", "#2f6fed")
DARK_TOKENS = ("#0f141a", "#1b222c", "#5b8ff9")
EXTERNAL = ("<script", "@import url(", "fonts.googleapis", 'src="http', "src='http", "@font-face url(http")


def render(html: Path, png: Path) -> tuple[bool, str]:
    """Screenshot through playwright's chromium; returns (ok, message)."""
    script = f"""
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    page = browser.new_page(viewport={{"width": 1600, "height": 1200}})
    page.goto("file://{html}")
    page.wait_for_timeout(400)
    page.screenshot(path="{png}", full_page=True)
    browser.close()
"""
    python = str(RENDER_PY) if RENDER_PY.exists() else sys.executable
    env = dict(os.environ)
    if RENDER_LIB.exists():
        env["LD_LIBRARY_PATH"] = f"{RENDER_LIB}:{env.get('LD_LIBRARY_PATH', '')}".rstrip(":")
    proc = subprocess.run([python, "-c", script], capture_output=True, text=True, timeout=180, env=env)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()[-400:]
    return png.exists(), ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Accept or reject a design artefact.")
    ap.add_argument("--dir", required=True, help="design directory, repository-relative")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--states", default="", help="comma separated states that must be rendered")
    ap.add_argument("--nav", action="store_true", help="require both navigation variants")
    ap.add_argument("--light", action="store_true", help="require the light palette and no dark theme")
    ap.add_argument("--dark", action="store_true", help="require the dark palette on a dark canvas")
    ap.add_argument("--shots", action="store_true", help="render a PNG next to every page")
    ap.add_argument("--min-components", type=int, default=3)
    ap.add_argument("--page", default="*.html", help="glob of the pages this verdict is about")
    ap.add_argument(
        "--require",
        action="append",
        default=[],
        help="a literal the page must contain (repeatable); the task's own acceptance",
    )
    ap.add_argument("--min-matches", type=int, default=1, help="how many times every --require literal must appear")
    a = ap.parse_args()

    root = Path(a.repo).expanduser().resolve() / a.dir
    errors: list[str] = []
    pages = sorted(root.glob(a.page)) if root.is_dir() else []
    if not pages:
        errors.append(f"no HTML page in {root}")

    for page in pages:
        html = page.read_text(encoding="utf-8", errors="replace")
        for needle in EXTERNAL:
            if needle in html:
                errors.append(f"{page.name}: uses {needle!r}; the artefact must be self-contained")
        if html.count("data-component") < a.min_components:
            errors.append(f"{page.name}: fewer than {a.min_components} data-component markers")
        if a.light:
            for token in LIGHT_TOKENS:
                if token not in html.lower():
                    errors.append(f"{page.name}: the light palette token {token} is missing")
            if 'data-theme="dark"' in html:
                errors.append(f'{page.name}: still carries data-theme="dark"')
        if a.dark:
            for token in DARK_TOKENS:
                if token not in html.lower():
                    errors.append(f"{page.name}: the dark palette token {token} is missing")
            if "#f4f8fd" in html.lower():
                errors.append(f"{page.name}: uses the light canvas #f4f8fd in a dark artefact")
        if a.nav:
            for variant in ("expanded", "collapsed"):
                if f'data-nav="{variant}"' not in html:
                    errors.append(f"{page.name}: navigation variant {variant!r} is not rendered")
        for needle in a.require:
            if html.count(needle) < a.min_matches:
                errors.append(
                    f"{page.name}: expects at least {a.min_matches} of {needle!r}, found {html.count(needle)}"
                )
        for state in [s.strip() for s in a.states.split(",") if s.strip()]:
            if f'data-state="{state}"' not in html:
                errors.append(f"{page.name}: state {state!r} is not rendered")

    shots = []
    if a.shots and pages:
        for page in pages:
            png = page.with_suffix(".png")
            ok, message = render(page, png)
            if ok:
                shots.append(str(png))
            else:
                errors.append(f"{page.name}: does not render: {message}")

    verdict = {"ok": not errors, "dir": str(root), "pages": [p.name for p in pages], "shots": shots, "errors": errors}
    print(json.dumps(verdict, ensure_ascii=False, indent=1))
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
