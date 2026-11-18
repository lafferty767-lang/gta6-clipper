# GTA 6 Clipper

A bot that runs 24/7, watches popular streamers while they play GTA 6 live, finds the moments
chat goes crazy, cuts them into vertical shorts with captions, and posts them to YouTube Shorts
and TikTok (or schedules them through Metricool).

```
 every 60s                      while a stream is live                         when a moment is found
┌────────────┐  live on GTA 6? ┌──────────────────────────────┐  score > X  ┌────────────────────────────────┐
│ check list │ ──────────────► │ record last 5 min (rolling)  │ ──────────► │ cut 30-60s → Whisper captions  │
│ Twitch/YT/ │                 │ read chat (velocity, emotes) │             │ → Claude scores + writes title │
│ Kick       │                 │ measure audio loudness       │             │ → render 9:16 (facecam/gameplay)│
└────────────┘                 │ watch viewer-made clips      │             └───────────────┬────────────────┘
                               └──────────────────────────────┘                             ▼
                                                                    review dashboard (approve) → YouTube / TikTok / Metricool
```

## What it does

- **Finds who's live on GTA 6** on Twitch, YouTube and Kick from your streamer list
  (`config.yaml`), and also lists the biggest GTA 6 streams on Twitch in the dashboard so you
  can add new streamers.
- **Detects clip-worthy moments** by combining chat speed spikes, hype emotes/keywords
  (KEKW, 💀, "CLIP IT"…), audio loudness spikes (screaming), and viewers pressing Twitch's Clip
  button. Each stream learns its own "normal" first, so a busy chat like Speed's doesn't trigger
  constantly.
- **Edits the clip**: 9:16 vertical, facecam on top + gameplay below (or blurred background),
  word-by-word burned-in captions, a credit line naming the streamer, loudness normalized.
- **AI judge (optional)**: Claude reads the transcript and chat reaction, scores the clip 1-10,
  skips weak ones, and writes the title, description and hashtags.
- **Uploads** to YouTube Shorts and TikTok, or schedules through Metricool, with a daily cap and
  spacing between posts. New uploaders are one small class each (`clipper/uploaders/`).
- **Review mode** (on by default): clips wait in a web dashboard where you watch them, edit the
  title and press Approve. Turn it off to run fully automatically.
- **Permission guard**: a streamer's clips are only uploaded once you mark their permission as
  `granted` in the config. See [STREAMERS.md](STREAMERS.md).

## Quick start

See **[SETUP.md](SETUP.md)** for the full guide (server, API keys, accounts). Short version:

```bash
git clone https://github.com/lafferty767-lang/gta6-clipper && cd gta6-clipper
cp config.example.yaml config.yaml   # streamers, review mode, upload targets
cp .env.example .env                 # API keys and dashboard password
docker compose up -d --build
docker compose logs -f               # watch it work
# dashboard: http://YOUR_SERVER_IP:8080
```

## Commands

```bash
docker compose run --rm clipper check            # who on your list is live on GTA 6 right now
docker compose run --rm clipper discover --lang en   # biggest GTA 6 streams on Twitch
docker compose run --rm clipper clip-file /data/vod.mp4 --streamer CaseOh            # test on a VOD
docker compose run --rm clipper clip-file /data/vod.mp4 --streamer CaseOh --at 754   # one clip at 12:34
```

`clip-file` is the easiest way to tune the edit: download a VOD into `data/`, run it, and look at
`data/clips/*/final.mp4`. Use it to find each streamer's facecam box for the split layout.

## Tuning

| Problem | Change in `config.yaml` |
|---|---|
| Too many boring clips | raise `detection.threshold` (e.g. 8) or `llm.min_score` |
| Missing good moments | lower `detection.threshold` (e.g. 4.5) |
| Clip starts too late/early | `clip_before_seconds` / `clip_after_seconds` |
| Facecam not on top | add `facecam: {x, y, w, h}` for that streamer |
| Posting too often | `uploads.max_per_day`, `uploads.min_minutes_between` |

The config is reloaded automatically when you save it.

## Layout of the code

| File | Job |
|---|---|
| `clipper/orchestrator.py` | main loop: live checks, starts/stops stream workers, upload queue |
| `clipper/platforms/` | Twitch Helix API, YouTube `/live` page, Kick channel API |
| `clipper/chat/` | Twitch IRC, YouTube live chat, Kick websocket |
| `clipper/recorder.py` | streamlink → ffmpeg rolling 5-minute buffer |
| `clipper/detector.py` | turns chat/audio/viewer-clip signals into moments |
| `clipper/editor.py` | 9:16 render + karaoke captions |
| `clipper/metadata.py` | Claude scoring and titles (template fallback) |
| `clipper/uploaders/` | YouTube, TikTok, Metricool, local folder |
| `clipper/dashboard.py` | review web UI |

Run the tests with `pip install -r requirements.txt pytest && pytest`.
