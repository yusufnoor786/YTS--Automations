"""Main posting job.  python -m autopilot.post [--niche gta] [--dry-run] [--force]

Every scheduled run, for each niche and each active platform:
  • skip if today's posts_per_day is already reached
  • pick the OLDEST video in Drive that was never posted on that platform
  • reject exact & near duplicates (moved to _duplicates)
  • normalise to 9:16, write caption with Gemini, publish, record in the ledger
  • move the Drive file to _posted
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import captions, growth, media
from .config import Config, Niche, load
from .drive import Drive
from .facebook import Facebook, FBError
from .instagram import IGError, Instagram
from .ledger import FAILED, POSTED, PUBLISHING, Ledger
from .util import MissingSecret, log, secret, summary
from .youtube import YouTube

WORK = Path("/tmp/yts-work")
MAX_SKIPS = 8  # max duplicates/bad files inspected per platform per run


class VideoProblem(RuntimeError):
    """The video itself is the problem (bad file, rejected by platform) — counts as an attempt."""


class Poster:
    def __init__(self, cfg: Config, ledger: Ledger, drive: Drive, dry_run: bool = False, force: bool = False):
        self.cfg, self.ledger, self.drive = cfg, ledger, drive
        self.dry, self.force = dry_run, force
        self.rows: list[tuple] = []
        self.errors = 0
        self.gs = growth.load_state()
        self.respect_schedule = not force

    # ── clients ──────────────────────────────────────────────
    def client(self, niche: Niche, platform: str):
        pc = niche.platform(platform)
        if platform == "instagram":
            c = Instagram(secret(pc["user_id_secret"]), secret(pc["token_secret"]))
            c.check_username(pc.get("username"))
        elif platform == "facebook":
            c = Facebook(secret(pc["page_id_secret"]), secret(pc["token_secret"]))
            c.check_name(pc.get("page_name"))
        else:
            c = YouTube(pc["token_secret"])
            c.check_handle(pc.get("handle"))
        return c

    # ── crash recovery ───────────────────────────────────────
    def recover(self, niche: Niche, platform: str, client) -> None:
        for md5, rec in self.ledger.videos.items():
            if rec["niche"] != niche.key:
                continue
            p = rec["platforms"].get(platform)
            if not p or p.get("status") != PUBLISHING:
                continue
            log.info("Recovering unfinished %s post of %s", platform, rec["name"])
            if platform == "instagram" and p.get("container_id"):
                st = client.container_status(p["container_id"])
                if st == "PUBLISHED":
                    self.ledger.mark(md5, platform, POSTED, recovered=True)
                    continue
                if st == "FINISHED" and not self.dry:
                    res = client.publish(p["container_id"])
                    self.ledger.mark(md5, platform, POSTED, id=res["id"], url=res["url"], recovered=True)
                    continue
            if platform == "facebook" and p.get("video_id"):
                try:
                    st = client.video_status(p["video_id"])
                except FBError:
                    st = {}
                if (st.get("publishing_phase") or {}).get("status") in ("complete", "in_progress") or \
                        st.get("video_status") in ("ready", "processing") and p.get("finished"):
                    self.ledger.mark(md5, platform, POSTED, id=p["video_id"],
                                     url=client.permalink(p["video_id"]), recovered=True)
                    continue
            if platform == "youtube":
                vid = client.find_existing(md5)
                if vid:
                    self.ledger.mark(md5, platform, POSTED, id=vid, url=f"https://youtube.com/shorts/{vid}",
                                     recovered=True)
                    continue
            self.ledger.mark(md5, platform, None)  # never went live → eligible again

    # ── candidate selection ──────────────────────────────────
    def candidates(self, folders: dict) -> list[dict]:
        files = []
        for where in ("inbox", "_posted"):
            for f in self.drive.list_videos(folders[where]):
                f["_where"] = where
                files.append(f)
        return sorted(files, key=lambda f: f.get("createdTime", ""))

    def stock(self, niche: Niche, folders: dict) -> int:
        n = 0
        active = self.cfg.active_platforms(niche) or ["instagram"]
        for f in self.drive.list_videos(folders["inbox"]):
            md5 = f.get("md5Checksum")
            if md5 and not all(self.ledger.is_posted(md5, p) for p in active):
                n += 1
        self.ledger.data.setdefault("stock", {})[niche.key] = n
        return n

    def sweep_exact_duplicates(self, niche: Niche, folders: dict) -> None:
        """Byte-identical copies of videos the bot already used go straight to _duplicates."""
        for f in self.drive.list_videos(folders["inbox"]):
            rec = self.ledger.get(f.get("md5Checksum") or "")
            if not rec or rec["file_id"] == f["id"]:
                continue
            used = rec["niche"] != niche.key or rec.get("duplicate_of") or any(
                p.get("status") in ("posted", "publishing") for p in rec["platforms"].values())
            if used:
                log.info("Exact copy of already-used video: %s → _duplicates", f["name"])
                self._move(f, folders["_duplicates"])

    def _move(self, f: dict, folder_id: str) -> None:
        if not self.dry:
            self.drive.move(f["id"], folder_id)

    # ── main per-platform step ───────────────────────────────
    def post_one(self, niche: Niche, platform: str, client, folders: dict) -> None:
        max_att = int(self.cfg.get("max_attempts", 3))
        skips = 0
        for f in self.candidates(folders):
            md5 = f.get("md5Checksum")
            if not md5:
                continue
            rec = self.ledger.get(md5)
            if rec:
                if rec.get("duplicate_of"):
                    continue
                if self.ledger.is_posted(md5, platform):
                    if rec["file_id"] != f["id"] and f["_where"] == "inbox":
                        log.info("Exact duplicate of an already-posted video: %s → _duplicates", f["name"])
                        self._move(f, folders["_duplicates"])
                    continue
                p = rec["platforms"].get(platform, {})
                if p.get("status") == PUBLISHING:
                    continue
                if p.get("status") == FAILED and int(p.get("attempts", 0)) >= max_att:
                    continue
                if rec["niche"] != niche.key:
                    log.info("%s is already used by niche '%s' → _duplicates", f["name"], rec["niche"])
                    self._move(f, folders["_duplicates"])
                    continue
                rec["file_id"] = f["id"]
            rec = self.ledger.ensure(md5, f["id"], f["name"], niche.key)

            try:
                done = self._process(niche, platform, client, folders, f, md5, rec)
            except Exception as e:  # noqa: BLE001
                p = self.ledger.plat(md5, platform)
                if p.get("status") == PUBLISHING and not isinstance(e, VideoProblem):
                    raise  # may have gone live — next run's recovery checks before retrying
                if not isinstance(e, VideoProblem) and isinstance(e, (MissingSecret, IGError, FBError)):
                    raise  # account problem, not the video's fault
                self.errors += 1
                att = self.ledger.fail(md5, platform, str(e))
                log.error("✗ %s on %s: %s (attempt %d/%d)", f["name"], platform, e, att, max_att)
                self.rows.append((niche.key, platform, f["name"], f"failed ({att}/{max_att}): {str(e)[:80]}"))
                if att >= max_att and f["_where"] == "inbox" and not any(
                        self.ledger.is_posted(md5, q) for q in ("instagram", "youtube", "facebook")):
                    self._move(f, folders["_failed"])
                return
            if done == "skip":
                skips += 1
                if skips >= MAX_SKIPS:
                    break
                continue
            return
        log.info("No new video left for %s/%s — refill the Drive folder!", niche.key, platform)
        self.rows.append((niche.key, platform, "—", "⚠️ no new videos in Drive"))

    def _process(self, niche, platform, client, folders, f, md5, rec) -> str:
        src = WORK / md5 / "source"
        out = WORK / md5 / "post.mp4"
        if not src.exists():
            log.info("Downloading %s (%s MB)", f["name"], round(int(f.get("size", 0)) / 1e6, 1))
            self.drive.download(f["id"], src)
        try:
            info = media.probe(src)
        except media.MediaError as e:
            raise VideoProblem(f"not a readable video: {e}") from e
        if info["duration"] < float(self.cfg.get("min_video_seconds", 3)):
            raise VideoProblem(f"too short ({info['duration']:.1f}s)")

        if not rec.get("phash"):
            rec["phash"] = media.phash(src, info["duration"])
            dup = self.ledger.find_near_duplicate(md5, rec["phash"], int(self.cfg.get("near_duplicate_threshold", 10)))
            if dup:
                rec["duplicate_of"] = dup
                self.ledger.save()
                log.info("%s looks the same as %s → _duplicates", f["name"], self.ledger.get(dup)["name"])
                if f["_where"] == "inbox":
                    self._move(f, folders["_duplicates"])
                return "skip"
            self.ledger.save()

        if not rec.get("caption"):
            variant = growth.choose_caption_variant(self.cfg, self.gs, niche, md5)
            frames = media.preview_frames(src, info["duration"])
            rec["caption"] = captions.generate(niche, md5, f["name"], f.get("description"), frames,
                                               model=self.cfg.get("gemini_model", "gemini-flash-latest"),
                                               variant=variant)
            rec["variant"] = {"style": variant["style"], "tags": variant["tags"]}
            self.ledger.save()
        rec.setdefault("variant", {})
        if rec["variant"].get("hour") is None and not self.dry:
            rec["variant"]["hour"] = growth.now().hour
        cap = rec["caption"]

        if not out.exists():
            try:
                media.normalize(src, out, info, float(self.cfg.get("max_video_seconds", 180)))
            except media.MediaError as e:
                raise VideoProblem(f"conversion failed: {e}") from e

        if self.dry:
            log.info("[dry-run] would post %s to %s/%s\n  caption: %s", f["name"], niche.key, platform,
                     cap["yt_title"] if platform == "youtube" else cap["ig_caption"])
            self.rows.append((niche.key, platform, f["name"], "dry-run OK"))
            return "done"

        if platform == "instagram":
            if client.quota_left() <= 0:
                self.rows.append((niche.key, platform, f["name"], "IG daily API limit reached"))
                return "done"
            cid = client.create_container(cap["ig_caption"])
            self.ledger.mark(md5, platform, PUBLISHING, container_id=cid)
            client.upload_file(cid, out)
            try:
                client.wait_ready(cid)
            except IGError as e:
                if "rejected" in str(e):
                    raise VideoProblem(str(e)) from e
                raise
            res = client.publish(cid)
        elif platform == "facebook":
            vid = client.start()
            self.ledger.mark(md5, platform, PUBLISHING, video_id=vid)
            client.upload_file(vid, out)
            client.finish(vid, cap["ig_caption"])
            self.ledger.mark(md5, platform, PUBLISHING, video_id=vid, finished=True)
            try:
                client.wait_published(vid)
            except FBError as e:
                if "rejected" in str(e):
                    raise VideoProblem(str(e)) from e
                raise
            res = {"id": vid, "url": client.permalink(vid)}
        else:
            self.ledger.mark(md5, platform, PUBLISHING)
            vid = client.upload(out, md5, cap["yt_title"], cap["yt_description"], cap["yt_tags"],
                                niche.youtube.get("category_id", "22"))
            res = {"id": vid, "url": f"https://youtube.com/shorts/{vid}"}

        self.ledger.mark(md5, platform, POSTED, id=res["id"], url=res.get("url"))
        log.info("✓ Posted %s to %s/%s → %s", f["name"], niche.key, platform, res.get("url"))
        self.rows.append((niche.key, platform, f["name"], f"✅ {res.get('url') or res['id']}"))
        if f["_where"] == "inbox":
            self._move(f, folders["_posted"])
            f["_where"] = "_posted"
        return "done"

    # ── run everything ───────────────────────────────────────
    def run(self, only: str | None = None) -> int:
        root = self.cfg.drive_root_folder_id
        for niche in self.cfg.niches.values():
            if only and niche.key != only:
                continue
            if not niche.enabled:
                continue
            active = self.cfg.active_platforms(niche)
            if not active:
                self.rows.append((niche.key, "—", "—", "no platform connected yet"))
            plan = growth.plan_for_today(self.cfg, self.gs, niche)
            allowed = niche.posts_per_day
            if self.respect_schedule:
                allowed = min(niche.posts_per_day, growth.due_count(self.cfg, self.gs, niche))
            if active and not self.force and all(self.ledger.posted_today(niche.key, p) >= allowed for p in active):
                nxt = [h for h in plan if h > growth.now().hour]
                if all(self.ledger.posted_today(niche.key, p) >= niche.posts_per_day for p in active):
                    self.rows.append((niche.key, "all", "—", f"daily limit reached ({niche.posts_per_day})"))
                    continue
                self.rows.append((niche.key, "all", "—", f"nothing due — next slot "
                                  f"{growth._label('hour', nxt[0]) if nxt else 'tomorrow'} (today: "
                                  f"{', '.join(growth._label('hour', h) for h in plan)})"))
                continue
            folders = self.drive.niche_folders(root, niche.key)
            self.sweep_exact_duplicates(niche, folders)
            for platform in active:
                done_today = self.ledger.posted_today(niche.key, platform)
                if not self.force and done_today >= allowed:
                    nxt = [h for h in plan if h > growth.now().hour]
                    why = (f"daily limit reached ({done_today})" if done_today >= niche.posts_per_day
                           else f"next slot {growth._label('hour', nxt[0]) if nxt else 'tomorrow'}")
                    self.rows.append((niche.key, platform, "—", why))
                    continue
                try:
                    client = self.client(niche, platform)
                    self.recover(niche, platform, client)
                    if not self.force and self.ledger.posted_today(niche.key, platform) >= allowed:
                        continue
                    self.post_one(niche, platform, client, folders)
                except Exception as e:  # noqa: BLE001
                    self.errors += 1
                    log.exception("%s/%s failed", niche.key, platform)
                    self.rows.append((niche.key, platform, "—", f"❌ {type(e).__name__}: {str(e)[:150]}"))
            left = self.stock(niche, folders)
            if left <= int(self.cfg.get("low_stock_warning", 5)):
                self.rows.append((niche.key, "drive", "—", f"⚠️ only {left} new videos left — refill Drive"))
        if not self.dry:
            growth.save_state(self.gs)
        self.ledger.log_run({"type": "post", "errors": self.errors, "rows": [list(r) for r in self.rows]})
        summary("### YTS Autopilot run\n\n| niche | platform | video | result |\n|---|---|---|---|\n" +
                "\n".join(f"| {a} | {b} | {c} | {d} |" for a, b, c, d in self.rows))
        return self.errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore posts_per_day for this run")
    a = ap.parse_args(argv)
    cfg = load()
    ledger = Ledger() if not a.dry_run else Ledger(Path("/tmp/yts-dry-ledger.json"))
    if a.dry_run:
        ledger.data = Ledger().data
    errors = Poster(cfg, ledger, Drive(), dry_run=a.dry_run, force=a.force).run(a.niche)
    shutil.rmtree(WORK, ignore_errors=True)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
