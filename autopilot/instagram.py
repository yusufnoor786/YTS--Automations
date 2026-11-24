"""Instagram Reels publishing through the official Instagram Graph API (Facebook Login).

The video is uploaded straight from the runner to Meta (resumable upload) — no public
hosting needed. Page access tokens made by tools/get_meta_tokens.py do not expire.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from .util import log, retry

VERSION = os.environ.get("IG_GRAPH_VERSION", "v25.0")
GRAPH = f"https://graph.facebook.com/{VERSION}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{VERSION}"


class IGError(RuntimeError):
    pass


class Instagram:
    def __init__(self, ig_user_id: str, token: str):
        self.ig = ig_user_id
        self.token = token

    # ── low level ────────────────────────────────────────────
    @retry(times=3, wait=5)
    def _req(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.token
        r = requests.request(method, f"{GRAPH}/{path}", params=params if method == "GET" else None,
                             data=params if method != "GET" else None, timeout=60)
        if r.status_code >= 500:
            raise requests.RequestException(f"Meta {r.status_code}")
        data = r.json()
        if "error" in data:
            e = data["error"]
            raise IGError(f"{e.get('message')} (code {e.get('code')}/{e.get('error_subcode')})")
        return data

    # ── info ─────────────────────────────────────────────────
    def profile(self) -> dict:
        return self._req("GET", self.ig, fields="username,followers_count,follows_count,media_count,name")

    def check_username(self, expected: str) -> None:
        got = self.profile().get("username", "")
        if expected and got.lower() != expected.lower():
            raise IGError(f"Token/ID belongs to @{got}, config expects @{expected}")

    def quota_left(self) -> int:
        try:
            d = self._req("GET", f"{self.ig}/content_publishing_limit", fields="quota_usage,config")
            item = d["data"][0]
            return int(item["config"]["quota_total"]) - int(item["quota_usage"])
        except Exception as e:  # noqa: BLE001
            log.warning("Could not read IG publishing limit: %s", e)
            return 999

    def container_status(self, container_id: str) -> str:
        return self._req("GET", container_id, fields="status_code").get("status_code", "")

    def recent_media(self, n: int = 25) -> list[dict]:
        return self._req("GET", f"{self.ig}/media", limit=n,
                         fields="id,caption,permalink,timestamp,like_count,comments_count,media_product_type"
                         ).get("data", [])

    def media_views(self, media_id: str) -> int | None:
        try:
            d = self._req("GET", f"{media_id}/insights", metric="views")
            return int(d["data"][0]["values"][0]["value"])
        except Exception:  # noqa: BLE001
            return None

    # ── publish ──────────────────────────────────────────────
    def create_container(self, caption: str) -> str:
        d = self._req("POST", f"{self.ig}/media", media_type="REELS", upload_type="resumable",
                      caption=caption, share_to_feed="true")
        return d["id"]

    def upload_file(self, container_id: str, path: Path) -> None:
        size = path.stat().st_size
        for attempt in range(3):
            with open(path, "rb") as fh:
                r = requests.post(f"{RUPLOAD}/{container_id}", data=fh, timeout=900, headers={
                    "Authorization": f"OAuth {self.token}", "offset": "0", "file_size": str(size)})
            try:
                success = r.ok and r.json().get("success", True)
            except ValueError:
                success = False
            if success:
                return
            log.warning("IG upload attempt %d failed: %s %s", attempt + 1, r.status_code, r.text[:300])
            time.sleep(10 * (attempt + 1))
        raise IGError(f"video upload to Meta failed: {r.status_code} {r.text[:300]}")

    def wait_ready(self, container_id: str, timeout: int = 900) -> None:
        start = time.time()
        while time.time() - start < timeout:
            st = self.container_status(container_id)
            if st == "FINISHED":
                return
            if st in ("ERROR", "EXPIRED"):
                info = self._req("GET", container_id, fields="status")
                raise IGError(f"Instagram rejected the video: {info.get('status')}")
            time.sleep(15)
        raise IGError("Instagram took too long to process the video")

    def publish(self, container_id: str) -> dict:
        media_id = self._req("POST", f"{self.ig}/media_publish", creation_id=container_id)["id"]
        try:
            link = self._req("GET", media_id, fields="permalink").get("permalink")
        except Exception:  # noqa: BLE001
            link = None
        return {"id": media_id, "url": link}
