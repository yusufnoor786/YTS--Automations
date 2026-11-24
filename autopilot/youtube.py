"""YouTube Shorts upload + stats through the official YouTube Data API v3.

Quota: every project gets 10,000 units/day. One upload = 1,600 units, so the default
of 2 Shorts/day on each of 3 channels (9,600 units) fits in one Google Cloud project.
"""
from __future__ import annotations

from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .google_auth import YOUTUBE_SCOPES, credentials
from .util import log


def marker(md5: str) -> str:
    """Hidden tag stored on every upload so the bot can recognise its own videos."""
    return f"yts{md5[:12]}"


class YouTube:
    def __init__(self, token_secret: str):
        self.svc = build("youtube", "v3", credentials=credentials(token_secret, YOUTUBE_SCOPES),
                         cache_discovery=False)
        self._channel = None

    def channel(self) -> dict:
        if self._channel is None:
            res = self.svc.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()
            if not res.get("items"):
                raise RuntimeError("This Google login has no YouTube channel")
            self._channel = res["items"][0]
        return self._channel

    def handle(self) -> str:
        return (self.channel()["snippet"].get("customUrl") or "").lower()

    def check_handle(self, expected: str) -> None:
        got = self.handle()
        if expected and got and got != expected.lower():
            raise RuntimeError(f"Token belongs to {got}, but config expects {expected}. "
                               "Re-create this channel's token and pick the right channel.")

    def recent_uploads(self, n: int = 50) -> list[dict]:
        pl = self.channel()["contentDetails"]["relatedPlaylists"]["uploads"]
        items = self.svc.playlistItems().list(part="contentDetails", playlistId=pl, maxResults=min(n, 50)).execute()
        ids = [i["contentDetails"]["videoId"] for i in items.get("items", [])]
        if not ids:
            return []
        return self.svc.videos().list(part="snippet,statistics,status", id=",".join(ids)).execute().get("items", [])

    def find_existing(self, md5: str) -> str | None:
        """If a previous run uploaded this video but crashed before saving, find it."""
        m = marker(md5)
        for v in self.recent_uploads():
            if m in (v["snippet"].get("tags") or []):
                return v["id"]
        return None

    def upload(self, path: Path, md5: str, title: str, description: str, tags: list[str],
               category_id: str = "22") -> str:
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": (tags + [marker(md5)])[:30],
                "categoryId": str(category_id),
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": False,
            },
        }
        media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=16 * 1024 * 1024, resumable=True)
        req = self.svc.videos().insert(part="snippet,status", body=body, media_body=media, notifySubscribers=True)
        resp = None
        while resp is None:
            try:
                status, resp = req.next_chunk(num_retries=3)
                if status:
                    log.info("  YouTube upload %d%%", int(status.progress() * 100))
            except HttpError as e:
                if e.resp.status in (500, 502, 503, 504):
                    continue
                raise
        vid = resp["id"]
        # YouTube locks API uploads from un-audited projects to private. Detect it loudly.
        st = resp.get("status", {})
        if st.get("privacyStatus") != "public":
            log.error("YouTube set video %s to %s — your API project is not audited yet.", vid,
                      st.get("privacyStatus"))
        return vid

    def stats(self) -> dict:
        s = self.channel()["statistics"]
        return {"subscribers": int(s.get("subscriberCount", 0)), "views": int(s.get("viewCount", 0)),
                "videos": int(s.get("videoCount", 0)), "handle": self.handle(),
                "title": self.channel()["snippet"]["title"]}
