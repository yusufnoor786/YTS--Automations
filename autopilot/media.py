"""FFmpeg helpers: probe, perceptual hash, preview frames, and 9:16 normalisation."""
from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

from .util import log

W, H = 1080, 1920


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, **kw)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "ignore")[-800:] if isinstance(p.stderr, bytes) else str(p.stderr)[-800:]
        raise MediaError(f"{cmd[0]} failed: {err}")
    return p


def probe(path: Path) -> dict:
    p = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)])
    info = json.loads(p.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise MediaError("file has no video stream")
    a = any(s["codec_type"] == "audio" for s in info["streams"])
    dur = float(info["format"].get("duration") or v.get("duration") or 0)
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = abs(int(float(sd["rotation"])))
    rot = rot or abs(int(v.get("tags", {}).get("rotate", 0) or 0))
    if rot in (90, 270):
        w, h = h, w
    return {"width": w, "height": h, "duration": dur, "has_audio": a}


def _gray_frame(path: Path, t: float, w: int, h: int) -> bytes:
    p = _run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
              "-vf", f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"])
    if len(p.stdout) < w * h:
        raise MediaError("could not read frame")
    return p.stdout[: w * h]


def phash(path: Path, duration: float) -> list[int]:
    """dHash of 3 frames (20/50/80 %) → 3 × 64-bit ints. Survives re-encoding, renaming, resizing."""
    out = []
    for frac in (0.2, 0.5, 0.8):
        px = _gray_frame(path, max(0.0, duration * frac), 9, 8)
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
        out.append(bits)
    return out


def preview_frames(path: Path, duration: float, n: int = 3) -> list[str]:
    """Small JPEG frames as base64 so Gemini can 'see' the video for captions."""
    frames = []
    for i in range(n):
        t = duration * (i + 1) / (n + 1)
        p = _run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                  "-vf", "scale=360:-2", "-q:v", "5", "-f", "image2", "-c:v", "mjpeg", "-"])
        frames.append(base64.b64encode(p.stdout).decode())
    return frames


def normalize(src: Path, dst: Path, info: dict, max_seconds: float) -> Path:
    """Convert to 1080×1920 H.264/AAC MP4 that both Instagram Reels and YouTube Shorts accept.
    Non-vertical clips get a blurred background instead of black bars."""
    w, h = info["width"], info["height"]
    vertical = abs((w / h) - (W / H)) < 0.02
    if vertical:
        vf = f"scale={W}:{H}:flags=lanczos,setsar=1"
        fc = None
    else:
        fc = (f"[0:v]split=2[a][b];"
              f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:2[bg];"
              f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if not info["has_audio"]:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    if fc:
        cmd += ["-filter_complex", fc, "-map", "[v]"]
    else:
        cmd += ["-vf", vf, "-map", "0:v:0"]
    cmd += ["-map", "0:a:0" if info["has_audio"] else "1:a:0"]
    if info["duration"] > max_seconds:
        log.warning("Clip is %.0fs — trimming to %ds", info["duration"], max_seconds)
        cmd += ["-t", str(max_seconds)]
    if not info["has_audio"]:
        cmd += ["-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-fpsmax", "60", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", str(dst)]
    _run(cmd)
    return dst
