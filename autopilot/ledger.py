"""The ledger is the bot's memory: every video it has ever seen and where it was posted.

It lives in state/ledger.json and is committed back to the repo after each run, so
the same video can never be posted twice on the same account — even if you put
the file in Drive again, rename it, or put it in another niche's folder.

Three layers of duplicate protection:
  1. Exact file match   — Google Drive's md5 checksum of the file bytes.
  2. Near-duplicate     — a perceptual hash of 3 frames, so a re-encoded / renamed /
                          re-downloaded copy of the same clip is still caught.
  3. Crash safety       — before publishing, the state is set to "publishing"; the next
                          run checks the platform before ever trying again.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT
from .util import now_iso, today

LEDGER_PATH = ROOT / "state" / "ledger.json"

POSTED = "posted"
PUBLISHING = "publishing"
FAILED = "failed"


def hamming(a: list[int], b: list[int]) -> int:
    return sum(bin(x ^ y).count("1") for x, y in zip(a, b))


class Ledger:
    def __init__(self, path: Path = LEDGER_PATH):
        self.path = path
        if path.exists() and path.read_text(encoding="utf-8").strip():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data = {"version": 1, "videos": {}, "runs": []}
        self.data.setdefault("videos", {})
        self.data.setdefault("runs", [])

    # ── persistence ──────────────────────────────────────────
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # ── records ──────────────────────────────────────────────
    @property
    def videos(self) -> dict:
        return self.data["videos"]

    def get(self, md5: str) -> dict | None:
        return self.videos.get(md5)

    def ensure(self, md5: str, file_id: str, name: str, niche: str) -> dict:
        rec = self.videos.get(md5)
        if rec is None:
            rec = self.videos[md5] = {
                "file_id": file_id,
                "name": name,
                "niche": niche,
                "first_seen": now_iso(),
                "phash": None,
                "caption": None,
                "duplicate_of": None,
                "platforms": {},
            }
        return rec

    def plat(self, md5: str, platform: str) -> dict:
        return self.videos[md5]["platforms"].setdefault(platform, {"status": None, "attempts": 0})

    def is_posted(self, md5: str, platform: str) -> bool:
        rec = self.get(md5)
        return bool(rec and rec["platforms"].get(platform, {}).get("status") == POSTED)

    def mark(self, md5: str, platform: str, status: str, **extra) -> None:
        p = self.plat(md5, platform)
        p["status"] = status
        p["updated"] = now_iso()
        if status == POSTED:
            p["at"] = now_iso()
            p["day"] = today()
            p.pop("error", None)
        p.update(extra)
        self.save()

    def fail(self, md5: str, platform: str, error: str) -> int:
        p = self.plat(md5, platform)
        p["attempts"] = int(p.get("attempts", 0)) + 1
        p["status"] = FAILED
        p["error"] = str(error)[:500]
        p["updated"] = now_iso()
        self.save()
        return p["attempts"]

    # ── queries ──────────────────────────────────────────────
    def posted_today(self, niche: str, platform: str) -> int:
        d = today()
        return sum(
            1
            for v in self.videos.values()
            if v["niche"] == niche and v["platforms"].get(platform, {}).get("day") == d
            and v["platforms"][platform].get("status") == POSTED
        )

    def find_near_duplicate(self, md5: str, phash: list[int], threshold: int) -> str | None:
        """Return the md5 of an already-known video that looks the same, if any."""
        if not phash:
            return None
        best, best_d = None, threshold + 1
        for other_md5, v in self.videos.items():
            if other_md5 == md5 or not v.get("phash") or v.get("duplicate_of"):
                continue
            d = hamming(phash, v["phash"])
            if d < best_d:
                best, best_d = other_md5, d
        return best

    def log_run(self, entry: dict) -> None:
        entry["at"] = now_iso()
        self.data["runs"] = (self.data["runs"] + [entry])[-200:]
        self.save()
