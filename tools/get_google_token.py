"""Run this ON YOUR PC (one time per account) to connect Google Drive and each YouTube channel.

    pip install google-auth-oauthlib requests
    python tools/get_google_token.py drive        → GDRIVE_REFRESH_TOKEN
    python tools/get_google_token.py youtube      → YT_TOKEN_<NICHE>   (run once per channel)

A browser opens. Log in, and for YouTube PICK THE RIGHT CHANNEL (brand account).
If you see "Google hasn't verified this app": click Advanced → Go to (unsafe) — it's your own app.
"""
import json
import os
import sys

import requests
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = {
    "drive": ["https://www.googleapis.com/auth/drive"],
    "youtube": ["https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly"],
}


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if mode not in SCOPES:
        print(__doc__)
        sys.exit(1)
    cid = os.environ.get("GOOGLE_CLIENT_ID") or input("Paste GOOGLE_CLIENT_ID: ").strip()
    secret = os.environ.get("GOOGLE_CLIENT_SECRET") or input("Paste GOOGLE_CLIENT_SECRET: ").strip()
    flow = InstalledAppFlow.from_client_config(
        {"installed": {"client_id": cid, "client_secret": secret,
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": "https://oauth2.googleapis.com/token",
                       "redirect_uris": ["http://localhost"]}},
        SCOPES[mode])
    print("\nIf your browser does not open by itself, copy the whole link below into Chrome/Edge")
    print("(on THIS computer), log in, and allow. Keep this window open until it finishes.\n")
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline", open_browser=True,
                                  authorization_prompt_message="{url}\n",
                                  success_message="Done! You can close this tab and go back to the black window.")
    if not creds.refresh_token:
        print("No refresh token returned. Remove the app at https://myaccount.google.com/permissions and retry.")
        sys.exit(1)

    who = ""
    if mode == "youtube":
        r = requests.get("https://www.googleapis.com/youtube/v3/channels",
                         params={"part": "snippet", "mine": "true"},
                         headers={"Authorization": f"Bearer {creds.token}"}, timeout=30).json()
        items = r.get("items", [])
        if items:
            sn = items[0]["snippet"]
            who = f'{sn["title"]} ({sn.get("customUrl", "")})'
        else:
            who = "⚠ no channel found on this login — did you pick the brand account?"
    else:
        r = requests.get("https://www.googleapis.com/drive/v3/about", params={"fields": "user"},
                         headers={"Authorization": f"Bearer {creds.token}"}, timeout=30).json()
        who = r.get("user", {}).get("emailAddress", "")

    print("\n" + "=" * 70)
    print(f"Connected: {who}")
    print("Add this as a GitHub secret "
          f"({'GDRIVE_REFRESH_TOKEN' if mode == 'drive' else 'YT_TOKEN_AESTHETIC / YT_TOKEN_GTA / YT_TOKEN_HORROR'}):")
    print("\n" + creds.refresh_token + "\n")
    print("=" * 70)
    with open(f"token_{mode}_{who.split(' ')[0][:20].strip('@') or 'account'}.json", "w") as f:
        json.dump({"refresh_token": creds.refresh_token, "account": who}, f)


if __name__ == "__main__":
    main()
