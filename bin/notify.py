#!/usr/bin/env python3
"""notify.py — send a short message to the owner's Telegram chat.

The gateway owns the bot; this script reuses the same token from ~/.hermes/.env so the orchestrator
can report a finished job without going through a chat session. Egress to api.telegram.org is
blocked directly, so the request goes through the LAN SOCKS proxy that the gateway also uses.

CLI:
  notify.py --text "message" [--text @file] [--silent]

Output (stdout, one JSON object): {"ok": true, "message_id": N}
Exit codes: 0 — sent, 1 — failure (reason in stderr).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse

from rig_conf import TELEGRAM_PROXY_ENV, proxy

ENV = os.path.expanduser("~/.hermes/.env")
PROXY = proxy(TELEGRAM_PROXY_ENV)  # None means: connect directly
MAX_CHARS = 3900


def env_value(key: str) -> str:
    for line in open(ENV, encoding="utf-8"):
        line = line.strip()
        if line.startswith(f"{key}=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a message to the owner via Telegram.")
    parser.add_argument("--text", required=True, help="message text or @path")
    parser.add_argument("--silent", action="store_true", help="deliver without a sound")
    args = parser.parse_args()

    text = open(os.path.expanduser(args.text[1:]), encoding="utf-8").read() if args.text.startswith("@") else args.text
    text = text.strip()
    if not text:
        print("ERROR: empty message", file=sys.stderr)
        return 1
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 20].rstrip() + "\n… (обрезано)"

    token = env_value("TELEGRAM_BOT_TOKEN")
    chat = env_value("TELEGRAM_HOME_CHANNEL") or env_value("TELEGRAM_ALLOWED_USERS").split(",")[0]
    if not token or not chat:
        print("ERROR: TELEGRAM_BOT_TOKEN or chat id is missing in ~/.hermes/.env", file=sys.stderr)
        return 1

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode(
        {
            "chat_id": chat,
            "text": text,
            "disable_notification": "true" if args.silent else "false",
        }
    )
    proc = subprocess.run(
        ["curl", "-sS", "--max-time", "30", *(["--proxy", PROXY] if PROXY else []), "-X", "POST", url, "--data", body],
        capture_output=True,
        text=True,
    )
    try:
        answer = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        print(f"ERROR: telegram answered non-JSON: {proc.stdout[:200]} {proc.stderr[:200]}", file=sys.stderr)
        return 1
    if not answer.get("ok"):
        print(f"ERROR: telegram refused: {json.dumps(answer)[:300]}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "message_id": (answer.get("result") or {}).get("message_id")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
