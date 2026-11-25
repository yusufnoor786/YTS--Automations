"""Builds docs/index.html — the analytics dashboard served free by GitHub Pages."""
from __future__ import annotations

from datetime import date, timedelta
from html import escape

from .config import ROOT

OUT = ROOT / "docs" / "index.html"
GOALS = {"instagram": 100_000, "youtube": 10_000, "facebook": 10_000}
ICON = {"instagram": "IG", "youtube": "YT", "facebook": "FB"}


def fmt(n) -> str:
    if n is None:
        return "—"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 10_000:
        return f"{n / 1e3:.0f}k"
    if n >= 1_000:
        return f"{n / 1e3:.1f}k"
    return str(n)


def spark(values: list[int], w: int = 220, h: int = 46) -> str:
    if len(values) < 2:
        return '<div class="nodata">chart appears after 2 days of data</div>'
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1
    step = w / (len(values) - 1)
    pts = " ".join(f"{i * step:.1f},{h - 4 - (v - lo) / rng * (h - 8):.1f}" for i, v in enumerate(values))
    return (f'<svg viewBox="0 0 {w} {h}" class="spark" preserveAspectRatio="none">'
            f'<polyline points="{pts}" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def series(stats: dict, key: str, days: int = 60) -> list[int]:
    out = []
    for d in sorted(stats.get("history", {}))[-days:]:
        v = stats["history"][d].get(key, {}).get("followers")
        if v is not None:
            out.append(int(v))
    return out


def delta(stats: dict, key: str, days: int) -> int | None:
    hist = stats.get("history", {})
    if not hist:
        return None
    last = sorted(hist)[-1]
    target = (date.fromisoformat(last) - timedelta(days=days)).isoformat()
    older = [d for d in sorted(hist) if d <= target and key in hist[d]]
    if key not in hist[last] or not older:
        return None
    return int(hist[last][key]["followers"]) - int(hist[older[-1]][key]["followers"])


def posts_count(ledger, niche: str, platform: str, days: int) -> int:
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    return sum(1 for v in ledger.videos.values() if v["niche"] == niche
               and v["platforms"].get(platform, {}).get("status") == "posted"
               and v["platforms"][platform].get("day", "") >= since)


def _when(iso: str) -> str:
    """'25 Nov, 9:31 pm' in IST."""
    from datetime import datetime
    from .util import TZ
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00").replace("+0000", "+00:00"))
    except Exception:  # noqa: BLE001
        return escape(iso[:16])
    if t.tzinfo is None:
        t = t.replace(tzinfo=TZ)
    t = t.astimezone(TZ)
    return f"{t.day} {t.strftime('%b')}, {(t.hour % 12) or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def niche_posts(ledger, stats: dict, niche: str, platform: str, n: int = 8) -> list[dict]:
    """Bot posts from the ledger (real time) merged with the platform's own latest posts (analytics)."""
    items: dict[str, dict] = {}
    for v in ledger.videos.values():
        if v["niche"] != niche:
            continue
        pd = v["platforms"].get(platform) or {}
        if pd.get("status") != "posted":
            continue
        cap = v.get("caption") or {}
        text = (cap.get("yt_title") if platform == "youtube" else (cap.get("ig_caption") or "").split("\n")[0]) or v["name"]
        key = pd.get("url") or pd.get("id") or v["name"]
        items[key] = {"url": pd.get("url"), "at": pd.get("at", ""), "text": text, "views": pd.get("views"),
                      "likes": None, "file": v["name"]}
    for it in stats.get("recent", {}).get(f"{niche}/{platform}", []):
        url = it.get("url")
        match = next((k for k, x in items.items() if url and x.get("url") and
                      (url.rstrip("/").split("/")[-1] == x["url"].rstrip("/").split("/")[-1])), None)
        if match:
            items[match]["views"] = it.get("views") if it.get("views") is not None else items[match]["views"]
            items[match]["likes"] = it.get("likes")
            continue
        items[url or str(len(items))] = {"url": url, "at": it.get("at") or "", "text": it.get("caption") or "post",
                                         "views": it.get("views"), "likes": it.get("likes"), "file": None}
    return sorted(items.values(), key=lambda x: x["at"] or "", reverse=True)[:n]


def posts_board(cfg, ledger, stats: dict) -> str:
    blocks = []
    for niche in cfg.niches.values():
        if not niche.enabled:
            continue
        cols = []
        for p in ("instagram", "facebook", "youtube"):
            pc = niche.platform(p)
            if not pc.get("enabled"):
                continue
            name = {"instagram": "@" + pc.get("username", ""), "youtube": pc.get("handle", ""),
                    "facebook": (pc.get("page_name") if isinstance(pc.get("page_name"), str)
                                 else (pc.get("page_name") or [""])[0])}[p]
            posts = niche_posts(ledger, stats, niche.key, p)
            total = sum(1 for v in ledger.videos.values() if v["niche"] == niche.key
                        and (v["platforms"].get(p) or {}).get("status") == "posted")
            today = ledger.posted_today(niche.key, p)
            rows = "".join(
                f'<li><a href="{escape(x["url"] or "#")}" target="_blank">{escape((x["text"] or "")[:70])}</a>'
                f'<div class="pmeta">{_when(x["at"]) if x["at"] else ""}'
                f'{" · 👁 " + fmt(x["views"]) if x["views"] is not None else ""}'
                f'{" · ♥ " + fmt(x["likes"]) if x.get("likes") is not None else ""}</div></li>'
                for x in posts)
            if not rows:
                rows = ('<li class="mut">waiting for API audit</li>' if p == "youtube" and not cfg.get("youtube_api_audited")
                        else '<li class="mut">no posts yet</li>')
            cols.append(f'<div class="pcol {p}"><div class="phead"><span class="badge {p}">{ICON[p]}</span>'
                        f'<b>{escape(name)}</b><span class="mut">today {today} · total {total}</span></div>'
                        f'<ol class="plist">{rows}</ol></div>')
        blocks.append(f'<div class="pniche"><h3>{escape(niche.label)}</h3><div class="pcols">{"".join(cols)}</div></div>')
    return "".join(blocks)


def _post_views(v: dict) -> int | None:
    """Total views of one video across platforms (48h score if frozen, else views so far)."""
    vals = [pd.get("views_48h") if pd.get("views_48h") is not None else pd.get("views")
            for pd in v["platforms"].values() if pd.get("status") == "posted"]
    vals = [int(x) for x in vals if x is not None]
    return sum(vals) if vals else None


def _avg(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def trend_board(cfg, ledger, tstate: dict, gs: dict) -> str:
    from . import growth, trends
    cards = []
    for niche in cfg.niches.values():
        if not niche.enabled:
            continue
        d = tstate.get(niche.key) or {}
        auto = (tstate.get("_auto") or {}).get(niche.key) or {}
        plan = d.get("plan") or {}
        posts = []
        for v in ledger.videos.values():
            if v["niche"] != niche.key or not any(pd.get("status") == "posted" for pd in v["platforms"].values()):
                continue
            var = v.get("variant") or {}
            tu = var.get("trend") or trends.trend_usage(niche.key, v.get("caption"), var, tstate, niche.hashtags)
            posts.append((tu, _post_views(v)))
        n_all = len(posts)

        def row(label, sel):
            a = [vw for tu, vw in posts if sel(tu)]
            b = [vw for tu, vw in posts if not sel(tu)]
            av, bv = _avg(a), _avg(b)
            if len(a) >= 3 and len(b) >= 3 and av is not None and bv is not None:
                diff = (av - bv) / max(bv, 1) * 100
                verdict = (f'<span style="color:var(--{"ok" if diff >= 0 else "yt"})">'
                           f'{"▲" if diff >= 0 else "▼"} {abs(diff):.0f}% vs normal</span>')
            else:
                verdict = f'<span class="mut">collecting ({len(a)} posts so far, needs 3)</span>'
            return (f"<tr><td>{label}</td><td>{len(a)}</td><td>{fmt(round(av)) if av is not None else '—'}</td>"
                    f"<td>{fmt(round(bv)) if bv is not None else '—'}</td><td>{verdict}</td></tr>")

        rows = [row("trend hashtags", lambda t: bool(t.get("tags")))]
        if auto.get("style"):
            rows.append(row(f"trend caption style", lambda t: t.get("style")))
        if auto.get("extra_hours"):
            rows.append(row("research posting time " + ", ".join(growth._label("hour", h) for h in auto["extra_hours"]),
                            lambda t: t.get("hour")))
        if auto.get("extra_tag_counts"):
            rows.append(row(f"{auto['extra_tag_counts'][0]} hashtags (top-post count)", lambda t: t.get("tag_count")))
        # per hashtag
        per_tag: dict[str, list] = {}
        for tu, vw in posts:
            for t in tu.get("tags") or []:
                per_tag.setdefault(t, []).append(vw)
        tag_html = " ".join(
            f'<span class="chip" title="{len(vs)} posts">#{escape(t)} <b>{fmt(round(_avg(vs))) if _avg(vs) is not None else "—"}</b>'
            f'<small> · {len(vs)}</small></span>'
            for t, vs in sorted(per_tag.items(), key=lambda kv: -(_avg(kv[1]) or 0))[:8])
        following = []
        if auto.get("hashtags"):
            following.append("hashtags " + " ".join(f'<span class="chip">#{escape(t)}</span>' for t in auto["hashtags"][:10]))
        st = plan.get("new_style") or {}
        if auto.get("style"):
            following.append(f'caption style <b>{escape(st.get("name") or "trend")}</b> '
                             f'<span class="mut">— {escape(auto["style"][:140])}</span>')
        if auto.get("extra_hours"):
            following.append("extra posting time " + ", ".join(growth._label("hour", h) for h in auto["extra_hours"]))
        if auto.get("extra_tag_counts"):
            following.append(f"{auto['extra_tag_counts'][0]} hashtags per post (what top posts use)")
        if plan.get("hooks") or (d.get("instagram") or {}).get("top_hooks"):
            following.append("opening hooks from this week's most-liked posts")
        upd = str(d.get("updated", ""))
        cards.append(f"""<div class="acct"><b>{escape(niche.label)}</b>
<div class="mut" style="margin:4px 0">research {('updated <span data-ago="' + escape(upd) + '">' + escape(upd[:10]) + '</span> · repeats every ~3.5 days') if upd else 'runs soon'}</div>
<div class="mut">🤖 Following now:</div><ul class="tight">{''.join(f'<li>{x}</li>' for x in following) or '<li class="mut">nothing yet</li>'}</ul>
<div class="mut">📊 Results ({n_all} posts; views so far, 48h score once old enough):</div>
<div class="scroll"><table><tr><th>trend</th><th>posts</th><th>avg views</th><th>others</th><th>result</th></tr>{''.join(rows)}</table></div>
{('<div class="mut" style="margin-top:6px">best trend hashtags (avg views · posts):</div><div>' + tag_html + '</div>') if tag_html else ''}
</div>""")
    log_items = [c for c in gs.get("changes", []) if "[auto from research]" in c.get("text", "")][-10:][::-1]
    log_html = "".join(f"<li>{escape(c['at'][:10])} — {escape(c['text'].replace('[auto from research] ', ''))}</li>"
                       for c in log_items) or "<li>No research changes yet.</li>"
    return (f'<div class="accts">{"".join(cards)}</div>'
            f'<h2 style="margin-top:14px">What the bot changed from research</h2><ul class="mut">{log_html}</ul>')


def build(cfg, ledger, stats: dict) -> None:
    cards = []
    for niche in cfg.niches.values():
        if not niche.enabled:
            continue
        inv = ledger.data.get("inventory", {}).get(niche.key) or {}
        stock = inv.get("new", ledger.data.get("stock", {}).get(niche.key))
        accts = []
        for p in ("instagram", "facebook", "youtube"):
            pc = niche.platform(p)
            if not pc.get("enabled"):
                continue
            key = f"{niche.key}/{p}"
            lt = stats.get("latest", {}).get(key, {})
            followers = lt.get("followers", lt.get("subscribers"))
            goal = GOALS[p]
            pct = min(100, (followers or 0) / goal * 100)
            d7 = delta(stats, key, 7)
            name = {"instagram": "@" + pc.get("username", ""), "youtube": pc.get("handle", ""),
                    "facebook": (pc.get("page_name") if isinstance(pc.get("page_name"), str)
                                 else (pc.get("page_name") or [""])[0]) + " (Page)"}[p]
            status = ""
            if p == "youtube" and not cfg.get("youtube_api_audited"):
                status = '<span class="pill warn">waiting for API audit</span>'
            elif lt and not lt.get("ok", True):
                status = f'<span class="pill bad" title="{escape(lt.get("error", ""))}">error</span>'
            elif not lt:
                status = '<span class="pill">not connected</span>'
            accts.append(f"""
      <div class="acct {p}">
        <div class="row"><span class="badge {p}">{ICON[p]}</span><b>{escape(name)}</b>{status}</div>
        <div class="big">{fmt(followers)} <small>{'subscribers' if p == 'youtube' else 'followers'}</small></div>
        <div class="bar"><i style="width:{pct:.2f}%"></i></div>
        <div class="meta">{pct:.1f}% of {fmt(goal)} goal ·
          7d: {('+' if (d7 or 0) >= 0 else '') + fmt(d7) if d7 is not None else '—'} ·
          posts 7d: {posts_count(ledger, niche.key, p, 7)}</div>
        <div class="sparkwrap {p}">{spark(series(stats, key))}</div>
      </div>""")
        stock_html = ("" if stock is None else
                      f'<span class="pill {"bad" if stock <= cfg.get("low_stock_warning", 5) else "ok"}">'
                      f'{stock} new videos in Drive</span>')
        cards.append(f"""
    <section class="card">
      <h2>{escape(niche.label)} {stock_html}</h2>
      <div class="accts">{''.join(accts)}</div>
    </section>""")

    recent_html = posts_board(cfg, ledger, stats)

    from . import growth
    gs = growth.load_state()
    lab_rows = growth.dashboard_rows(cfg, gs)
    dim_name = {"hour": "posting time", "style": "caption style", "tags": "hashtags"}
    lab_html = "".join(
        f'<tr><td>{escape(r["niche"])}</td><td>{dim_name.get(r["dim"], r["dim"])}</td>'
        f'<td>{"🏆 " if r["leader"] else ""}{escape(r["option"])}</td><td>{r["n"]}</td>'
        f'<td style="color:{"var(--ok)" if r["score"] > 0 else "var(--yt)" if r["score"] < 0 else "inherit"}">'
        f'{r["score"]:+.2f}</td><td>{"testing" if r["testing"] else "measured"}</td></tr>'
        for r in lab_rows) or ('<tr><td colspan="6">Collecting data — the first scores appear 2–3 days after the '
                               'first posts (each post is scored 48h after it goes live).</td></tr>')
    plans_today = {k.split("/", 1)[1]: v["hours"] for k, v in gs.get("plans", {}).items()
                   if k.startswith(growth.today() + "/")}
    plan_html = " · ".join(f"<b>{escape(n)}</b>: {', '.join(growth._label('hour', h) for h in hs)}"
                           for n, hs in plans_today.items()) or "—"
    changes_html = "".join(f"<li>{escape(c['at'][:10])} — {escape(c['text']).replace('**', '')}</li>"
                           for c in gs.get("changes", [])[-8:][::-1]) or "<li>No changes yet.</li>"

    from . import trends
    tstate = trends.load_state()
    trend_cards = []
    for key, d in tstate.items():
        n = cfg.niches.get(key)
        if not n or not isinstance(d, dict):
            continue
        yt, plan = d.get("youtube", {}), d.get("plan", {}) or {}
        top = "".join(f'<li><a href="{escape(x["url"])}" target="_blank">{escape(x["title"][:70])}</a> '
                      f'<span class="mut">· {escape(x["channel"][:22])} · {fmt(x["views_per_day"])}/day · {x["seconds"]}s</span></li>'
                      for x in yt.get("top", [])[:5]) or f'<li class="mut">{escape(yt.get("skipped", "no data yet"))}</li>'
        ig = d.get("instagram", {}) or {}
        igtags = " ".join(f"#{escape(h['tag'])} ({fmt(h['avg_engagement'])})" for h in ig.get("hashtags", [])[:6]
                          if h.get("avg_engagement") is not None)
        ighours = ", ".join(growth._label("hour", h) for h in ig.get("best_hours", []))
        auto = (tstate.get("_auto") or {}).get(key, {})
        auto_txt = "; ".join(x for x in [
            ("testing extra hours " + ", ".join(growth._label("hour", h) for h in auto.get("extra_hours", [])))
            if auto.get("extra_hours") else "",
            (f"testing {auto['extra_tag_counts'][0]} hashtags" if auto.get("extra_tag_counts") else ""),
            ("hashtag pool " + " ".join("#" + t for t in auto.get("hashtags", [])[:6])) if auto.get("hashtags") else ""] if x)
        ideas = "".join(f"<li>{escape(i)}</li>" for i in plan.get("content_ideas", [])[:5])
        tags = " ".join("#" + escape(t) for t in plan.get("hashtags_to_try", [])[:8])
        style = plan.get("new_style") or {}
        trend_cards.append(f"""<div class="acct"><b>{escape(n.label)}</b>
<p class="mut">{escape(plan.get("summary", ""))}</p>
<div class="mut">Top Shorts in this niche (7 days):</div><ul>{top}</ul>
{"<div class='mut'>Instagram top hashtags (avg engagement of top posts): " + igtags + "</div>" if igtags else ""}
{"<div class='mut'>Instagram top posts go out at: " + ighours + "</div>" if ighours else ""}
{"<div class='mut' style='color:var(--ok)'>🤖 Applied automatically: " + escape(auto_txt) + "</div>" if auto_txt else ""}
{"<div class='mut'>Ideas to film:</div><ul>" + ideas + "</ul>" if ideas else ""}
{"<div class='mut'>Hashtags being tested: " + tags + "</div>" if tags else ""}
{"<div class='mut'>New caption style being tested: <b>" + escape(style.get("name", "")) + "</b> — " + escape(style.get("instruction", "")) + "</div>" if style else ""}
<div class="mut" style="margin-top:6px">updated {escape(str(d.get("updated", ""))[:10])}</div></div>""")
    trends_html = "".join(trend_cards) or '<p class="mut">First research runs on Wednesday / Saturday.</p>'

    from .util import now_iso
    inv_all = ledger.data.get("inventory", {})
    plat_short = {"instagram": "IG", "facebook": "FB", "youtube": "YT"}

    def per_plat(d: dict, active: list, cls: str = "") -> str:
        return " · ".join(f'<span class="{cls}">{plat_short[p]} <b>{d[p]:,}</b></span>' if d.get(p) is not None else f"{plat_short[p]} —"
                          for p in ("instagram", "facebook", "youtube") if p in active) or "—"

    inv_rows = []
    for niche in cfg.niches.values():
        if not niche.enabled:
            continue
        iv = inv_all.get(niche.key)
        if not iv:
            inv_rows.append(f'<tr><td><b>{escape(niche.label)}</b></td><td colspan="8" class="mut">counting on the next run…</td></tr>')
            continue
        act = iv.get("active") or []
        nh = iv.get("next_hour")
        nxt = ("due now" if iv.get("due_now") else growth._label("hour", nh) + " today") if nh is not None else "tomorrow"
        if not act:
            nxt = "no page connected"
        elif not any((iv.get("left") or {}).values()):
            nxt = "⚠️ add videos to Drive"
        low = iv.get("new", 0) <= cfg.get("low_stock_warning", 5)
        inv_rows.append(
            f'<tr><td><b>{escape(niche.label)}</b></td>'
            f'<td class="num">{iv.get("in_drive", 0):,}</td>'
            f'<td class="num {"bad" if low else "okc"}">{iv.get("new", 0):,}</td>'
            f'<td>{per_plat(iv.get("posted", {}), act)}</td>'
            f'<td>{per_plat(iv.get("left", {}), act)}</td>'
            f'<td>{per_plat(iv.get("posted_today", {}), act)} <span class="mut">/ {iv.get("per_day")}</span></td>'
            f'<td>{escape(nxt)}</td>'
            f'<td class="num">{iv.get("duplicates", 0)} / {iv.get("failed", 0)}</td>'
            f'<td class="num">{iv.get("days_left", 0):,}</td></tr>')
    inv_html = "".join(inv_rows)
    inv_at = max((v.get("at", "") for v in inv_all.values()), default="")

    all_runs = ledger.data.get("runs", [])
    last_run = all_runs[-1]["at"] if all_runs else ""
    built_at = now_iso()
    runs = ledger.data.get("runs", [])[-12:][::-1]
    runs_html = "".join(
        f'<tr><td>{escape(r.get("at", "")[:16].replace("T", " "))}</td>'
        f'<td>{"✅" if not r.get("errors") else "❌ " + str(r["errors"])}</td>'
        f'<td>{escape("; ".join(f"{a}/{b}: {d}" for a, b, c, d in r.get("rows", []))[:300])}</td></tr>'
        for r in runs) or '<tr><td colspan="3">No runs yet</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YTS Autopilot — Dashboard</title>
<style>
:root{{--bg:#0f1115;--card:#171a21;--line:#262b36;--txt:#e8eaf0;--mut:#8b93a7;--ig:#e1306c;--yt:#ff3d3d;--fb:#1877f2;--ok:#2fbf71;--warn:#e0a526}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--txt);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:20px 16px 60px}}
header{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:18px}}
h1{{font-size:22px;margin:0}}h2{{font-size:16px;margin:0 0 12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
.mut{{color:var(--mut);font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px}}
.accts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}
.acct{{border:1px solid var(--line);border-radius:12px;padding:12px}}
.row{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
.badge{{font-size:11px;font-weight:700;padding:2px 6px;border-radius:6px;color:#fff}}.badge.instagram{{background:var(--ig)}}.badge.youtube{{background:var(--yt)}}.badge.facebook{{background:var(--fb)}}
.big{{font-size:28px;font-weight:700;margin:6px 0 4px}}.big small{{font-size:13px;color:var(--mut);font-weight:400}}
.bar{{height:6px;background:var(--line);border-radius:6px;overflow:hidden}}.bar i{{display:block;height:100%;background:var(--ok)}}
.meta{{color:var(--mut);font-size:12.5px;margin-top:6px}}
.sparkwrap{{margin-top:8px}}.sparkwrap.instagram{{color:var(--ig)}}.sparkwrap.youtube{{color:var(--yt)}}.sparkwrap.facebook{{color:var(--fb)}}
.spark{{width:100%;height:46px}}.nodata{{color:var(--mut);font-size:12px}}
.pill{{font-size:12px;font-weight:500;padding:2px 8px;border-radius:99px;border:1px solid var(--line);color:var(--mut)}}
.pill.ok{{color:var(--ok);border-color:var(--ok)}}.pill.bad{{color:var(--yt);border-color:var(--yt)}}.pill.warn{{color:var(--warn);border-color:var(--warn)}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}}td,th{{text-align:left;padding:7px 6px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-weight:500}}a{{color:#8ab4ff;text-decoration:none}}
.scroll{{overflow-x:auto}}
.chip{{display:inline-block;font-size:12px;border:1px solid var(--line);border-radius:99px;padding:1px 8px;margin:2px 2px 0 0}}.chip small{{color:var(--mut)}}
ul.tight{{margin:4px 0 8px;padding-left:18px;font-size:13.5px}}
.pniche{{margin-top:14px}}.pniche h3{{font-size:15px;margin:0 0 8px}}
.pcols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px}}
.pcol{{border:1px solid var(--line);border-radius:12px;padding:10px;border-top:3px solid var(--line)}}
.pcol.instagram{{border-top-color:var(--ig)}}.pcol.facebook{{border-top-color:var(--fb)}}.pcol.youtube{{border-top-color:var(--yt)}}
.phead{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px}}.phead .mut{{margin-left:auto}}
.plist{{margin:0;padding-left:20px;font-size:13.5px}}.plist li{{margin:6px 0}}.pmeta{{color:var(--mut);font-size:12px}}
.num{{font-variant-numeric:tabular-nums;font-weight:600}}.okc{{color:var(--ok)}}td.bad{{color:var(--yt)}}
.status{{display:flex;gap:14px;flex-wrap:wrap;align-items:center;font-size:13.5px}}
.dot{{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--ok);margin-right:6px}}.dot.late{{background:var(--warn)}}.dot.dead{{background:var(--yt)}}
</style></head><body><main>
<header><h1>YTS Autopilot <small class="mut">· total audience {fmt(sum(int(v.get("followers", v.get("subscribers", 0)) or 0) for v in stats.get("latest", {}).values() if v.get("ok")))}</small></h1><span class="mut">followers &amp; views updated <b data-ago="{escape(stats.get("updated", ""))}">{escape(stats.get("updated", "—")[:16].replace("T", " "))}</b></span></header>
<section class="card"><div class="status">
  <span><span id="dot" class="dot"></span>Bot last ran <b id="lastrun" data-t="{escape(last_run)}">{escape(last_run[:16].replace("T", " "))}</b></span>
  <span class="mut">next update <b id="nextrun">soon</b> · the bot refreshes every number every 15 min and this page reloads by itself</span>
  <span class="mut" id="livestate"></span></div></section>
<section class="card"><h2>📦 Videos <span class="pill ok">live count</span>
  <span class="mut" style="font-weight:400">counted <span data-ago="{escape(inv_at)}">{escape(inv_at[:16].replace("T", " "))}</span></span></h2>
<div class="scroll"><table>
<tr><th>page</th><th>in Drive</th><th>new</th><th>posted (all time)</th><th>left to post</th><th>posted today</th><th>next post</th><th>dupes / failed</th><th>days left</th></tr>
{inv_html}</table></div>
<p class="mut">in Drive = every video in the niche folder incl. _posted/_duplicates/_failed · new = never posted anywhere ·
left to post = videos each platform still hasn't posted · days left = how long the stock lasts at the current posts/day.</p></section>
{''.join(cards)}
<section class="card"><h2>🎬 Posts by page <span class="pill">every niche · every channel</span></h2>
{recent_html}</section>
<section class="card"><h2>📈 Niche Trends <span class="pill">what others are winning with</span></h2>
<p class="mut">Twice a week the bot studies the most-viewed new Shorts and Instagram hashtags in each niche, then tests
the best ideas on your pages. Full report: <a href="https://github.com/yusufnoor786/YTS--Automations/blob/main/TRENDS.md" target="_blank">TRENDS.md</a></p>
<div class="accts">{trends_html}</div></section>
<section class="card"><h2>🔥 Trends followed &amp; results <span class="pill ok">auto-applied</span></h2>
<p class="mut">Everything below was picked by the bot from its own research and is applied to new posts automatically.
Each trend is compared with the page's other posts; a trend that loses is dropped by the Growth Lab.</p>
{trend_board(cfg, ledger, tstate, gs)}</section>
<section class="card"><h2>🧪 Growth Lab <span class="pill">what's working</span></h2>
<p class="mut">The bot tests posting times, caption styles and hashtag amounts, scores every post by its views
after 48 hours (vs. that account's normal), and shifts toward the winners while still testing.
Score above 0 = better than usual. Full history: <a href="https://github.com/yusufnoor786/YTS--Automations/blob/main/GROWTH_LOG.md" target="_blank">GROWTH_LOG.md</a></p>
<p class="mut">Today's posting times: {plan_html}</p>
<div class="scroll"><table><tr><th>page</th><th>testing</th><th>option</th><th>posts</th><th>score</th><th>status</th></tr>{lab_html}</table></div>
<h2 style="margin-top:14px">Latest changes</h2><ul class="mut">{changes_html}</ul></section>
<section class="card"><h2>Bot runs</h2><div class="scroll"><table>
<tr><th>time</th><th>status</th><th>details</th></tr>{runs_html}</table></div></section>
</main>
<script>
const BUILT="{built_at}";
function ago(t){{if(!t)return"never";const m=Math.round((Date.now()-new Date(t))/60000);
 if(m<1)return"just now";if(m<60)return m+" min ago";const h=Math.floor(m/60);return h+"h "+(m%60)+"m ago";}}
function tick(){{const lr=document.getElementById("lastrun");const t=lr.dataset.t;lr.textContent=ago(t);
 const m=t?(Date.now()-new Date(t))/60000:1e9;document.getElementById("dot").className="dot"+(m>60?" dead":m>30?" late":"");
 const ls=document.getElementById("livestate"); if(m>60&&!ls.dataset.busy){{ls.innerHTML='<b style="color:var(--yt)">⚠️ the bot is late — it keeps retrying by itself (GitHub was having problems). Numbers below are from '+ago(t)+'.</b>';}}
 document.querySelectorAll("[data-ago]").forEach(e=>e.textContent=ago(e.dataset.ago));
 const q=15*60000, nxt=Math.ceil((Date.now()-90000)/q)*q+90000+150000, mm=Math.max(0,Math.round((nxt-Date.now())/60000));
 document.getElementById("nextrun").textContent= mm<=1?"any moment":"in ~"+mm+" min";}}
async function poll(){{try{{const r=await fetch("https://api.github.com/repos/yusufnoor786/YTS--Automations/contents/docs/live.json?ref=main",
 {{headers:{{Accept:"application/vnd.github.raw+json"}},cache:"no-store"}});if(!r.ok)return;const j=await r.json();
 if(j.built&&new Date(j.built)>new Date(BUILT)){{const ls2=document.getElementById("livestate");ls2.dataset.busy=1;ls2.textContent="new data — refreshing…";
 setTimeout(()=>location.reload(),60000);}}}}catch(e){{}}}}
tick();setInterval(tick,20000);poll();setInterval(poll,120000);
</script></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    import json
    (OUT.parent / "live.json").write_text(json.dumps(
        {"built": built_at, "last_run": last_run, "inventory": inv_all}, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    from .analytics import load_stats
    from .config import load
    from .ledger import Ledger

    build(load(), Ledger(), load_stats())
