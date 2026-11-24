"""Loads config/accounts.yaml and answers 'is this platform active right now?'."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .util import has_secret

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "accounts.yaml"
PLATFORMS = ("instagram", "youtube", "facebook")


@dataclass
class Niche:
    key: str
    label: str
    enabled: bool
    posts_per_day: int
    style: str
    hashtags: list[str]
    instagram: dict = field(default_factory=dict)
    youtube: dict = field(default_factory=dict)
    facebook: dict = field(default_factory=dict)

    def platform(self, p: str) -> dict:
        return getattr(self, p) or {}


@dataclass
class Config:
    raw: dict
    niches: dict[str, Niche]

    def __getattr__(self, item):
        try:
            return self.raw[item]
        except KeyError as e:
            raise AttributeError(item) from e

    def get(self, key, default=None):
        return self.raw.get(key, default)

    def active_platforms(self, niche: Niche) -> list[str]:
        """Platforms that are switched on AND have their secrets present."""
        out = []
        for p in PLATFORMS:
            pc = niche.platform(p)
            if not (niche.enabled and pc.get("enabled")):
                continue
            if p == "youtube" and not self.raw.get("youtube_api_audited"):
                continue
            if p == "instagram" and not (has_secret(pc["token_secret"]) and has_secret(pc["user_id_secret"])):
                continue
            if p == "youtube" and not has_secret(pc["token_secret"]):
                continue
            if p == "facebook" and not (has_secret(pc["token_secret"]) and has_secret(pc["page_id_secret"])):
                continue
            out.append(p)
        return out


def load(path: Path = CONFIG_PATH) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    niches = {}
    for key, n in (raw.get("niches") or {}).items():
        niches[key] = Niche(
            key=key,
            label=n.get("label", key),
            enabled=bool(n.get("enabled", False)),
            posts_per_day=int(n.get("posts_per_day", 1)),
            style=n.get("style", ""),
            hashtags=list(n.get("hashtags") or []),
            instagram=n.get("instagram") or {},
            youtube=n.get("youtube") or {},
            facebook=n.get("facebook") or {},
        )
    return Config(raw=raw, niches=niches)
