"""Setup checker — run from the Actions tab ("Check setup"). Tests every connection,
creates the Drive folders, and prints a ✅/❌ table telling you exactly what to fix."""
from __future__ import annotations

import sys

import requests

from .config import load
from .util import has_secret, secret, summary

rows: list[tuple[str, str, str]] = []


def ok(what, msg=""):
    rows.append(("✅", what, msg))


def bad(what, msg):
    rows.append(("❌", what, msg))


def warn(what, msg):
    rows.append(("⚠️", what, msg))


def main() -> int:
    cfg = load()
    for s in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN", "GEMINI_API_KEY"):
        (ok if has_secret(s) else bad)(f"secret {s}", "" if has_secret(s) else "missing — see SETUP.md")

    # Drive
    if "PASTE" in str(cfg.drive_root_folder_id):
        bad("Drive folder", "put your YTS folder ID in config/accounts.yaml")
    elif has_secret("GDRIVE_REFRESH_TOKEN"):
        try:
            from .drive import Drive
            d = Drive()
            name = d.root_name(cfg.drive_root_folder_id)
            for n in cfg.niches.values():
                if n.enabled:
                    f = d.niche_folders(cfg.drive_root_folder_id, n.key)
                    cnt = len(d.list_videos(f["inbox"]))
                    ok(f"Drive '{name}/{n.key}'", f"{cnt} videos waiting")
        except Exception as e:  # noqa: BLE001
            bad("Google Drive", str(e)[:200])

    # Gemini
    if has_secret("GEMINI_API_KEY"):
        last = ""
        for model in dict.fromkeys([cfg.get("gemini_model", "gemini-flash-latest"), "gemini-flash-lite-latest"]):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": secret("GEMINI_API_KEY")},
                    json={"contents": [{"parts": [{"text": "Reply with the word ok"}]}]}, timeout=60)
                if r.ok:
                    ok("Gemini API", f"{model} works")
                    break
                last = f"{r.status_code}: {r.text[:150]}"
                if r.status_code in (400, 401, 403):
                    bad("Gemini API", "key rejected — " + last)
                    break
            except Exception as e:  # noqa: BLE001
                last = str(e)[:150]
        else:
            warn("Gemini API", "key OK but Google is busy right now (" + last[:80] + ") — captions fall back automatically")

    # Accounts
    for n in cfg.niches.values():
        if not n.enabled:
            continue
        ig = n.instagram
        if ig.get("enabled"):
            if not (has_secret(ig["token_secret"]) and has_secret(ig["user_id_secret"])):
                bad(f"Instagram @{ig['username']}", f"set secrets {ig['token_secret']} and {ig['user_id_secret']}")
            else:
                try:
                    from .instagram import Instagram
                    c = Instagram(secret(ig["user_id_secret"]), secret(ig["token_secret"]))
                    c.check_username(ig["username"])
                    p = c.profile()
                    ok(f"Instagram @{ig['username']}",
                       f"{p.get('followers_count', 0)} followers, {c.quota_left()} API posts left today")
                except Exception as e:  # noqa: BLE001
                    bad(f"Instagram @{ig['username']}", str(e)[:200])
        fb = n.facebook
        if fb.get("enabled"):
            from .facebook import client_for, has_credentials
            if not has_credentials(n):
                bad(f"Facebook '{fb['page_name'] if isinstance(fb['page_name'], str) else fb['page_name'][0]}'",
                    "no token yet (uses the Instagram token of this niche)")
            else:
                try:
                    c = client_for(n)
                    c.check_name(fb.get("page_name"))
                    p = c.profile()
                    ok(f"Facebook '{p.get('name')}'", f"{p.get('followers_count', p.get('fan_count', 0))} followers")
                except Exception as e:  # noqa: BLE001
                    bad("Facebook (" + n.key + ")", str(e)[:200])
        yt = n.youtube
        if yt.get("enabled"):
            if not has_secret(yt["token_secret"]):
                bad(f"YouTube {yt['handle']}", f"set secret {yt['token_secret']}")
            else:
                try:
                    from .youtube import YouTube
                    c = YouTube(yt["token_secret"])
                    c.check_handle(yt["handle"])
                    s = c.stats()
                    msg = f"{s['subscribers']} subscribers"
                    if cfg.get("youtube_api_audited"):
                        ok(f"YouTube {yt['handle']}", msg + " — uploads ON")
                    else:
                        warn(f"YouTube {yt['handle']}", msg + " — connected; uploads wait for API audit "
                             "(set youtube_api_audited: true after approval)")
                except Exception as e:  # noqa: BLE001
                    bad(f"YouTube {yt['handle']}", str(e)[:200])

    summary("### Setup check\n\n| | item | details |\n|---|---|---|\n" +
            "\n".join(f"| {a} | {b} | {c} |" for a, b, c in rows))
    return 1 if any(r[0] == "❌" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
