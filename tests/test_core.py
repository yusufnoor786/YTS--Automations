"""End-to-end tests with fake Drive / Instagram / YouTube and real FFmpeg.
Run:  python -m pytest -q tests   (or python tests/test_core.py)"""
from __future__ import annotations

import hashlib
import itertools
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot import captions, media  # noqa: E402
from autopilot import post as postmod  # noqa: E402
from autopilot.config import load  # noqa: E402
from autopilot.ledger import POSTED, PUBLISHING, Ledger  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="yts-test-"))
from autopilot import growth  # noqa: E402
growth.STATE = TMP / "growth.json"
growth.LOG_MD = TMP / "GROWTH_LOG.md"
ids = itertools.count(1)


def make_video(name: str, w: int, h: int, secs: float, pattern: str = "testsrc2", audio=True, crf=23) -> Path:
    out = TMP / "src" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{pattern}=size={w}x{h}:rate=30", "-t", str(secs)]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    subprocess.run(cmd + [str(out)], check=True)
    return out


# ── fakes ────────────────────────────────────────────────────
class FakeDrive:
    def __init__(self):
        self.folders: dict[str, list[dict]] = {}
        self.named: dict[tuple, str] = {}
        self.t = itertools.count(1)

    def folder(self, parent, name, create=True):
        k = (parent, name)
        if k not in self.named:
            self.named[k] = f"F{next(ids)}"
            self.folders[self.named[k]] = []
        return self.named[k]

    def niche_folders(self, root, niche):
        inbox = self.folder(root, niche)
        return {"inbox": inbox, **{s: self.folder(inbox, s) for s in ("_posted", "_duplicates", "_failed")}}

    def add(self, folder, path: Path, name=None):
        f = {"id": f"file{next(ids)}", "name": name or path.name, "_path": path,
             "md5Checksum": hashlib.md5(path.read_bytes()).hexdigest(),
             "createdTime": f"2026-01-01T00:00:{next(self.t):02d}Z", "size": path.stat().st_size, "description": None}
        self.folders[folder].append(f)
        return f

    def list_videos(self, folder):
        return [dict(f) for f in self.folders.get(folder, [])]

    def download(self, fid, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        for fs in self.folders.values():
            for f in fs:
                if f["id"] == fid:
                    shutil.copy(f["_path"], dest)
                    return dest
        raise KeyError(fid)

    def move(self, fid, to):
        for fs in self.folders.values():
            for f in list(fs):
                if f["id"] == fid:
                    fs.remove(f)
                    self.folders[to].append(f)
                    return

    def where(self, fid):
        for k, fs in self.folders.items():
            if any(f["id"] == fid for f in fs):
                return [n for (p, n), v in self.named.items() if v == k][0]


class FakeIG:
    def __init__(self):
        self.published, self.containers, self.fail_next = [], {}, None

    def check_username(self, u): ...
    def quota_left(self): return 50
    def container_status(self, cid): return self.containers.get(cid, "IN_PROGRESS")

    def create_container(self, caption):
        cid = f"C{next(ids)}"
        self.containers[cid] = "IN_PROGRESS"
        return cid

    def upload_file(self, cid, path):
        assert media.probe(path)["width"] == 1080
        self.containers[cid] = "FINISHED"

    def wait_ready(self, cid):
        if self.fail_next:
            self.containers[cid] = "ERROR"
            from autopilot.instagram import IGError
            raise IGError("Instagram rejected the video: bad")

    def publish(self, cid):
        assert self.containers[cid] == "FINISHED"
        self.containers[cid] = "PUBLISHED"
        self.published.append(cid)
        return {"id": f"M{cid}", "url": f"https://instagram.com/reel/{cid}"}


class FakeYT:
    def __init__(self):
        self.uploads = []

    def check_handle(self, h): ...
    def find_existing(self, md5):
        return next((v for v, m in self.uploads if m == md5), None)

    def upload(self, path, md5, title, desc, tags, cat):
        assert len(title) <= 100 and "#shorts" in title
        vid = f"Y{next(ids)}"
        self.uploads.append((vid, md5))
        return vid


class FakeFB:
    def __init__(self):
        self.reels, self.status = [], {}

    def check_name(self, n): ...
    def video_status(self, vid): return self.status.get(vid, {})
    def permalink(self, vid): return f"https://www.facebook.com/reel/{vid}"

    def start(self):
        vid = f"V{next(ids)}"
        self.status[vid] = {"video_status": "uploading"}
        return vid

    def upload_file(self, vid, path):
        assert media.probe(path)["height"] == 1920
        self.status[vid] = {"video_status": "upload_complete"}

    def finish(self, vid, desc):
        self.status[vid] = {"video_status": "ready", "publishing_phase": {"status": "complete"}}
        self.reels.append(vid)

    def wait_published(self, vid): ...


class TestPoster(postmod.Poster):
    fakes: dict = {}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.respect_schedule = False  # tests don't wait for real clock slots

    def client(self, niche, platform):
        return self.fakes.setdefault((niche.key, platform),
                                     {"instagram": FakeIG, "youtube": FakeYT, "facebook": FakeFB}[platform]())


def setup_env():
    for k in ["IG_TOKEN_AESTHETIC", "IG_ID_AESTHETIC", "IG_TOKEN_GTA", "IG_ID_GTA", "IG_TOKEN_HORROR", "IG_ID_HORROR",
              "YT_TOKEN_AESTHETIC", "YT_TOKEN_GTA", "YT_TOKEN_HORROR",
              "FB_TOKEN_AESTHETIC", "FB_PAGE_ID_AESTHETIC", "FB_TOKEN_GTA", "FB_PAGE_ID_GTA",
              "FB_TOKEN_HORROR", "FB_PAGE_ID_HORROR"]:
        os.environ[k] = "x"
    os.environ.pop("GEMINI_API_KEY", None)


def new_run(cfg, ledger, drive, **kw):
    p = TestPoster(cfg, ledger, drive, **kw)
    errs = p.run()
    return p, errs


# ── tests ────────────────────────────────────────────────────
def test_media_pipeline():
    h = make_video("land.mp4", 1280, 720, 4)
    info = media.probe(h)
    assert info["width"] == 1280 and info["has_audio"]
    out = media.normalize(h, TMP / "land_out.mp4", info, 180)
    o = media.probe(out)
    assert (o["width"], o["height"]) == (1080, 1920) and o["has_audio"]
    silent = make_video("silent.mp4", 720, 1280, 3.5, audio=False)
    o2 = media.probe(media.normalize(silent, TMP / "silent_out.mp4", media.probe(silent), 180))
    assert o2["has_audio"] and o2["width"] == 1080
    # trimming
    long = make_video("long.mp4", 720, 1280, 8)
    o3 = media.probe(media.normalize(long, TMP / "long_out.mp4", media.probe(long), 5))
    assert o3["duration"] <= 5.2
    assert len(media.preview_frames(h, info["duration"])) == 3


def test_phash_catches_reencode_but_not_different_clip():
    from autopilot.ledger import hamming
    a = make_video("a.mp4", 720, 1280, 4, "testsrc2")
    a2 = TMP / "a_reencoded.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(a), "-vf", "scale=540:960", "-c:v", "libx264",
                    "-crf", "35", "-c:a", "copy", str(a2)], check=True)
    b = make_video("b.mp4", 720, 1280, 4, "mandelbrot")
    ha, ha2, hb = (media.phash(x, media.probe(x)["duration"]) for x in (a, a2, b))
    assert hamming(ha, ha2) <= 10, hamming(ha, ha2)
    assert hamming(ha, hb) > 10, hamming(ha, hb)


def test_full_flow_no_duplicates():
    setup_env()
    cfg = load()
    cfg.raw["youtube_api_audited"] = True
    cfg.raw["drive_root_folder_id"] = "ROOT"
    ledger = Ledger(TMP / "ledger.json")
    drive = FakeDrive()
    TestPoster.fakes = {}
    gta = drive.niche_folders("ROOT", "gta")
    hor = drive.niche_folders("ROOT", "horror")
    aes = drive.niche_folders("ROOT", "aesthetic")
    v1 = drive.add(gta["inbox"], make_video("gta1.mp4", 1920, 1080, 4, "testsrc2"))
    v2 = drive.add(gta["inbox"], make_video("gta2.mp4", 1920, 1080, 4, "mandelbrot"))
    v3 = drive.add(gta["inbox"], make_video("gta3.mp4", 1920, 1080, 4, "rgbtestsrc"))
    h1 = drive.add(hor["inbox"], make_video("hor1.mp4", 720, 1280, 4, "smptebars"))
    # same bytes as gta1 put into horror → cross-niche exact duplicate
    hdup = drive.add(hor["inbox"], TMP / "src" / "gta1.mp4", name="renamed_copy.mp4")

    # run 1
    p, errs = new_run(cfg, ledger, drive)
    assert errs == 0, p.rows
    ig_gta, yt_gta = TestPoster.fakes[("gta", "instagram")], TestPoster.fakes[("gta", "youtube")]
    assert len(ig_gta.published) == 1 and len(yt_gta.uploads) == 1
    # both platforms got the SAME (oldest) video, and it moved to _posted
    assert ledger.is_posted(v1["md5Checksum"], "instagram") and ledger.is_posted(v1["md5Checksum"], "youtube")
    assert drive.where(v1["id"]) == "_posted"
    assert drive.where(h1["id"]) == "_posted"

    # run 2 → second video; run 3 → daily cap (2) reached, nothing posted
    new_run(cfg, ledger, drive)
    p3, _ = new_run(cfg, ledger, drive)
    assert len(ig_gta.published) == 2 and len(yt_gta.uploads) == 2
    assert any("daily limit" in r[3] for r in p3.rows)
    assert drive.where(v3["id"]) == "gta"

    # horror: second run met the cross-niche duplicate → moved to _duplicates, never posted
    assert drive.where(hdup["id"]) == "_duplicates"
    assert len(TestPoster.fakes[("horror", "instagram")].published) == 1

    # next day: same file re-uploaded to Drive with a new name → _duplicates, not posted
    for v in ledger.videos.values():
        for pl in v["platforms"].values():
            if pl.get("day"):
                pl["day"] = "2000-01-01"
    again = drive.add(gta["inbox"], TMP / "src" / "gta1.mp4", name="gta1 (1).mp4")
    reenc = TMP / "src" / "gta2_reenc.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(TMP / "src" / "gta2.mp4"), "-c:v", "libx264",
                    "-crf", "33", "-c:a", "copy", str(reenc)], check=True)
    near = drive.add(gta["inbox"], reenc)
    new_run(cfg, ledger, drive)
    assert drive.where(again["id"]) == "_duplicates"
    assert drive.where(v3["id"]) == "_posted"
    new_run(cfg, ledger, drive)  # next slot reaches the re-encoded copy
    assert drive.where(near["id"]) == "_duplicates", drive.where(near["id"])
    fb_gta = TestPoster.fakes[("gta", "facebook")]
    assert len(fb_gta.reels) == 3 and all(ledger.is_posted(v["md5Checksum"], "facebook") for v in (v1, v2, v3))
    # every video posted at most once per platform
    md5s = [m for _, m in yt_gta.uploads]
    assert len(md5s) == len(set(md5s)) == 3

    # nothing left → warns to refill, still no duplicate posts
    p5, _ = new_run(cfg, ledger, drive, force=True)
    assert any("no new videos" in r[3] for r in p5.rows)
    assert len(set(m for _, m in yt_gta.uploads)) == len(yt_gta.uploads) == 3
    assert len(fb_gta.reels) == 3


def test_crash_recovery_and_youtube_backlog():
    setup_env()
    cfg = load()
    cfg.raw["drive_root_folder_id"] = "ROOT"
    cfg.raw["youtube_api_audited"] = False  # YouTube off at first
    ledger = Ledger(TMP / "ledger2.json")
    drive = FakeDrive()
    TestPoster.fakes = {}
    aes = drive.niche_folders("ROOT", "aesthetic")
    a1 = drive.add(aes["inbox"], make_video("aes1.mp4", 720, 1280, 4, "testsrc"))
    a2 = drive.add(aes["inbox"], make_video("aes2.mp4", 720, 1280, 4, "cellauto"))
    new_run(cfg, ledger, drive)
    ig = TestPoster.fakes[("aesthetic", "instagram")]
    assert len(ig.published) == 1 and ("aesthetic", "youtube") not in TestPoster.fakes

    # simulate crash: container created+uploaded but publish never happened
    md5 = a2["md5Checksum"]
    ledger.ensure(md5, a2["id"], a2["name"], "aesthetic")
    cid = ig.create_container("x")
    ig.containers[cid] = "FINISHED"
    ledger.mark(md5, "instagram", PUBLISHING, container_id=cid)
    new_run(cfg, ledger, drive)
    assert ledger.is_posted(md5, "instagram") and ig.published.count(cid) == 1
    assert len(ig.published) == 2  # recovered, NOT re-uploaded as a new post

    # Facebook crash after finish: next run must mark it posted, not re-upload
    fb = TestPoster.fakes[("aesthetic", "facebook")]
    a3 = drive.add(aes["inbox"], make_video("aes3.mp4", 720, 1280, 4, "rgbtestsrc"))
    md5c = a3["md5Checksum"]
    ledger.ensure(md5c, a3["id"], a3["name"], "aesthetic")
    vid = fb.start(); fb.finish(vid, "x"); before = len(fb.reels)
    ledger.mark(md5c, "facebook", PUBLISHING, video_id=vid, finished=True)
    for v in ledger.videos.values():
        for pl in v["platforms"].values():
            pl["day"] = "2000-01-01"
    new_run(cfg, ledger, drive)
    assert ledger.is_posted(md5c, "facebook") and fb.reels.count(vid) == 1

    # audit approved → YouTube works through the backlog (videos already on IG)
    cfg.raw["youtube_api_audited"] = True
    for v in ledger.videos.values():
        for pl in v["platforms"].values():
            pl["day"] = "2000-01-01"
    new_run(cfg, ledger, drive)
    new_run(cfg, ledger, drive)
    yt = TestPoster.fakes[("aesthetic", "youtube")]
    assert len(yt.uploads) == len({m for _, m in yt.uploads}) >= 2
    assert len(ig.published) == 3 and len(set(ig.published)) == 3  # 3 videos, no repeats


def test_bad_video_goes_to_failed():
    setup_env()
    cfg = load()
    cfg.raw["drive_root_folder_id"] = "ROOT"
    cfg.raw["youtube_api_audited"] = False
    ledger = Ledger(TMP / "ledger3.json")
    drive = FakeDrive()
    TestPoster.fakes = {}
    gta = drive.niche_folders("ROOT", "gta")
    short = drive.add(gta["inbox"], make_video("tiny.mp4", 720, 1280, 1.5))
    bad = TMP / "src" / "broken.mp4"
    bad.write_bytes(b"not a video at all" * 100)
    broken = drive.add(gta["inbox"], bad)
    for _ in range(3):
        new_run(cfg, ledger, drive, force=True)
    new_run(cfg, ledger, drive, force=True)
    for _ in range(3):
        new_run(cfg, ledger, drive, force=True)
    assert drive.where(short["id"]) == "_failed"
    assert drive.where(broken["id"]) == "_failed"


def test_captions_fallback_and_format():
    cfg = load()
    n = cfg.niches["horror"]
    c = captions.fallback(n, "abcdef1234567890")
    assert c["ig_caption"].count("#") <= 12 and c["yt_title"].endswith("#shorts") and len(c["yt_title"]) <= 100
    c2 = captions._finish(n, {"ig_caption": "Door khula tha... 👻 #old #tags", "hashtags": ["#Bhoot", "bhoot", "x y"],
                              "yt_title": "A <very> long title " * 10, "yt_description": "d", "yt_tags": ["a"]})
    assert "<" not in c2["yt_title"] and len(c2["yt_title"]) <= 100
    assert c2["ig_caption"].startswith("Door khula tha") and "#old" not in c2["ig_caption"]
    assert captions._hint_from_name("VID_20240101_123456.mp4") == ""
    assert captions._hint_from_name("trevor_jumps_bridge.mp4") == "trevor jumps bridge"


def test_dashboard_builds():
    from autopilot import dashboard
    cfg = load()
    ledger = Ledger(TMP / "ledger.json")
    stats = {"history": {"2026-11-20": {"gta/instagram": {"followers": 100}},
                         "2026-11-27": {"gta/instagram": {"followers": 180}}},
             "latest": {"gta/instagram": {"followers": 180, "ok": True}}, "recent": {}, "updated": "2026-11-27T10:00"}
    dashboard.OUT = TMP / "index.html"
    dashboard.build(cfg, ledger, stats)
    html = dashboard.OUT.read_text()
    assert "+80" in html and "@wanted_level_yts" in html



def test_instagram_and_gemini_http_flow(monkeypatch=None):
    """Exercise the real Instagram + Gemini code paths against a fake HTTP server."""
    import json as _json

    import requests

    from autopilot import instagram as igm

    calls = []

    class R:
        def __init__(self, code, data):
            self.status_code, self._d, self.ok = code, data, code < 400
            self.text = _json.dumps(data)

        def json(self):
            return self._d

        def raise_for_status(self):
            if not self.ok:
                raise requests.HTTPError(str(self.status_code))

    state = {"polls": 0}

    def fake_request(method, url, params=None, data=None, timeout=None):
        calls.append((method, url, params or data))
        if url.endswith("/media") and method == "POST":
            assert data["media_type"] == "REELS" and data["upload_type"] == "resumable"
            return R(200, {"id": "C1", "uri": "x"})
        if url.endswith("/C1"):
            state["polls"] += 1
            return R(200, {"status_code": "FINISHED" if state["polls"] > 1 else "IN_PROGRESS"})
        if url.endswith("/media_publish"):
            assert data["creation_id"] == "C1"
            return R(200, {"id": "M1"})
        if url.endswith("/M1"):
            return R(200, {"permalink": "https://www.instagram.com/reel/abc/"})
        if url.endswith("content_publishing_limit"):
            return R(200, {"data": [{"quota_usage": 3, "config": {"quota_total": 100}}]})
        if "/IG1" in url:
            return R(200, {"username": "wanted_level_yts", "followers_count": 5})
        return R(400, {"error": {"message": "bad", "code": 100}})

    def fake_post(url, data=None, headers=None, timeout=None, json=None):
        if "rupload" in url:
            assert headers["file_size"] and headers["offset"] == "0" and headers["Authorization"].startswith("OAuth")
            return R(200, {"success": True})
        if "generativelanguage" in url:
            body = {"ig_caption": "Vice City at night hits different 🌴", "hashtags": ["gta6", "vicecity"],
                    "yt_title": "This GTA 6 chase is insane", "yt_description": "Wild chase.", "yt_tags": ["gta 6"]}
            return R(200, {"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True},
                                                                 {"text": _json.dumps(body)}]}}]})
        raise AssertionError(url)

    orig_req, orig_post = requests.request, requests.post
    requests.request, requests.post = fake_request, fake_post
    igm.time.sleep = lambda s: None
    try:
        c = igm.Instagram("IG1", "tok")
        c.check_username("wanted_level_yts")
        assert c.quota_left() == 97
        cid = c.create_container("hello")
        vid = make_video("ig.mp4", 720, 1280, 3)
        c.upload_file(cid, vid)
        c.wait_ready(cid)
        assert c.publish(cid)["url"].startswith("https://www.instagram.com/reel/")
        try:
            igm.Instagram("IG1", "tok").check_username("someone_else")
            raise AssertionError("should refuse wrong account")
        except igm.IGError:
            pass
        os.environ["GEMINI_API_KEY"] = "k"
        cap = captions.generate(load().niches["gta"], "a" * 32, "clip.mp4", None, ["Zm9v"])
        assert cap["source"] == "gemini" and "#gta6" in cap["ig_caption"] and cap["yt_title"].endswith("#shorts")
    finally:
        requests.request, requests.post = orig_req, orig_post
        os.environ.pop("GEMINI_API_KEY", None)



def test_growth_lab_learns_and_logs():
    from autopilot.ledger import Ledger as L
    cfg = load()
    led = L(TMP / "ledger_growth.json")
    st = growth.load_state()
    n = cfg.niches["aesthetic"]
    # every option gets tried before exploiting
    seen = {growth.choose_caption_variant(cfg, st, n, f"{i:032x}")["style"] for i in range(40)}
    assert seen == set(growth.options(cfg, "style"))
    hours = growth.plan_for_today(cfg, st, n)
    assert len(hours) == n.posts_per_day and len(set(hours)) == len(hours)
    # fake 30 scored posts: 'curiosity' gets 10x views, 'short' gets few
    from datetime import timedelta
    old = (growth.now() - timedelta(hours=60)).isoformat(timespec="seconds")
    for i in range(50):
        style = ["curiosity", "short", "question", "relatable", "story"][i % 5]
        views = {"curiosity": 5000, "short": 50, "question": 500, "relatable": 800, "story": 300}[style]
        md5 = f"{i:032x}"
        led.videos[md5] = {"file_id": str(i), "name": f"v{i}", "niche": "aesthetic", "first_seen": old, "phash": None,
                           "caption": {}, "duplicate_of": None,
                           "variant": {"style": style, "tags": [3, 6, 10][i % 3], "hour": [8, 19, 21][i % 3]},
                           "platforms": {"instagram": {"status": "posted", "id": f"m{i}", "at": old,
                                                       "day": old[:10], "views_48h": views}}}
    changes = growth.rescore(cfg, led, st)
    assert st["leaders"]["aesthetic"]["style"] == "curiosity" and any("curiosity" in c for c in changes)
    # exploitation now mostly picks the winner
    picks = [growth.choose_caption_variant(cfg, st, n, f"{1000 + i:032x}")["style"] for i in range(200)]
    assert picks.count("curiosity") > 120, picks.count("curiosity")
    rep = growth.weekly_report(cfg, st, led, {"latest": {}}, force=True)
    assert "What's working" in rep and growth.LOG_MD.exists()
    rows = growth.dashboard_rows(cfg, st)
    assert any(r["leader"] and r["option"] == "curiosity" for r in rows)
    growth.save_state(st)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
    print("all tests passed")

