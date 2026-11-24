"""Keeps the bot running on time without relying on GitHub's cron (which is often hours late or skipped
on new repos).  Every post run ends with a small "heartbeat" job that:
  1. starts Analytics / Niche research if they are due (stdlib only, reads state files), then
  2. sleeps until the next half-hour slot and starts the next post run.
Only one heartbeat can exist at a time (concurrency group with cancel-in-progress), so it never doubles.

  python -m autopilot.heartbeat due      → prints workflow files that are due, one per line
  python -m autopilot.heartbeat sleep    → prints seconds until the next slot
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
INTERVAL_MIN = 30           # a post run every 30 minutes → dashboard is never more than ~30 min old
ANALYTICS_EVERY_H = 2       # followers / views refresh
TRENDS_EVERY_H = 84         # niche research twice a week


def _read(name: str) -> dict:
    try:
        return json.loads((STATE / name).read_text(encoding="utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return {}


def _age_h(iso: str | None) -> float:
    if not iso:
        return 1e9
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return 1e9
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600


def due() -> list[str]:
    out = []
    if _age_h(_read("stats.json").get("updated")) >= ANALYTICS_EVERY_H - 0.25:
        out.append("analytics.yml")
    tr = _read("trends.json")
    stamps = [d.get("updated") for k, d in tr.items() if isinstance(d, dict) and not k.startswith("_")]
    if not stamps or min(_age_h(s) for s in stamps) >= TRENDS_EVERY_H:
        out.append("trends.yml")
    return out


def sleep_seconds(now: float | None = None) -> int:
    now = time.time() if now is None else now
    step = INTERVAL_MIN * 60
    return int(step - now % step) + 90   # land at :01:30 / :31:30 UTC (= :31 / :01 IST), away from the busy :00


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "due"
    if cmd == "due":
        print("\n".join(due()))
    elif cmd == "sleep":
        print(sleep_seconds())
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
