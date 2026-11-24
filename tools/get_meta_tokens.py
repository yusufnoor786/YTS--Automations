"""Run this ON YOUR PC once to get NON-EXPIRING Instagram tokens for all 3 pages.

    pip install requests pyyaml
    python tools/get_meta_tokens.py

You need: your Meta App ID + App Secret, and a short-lived User token from
Graph API Explorer with these permissions:
  instagram_basic, instagram_content_publish, instagram_manage_insights,
  pages_show_list, pages_read_engagement, business_management
"""
import sys
from pathlib import Path

import requests

V = "v25.0"
G = f"https://graph.facebook.com/{V}"


def die(msg):
    print("\n❌ " + msg)
    sys.exit(1)


def main():
    app_id = input("Meta App ID: ").strip()
    app_secret = input("Meta App Secret: ").strip()
    short = input("Short-lived User token from Graph API Explorer: ").strip()

    r = requests.get(f"{G}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id,
        "client_secret": app_secret, "fb_exchange_token": short}, timeout=30).json()
    if "access_token" not in r:
        die(f"Could not get long-lived token: {r}")
    long_user = r["access_token"]

    pages = requests.get(f"{G}/me/accounts", params={
        "fields": "name,access_token,instagram_business_account{id,username}",
        "limit": 100, "access_token": long_user}, timeout=30).json()
    if "error" in pages:
        die(str(pages["error"]))

    want = {}
    cfg = Path(__file__).resolve().parent.parent / "config" / "accounts.yaml"
    if cfg.exists():
        import yaml
        for key, n in (yaml.safe_load(cfg.read_text(encoding="utf-8")).get("niches") or {}).items():
            ig = n.get("instagram") or {}
            if ig.get("username"):
                want[ig["username"].lower()] = (ig["token_secret"], ig["user_id_secret"])

    found = 0
    print("\n" + "=" * 70 + "\nAdd these as GitHub secrets:\n")
    for p in pages.get("data", []):
        ig = p.get("instagram_business_account")
        if not ig:
            print(f"(Page '{p['name']}' has no Instagram professional account linked — skipped)")
            continue
        uname = ig.get("username", "").lower()
        tok_name, id_name = want.get(uname, (f"IG_TOKEN_{uname.upper()}", f"IG_ID_{uname.upper()}"))
        dbg = requests.get(f"{G}/debug_token", params={"input_token": p["access_token"],
                           "access_token": f"{app_id}|{app_secret}"}, timeout=30).json().get("data", {})
        exp = "never expires ✅" if dbg.get("expires_at", 1) == 0 else f"expires_at={dbg.get('expires_at')}"
        print(f"@{uname}  (Page: {p['name']}, token {exp})")
        print(f"  {id_name} = {ig['id']}")
        print(f"  {tok_name} = {p['access_token']}\n")
        found += 1
    print("=" * 70)
    if not found:
        die("No Instagram accounts found. Make sure each IG is Professional and linked to a Facebook Page, "
            "and that you ticked all Pages when Graph API Explorer asked.")


if __name__ == "__main__":
    main()
