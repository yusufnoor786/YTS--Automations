"""Niche Research — what is working for OTHER creators in each niche right now.

Twice a week, per niche:
  • YouTube: searches public Shorts from the last 7 days (India) for the niche's search phrases,
    ordered by views → top videos, views/day, typical length, popular title words, hashtags, top channels.
  • Instagram: Hashtag Search API → top posts for the niche's hashtags → engagement per hashtag and
    which other hashtags appear next to them.
  • Gemini turns the numbers into a short plan: content ideas, title patterns, hashtags to try and
    ONE new caption style that the Growth Lab then tests automatically (style "trend").

Results: state/trends.json (machine), TRENDS.md (human), dashboard "Niche Trends" section, and a line
in GROWTH_LOG.md. Raw API data is overwritten every run (nothing older than 30 days is kept).
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timedelta

from .config import ROOT, load
from .instagram import GRAPH, Instagram
from .util import has_secret, log, now, now_iso, secret, summary, today

STATE = ROOT / "state" / "trends.json"
REPORT = ROOT / "TRENDS.md"
STOP = set("""a an the and or of to in on for with is are was were be this that it its i you we they he she my your our
his her their me us them at by from as so if not no yes do does did just how what why when who which will can
shorts short video videos viral trending new part full ka ki ke hai ho se me mein ko aur na ne bhi ye wo kya""".split())


def load_state() -> dict:
    if STATE.exists() and STATE.read_text(encoding="utf-8").strip():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def save_state(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")


def _iso_dur(s: str) -> int:
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    h, mi, se = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + se


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Zऀ-ॿ]{3,}", (text or "").lower()) if w not in STOP]


def _tags(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"#(\w{2,40})", text or "")]


# ── YouTube ─────────────────────────────────────────────────
def youtube_research(cfg, niche, max_queries: int) -> dict:
    from .youtube import YouTube

    r = niche.research or {}
    queries = list(r.get("youtube_queries") or [])[:max_queries]
    tok = niche.youtube.get("token_secret")
    if not queries or not tok or not has_secret(tok):
        return {"skipped": "no YouTube token or queries"}
    yt = YouTube(tok).svc
    after = (now() - timedelta(days=7)).astimezone().isoformat(timespec="seconds")
    ids: list[str] = []
    for q in queries:
        res = yt.search().list(part="id", q=q, type="video", videoDuration="short", order="viewCount",
                               publishedAfter=after, regionCode=r.get("region", "IN"), maxResults=25,
                               relevanceLanguage=r.get("language", "hi")).execute()
        ids += [i["id"]["videoId"] for i in res.get("items", []) if i.get("id", {}).get("videoId")]
    ids = list(dict.fromkeys(ids))
    vids = []
    for i in range(0, len(ids), 50):
        res = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50])).execute()
        vids += res.get("items", [])
    rows = []
    for v in vids:
        sn, st = v["snippet"], v.get("statistics", {})
        views = int(st.get("viewCount", 0))
        age_d = max(0.5, (now() - datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
                          ).total_seconds() / 86400)
        rows.append({"title": sn["title"][:120], "channel": sn["channelTitle"], "views": views,
                     "views_per_day": int(views / age_d), "likes": int(st.get("likeCount", 0)),
                     "comments": int(st.get("commentCount", 0)), "seconds": _iso_dur(v["contentDetails"]["duration"]),
                     "url": f"https://youtube.com/shorts/{v['id']}",
                     "tags": list(dict.fromkeys(_tags(sn["title"] + " " + sn.get("description", ""))
                                                + [t.lower().replace(" ", "") for t in (sn.get("tags") or [])][:10]))})
    rows.sort(key=lambda x: -x["views_per_day"])
    top = rows[:30]
    words, tags, chans = Counter(), Counter(), Counter()
    for x in top:
        w = math.log1p(x["views_per_day"])
        for t in set(_words(x["title"])):
            words[t] += w
        for t in set(x["tags"]):
            tags[t] += w
        chans[x["channel"]] += x["views"]
    lens = sorted(x["seconds"] for x in top[:15] if x["seconds"])
    return {
        "queries": queries,
        "top": top[:10],
        "title_words": [w for w, _ in words.most_common(15)],
        "hashtags": [t for t, _ in tags.most_common(15)],
        "top_channels": [c for c, _ in chans.most_common(8)],
        "median_seconds": lens[len(lens) // 2] if lens else None,
        "videos_scanned": len(rows),
    }


# ── Instagram ───────────────────────────────────────────────
def instagram_research(cfg, niche, ig_client: Instagram | None) -> dict:
    tags = list((niche.research or {}).get("instagram_hashtags") or [])[:6]
    if not ig_client or not tags:
        return {"skipped": "no Instagram account connected for research"}
    out, related = [], Counter()
    for tag in tags:
        try:
            hid = ig_client._req("GET", "ig_hashtag_search", user_id=ig_client.ig, q=tag)["data"][0]["id"]
            media = ig_client._req("GET", f"{hid}/top_media", user_id=ig_client.ig, limit=30,
                                   fields="caption,like_count,comments_count,media_type,timestamp,permalink"
                                   ).get("data", [])
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "permission" in msg.lower() or "(#10)" in msg or "code 10" in msg:
                return {"skipped": "Instagram hashtag research needs the 'Instagram Public Content Access' feature "
                                   "on the Meta app", "error": msg[:200]}
            out.append({"tag": tag, "error": msg[:120]})
            continue
        eng = [int(m.get("like_count") or 0) + 2 * int(m.get("comments_count") or 0) for m in media]
        reels = sum(1 for m in media if m.get("media_type") == "VIDEO")
        for m in media:
            for t in set(_tags(m.get("caption", ""))):
                if t != tag:
                    related[t] += 1
        out.append({"tag": tag, "posts": len(media), "avg_engagement": int(sum(eng) / len(eng)) if eng else 0,
                    "reels_share": round(reels / len(media), 2) if media else 0,
                    "best": max(media, key=lambda m: int(m.get("like_count") or 0)).get("permalink") if media else None})
    out.sort(key=lambda x: -x.get("avg_engagement", 0))
    return {"hashtags": out, "related_hashtags": [t for t, _ in related.most_common(15)]}


# ── Gemini summary ──────────────────────────────────────────
def gemini_plan(cfg, niche, yt: dict, ig: dict) -> dict:
    if not has_secret("GEMINI_API_KEY"):
        return {}
    data = {
        "top_youtube_shorts_last_7_days": [{k: x[k] for k in ("title", "views_per_day", "seconds")}
                                           for x in yt.get("top", [])[:10]],
        "popular_title_words": yt.get("title_words", []),
        "popular_hashtags_youtube": yt.get("hashtags", []),
        "median_length_seconds": yt.get("median_seconds"),
        "instagram_hashtags_by_engagement": [{k: h.get(k) for k in ("tag", "avg_engagement", "reels_share")}
                                             for h in ig.get("hashtags", [])],
        "instagram_related_hashtags": ig.get("related_hashtags", []),
    }
    prompt = f"""You are a growth strategist for a faceless short-video page.
Page: {niche.label}. Voice: {niche.style}
Here is this week's research about what performs in this niche (India):
{json.dumps(data, ensure_ascii=False)}
Return ONLY JSON:
{{"summary": "2 sentences: what is working right now",
  "content_ideas": ["5 specific video ideas the page could post this week"],
  "title_patterns": ["3 title/hook patterns copied in spirit from the winners"],
  "hashtags_to_try": ["8 hashtags without #, mix of big and niche"],
  "best_length_seconds": number,
  "new_style": {{"name": "1-2 words", "instruction": "one-sentence caption-writing instruction based on what wins"}}}}"""
    from .captions import _call

    for m in dict.fromkeys([cfg.get("gemini_model", "gemini-flash-latest"), "gemini-flash-lite-latest"]):
        try:
            return _call(m, [{"text": prompt}])
        except Exception as e:  # noqa: BLE001
            log.warning("Gemini trend summary (%s) failed: %s", m, e)
    return {}


# ── helpers used by the bot ─────────────────────────────────
def niche_hints(niche_key: str) -> dict:
    d = load_state().get(niche_key) or {}
    plan = d.get("plan") or {}
    return {"hashtags": [re.sub(r"\W", "", t.lower()) for t in (plan.get("hashtags_to_try") or [])][:8],
            "style": (plan.get("new_style") or {}).get("instruction"),
            "ideas": plan.get("content_ideas") or []}


def write_report(cfg, state: dict) -> str:
    lines = [f"# Niche Trends — updated {today()}\n",
             "What is getting views for other creators in each niche (last 7 days, India). "
             "The bot uses this to test new hashtags and a new caption style automatically.\n"]
    for key, d in state.items():
        n = cfg.niches.get(key)
        if not n or not isinstance(d, dict):
            continue
        yt, ig, plan = d.get("youtube", {}), d.get("instagram", {}), d.get("plan", {})
        lines.append(f"\n## {n.label}\n")
        if plan.get("summary"):
            lines.append(f"**In short:** {plan['summary']}\n")
        if yt.get("top"):
            lines.append("| top Shorts this week | channel | views/day | length |\n|---|---|---|---|")
            for x in yt["top"][:6]:
                lines.append(f"| [{x['title'][:60].replace('|', '/')}]({x['url']}) | {x['channel'][:25]} | "
                             f"{x['views_per_day']:,} | {x['seconds']}s |")
        elif yt.get("skipped"):
            lines.append(f"_YouTube research skipped: {yt['skipped']}_")
        if yt.get("hashtags"):
            lines.append(f"\n**Hot YouTube hashtags:** {' '.join('#' + t for t in yt['hashtags'][:10])}")
        if yt.get("median_seconds"):
            lines.append(f"**Typical length of top Shorts:** {yt['median_seconds']}s")
        if ig.get("hashtags"):
            lines.append("\n| Instagram hashtag | avg engagement (top posts) | reels share |\n|---|---|---|")
            for h in ig["hashtags"]:
                if "avg_engagement" in h:
                    lines.append(f"| #{h['tag']} | {h['avg_engagement']:,} | {int(h['reels_share'] * 100)}% |")
        elif ig.get("skipped"):
            lines.append(f"\n_Instagram research skipped: {ig['skipped']}_")
        if plan.get("content_ideas"):
            lines.append("\n**Content ideas for this week:**")
            lines += [f"- {i}" for i in plan["content_ideas"]]
        if plan.get("title_patterns"):
            lines.append("\n**Hook / title patterns that win:**")
            lines += [f"- {i}" for i in plan["title_patterns"]]
        if plan.get("hashtags_to_try"):
            lines.append(f"\n**Hashtags the bot will test:** {' '.join('#' + t for t in plan['hashtags_to_try'])}")
        if plan.get("new_style"):
            lines.append(f"\n**New caption style being tested:** *{plan['new_style'].get('name')}* — "
                         f"{plan['new_style'].get('instruction')}")
    text = "\n".join(lines) + "\n"
    REPORT.write_text(text, encoding="utf-8")
    return text


def youtube_query_budget(cfg) -> int:
    """search.list costs 100 units; never let research eat the upload quota."""
    total = int(cfg.get("youtube_daily_quota", 10000))
    uploads = 0
    if cfg.get("youtube_api_audited"):
        uploads = sum(n.posts_per_day for n in cfg.niches.values() if n.enabled and n.youtube.get("enabled")) * 1600
    return max(0, (total - uploads - 300) // 100)


def main() -> int:
    cfg = load()
    state = load_state()
    budget = youtube_query_budget(cfg)
    niches = [n for n in cfg.niches.values() if n.enabled]
    per_niche = max(0, budget // max(1, len(niches)))
    # any connected Instagram account can run hashtag searches (30 unique tags / 7 days / account)
    ig_clients = {}
    for n in cfg.niches.values():
        ig = n.instagram
        if ig.get("enabled") and has_secret(ig.get("token_secret", "")) and has_secret(ig.get("user_id_secret", "")):
            ig_clients[n.key] = Instagram(secret(ig["user_id_secret"]), secret(ig["token_secret"]))
    fallback_ig = next(iter(ig_clients.values()), None)
    rows = []
    for n in niches:
        d = {"updated": now_iso()}
        try:
            d["youtube"] = youtube_research(cfg, n, min(per_niche, 3)) if per_niche else {
                "skipped": "YouTube quota reserved for uploads today"}
        except Exception as e:  # noqa: BLE001
            log.exception("YouTube research failed for %s", n.key)
            d["youtube"] = {"skipped": f"error: {str(e)[:150]}"}
        try:
            d["instagram"] = instagram_research(cfg, n, ig_clients.get(n.key) or fallback_ig)
        except Exception as e:  # noqa: BLE001
            log.exception("Instagram research failed for %s", n.key)
            d["instagram"] = {"skipped": f"error: {str(e)[:150]}"}
        d["plan"] = gemini_plan(cfg, n, d["youtube"], d["instagram"]) or (state.get(n.key) or {}).get("plan", {})
        state[n.key] = d
        rows.append(f"| {n.key} | {d['youtube'].get('videos_scanned', d['youtube'].get('skipped', '—'))} | "
                    f"{len(d['instagram'].get('hashtags', [])) or d['instagram'].get('skipped', '—')} | "
                    f"{'✅' if d['plan'] else '—'} |")
    save_state(state)
    report = write_report(cfg, state)
    try:
        from .growth import LOG_MD
        ideas = "; ".join(f"{k}: {(v.get('plan') or {}).get('summary', '')[:140]}" for k, v in state.items()
                          if isinstance(v, dict) and (v.get("plan") or {}).get("summary"))
        if ideas:
            old = LOG_MD.read_text(encoding="utf-8") if LOG_MD.exists() else "# Growth Log\n"
            LOG_MD.write_text(old.rstrip() + f"\n\n### {today()} — niche research\n{ideas}\n"
                              "Details: [TRENDS.md](TRENDS.md)\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        log.exception("could not write growth log line")
    summary("### Niche research\n\n| niche | YouTube videos scanned | IG hashtags | AI plan |\n|---|---|---|---|\n"
            + "\n".join(rows) + "\n\n" + report[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
