"""Daily analytics: followers / subscribers / views for every account → state/stats.json,
then rebuilds the dashboard (docs/index.html).   python -m autopilot.analytics"""
from __future__ import annotations

import json
import sys

from .config import ROOT, load
from .facebook import Facebook
from .instagram import Instagram
from .ledger import Ledger
from .util import has_secret, log, now_iso, secret, summary, today
from .youtube import YouTube

STATS_PATH = ROOT / "state" / "stats.json"


def load_stats() -> dict:
    if STATS_PATH.exists() and STATS_PATH.read_text(encoding="utf-8").strip():
        return json.loads(STATS_PATH.read_text(encoding="utf-8"))
    return {"history": {}, "latest": {}, "recent": {}}


def collect(cfg, ledger: Ledger) -> dict:
    stats = load_stats()
    day = stats["history"].setdefault(today(), {})
    for niche in cfg.niches.values():
        if not niche.enabled:
            continue
        ig = niche.instagram
        if ig.get("enabled") and has_secret(ig["token_secret"]) and has_secret(ig["user_id_secret"]):
            key = f"{niche.key}/instagram"
            try:
                c = Instagram(secret(ig["user_id_secret"]), secret(ig["token_secret"]))
                prof = c.profile()
                media = c.recent_media(12)
                for m in media:
                    if m.get("media_product_type") == "REELS":
                        m["views"] = c.media_views(m["id"])
                snap = {"followers": prof.get("followers_count", 0), "posts": prof.get("media_count", 0),
                        "username": prof.get("username")}
                stats["latest"][key] = {**snap, "at": now_iso(), "ok": True}
                stats["recent"][key] = [{"url": m.get("permalink"), "at": m.get("timestamp"),
                                         "likes": m.get("like_count"), "comments": m.get("comments_count"),
                                         "views": m.get("views"),
                                         "caption": (m.get("caption") or "")[:80]} for m in media]
                day[key] = snap
            except Exception as e:  # noqa: BLE001
                log.exception("IG stats failed for %s", niche.key)
                stats["latest"].setdefault(key, {})
                stats["latest"][key].update({"ok": False, "error": str(e)[:200], "at": now_iso()})
        fb = niche.facebook
        if fb.get("enabled") and has_secret(fb["token_secret"]) and has_secret(fb["page_id_secret"]):
            key = f"{niche.key}/facebook"
            try:
                c = Facebook(secret(fb["page_id_secret"]), secret(fb["token_secret"]))
                prof = c.profile()
                followers = int(prof.get("followers_count") or prof.get("fan_count") or 0)
                reels = c.recent_reels(12)
                stats["latest"][key] = {"followers": followers, "name": prof.get("name"),
                                        "url": prof.get("link"), "at": now_iso(), "ok": True}
                stats["recent"][key] = [{"url": r.get("permalink_url"), "at": r.get("created_time"),
                                         "views": r.get("views"), "likes": None, "comments": None,
                                         "caption": (r.get("description") or "")[:80]} for r in reels]
                day[key] = {"followers": followers}
            except Exception as e:  # noqa: BLE001
                log.exception("FB stats failed for %s", niche.key)
                stats["latest"].setdefault(key, {})
                stats["latest"][key].update({"ok": False, "error": str(e)[:200], "at": now_iso()})
        yt = niche.youtube
        if yt.get("enabled") and has_secret(yt["token_secret"]):
            key = f"{niche.key}/youtube"
            try:
                c = YouTube(yt["token_secret"])
                s = c.stats()
                vids = c.recent_uploads(12)
                stats["latest"][key] = {**s, "at": now_iso(), "ok": True}
                stats["recent"][key] = [{"url": f"https://youtube.com/shorts/{v['id']}",
                                         "at": v["snippet"]["publishedAt"],
                                         "likes": int(v["statistics"].get("likeCount", 0)),
                                         "comments": int(v["statistics"].get("commentCount", 0)),
                                         "views": int(v["statistics"].get("viewCount", 0)),
                                         "privacy": v.get("status", {}).get("privacyStatus"),
                                         "caption": v["snippet"]["title"][:80]} for v in vids]
                day[key] = {"followers": s["subscribers"], "views": s["views"], "posts": s["videos"]}
            except Exception as e:  # noqa: BLE001
                log.exception("YT stats failed for %s", niche.key)
                stats["latest"].setdefault(key, {})
                stats["latest"][key].update({"ok": False, "error": str(e)[:200], "at": now_iso()})
    # keep one year
    for d in sorted(stats["history"])[:-366]:
        del stats["history"][d]
    stats["updated"] = now_iso()
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(stats, indent=1, ensure_ascii=False), encoding="utf-8")
    return stats


def main() -> int:
    from . import dashboard

    cfg = load()
    ledger = Ledger()
    if has_secret("GDRIVE_REFRESH_TOKEN"):
        try:
            from .drive import Drive
            from .post import Poster

            drive, poster = Drive(), None
            poster = Poster(cfg, ledger, drive)
            for niche in cfg.niches.values():
                if niche.enabled:
                    poster.stock(niche, drive.niche_folders(cfg.drive_root_folder_id, niche.key))
            ledger.save()
        except Exception:  # noqa: BLE001
            log.exception("Could not count Drive stock")
    stats = collect(cfg, ledger)
    dashboard.build(cfg, ledger, stats)
    rows = [f"| {k} | {v.get('followers', v.get('subscribers', '—'))} | {'✅' if v.get('ok') else '❌ ' + v.get('error', '')} |"
            for k, v in stats["latest"].items()]
    summary("### Analytics\n\n| account | followers/subs | status |\n|---|---|---|\n" + "\n".join(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
