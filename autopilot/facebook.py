"""Facebook Page Reels publishing + Page stats through the official Graph API (Reels Publishing API).

Flow: POST /{page}/video_reels upload_phase=start → upload bytes to rupload.facebook.com →
POST /{page}/video_reels upload_phase=finish video_state=PUBLISHED → poll the video status.
Uses the same non-expiring Page access token style as Instagram (needs pages_manage_posts).
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

from .instagram import GRAPH, VERSION
from .util import has_secret, log, retry, secret

RUPLOAD = f"https://rupload.facebook.com/video-upload/{VERSION}"


class FBError(RuntimeError):
    pass


class Facebook:
    def __init__(self, page_id: str, token: str):
        self.page = page_id
        self.token = token

    @retry(times=3, wait=5)
    def _req(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.token
        r = requests.request(method, f"{GRAPH}/{path}", params=params if method == "GET" else None,
                             data=params if method != "GET" else None, timeout=60)
        if r.status_code >= 500:
            raise requests.RequestException(f"Meta {r.status_code}")
        data = r.json()
        if isinstance(data, dict) and "error" in data:
            e = data["error"]
            raise FBError(f"{e.get('message')} (code {e.get('code')}/{e.get('error_subcode')})")
        return data

    # ── info ─────────────────────────────────────────────────
    def profile(self) -> dict:
        return self._req("GET", self.page, fields="name,followers_count,fan_count,link")

    def check_name(self, expected) -> None:
        """expected may be a name or a list of accepted names (e.g. old + new name while a rename is pending)."""
        if not expected:
            return
        names = [expected] if isinstance(expected, str) else list(expected)
        got = self.profile().get("name", "")
        if got.strip().lower() not in {n.strip().lower() for n in names}:
            raise FBError(f"Token/ID belongs to Page '{got}', config expects {' or '.join(names)}")

    def video_status(self, video_id: str) -> dict:
        return self._req("GET", video_id, fields="status,permalink_url").get("status", {}) or {}

    def is_published(self, video_id: str) -> bool:
        st = self.video_status(video_id)
        pub = (st.get("publishing_phase") or {}).get("status")
        return st.get("video_status") == "ready" and pub == "complete"

    def permalink(self, video_id: str) -> str | None:
        try:
            link = self._req("GET", video_id, fields="permalink_url").get("permalink_url")
            if link and link.startswith("/"):
                link = "https://www.facebook.com" + link
            return link
        except Exception:  # noqa: BLE001
            return None

    def recent_reels(self, n: int = 12) -> list[dict]:
        try:
            data = self._req("GET", f"{self.page}/video_reels", limit=n,
                             fields="id,description,permalink_url,created_time").get("data", [])
        except FBError:
            data = self._req("GET", f"{self.page}/videos", limit=n,
                             fields="id,description,permalink_url,created_time").get("data", [])
        for v in data:
            v["views"] = self.reel_plays(v["id"])
            if (v.get("permalink_url") or "").startswith("/"):
                v["permalink_url"] = "https://www.facebook.com" + v["permalink_url"]
        return data

    def reel_plays(self, video_id: str) -> int | None:
        for metric in ("blue_reels_play_count", "fb_reels_total_plays", "total_video_views"):
            try:
                d = self._req("GET", f"{video_id}/video_insights", metric=metric)
                vals = d.get("data") or []
                if vals:
                    return int(vals[0]["values"][0]["value"])
            except Exception:  # noqa: BLE001
                continue
        return None

    # ── publish ──────────────────────────────────────────────
    def start(self) -> str:
        d = self._req("POST", f"{self.page}/video_reels", upload_phase="start")
        return d["video_id"]

    def upload_file(self, video_id: str, path: Path) -> None:
        size = path.stat().st_size
        r = None
        for attempt in range(3):
            with open(path, "rb") as fh:
                r = requests.post(f"{RUPLOAD}/{video_id}", data=fh, timeout=900, headers={
                    "Authorization": f"OAuth {self.token}", "offset": "0", "file_size": str(size)})
            try:
                ok = r.ok and r.json().get("success", False)
            except ValueError:
                ok = False
            if ok:
                return
            log.warning("FB upload attempt %d failed: %s %s", attempt + 1, r.status_code, r.text[:300])
            time.sleep(10 * (attempt + 1))
        raise FBError(f"video upload to Facebook failed: {r.status_code if r else '?'} {r.text[:300] if r else ''}")

    def finish(self, video_id: str, description: str) -> None:
        d = self._req("POST", f"{self.page}/video_reels", upload_phase="finish", video_id=video_id,
                      video_state="PUBLISHED", description=description[:2200])
        if not d.get("success", False):
            raise FBError(f"Facebook did not accept the reel: {d}")

    def wait_published(self, video_id: str, timeout: int = 900) -> None:
        start = time.time()
        while time.time() - start < timeout:
            st = self.video_status(video_id)
            if st.get("video_status") == "error" or (st.get("processing_phase") or {}).get("status") == "error":
                raise FBError(f"Facebook rejected the video: {st}")
            if (st.get("publishing_phase") or {}).get("status") == "complete":
                return
            time.sleep(15)
        log.warning("Facebook still processing reel %s — it will go live by itself", video_id)


def has_credentials(niche) -> bool:
    fb, ig = niche.facebook or {}, niche.instagram or {}
    return has_secret(fb.get("token_secret", "")) or has_secret(ig.get("token_secret", ""))


def client_for(niche) -> "Facebook":
    """Use FB_TOKEN_/FB_PAGE_ID_ secrets if set; otherwise reuse the niche's Instagram Page token
    (it is the same Facebook Page token) and look the Page ID up from it."""
    fb, ig = niche.facebook or {}, niche.instagram or {}
    token = secret(fb["token_secret"]) if has_secret(fb.get("token_secret", "")) else secret(ig["token_secret"])
    if has_secret(fb.get("page_id_secret", "")):
        page_id = secret(fb["page_id_secret"])
    else:
        r = requests.get(f"{GRAPH}/me", params={"fields": "id,name", "access_token": token}, timeout=30).json()
        if "id" not in r:
            raise FBError(f"could not find the Facebook Page for this token: {r.get('error', r)}")
        page_id = r["id"]
    return Facebook(page_id, token)
