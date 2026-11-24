"""Growth Lab — the bot experiments on itself and keeps what works.

Every post is tagged with a *variant*:
    hour  – the IST hour it went out
    style – the caption/hook style Gemini was asked to write
    tags  – how many hashtags were used

48 hours after posting, analytics records the views on each platform. A post's score is
how much better/worse it did than that account's normal (log-views minus the account's
median log-views), so small and big accounts can be compared fairly.

Choosing the next variant (per niche, per dimension):
  1. any option with fewer than `min_samples` scored posts gets tried first (systematic testing)
  2. otherwise, with probability `explore` a random option is tried (keeps testing)
  3. otherwise the option with the best average score wins (exploit)

Every day's plan, every winner change and a weekly summary are written to
state/growth.json and GROWTH_LOG.md so you (and the weekly review) can see what happened.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from datetime import datetime, timedelta

from .config import ROOT
from .util import TZ, now, now_iso, today

STATE = ROOT / "state" / "growth.json"
LOG_MD = ROOT / "GROWTH_LOG.md"

DEFAULTS = {
    "hours": [8, 10, 12, 13, 15, 17, 18, 19, 20, 21, 22],
    "styles": {
        "question": "End with a direct question that makes people comment.",
        "relatable": "Make it super relatable ('POV:', 'when you…', 'that one friend…').",
        "curiosity": "Open with a curiosity hook that makes people watch till the end ('wait for it', 'nobody noticed…').",
        "short": "Ultra short: max 6 words + 1 emoji. No question.",
        "story": "Tell a tiny 1-line story or confession that sets up the clip.",
        "trend": "(filled automatically every week from niche research)",
    },
    "tag_counts": [3, 6, 10],
    "min_samples": 3,
    "explore": 0.25,
    "score_after_hours": 48,
}

DIMENSIONS = ("hour", "style", "tags")


def settings(cfg) -> dict:
    s = dict(DEFAULTS)
    s.update((cfg.get("growth") or {}))
    return s


def load_state() -> dict:
    if STATE.exists() and STATE.read_text(encoding="utf-8").strip():
        d = json.loads(STATE.read_text(encoding="utf-8"))
    else:
        d = {}
    d.setdefault("plans", {})
    d.setdefault("arms", {})
    d.setdefault("leaders", {})
    d.setdefault("changes", [])
    d.setdefault("daily", {})
    d.setdefault("last_weekly", None)
    return d


def save_state(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    d["plans"] = dict(sorted(d["plans"].items())[-14:])
    d["changes"] = d["changes"][-300:]
    d["daily"] = dict(sorted(d["daily"].items())[-400:])
    STATE.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")


def options(cfg, dim: str) -> list:
    s = settings(cfg)
    return {"hour": [int(h) for h in s["hours"]], "style": list(s["styles"]),
            "tags": [int(t) for t in s["tag_counts"]]}[dim]


def _arm_stats(state: dict, niche: str, dim: str) -> dict:
    return state["arms"].get(niche, {}).get(dim, {})


def _pick(cfg, state: dict, niche: str, dim: str, rng: random.Random, exclude=()) -> object:
    s = settings(cfg)
    opts = [o for o in options(cfg, dim) if o not in exclude] or options(cfg, dim)
    stats = _arm_stats(state, niche, dim)
    n = {o: stats.get(str(o), {}).get("n", 0) for o in opts}
    under = [o for o in opts if n[o] < int(s["min_samples"])]
    if under:
        low = min(n[o] for o in under)
        return rng.choice([o for o in under if n[o] == low])
    if rng.random() < float(s["explore"]):
        return rng.choice(opts)
    return max(opts, key=lambda o: stats.get(str(o), {}).get("mean", -9))


# ── daily plan (which hours to post) ─────────────────────────
def plan_for_today(cfg, state: dict, niche) -> list[int]:
    d = today()
    key = f"{d}/{niche.key}"
    if key in state["plans"]:
        return state["plans"][key]["hours"]
    rng = random.Random(f"{d}-{niche.key}")
    hours: list[int] = []
    for _ in range(max(1, niche.posts_per_day)):
        hours.append(_pick(cfg, state, niche.key, "hour", rng, exclude=hours))
    hours.sort()
    state["plans"][key] = {"hours": hours, "made": now_iso()}
    return hours


def due_count(cfg, state: dict, niche) -> int:
    """How many posts should exist by now today (slots whose hour has started)."""
    h = now().hour
    return sum(1 for x in plan_for_today(cfg, state, niche) if x <= h)


def choose_caption_variant(cfg, state: dict, niche, md5: str) -> dict:
    rng = random.Random(f"{md5}-{today()}")
    style = _pick(cfg, state, niche.key, "style", rng)
    tags = _pick(cfg, state, niche.key, "tags", rng)
    hint = settings(cfg)["styles"].get(style, "")
    try:
        from .trends import niche_hints
        th = niche_hints(niche.key)
    except Exception:  # noqa: BLE001
        th = {}
    if style == "trend":
        hint = th.get("style") or settings(cfg)["styles"].get("curiosity", "")
    return {"style": style, "style_hint": hint, "tags": int(tags),
            "trend_tags": th.get("hashtags", []), "trend_ideas": th.get("ideas", [])[:3]}


# ── scoring ──────────────────────────────────────────────────
def _age_hours(iso: str) -> float:
    try:
        t = datetime.fromisoformat(iso)
    except Exception:  # noqa: BLE001
        return 0
    if t.tzinfo is None:
        t = t.replace(tzinfo=TZ)
    return (now() - t).total_seconds() / 3600


def rescore(cfg, ledger, state: dict) -> list[str]:
    """Recompute every arm from the ledger's 48h views. Returns human-readable leader changes."""
    s = settings(cfg)
    min_age = float(s["score_after_hours"])
    # baseline per niche/platform = median log-views of scored posts
    per_acct: dict[tuple, list[float]] = {}
    scored: list[tuple] = []
    for md5, v in ledger.videos.items():
        var = v.get("variant") or {}
        if not var:
            continue
        for p, pd in v["platforms"].items():
            if pd.get("status") != "posted" or pd.get("views_48h") is None:
                continue
            if _age_hours(pd.get("at", "")) < min_age:
                continue
            lv = math.log1p(max(0, int(pd["views_48h"])))
            per_acct.setdefault((v["niche"], p), []).append(lv)
            scored.append((v["niche"], p, var, lv))
    base = {k: statistics.median(vals) for k, vals in per_acct.items()}
    arms: dict = {}
    for niche, p, var, lv in scored:
        score = lv - base[(niche, p)]
        for dim in DIMENSIONS:
            if var.get(dim) is None:
                continue
            a = arms.setdefault(niche, {}).setdefault(dim, {}).setdefault(str(var[dim]), {"n": 0, "sum": 0.0})
            a["n"] += 1
            a["sum"] += score
    for niche in arms.values():
        for dim in niche.values():
            for a in dim.values():
                a["mean"] = round(a["sum"] / a["n"], 3)
                a["sum"] = round(a["sum"], 3)
    state["arms"] = arms
    # leader changes
    changes = []
    for niche, dims in arms.items():
        for dim, opts in dims.items():
            ready = {k: o for k, o in opts.items() if o["n"] >= int(s["min_samples"])}
            if not ready:
                continue
            best = max(ready, key=lambda k: ready[k]["mean"])
            old = state["leaders"].get(niche, {}).get(dim)
            if best != old:
                state["leaders"].setdefault(niche, {})[dim] = best
                msg = (f"{niche}: best {dim} is now **{_label(dim, best)}** "
                       f"(score {ready[best]['mean']:+.2f}, {ready[best]['n']} posts)"
                       + (f" — was {_label(dim, old)}" if old else ""))
                changes.append(msg)
                state["changes"].append({"at": now_iso(), "text": msg})
    return changes


def _label(dim, v) -> str:
    if v is None:
        return "—"
    if dim == "hour":
        h = int(v)
        return f"{(h % 12) or 12}{'am' if h < 12 else 'pm'}"
    if dim == "tags":
        return f"{v} hashtags"
    return str(v)


# ── daily + weekly logs ──────────────────────────────────────
def record_daily(state: dict, stats: dict, ledger) -> None:
    day = today()
    latest = stats.get("latest", {})
    posted = sum(1 for v in ledger.videos.values() for pd in v["platforms"].values()
                 if pd.get("status") == "posted" and pd.get("day") == day)
    state["daily"][day] = {
        "followers": {k: v.get("followers", v.get("subscribers")) for k, v in latest.items() if v.get("ok")},
        "posts": posted,
    }


def weekly_report(cfg, state: dict, ledger, stats: dict, force: bool = False) -> str | None:
    last = state.get("last_weekly")
    if not force and last and (now() - datetime.fromisoformat(last)).days < 7:
        return None
    d7 = (now() - timedelta(days=7)).strftime("%Y-%m-%d")
    days = sorted(k for k in state["daily"] if k >= d7)
    lines = [f"\n## Week ending {today()} (automatic)\n"]
    # followers
    if days:
        first, lastd = state["daily"][days[0]]["followers"], state["daily"][days[-1]]["followers"]
        lines.append("| account | followers now | change (7d) |\n|---|---|---|")
        for k in sorted(lastd):
            a, b = first.get(k), lastd.get(k)
            ch = (b - a) if (a is not None and b is not None) else None
            lines.append(f"| {k} | {b} | {('+' if (ch or 0) >= 0 else '') + str(ch) if ch is not None else '—'} |")
    # views
    views: dict = {}
    for v in ledger.videos.values():
        for p, pd in v["platforms"].items():
            if pd.get("status") == "posted" and pd.get("day", "") >= d7:
                views.setdefault(f"{v['niche']}/{p}", []).append(pd.get("views_48h") or pd.get("views") or 0)
    if views:
        lines.append("\n| account | posts (7d) | avg views | best post views |\n|---|---|---|---|")
        for k in sorted(views):
            vs = views[k]
            lines.append(f"| {k} | {len(vs)} | {round(sum(vs) / len(vs))} | {max(vs)} |")
    # winners
    lines.append("\n**What's working (Growth Lab):**")
    any_w = False
    for niche, dims in state.get("leaders", {}).items():
        parts = [f"{dim}: {_label(dim, v)}" for dim, v in dims.items()]
        if parts:
            any_w = True
            lines.append(f"- {niche}: " + ", ".join(parts))
    if not any_w:
        lines.append("- Still collecting data (each option needs 3 scored posts first).")
    recent = [c for c in state["changes"] if c["at"][:10] >= d7]
    if recent:
        lines.append("\n**Changes the bot made this week:**")
        lines += [f"- {c['at'][:10]}: {c['text']}" for c in recent]
    text = "\n".join(lines) + "\n"
    header = "# Growth Log\n\nAutomatic weekly reports from the bot + notes from the weekly review.\n"
    old = LOG_MD.read_text(encoding="utf-8") if LOG_MD.exists() else header
    LOG_MD.write_text(old.rstrip() + "\n" + text, encoding="utf-8")
    state["last_weekly"] = now_iso()
    return text


def dashboard_rows(cfg, state: dict) -> list[dict]:
    """For the dashboard's Growth Lab table."""
    s = settings(cfg)
    rows = []
    for niche, dims in state.get("arms", {}).items():
        for dim, opts in dims.items():
            leader = state.get("leaders", {}).get(niche, {}).get(dim)
            for k, a in sorted(opts.items(), key=lambda kv: -kv[1]["mean"]):
                rows.append({"niche": niche, "dim": dim, "option": _label(dim, k), "n": a["n"],
                             "score": a["mean"], "leader": k == leader,
                             "testing": a["n"] < int(s["min_samples"])})
    return rows
