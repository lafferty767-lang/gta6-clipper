# Setup guide

This puts the bot on a free Oracle Cloud server. Plan on about an hour of clicking, plus some
waiting. It costs nothing: Oracle's "Always Free" ARM server is enough for the bot.

The scripts do the technical parts for you: they create the server and its network and firewall,
install Docker and the bot, set up HTTPS, and update the bot every night. You only create accounts,
press a few buttons and paste keys into the bot's web dashboard.

**Want to run it on your Mac instead, with no server?** See [Running it on your Mac](#running-it-on-your-mac).

**Don't want to wait for Oracle?** A paid server (about $40 a month, or $20 for a smaller one) is
ready in 15 minutes with no waiting for capacity: see [Using a paid server instead](#using-a-paid-server-instead-vultr).
Then continue at [step 3](#3-open-the-dashboard).

## What only you can do

- [ ] **Make the GitHub repo public** (GitHub → the repo → Settings → Danger Zone → Change visibility),
      so the server can download the code without a password. No keys are stored in the repo.
- [ ] **Create an Oracle Cloud account** and pick its home region ([step 1](#1-oracle-cloud-account)).
- [ ] **Paste one command into Oracle Cloud Shell** ([step 2](#2-create-the-server-one-command)).
- [ ] **Twitch app keys** (required, 5 minutes, [step 4](#4-twitch-required)).
- [ ] **Google/YouTube app + Connect YouTube** ([step 6](#6-youtube-shorts)).
- [ ] **TikTok developer app + Connect TikTok** ([step 7](#7-tiktok)).
- [ ] Claude API key for AI titles and scoring (optional but recommended, [step 5](#5-claude-api-key-optional-recommended)).
- [ ] Metricool keys (optional, [step 8](#8-metricool-optional)).
- [ ] **Ask the streamers for permission** and mark them in Settings ([step 9](#9-streamers-and-permission)).
- [ ] Optional: upgrade Oracle to Pay As You Go. It stays free, gets a server faster and keeps
      Oracle from reclaiming an idle server ([details](#out-of-capacity)).

## 1. Oracle Cloud account

1. Go to <https://signup.cloud.oracle.com> and sign up.
2. You need a phone number and a **credit card** (or a debit card that works like one; prepaid and
   virtual cards are refused). Oracle puts a temporary $1 hold on it to check it and does not charge
   you unless you upgrade later.
3. **Home region: choose carefully, it can't be changed.** The free server can only live there.
   Pick one near you, ideally one with 3 "availability domains", which gives the script more
   places to find a free server: **US East (Ashburn)**, **US West (Phoenix)**, **US Midwest
   (Chicago)**, **Germany Central (Frankfurt)** or **UK South (London)**. Don't pick South Korea
   North (Chuncheon): it has no free ARM servers.
4. Wait for the "your account is ready" email (minutes to a few hours), then sign in at
   <https://cloud.oracle.com>.

## 2. Create the server (one command)

1. In the Oracle console, click the **Developer tools** icon at the top right and choose
   **Cloud Shell**. A terminal opens at the bottom of the page.
2. Paste this and press Enter:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/lafferty767-lang/gta6-clipper/main/deploy/oracle-cloud-shell.sh | bash
   ```
3. It creates the network and firewall, then asks Oracle for a free ARM server
   (2 CPUs, 12 GB memory, 100 GB disk, Ubuntu 24.04). Free ARM servers are popular, so you may see
   `no free capacity yet` for a while. **Leave the tab open**; it keeps trying every minute.
   If Cloud Shell closes or stops, run the same command again: it continues where it stopped.
4. Once the server exists, it installs the bot by itself (10-15 minutes) and the script shows
   its progress. At the end you get:
   ```
   Dashboard: https://123-45-67-89.sslip.io
   Backup:    https://123-45-67-89.nip.io
   Username:  admin
   Password:  ...
   ```
   Save these. To see them again later, open Cloud Shell and run `cat ~/gta6-clipper-server.txt`.

**If the command fails with "Could not resolve host" or does nothing:** your Cloud Shell has no
internet access. At the top of the Cloud Shell window, open **Network** and choose **Public
network**, then run it again. If that option isn't there, do it without internet: open
[deploy/oracle-cloud-shell.sh](deploy/oracle-cloud-shell.sh) on GitHub, click **Download raw
file**, then in Cloud Shell use the gear menu → **Upload** to upload it, and run
`bash oracle-cloud-shell.sh`.

### Out of capacity

"No free capacity" is common and can last hours or days in busy regions. Your options:

- **Wait.** The script retries every minute for up to 23 hours.
- **Upgrade to Pay As You Go** (fastest; menu → Billing & Cost Management → Upgrade and Manage
  Payment). Always Free resources stay free after upgrading and Oracle only bills what goes over
  the free limits, which this setup doesn't. Oracle puts a temporary $100 hold on your card, and
  the upgrade can take a day. Paid accounts usually get a server much sooner and reportedly aren't
  subject to idle reclaiming. To be safe, add a $1 budget alert (Billing → Budgets).
- **Try a smaller server**, which is easier to place but handles fewer streams at once:
  `curl -fsSL https://raw.githubusercontent.com/lafferty767-lang/gta6-clipper/main/deploy/oracle-cloud-shell.sh | OCPUS=1 MEMORY_GB=6 bash`

### Keeping it free

- Don't create other ARM servers. The free allowance is 2 CPUs and 12 GB in total; if you go over,
  Oracle disables the ARM servers.
- Oracle can reclaim a free server that sits idle for 7 days (CPU, network and memory all under
  20%). The bot is busy while your streamers are live, but a week with nobody live could get it
  flagged. Pay As You Go accounts are reportedly exempt.
- Sign in to Oracle at least once a month; accounts unused for 30 days can be suspended.

## 3. Open the dashboard

Open the **Dashboard** address and log in with `admin` and the password from step 2.

- The very first visit can show a certificate warning for a few minutes while the server gets its
  free HTTPS certificate. If the address still doesn't work after an hour, the free certificate
  service for `sslip.io` is busy that week: open the **Backup** address instead, go to **Setup**
  and press **Use … as the main address**.
- The **Setup** tab has a status table (green = working) and a box for every key below. Paste a
  value, press Save, and the bot uses it right away.
- The **Settings** tab is the bot's settings file: streamers, review mode, upload targets.

## 4. Twitch (required)

Needed to check Twitch streamers, use viewers' clips as a signal, and suggest new streamers.

1. Go to <https://dev.twitch.tv/console/apps> and log in with any Twitch account (Twitch may ask
   you to turn on two-factor authentication first).
2. **Register Your Application**: any name, OAuth Redirect URL `http://localhost`, category
   **Application Integration**, client type **Confidential**.
3. Open the app, copy the **Client ID**, press **New Secret** and copy the secret.
4. Paste both into the **Twitch** box on the Setup page and press Save.

YouTube and Kick live checks need no keys.

## 5. Claude API key (optional, recommended)

Claude watches each clip's transcript and chat reaction, scores it 1-10, skips weak ones and writes
the title, description and hashtags. Without a key (or when its credit runs out), clips get simple
titles and always wait for you to approve them, even after you turn review mode off.

1. <https://console.anthropic.com> → **API Keys** → Create key. Add a few dollars of credit.
2. Paste it into the **Claude** box on the Setup page.

Each clip is one short request, so even 100 clips a day costs little.

## 6. YouTube Shorts

You create a small Google "app" that is only for you, then connect your channel with a code.

1. Go to <https://console.cloud.google.com> and create a project (e.g. `gta6-clipper`).
2. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
3. **Google Auth Platform** (formerly "OAuth consent screen") → **Get started**: app name
   `GTA 6 Clipper`, your email as support email, Audience **External**, your email as contact.
4. **Branding**: fill in the three links shown at the bottom of the bot's Setup page
   (home page `…/about`, privacy policy `…/privacy`, terms `…/terms`). If it asks for an
   authorized domain, add `sslip.io` (or `nip.io` if you switched to the backup address). If
   Google won't accept the links, leave them empty and try the next step anyway.
5. **Audience** → **Publish app** → confirm. This matters: while the app is in "Testing", Google
   logs you out of it every 7 days. You don't need Google's verification for your own use.
   If Google won't let you publish (it says the Branding page isn't complete), stay in Testing:
   on the same **Audience** page, under **Test users**, press **Add users** and add the Google
   account that owns your YouTube channel. Everything works, but you'll need to press Connect
   YouTube again once a week until the app is published.
6. **Clients → Create client**, application type **TVs and Limited Input devices**, any name.
   Copy the **Client ID** and **Client secret** into the **YouTube** box on the Setup page and Save.
7. Press **Connect YouTube**. The page shows a code. On your phone or computer open
   <https://www.google.com/device>, sign in with the Google account that owns your YouTube channel,
   type the code and allow access. Google warns that the app isn't verified: click **Advanced →
   Go to GTA 6 Clipper**. That's expected for your own app. The Setup page turns green by itself.

Uploads are public by default (`privacy` under `uploads.targets.youtube` in Settings). Google
allows 100 uploads a day per project, far more than the bot's default of 5 a day.

## 7. TikTok

1. Go to <https://developers.tiktok.com>, log in and open **Manage apps → Connect an app**.
2. Fill in the app details. For the website, terms and privacy links, use the three links at the
   bottom of the bot's Setup page.
3. Add the products **Login Kit** and **Content Posting API**, and turn on the scopes
   `user.info.basic` and `video.upload` (`video.publish` appears only if you turn on Direct Post; inbox mode doesn't need it).
4. **Login Kit → Redirect URI (Web)**: paste the exact address shown in the TikTok box on the Setup
   page (it ends in `/connect/tiktok/callback`).
5. **URL properties → Verify**: choose **URL prefix**, enter your dashboard address with a `/` at
   the end, and download the file TikTok gives you. Upload it in the TikTok box on the Setup page,
   then press **Verify** in TikTok.
6. Until TikTok reviews your app, it only works in **Sandbox** mode: create a sandbox, add your
   TikTok account under **Target users**, and use the sandbox's **Client key** and **Client
   secret**.
7. Paste the client key and secret into the **TikTok** box on the Setup page and Save. Press
   **Connect TikTok**, log in with the account you want to post to, and approve.

How posting works (`mode` under `uploads.targets.tiktok` in Settings):

- `inbox` (default): each clip arrives in the TikTok app as a draft notification. You open it, add
  a sound if you like and tap Post. TikTok allows at most 5 waiting drafts per 24 hours, so post or
  delete each draft when it arrives. If TikTok refuses a draft, the clip shows as failed on the
  Clips tab with a Retry button; retrying only sends it to the places it hasn't reached yet.
- `direct`: posts straight to your profile, but only after TikTok has reviewed (audited) your app.
  Before that, direct posts are private only and the TikTok account itself must be set to private.
  To post publicly on autopilot, submit the app for review in the developer portal.

## 8. Metricool (optional)

Use this to schedule through Metricool instead of, or as well as, posting directly. Metricool's API
needs its **Advanced** plan or higher.

1. Metricool → Account settings → **API** → copy the token.
2. Open your brand in Metricool. The browser address contains `userId=...` and `blogId=...`.
3. Paste all three into the **Metricool** box on the Setup page.
4. In Settings, set `uploads.targets.metricool.enabled: true` and pick the `networks`. If Metricool
   posts to YouTube and TikTok for you, set those two targets to `enabled: false` so clips aren't
   posted twice.

## 9. Streamers and permission

Posting other people's streams without permission can get your channels struck or banned, so the bot
only uploads a streamer's clips once you mark them as allowed. Clips are still made and shown in the
dashboard either way.

1. Ask the streamer or their team (see [STREAMERS.md](STREAMERS.md) for who to ask and how).
2. On the **Settings** tab, change that streamer's `permission: unknown` to `permission: granted`
   and save.

## 10. Going fully automatic

Review mode is on at first: every clip waits on the **Clips** tab until you press Approve. Once you
trust the clips, set `review_mode: false` in Settings. Clips that pass the AI check then upload on
their own, up to `uploads.max_per_day` (5) a day, at least `uploads.min_minutes_between` (20)
minutes apart. Clips the AI couldn't score (no Claude key, or no credit left) still wait for you.

## Everyday use

Everything is in the dashboard. The bot updates itself every night (04:23 server time, UTC).

For the server command line: open Oracle Cloud Shell, set its **Network** (menu at the top of
Cloud Shell) to **Public**, run `cat ~/gta6-clipper-server.txt`, and run the command shown after
`SSH:` (it looks like `ssh ubuntu@1.2.3.4`). Then:

| Command | What it does |
|---|---|
| `clipper status` | is it running, and the dashboard address |
| `clipper logs` | watch what the bot is doing (Ctrl+C to stop watching) |
| `clipper doctor` | what's connected and what's missing |
| `clipper password` | show the dashboard password |
| `clipper update` | get the newest version now |
| `clipper restart` | restart the bot |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Cloud Shell script keeps saying "no free capacity" | see [Out of capacity](#out-of-capacity) |
| Dashboard address shows a certificate error after an hour | use the Backup address and press **Use … as the main address** on Setup |
| Twitch streamers never show as live | check the Twitch keys on Setup (the status table tests them) |
| A YouTube streamer never shows as live | check their `@handle`; set `game_check: false` if they don't tag the game |
| Kick returns HTTP 403 | Kick's bot protection; it usually clears up, or use their Twitch/YouTube instead |
| No captions on the first clip | the speech model is still downloading (about 500 MB, once) |
| Cloud Shell says the install on the server failed | copy the log lines it shows when asking for help; to try again, run the `ssh ... /root/gta6-firstboot.sh` line it prints (Cloud Shell Network: Public) |
| Connect YouTube says access was denied, or Google shows "Error 403: access_denied" | the Google app is in "Testing" and your account isn't a test user; add it or publish the app (step 6.5) |
| YouTube login stopped working after a week | the Google app is still in "Testing"; publish it (step 6.5) and press Connect YouTube again |
| TikTok "too many pending shares" | more than 5 drafts waiting in the TikTok app; post or delete them |
| TikTok posts are private | the TikTok app isn't reviewed yet; use `mode: inbox` or submit it for review |

## Running it on your Mac

Free, and no server needed. The bot works while your Mac is awake and Docker Desktop is running.
When the lid is closed or the Mac sleeps, it pauses and misses any streams in that time, then picks
up again by itself.

1. Install **Docker Desktop** from <https://www.docker.com/products/docker-desktop/> (choose Apple
   Silicon or Intel chip to match your Mac: Apple menu → About This Mac). Open it once and accept.
   In Docker Desktop's **Settings → General**, turn on **Start Docker Desktop when you sign in**.
2. Open the **Terminal** app, paste this and press Enter:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/lafferty767-lang/gta6-clipper/main/deploy/mac/install-mac.sh | bash
   ```
   The first time takes 5-10 minutes. At the end it opens the dashboard at <http://localhost:8080>
   and shows your password (also saved in `GTA6-Clipper/WELCOME.txt` in your home folder).
3. Keep it awake while plugged in: **System Settings → Battery → Options** → turn on **Prevent
   automatic sleeping on power adapter when the display is off**.
4. Continue at [step 4](#4-twitch-required) to add your keys.

Things that work differently on a Mac:

- The public address (needed for Connect TikTok and Metricool) is a free Cloudflare address like
  `https://some-words.trycloudflare.com`, shown in the TikTok box on the Setup page. It **changes
  every time the Mac restarts or Docker restarts**, and the bot follows it by itself. TikTok only
  needs it once: put the current redirect address into TikTok's portal right before you press
  Connect TikTok (step 7.4). For TikTok's "URL properties / Verify" step, use the permanent
  GitHub Pages address instead (step 7).
- If TikTok sends you back to a page that says "This site can't be reached", change the start of
  the address (`https://….trycloudflare.com`) to `http://localhost:8080`, keep the rest, and press
  Enter. A VPN can cause this; turn it off while connecting.
- To update, paste the same command again. Your keys, settings and clips are kept.
- To stop it: `cd ~/GTA6-Clipper && docker compose -f docker-compose.yml -f docker-compose.mac.yml stop`.
- Docker Desktop uses part of your Mac's memory. With 8 GB of memory, watch one or two streamers at a time.

## Using a paid server instead (Vultr)

Checked in November 2026: Vultr's 4 CPU / 8 GB server costs $40 a month (billed by the hour, no
contract, cancel any time), has data centers in Los Angeles and Silicon Valley, and installs the
bot by itself from a script you paste while creating the server. No terminal needed.

1. Sign up at <https://www.vultr.com> and add a credit card. New accounts currently get their first
   deposit matched up to $100 (see vultr.com/coupons).
2. **Deploy** → **Cloud Compute**, shared CPU, **Regular Performance**.
   - Location: **Los Angeles** or **Silicon Valley** (any US city works if those are full).
   - Image: **Ubuntu 24.04**.
   - Plan: **4 vCPU, 8 GB memory, 160 GB** ($40/month). For one streamer at a time, the
     2 vCPU / 4 GB plan ($20/month) also works; you can upgrade later.
3. Under **Additional Features**, tick **Enable Cloud-Init User-Data** and paste this, after
   changing `PICK-A-PASSWORD` to your own dashboard password (letters and numbers only):
   ```bash
   #!/bin/bash
   export DASHBOARD_PASSWORD='PICK-A-PASSWORD'
   for try in 1 2 3; do
     curl -fsSL https://raw.githubusercontent.com/lafferty767-lang/gta6-clipper/main/deploy/install.sh -o /root/gta6-install.sh \
       && bash /root/gta6-install.sh && break
     sleep 60
   done
   ```
4. Press **Deploy Now** and wait about 15 minutes while it installs the bot.
5. Copy the server's IP address from the Vultr dashboard (for example `45.76.12.34`), replace the dots
   with dashes and open `https://45-76-12-34.sslip.io`. Log in as `admin` with your password, then
   continue at [step 3](#3-open-the-dashboard).

If you get no page after 30 minutes, open the server in Vultr, press **View Console**, log in as
`root` with the password Vultr shows on the server's page, and run `tail -n 30 /var/log/cloud-init-output.log`.

## Using another server

Any Ubuntu 22.04/24.04 or Debian server with 4 CPUs and 8 GB of memory works (about 3-4 live
streams at once). Open ports 80 and 443 in the provider's firewall, then on the server run:

```bash
curl -fsSL https://raw.githubusercontent.com/lafferty767-lang/gta6-clipper/main/deploy/install.sh | sudo bash
```

It prints the dashboard address and password at the end. To use your own domain, point it at the
server and run the same line again ending in `| sudo DOMAIN=clips.example.com bash`.
