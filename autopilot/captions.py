"""Captions, titles and hashtags written by Gemini (free tier), with a safe fallback."""
from __future__ import annotations

import json
import re

import requests

from .util import has_secret, log, retry, secret

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

FALLBACK = {
    "aesthetic": ["soft life, soft heart ✨", "romanticize your little moments 🤍", "glowing in my own lane 🌸",
                  "main character energy, quietly ✨", "this is your sign to slow down 🤍"],
    "gta": ["Vice City is calling 🔥 would you do this?", "GTA 6 hits different 🎮", "5 stars in 5 seconds 🚔",
            "Rate this move 1-10 👇", "Leonida never sleeps 🌴"],
    "horror": ["Don't watch this alone 👻", "Would you survive this? 🕯️", "Darr ke aage... 😱",
               "Turn the lights on before you scroll 👁️", "Something is wrong here... 👻"],
}
GENERIC = ["Wait for it 👀", "Watch till the end 🔥", "Would you try this? 👇"]


def _hint_from_name(name: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name)
    text = re.sub(r"[_\-.]+", " ", stem).strip()
    letters = sum(c.isalpha() for c in text)
    # ignore camera / random names like VID_20240101_123456 or a3f9c0...
    if letters < 4 or re.fullmatch(r"(?i)(vid|img|video|snaptik|ssstik|download|copy)?[\s\da-f]*", text):
        return ""
    return text[:150]


def _clean_tags(tags, limit: int) -> list[str]:
    out, seen = [], set()
    for t in tags:
        t = re.sub(r"[^\w]", "", str(t).lstrip("#")).lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:limit]


def _finish(niche, data: dict) -> dict:
    base_tags = niche.hashtags
    ig_tags = _clean_tags(list(data.get("hashtags") or []) + base_tags, 12)
    ig_caption = (data.get("ig_caption") or "").strip()
    ig_caption = re.sub(r"(\s#\w+)+\s*$", "", ig_caption)  # we add hashtags ourselves
    ig_full = (ig_caption + "\n.\n.\n" + " ".join("#" + t for t in ig_tags))[:2200]

    title = re.sub(r"[<>]", "", (data.get("yt_title") or ig_caption or "Wait for it").strip())
    title = re.sub(r"\s*#shorts\b", "", title, flags=re.I)
    title = (title[:88].rstrip() + " #shorts")[:100]
    desc = re.sub(r"[<>]", "", (data.get("yt_description") or ig_caption).strip())
    yt_tags = _clean_tags(list(data.get("yt_tags") or []) + base_tags + ["shorts"], 15)
    desc = (desc + "\n\n" + " ".join("#" + t for t in yt_tags[:5]))[:4900]
    return {"ig_caption": ig_full, "yt_title": title, "yt_description": desc, "yt_tags": yt_tags,
            "source": data.get("_source", "gemini")}


def fallback(niche, md5: str) -> dict:
    lines = FALLBACK.get(niche.key, GENERIC)
    line = lines[int(md5[:8], 16) % len(lines)]
    return _finish(niche, {"ig_caption": line, "yt_title": line, "yt_description": line, "_source": "fallback"})


@retry(times=3, wait=8)
def _call(model: str, parts: list[dict]) -> dict:
    r = requests.post(
        API.format(model=model),
        headers={"x-goog-api-key": secret("GEMINI_API_KEY"), "Content-Type": "application/json"},
        json={"contents": [{"role": "user", "parts": parts}],
              "generationConfig": {"responseMimeType": "application/json"}},
        timeout=90,
    )
    if r.status_code in (429, 500, 503):
        raise requests.RequestException(f"Gemini {r.status_code}")
    r.raise_for_status()
    cand = r.json()["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in cand if not p.get("thought"))
    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    return json.loads(text)


def generate(niche, md5: str, file_name: str, description: str | None, frames_b64: list[str],
             model: str = "gemini-flash-latest") -> dict:
    if not has_secret("GEMINI_API_KEY"):
        return fallback(niche, md5)
    hint = " ".join(x for x in [(description or "").strip(), _hint_from_name(file_name)] if x)
    prompt = f"""You write viral short-video captions for an Instagram Reels page and its matching YouTube Shorts channel.
Page: {niche.label}
Voice and style: {niche.style}
{"What the uploader says the clip is about: " + hint if hint else "Look at the frames to understand the clip."}

Look at the attached frames from the video. Write in the page's voice. Hook in the first 5 words.
No fake claims, no clickbait lies, don't mention "frames" or "video clip". Max 2 emojis per line.
Return ONLY JSON with these keys:
  "ig_caption": 1-3 short lines, ends with a question or call to comment, NO hashtags,
  "hashtags": 6-10 relevant niche hashtags without '#',
  "yt_title": max 70 characters, curiosity hook, no hashtags,
  "yt_description": 1-2 sentences,
  "yt_tags": 8-12 search keywords."""
    parts = [{"text": prompt}] + [{"inline_data": {"mime_type": "image/jpeg", "data": f}} for f in frames_b64]
    for m in dict.fromkeys([model, "gemini-flash-latest", "gemini-flash-lite-latest"]):
        try:
            return _finish(niche, _call(m, parts))
        except Exception as e:  # noqa: BLE001
            log.warning("Gemini (%s) caption failed: %s", m, e)
    return fallback(niche, md5)
