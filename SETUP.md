# Setup guide

Plan on about an hour. You need: a small cloud server, a Twitch developer app, and the
accounts you want to post to. The Claude API key is optional but makes the clips much better.

## 1. Get a server (VPS)

Any Linux server with **4 vCPU, 8 GB RAM, 80 GB disk** handles 3-4 live streams at once.
Examples: Hetzner CPX31 (about €15/month), DigitalOcean 4 vCPU/8 GB, Vultr, Contabo.
Pick Ubuntu 24.04.

Bandwidth: each stream at 720p is about 3-6 Mbit/s, so roughly 1.5-2.5 TB per stream per month
if someone on your list is live all the time. Pick a provider with generous included traffic
(Hetzner includes 20 TB in the EU).

On the server:

```bash
ssh root@YOUR_SERVER_IP
curl -fsSL https://get.docker.com | sh
apt-get install -y git
```

## 2. Get the code onto the server

The repo is private, so create a GitHub token first: GitHub → Settings → Developer settings →
Fine-grained tokens → "Generate new token", give it read access to `gta6-clipper` (Contents: Read).

```bash
git clone https://github.com/lafferty767-lang/gta6-clipper
# username: lafferty767-lang, password: paste the token
cd gta6-clipper
cp config.example.yaml config.yaml
cp .env.example .env
nano .env        # fill in keys as you go through the steps below
```

To update later: `git pull && docker compose up -d --build`.

## 3. Twitch app (required for Twitch streamers, viewer clips and discovery)

1. Go to <https://dev.twitch.tv/console/apps> and log in with any Twitch account.
2. **Register Your Application**: any name, OAuth Redirect URL `http://localhost`,
   category "Application Integration", client type **Confidential**.
3. Open it, copy the **Client ID**, press **New Secret** and copy it.
4. In `.env`: `TWITCH_CLIENT_ID=...` and `TWITCH_CLIENT_SECRET=...`

YouTube and Kick live checks need no keys.

## 4. Claude API key (AI scoring + titles, optional)

1. <https://console.anthropic.com> → API Keys → Create key. Add a few dollars of credit.
2. In `.env`: `ANTHROPIC_API_KEY=...`

Each clip is one short request, so even 100 clips a day costs very little. Without a key, clips get
template titles and every clip goes to review.

## 5. YouTube Shorts

You do this part on your own computer, because Google opens a browser to log in.

1. <https://console.cloud.google.com> → create a project (e.g. "gta6-clipper").
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **OAuth consent screen**: User type External, fill in the app name and your email. Add your
   Google account under Test users. Then press **Publish app** (so the login doesn't expire after
   7 days; Google shows an "unverified app" warning for your own use, which is fine).
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON and
   save it as `client_secret.json` in the repo folder on your computer.
5. On your computer, in the repo folder:
   ```bash
   pip install python-dotenv google-auth-oauthlib
   python -m clipper auth-youtube --client-secrets client_secret.json
   ```
   A browser opens: pick the YouTube channel you want to post to and allow access.
6. Copy the three printed lines (`YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`,
   `YOUTUBE_REFRESH_TOKEN`) into `.env` on the server.

Quota: a new Google project gets 10,000 API units a day and one upload costs about 1,600, so
roughly 6 uploads a day. That matches the default `max_per_day: 8` (the extra ones go to TikTok
or wait for the next day). If you need more, request a quota increase in the Google Cloud console
(YouTube Data API → Quotas).

## 6. TikTok

1. <https://developers.tiktok.com> → log in → **Manage apps → Connect an app**.
2. Add the products **Login Kit** and **Content Posting API**. Turn on the scopes
   `user.info.basic`, `video.upload` and `video.publish`.
3. Under Login Kit, add a **Redirect URI**. It must be https. Any https page you control works
   (the page doesn't need to do anything; you only copy the URL it lands on). Put the same value in
   `.env` as `TIKTOK_REDIRECT_URI`.
4. Copy the **Client key** and **Client secret** into `.env`.
5. While the app is unaudited, use its **Sandbox** and add your TikTok account as a target user.
6. Get the refresh token (on the server or your computer):
   ```bash
   docker compose run --rm clipper auth-tiktok
   ```
   Open the printed link, log in with the TikTok account to post to, approve, then paste the
   URL you land on. Copy the printed `TIKTOK_REFRESH_TOKEN` into `.env`.

Posting modes (`uploads.targets.tiktok.mode` in `config.yaml`):

- `inbox` (default): the video lands in your TikTok inbox as a draft; you add a sound/caption
  and tap Post in the app. Works before TikTok audits your app.
- `direct`: posts straight to your profile. Until TikTok approves your app in its audit, direct
  posts are forced to private. Submit the app for review in the developer portal to post publicly.

## 7. Metricool (optional, instead of or in addition to direct posting)

Metricool's API is on its Advanced plan and above.

1. Metricool → Account settings → **API** → copy the token into `METRICOOL_USER_TOKEN`.
2. Open your brand in Metricool; the browser URL contains `blogId=...` and `userId=...`.
   Put them in `METRICOOL_BLOG_ID` and `METRICOOL_USER_ID`.
3. Metricool downloads the video from your server, so the server needs a public https address.
   Point a domain (e.g. `clips.yourdomain.com`) at the server and put Caddy in front:
   ```bash
   apt-get install -y caddy
   echo 'clips.yourdomain.com {
     reverse_proxy localhost:8080
   }' > /etc/caddy/Caddyfile && systemctl reload caddy
   ```
   Then set `PUBLIC_BASE_URL=https://clips.yourdomain.com`. Only approved clips are reachable
   at their random public link; everything else needs the dashboard password.
4. In `config.yaml` set `uploads.targets.metricool.enabled: true` and pick the `networks`.
   If you schedule through Metricool, set `youtube` and `tiktok` targets to `enabled: false` so
   clips aren't posted twice.

## 8. Start it

```bash
nano .env                 # set DASHBOARD_PASSWORD to something strong
nano config.yaml          # streamers, review_mode, permissions
docker compose up -d --build
docker compose logs -f
```

Open `http://YOUR_SERVER_IP:8080` (or your https domain) and log in with `DASHBOARD_USER` /
`DASHBOARD_PASSWORD`. You'll see who's live, clips waiting for review, and suggested streamers.

The first clip needs the Whisper speech model, which is downloaded once (about 500 MB for `small`)
and kept in `data/hf-cache`.

## 9. Going fully automatic

Once you trust the clips:

1. Get permission from the streamers you clip and set `permission: granted` for them (see
   [STREAMERS.md](STREAMERS.md)).
2. Set `review_mode: false`. Clips that pass the AI check (`llm.min_score`) upload on their own,
   up to `uploads.max_per_day`, spaced by `uploads.min_minutes_between`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Twitch streamers never show as live | check `TWITCH_CLIENT_ID/SECRET`; run `docker compose run --rm clipper check` |
| A YouTube streamer never shows as live | check the `@handle`; set `game_check: false` if they don't tag the game |
| Kick returns HTTP 403 | Kick's bot protection; it usually clears up, or use their Twitch/YouTube instead |
| No captions | first run is downloading the Whisper model; see the logs |
| YouTube `quotaExceeded` | daily quota used up; lower `max_per_day` or request more quota |
| TikTok posts are private | the app isn't audited yet; use `mode: inbox` or submit for audit |
