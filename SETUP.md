# YTS Autopilot — one-time setup (about 1–2 hours)

You do this once. After that the bot runs by itself on GitHub's free servers.
Your only jobs afterwards: **drop videos into Google Drive** and **look at the dashboard**.

Your accounts are paired like this:

| Niche (Drive folder) | Instagram | YouTube |
|---|---|---|
| `aesthetic` | @bliss_nglow | @blissnglowshorts |
| `gta` | @wanted_level_yts | @WantedLevel-YTS |
| `horror` | @darrdiaries_yts | @DarrDiaries-YTS |

Each video goes to **both** the Instagram page and the YouTube channel of its niche, **once each, never again**.

Keep a notepad open. You'll collect these **secrets** as you go:

```
GOOGLE_CLIENT_ID        GOOGLE_CLIENT_SECRET     GDRIVE_REFRESH_TOKEN    GEMINI_API_KEY
YT_TOKEN_AESTHETIC      YT_TOKEN_GTA             YT_TOKEN_HORROR
IG_ID_AESTHETIC         IG_ID_GTA                IG_ID_HORROR
IG_TOKEN_AESTHETIC      IG_TOKEN_GTA             IG_TOKEN_HORROR
```

---

## Step 1 — Make the repo public (free dashboard + unlimited free runs)

GitHub → your repo **YTS--Automations** → **Settings** → bottom of the page → **Change visibility → Public**.

Your secrets stay hidden even in a public repo. Only the code, the video file names and the stats can be seen.
If you'd rather keep it private, that works too. You get 2,000 free minutes a month, which is enough, but the dashboard page needs GitHub Pro.

## Step 2 — Google Drive folder

1. In Google Drive, create a folder named **YTS**.
2. Open it and copy the ID from the address bar: `drive.google.com/drive/folders/`**`THIS_PART`**
3. In the repo, open `config/accounts.yaml`, click ✏️, and paste it into `drive_root_folder_id: "..."`. Then **Commit**.

The bot creates `aesthetic`, `gta` and `horror` inside YTS on its first run. Each one gets `_posted`, `_duplicates` and `_failed` sub-folders.

> 💡 For better captions, give files real names (`trevor_jumps_off_bridge.mp4`) or add a description in Drive
> (right-click → File information → Details → Description). Gemini also looks at the video frames, so random names still work.

## Step 3 — Google Cloud (Drive + YouTube access) — free

1. Go to <https://console.cloud.google.com/> → top bar **Select project → New project** → name it `yts-autopilot` → Create.
2. Go to **APIs & Services → Library** and enable **YouTube Data API v3** and **Google Drive API**.
3. Go to **APIs & Services → OAuth consent screen** (also called "Google Auth Platform"):
   - User type **External**. App name `YTS Autopilot`. Use your Gmail for the support and developer email.
   - **Audience → Publish app → "In production"**. ⚠️ This is important. If you leave it in "Testing", Google cancels your tokens after 7 days.
4. Go to **Credentials → Create credentials → OAuth client ID → Application type: Desktop app** → Create.
   Copy the **Client ID** → `GOOGLE_CLIENT_ID` and the **Client secret** → `GOOGLE_CLIENT_SECRET`.
5. Copy the **Project number** from the Google Cloud home page. You need it in Step 5.

## Step 4 — Connect Drive and the 3 YouTube channels (on your PC)

1. Install Python 3.12 from <https://www.python.org/downloads/>. Tick **"Add Python to PATH"**.
2. Download your repo: **Code → Download ZIP**, and unzip it.
3. Open the folder, click the address bar, type `cmd` and press Enter. Then run:

```
pip install google-auth-oauthlib requests pyyaml
python tools/get_google_token.py drive
```

A browser opens. Log in with the Google account that owns the **YTS** Drive folder.
If you see "Google hasn't verified this app", click **Advanced → Go to YTS Autopilot**. It's your own app, so that's safe.
Copy the long token it prints → `GDRIVE_REFRESH_TOKEN`.

4. Run it 3 more times, once for each channel:

```
python tools/get_google_token.py youtube
```

When Google asks **"Choose an account or brand account"**, pick the right channel each time. The script prints which channel you connected.

| Channel shown | Save token as |
|---|---|
| @blissnglowshorts | `YT_TOKEN_AESTHETIC` |
| @wantedlevel-yts | `YT_TOKEN_GTA` |
| @darrdiaries-yts | `YT_TOKEN_HORROR` |

## Step 5 — YouTube API audit (needed before YouTube uploads go public)

YouTube **locks every video uploaded through an un-audited API project to Private**, and you can't appeal that.
So the bot **won't upload to YouTube until you're approved**. It keeps a backlog instead: each video already posted on Instagram
goes to YouTube later, once each, oldest first.

1. Open the form: <https://support.google.com/youtube/contact/yt_api_form>
2. Fill it in honestly. Suggested answers:
   - Organisation: your name (individual creator). Project number: from Step 3.
   - Use case: *"Personal tool that uploads my own short videos to my own 3 YouTube channels
     (@blissnglowshorts, @WantedLevel-YTS, @DarrDiaries-YTS) and reads their public statistics. Not used by
     other users. No YouTube data is stored or shared."*
   - API client link: `https://github.com/yusufnoor786/YTS--Automations`
3. Approval usually takes a few days to a few weeks. Once approved, change `youtube_api_audited: false` to **`true`** in `config/accounts.yaml`.

Until then, Instagram runs normally and the dashboard already shows your YouTube subscriber counts.

## Step 6 — Instagram (3 pages) — free

**6a. Prepare each Instagram account** (in the Instagram app, for bliss_nglow, wanted_level_yts and darrdiaries_yts):
- Settings → **Account type and tools → Switch to professional account** (Creator is fine).
- Create a **Facebook Page** for each one (on Facebook: Menu → Pages → Create. One Page per Instagram account).
- Link each Instagram account to its Page: Facebook Page → **Settings → Linked accounts → Instagram → Connect**.

**6b. Create a Meta app**
1. Go to <https://developers.facebook.com/> → **Get started** (register as a developer with your Facebook account).
2. **My Apps → Create app**. Name it `YTS Autopilot`. Pick the option for managing content on your **Pages / Instagram via Facebook Login**.
   If asked for an app type, choose **Business**.
3. Add the products **Instagram (API setup with Facebook Login)** and **Facebook Login for Business**.
4. Go to **App settings → Basic** and copy the **App ID** and **App secret**.
   Leave the app in **Development mode**. You're the admin, so it works on your own pages without Meta's review.

**6c. Get the tokens**
1. Open <https://developers.facebook.com/tools/explorer/> and select your app (top right).
2. Click **Get Token → Get User Access Token** and add these permissions:
   `instagram_basic`, `instagram_content_publish`, `instagram_manage_insights`,
   `pages_show_list`, `pages_read_engagement`, `business_management`
3. Click **Generate Access Token**. When Facebook asks, **tick all 3 Pages and all 3 Instagram accounts**.
4. Copy the token. On your PC, run:

```
python tools/get_meta_tokens.py
```

Paste the App ID, App secret and the token. The script prints `IG_ID_…` and `IG_TOKEN_…` for each page,
and checks that every token **never expires ✅**.

## Step 6d — Facebook Pages (Reels + stats)

The same Facebook Pages also post every reel, Facebook Reels style. The Meta app needs the
`pages_manage_posts` and `read_insights` permissions, then you re-run the token step. Secrets per Page:
`FB_PAGE_ID_<NICHE>` and `FB_TOKEN_<NICHE>` (the Page token, never expires).
Facebook allows 30 API reels per Page per day.

## Step 7 — Gemini key (for AI captions) — free

<https://aistudio.google.com/apikey> → **Create API key** → `GEMINI_API_KEY`.
Your Gemini Pro subscription isn't needed for this, because the API free tier is enough.

## Step 8 — Put the secrets in GitHub

Repo → **Settings → Secrets and variables → Actions → New repository secret**.
Add all 13 from your notepad, using exactly the names above.

## Step 9 — Turn on the dashboard

Repo → **Settings → Pages** → Source: **Deploy from a branch** → Branch **main**, folder **/docs** → Save.
Your dashboard: **https://yusufnoor786.github.io/YTS--Automations/** (bookmark it on your phone).

## Step 10 — Test, then let it run

1. Repo → **Actions** → if asked, click **"I understand my workflows, enable them"**.
2. **Check setup → Run workflow**. Open the run: every line should be ✅. It tells you exactly what to fix if not.
3. Put 2–3 videos into each Drive folder (`YTS/aesthetic`, `YTS/gta`, `YTS/horror`).
4. **Post videos → Run workflow → tick "Dry run"**. This prepares everything without posting. Check the captions in the run log.
5. **Post videos → Run workflow** (no tick). Your first real posts go out. 🎉

That's it. From now on it posts automatically **2× a day per account** (12:47 and 19:13 IST, with 21:41 as a backup slot),
and the dashboard updates every night.

---

## Daily life (zero work)

- **Add videos:** drop them into `YTS/<niche>` in Drive. Any time, any amount, from your phone.
- **Look:** the dashboard shows followers and subscribers, % to goal (100k IG / 10k YT), 7-day growth,
  latest posts with views, and **how many videos are left**. It warns you at 5.
- **Errors:** install the **GitHub mobile app**. It notifies you if a run fails.

## How "never the same video twice" works

1. Every file's fingerprint (md5) is recorded in `state/ledger.json` together with where it was posted.
2. A **visual fingerprint** of 3 frames catches the *same clip* re-downloaded, renamed or re-compressed.
3. Copies go to `_duplicates`, broken or too-short files to `_failed`, and posted ones to `_posted`.
4. If a run crashes mid-post, the next run first checks Instagram/YouTube to see whether it actually went live before trying again.

## Change things (in `config/accounts.yaml`)

- Posts per day: `posts_per_day` (Instagram allows 100 a day through the API. Keep YouTube ≤ 2 per channel because of the free quota).
- Caption voice: `style`. Hashtags: `hashtags`.
- Posting times: the `cron` lines in `.github/workflows/post.yml` (times are UTC = IST − 5:30).
- New niche (Mr Bean-style comedy): create the Instagram/YouTube accounts, connect them like above with the
  `_COMEDY` secret names, and set `enabled: true` in the `comedy` block.

## Problems?

| Message in "Check setup" | Fix |
|---|---|
| `invalid_grant` (Google) | The OAuth app was in "Testing", or you changed your password. Set it to In production (Step 3.3) and redo Step 4. |
| `Token belongs to X, config expects Y` | You picked the wrong channel or page. Redo that token. |
| Instagram `code 190` | The token was revoked. Redo Step 6c. |
| Instagram `code 10 / 200` (permission) | A permission is missing in 6c, or the Page/IG wasn't ticked. Redo 6c. |
| YouTube video private | `youtube_api_audited` is true but the audit wasn't approved yet. Set it back to false. |
