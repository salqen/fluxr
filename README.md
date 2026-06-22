# fluxr

Instagram content manager — **publish** posts/reels/stories, **schedule** them ahead, generate **AI captions**, and run an optional local **engagement bot**. Flask backend + single-file dashboard, deployed on Railway.

---

## Features

- 📤 **Publisher** — post photos, reels, and stories via the official Instagram Graph API
- 📅 **Scheduler** — queue content to publish automatically at a chosen time
- ✨ **AI captions** — generate captions + hashtags with Claude (Anthropic API)
- 🤖 **Reach Booster** — Selenium engagement bot (likes/comments/stories) — **local only**
- 🩺 **System Health** panel that tells you exactly what's configured and what isn't

---

## Architecture

| File | Role |
|------|------|
| `bot_server.py` | Flask app: OAuth, REST API, bot controller |
| `ig_publisher.py` | Instagram Graph API wrapper + scheduler thread |
| `dashboard.html` | Single-file SPA dashboard (no build step) |
| `login.html` | OAuth landing page |
| `Procfile` / `railway.json` | Railway deploy config (gunicorn) |

State is stored as JSON files under `DATA_DIR` (default `/tmp/fluxr_data`).

> ⚠️ On Railway, `/tmp` is wiped on every redeploy — users, tokens, and schedules reset. Mount a [Railway volume](https://docs.railway.app/reference/volumes) and point `DATA_DIR` at it for persistence.

---

## Setup

### 1. Environment variables

Copy `.env.example` and fill it in (or set these in **Railway → Variables**):

| Variable | Required | Notes |
|----------|----------|-------|
| `META_APP_SECRET` | ✅ | App Secret from the Meta dashboard |
| `REDIRECT_URI` | ✅ | Must **exactly** match the URI registered in your Meta app |
| `BASE_URL` | ✅ (prod) | Public HTTPS URL — used to build media URLs the IG API can fetch |
| `SECRET_KEY` | ✅ | Stable random value, else sessions reset on restart |
| `ANTHROPIC_API_KEY` | optional | Enables AI captions |
| `META_APP_ID` | optional | Defaults to the bundled app id |
| `DATA_DIR` | optional | Persistent data path |

Generate a `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Meta app + Instagram setup

Publishing requires an **Instagram Business or Creator** account linked to a **Facebook Page**:

1. Convert your IG account to **Business/Creator** (IG app → Settings → Account type).
2. Link it to a Facebook Page (IG → Settings → Linked accounts, or Page → Settings → Linked accounts).
3. At [developers.facebook.com](https://developers.facebook.com): create an app → add **Instagram Graph API** + **Facebook Login**.
4. In **Facebook Login → Settings**, add your `REDIRECT_URI` to **Valid OAuth Redirect URIs**.
5. Request permissions: `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`.
6. Copy the **App Secret** → `META_APP_SECRET`.

Then **log in through fluxr** — the OAuth flow auto-discovers your IG Business account and stores the Page token used for publishing.

> If auto-discovery fails (no linked Business account found), the **Settings → Publisher** panel lets you paste an **IG Business User ID** + **Page Access Token** manually as a fallback.

### 3. Run

**Local:**

```bash
pip install -r requirements.txt
python bot_server.py
# http://localhost:5000
```

**Railway:** push to the connected repo — `Procfile` / `railway.json` handle the rest:

```
gunicorn bot_server:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120
```

> Keep `--workers 1`. Bot state and schedulers live in memory; multiple workers would desync them.

---

## The Reach Booster (engagement bot)

Selenium-based and **only runs locally** — Railway has no browser. It attaches to a Chrome instance started with remote debugging:

```bash
chrome --remote-debugging-port=9222 --user-data-dir="C:/chrome-bot"
```

Then start the bot from the dashboard. On the server this feature is intentionally disabled (shown as "len lokálne" in System Health).

> ⚠️ Automating likes/comments/follows can violate Instagram's Terms of Service and risks rate-limits or bans. Use at your own risk.

---

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| Account shows `—`, Publisher = *chyba* | Backend not redeployed, or not logged in |
| `Object with ID '…' does not exist` on publish | No IG **Business** account linked — convert IG + link a Page, then re-login |
| AI captions error | `ANTHROPIC_API_KEY` not set |
| Login loops back to start | `SESSION_COOKIE_SECURE` requires HTTPS — use `https://` `BASE_URL`/`REDIRECT_URI` in prod |
| Users logged out after deploy | `/tmp` wiped — set `SECRET_KEY` + a persistent `DATA_DIR` volume |
