# YTS Autopilot

Fully automated posting for 3 faceless short-video brands, running on free GitHub Actions.

```
Google Drive (YTS/aesthetic · YTS/gta · YTS/horror)
        │   you only drop videos here
        ▼
GitHub Actions — 2×/day per account
  pick oldest unused video → duplicate check → 9:16 convert (FFmpeg)
  → Gemini caption/title/hashtags → publish → move to _posted
        │
        ├─► Instagram Reels   @bliss_nglow · @wanted_level_yts · @darrdiaries_yts
        └─► YouTube Shorts    @blissnglowshorts · @WantedLevel-YTS · @DarrDiaries-YTS
        ▼
Dashboard (GitHub Pages) — followers, goal %, growth, views, videos left
```

**Setup:** read [SETUP.md](SETUP.md) (one time, about 1–2 hours).
**Dashboard:** https://yusufnoor786.github.io/YTS--Automations/

| Workflow | When | What |
|---|---|---|
| Post videos | 12:47 / 19:13 / 21:41 IST | posts up to `posts_per_day` per account |
| Analytics | 23:37 IST | followers, subs, views → dashboard |
| Check setup | manual | tests every connection, shows ✅/❌ |

Code: `autopilot/` (Python) · settings: `config/accounts.yaml` · memory: `state/ledger.json` · tests: `python tests/test_core.py`
