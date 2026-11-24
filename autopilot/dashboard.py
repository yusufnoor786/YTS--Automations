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


def build(cfg, ledger, stats: dict) -> None:
    cards = []
    for niche in cfg.niches.values():
        if not niche.enabled:
            continue
        stock = ledger.data.get("stock", {}).get(niche.key)
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
                      f'{stock} videos left in Drive</span>')
        cards.append(f"""
    <section class="card">
      <h2>{escape(niche.label)} {stock_html}</h2>
      <div class="accts">{''.join(accts)}</div>
    </section>""")

    recent_rows = []
    for key, items in stats.get("recent", {}).items():
        for it in items[:4]:
            recent_rows.append((it.get("at") or "", key, it))
    recent_rows.sort(key=lambda r: r[0], reverse=True)
    recent_html = "".join(
        f'<tr><td>{escape(k)}</td><td><a href="{escape(it.get("url") or "#")}" target="_blank">'
        f'{escape(it.get("caption") or "open")}</a></td><td>{fmt(it.get("views"))}</td>'
        f'<td>{fmt(it.get("likes"))}</td><td>{fmt(it.get("comments"))}</td><td>{escape((at or "")[:10])}</td></tr>'
        for at, k, it in recent_rows[:20]) or '<tr><td colspan="6">No posts yet</td></tr>'

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
        ideas = "".join(f"<li>{escape(i)}</li>" for i in plan.get("content_ideas", [])[:5])
        tags = " ".join("#" + escape(t) for t in plan.get("hashtags_to_try", [])[:8])
        style = plan.get("new_style") or {}
        trend_cards.append(f"""<div class="acct"><b>{escape(n.label)}</b>
<p class="mut">{escape(plan.get("summary", ""))}</p>
<div class="mut">Top Shorts in this niche (7 days):</div><ul>{top}</ul>
{"<div class='mut'>Ideas to film:</div><ul>" + ideas + "</ul>" if ideas else ""}
{"<div class='mut'>Hashtags being tested: " + tags + "</div>" if tags else ""}
{"<div class='mut'>New caption style being tested: <b>" + escape(style.get("name", "")) + "</b> — " + escape(style.get("instruction", "")) + "</div>" if style else ""}
<div class="mut" style="margin-top:6px">updated {escape(str(d.get("updated", ""))[:10])}</div></div>""")
    trends_html = "".join(trend_cards) or '<p class="mut">First research runs on Wednesday / Saturday.</p>'

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
</style></head><body><main>
<header><h1>YTS Autopilot <small class="mut">· total audience {fmt(sum(int(v.get("followers", v.get("subscribers", 0)) or 0) for v in stats.get("latest", {}).values() if v.get("ok")))}</small></h1><span class="mut">updated {escape(stats.get("updated", "—")[:16].replace("T", " "))} IST</span></header>
{''.join(cards)}
<section class="card"><h2>Latest posts</h2><div class="scroll"><table>
<tr><th>account</th><th>post</th><th>views</th><th>likes</th><th>comments</th><th>date</th></tr>{recent_html}</table></div></section>
<section class="card"><h2>📈 Niche Trends <span class="pill">what others are winning with</span></h2>
<p class="mut">Twice a week the bot studies the most-viewed new Shorts and Instagram hashtags in each niche, then tests
the best ideas on your pages. Full report: <a href="https://github.com/yusufnoor786/YTS--Automations/blob/main/TRENDS.md" target="_blank">TRENDS.md</a></p>
<div class="accts">{trends_html}</div></section>
<section class="card"><h2>🧪 Growth Lab <span class="pill">what's working</span></h2>
<p class="mut">The bot tests posting times, caption styles and hashtag amounts, scores every post by its views
after 48 hours (vs. that account's normal), and shifts toward the winners while still testing.
Score above 0 = better than usual. Full history: <a href="https://github.com/yusufnoor786/YTS--Automations/blob/main/GROWTH_LOG.md" target="_blank">GROWTH_LOG.md</a></p>
<p class="mut">Today's posting times: {plan_html}</p>
<div class="scroll"><table><tr><th>page</th><th>testing</th><th>option</th><th>posts</th><th>score</th><th>status</th></tr>{lab_html}</table></div>
<h2 style="margin-top:14px">Latest changes</h2><ul class="mut">{changes_html}</ul></section>
<section class="card"><h2>Bot runs</h2><div class="scroll"><table>
<tr><th>time</th><th>status</th><th>details</th></tr>{runs_html}</table></div></section>
</main></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    from .analytics import load_stats
    from .config import load
    from .ledger import Ledger

    build(load(), Ledger(), load_stats())
