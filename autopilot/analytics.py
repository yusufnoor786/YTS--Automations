"""Daily analytics: followers / subscribers / views for every account → state/stats.json,
then rebuilds the dashboard (docs/index.html).   python -m autopilot.analytics"""
from __future__ import annotations

import json
import sys

from . import growth
from .config import ROOT, load
from .facebook import client_for as fb_client, has_credentials as fb_has
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
        if fb.get("enabled") and fb_has(niche):
            key = f"{niche.key}/facebook"
            try:
                c = fb_client(niche)
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


def collect_post_metrics(cfg, ledger: Ledger, max_age_days: int = 8) -> int:
    """Fetch views for every post younger than max_age_days; freeze `views_48h` once a post is 48h old."""
    after = growth.settings(cfg)["score_after_hours"]
    updated = 0
    for niche in cfg.niches.values():
        if not niche.enabled:
            continue
        posts = {p: [] for p in ("instagram", "facebook", "youtube")}
        for md5, v in ledger.videos.items():
            if v["niche"] != niche.key:
                continue
            for p, pd in v["platforms"].items():
                if pd.get("status") == "posted" and pd.get("id") and pd.get("views_48h") is None:
                    age = growth._age_hours(pd.get("at", ""))
                    if age <= max_age_days * 24:
                        posts.setdefault(p, []).append((pd, age))
        clients = {}
        try:
            ig = niche.instagram
            if posts["instagram"] and has_secret(ig.get("token_secret", "")) and has_secret(ig.get("user_id_secret", "")):
                clients["instagram"] = Instagram(secret(ig["user_id_secret"]), secret(ig["token_secret"]))
            fb = niche.facebook
            if posts["facebook"] and fb.get("enabled") and fb_has(niche):
                clients["facebook"] = fb_client(niche)
            yt = niche.youtube
            if posts["youtube"] and has_secret(yt.get("token_secret", "")):
                clients["youtube"] = YouTube(yt["token_secret"])
        except Exception:  # noqa: BLE001
            log.exception("metrics clients failed for %s", niche.key)
        for p, items in posts.items():
            c = clients.get(p)
            if not c or not items:
                continue
            yt_views = {}
            if p == "youtube":
                ids = [pd["id"] for pd, _ in items]
                for i in range(0, len(ids), 50):
                    res = c.svc.videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
                    for it in res.get("items", []):
                        yt_views[it["id"]] = int(it["statistics"].get("viewCount", 0))
            for pd, age in items:
                try:
                    if p == "instagram":
                        views = c.media_views(pd["id"])
                    elif p == "facebook":
                        views = c.reel_plays(pd["id"])
                    else:
                        views = yt_views.get(pd["id"])
                except Exception:  # noqa: BLE001
                    views = None
                if views is None:
                    continue
                pd["views"] = int(views)
                pd["views_checked"] = now_iso()
                if age >= after:
                    pd["views_48h"] = int(views)
                updated += 1
    ledger.save()
    return updated


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
    try:
        n = collect_post_metrics(cfg, ledger)
        log.info("Updated views for %d posts", n)
    except Exception:  # noqa: BLE001
        log.exception("post metrics failed")
    gs = growth.load_state()
    changes = growth.rescore(cfg, ledger, gs)
    growth.record_daily(gs, stats, ledger)
    report = growth.weekly_report(cfg, gs, ledger, stats)
    growth.save_state(gs)
    for c in changes:
        log.info("Growth Lab: %s", c)
    if report:
        summary(report)
    dashboard.build(cfg, ledger, stats)
    rows = [f"| {k} | {v.get('followers', v.get('subscribers', '—'))} | {'✅' if v.get('ok') else '❌ ' + v.get('error', '')} |"
            for k, v in stats["latest"].items()]
    summary("### Analytics\n\n| account | followers/subs | status |\n|---|---|---|\n" + "\n".join(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
