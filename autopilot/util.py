"""Small shared helpers: logging, time, secrets, HTTP retries."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("yts")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

TZ = ZoneInfo(os.environ.get("YTS_TZ", "Asia/Kolkata"))


def now() -> datetime:
    return datetime.now(TZ)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def today() -> str:
    return now().strftime("%Y-%m-%d")


class MissingSecret(RuntimeError):
    pass


def secret(name: str, required: bool = True) -> str | None:
    val = os.environ.get(name, "").strip()
    if not val and required:
        raise MissingSecret(f"GitHub secret {name} is not set")
    return val or None


def has_secret(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def retry(times: int = 3, wait: float = 5.0, exceptions=(requests.RequestException,)):
    """Retry a function on network errors with growing back-off."""

    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            for i in range(times):
                try:
                    return fn(*a, **kw)
                except exceptions as e:  # noqa: PERF203
                    if i == times - 1:
                        raise
                    log.warning("%s failed (%s) — retry %d/%d", fn.__name__, e, i + 1, times - 1)
                    time.sleep(wait * (i + 1))
        return inner

    return deco


def summary(text: str) -> None:
    """Append Markdown to the GitHub Actions job summary (no-op locally)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")
    print(text)
