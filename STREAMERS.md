# Streamers and permission

## Who to watch

GTA 6 launched on November 19, 2026 (rollout started November 18 UTC) and broke streaming
records on day one. The list in `config.example.yaml`:

| Streamer | Where they stream | Why they're on the list |
|---|---|---|
| IShowSpeed | YouTube `@IShowSpeed` | live on GTA 6 at launch; huge clip audience |
| CaseOh | Twitch `caseoh_` | one of the most-clipped streamers; big GTA audience |
| Kai Cenat | Twitch `kaicenat` | announced a 100% GTA 6 completion marathon |
| xQc | Kick / Twitch `xqc` | #1 GTA creator by hours watched in 2026 (22.7M) |
| Buddha | Twitch / Kick `buddha` | #2 GTA creator in 2026 (15.9M hours) |
| Lord Kebun | Kick `lordkebun` | #3 GTA creator in 2026 (12.3M hours) |
| Summit1g | Twitch `summit1g` | long-time GTA RP streamer |
| Typical Gamer | YouTube `@TypicalGamer` | large GTA YouTube channel |
| Sykkuno | YouTube `@Sykkuno` | popular GTA RP personality |

Off by default (not English, but among the biggest GTA 6 launch streams): alanzoka (PT, 362K
peak), TheGrefg (ES), DjMaRiiO (ES, 140K peak). Others with big launch peaks: Windah Basudara
(YouTube, Indonesian, 393K), PAULINHO O LOKO (YouTube, PT), Mythpat (Twitch, Hindi), rraenee (Kick, TR).

The dashboard's "Other big GTA 6 streams on Twitch right now" table updates every 15 minutes, so
you can spot new streamers and add them to `config.yaml`.

Double-check the handles above before relying on them. If one is wrong, the dashboard's
"Your streamer list" table will show that platform as never live.

Sources: [Streams Charts: top GTA creators of 2026](https://streamscharts.com/news/gta-6-streamers-top-gta-creators-2026),
[Streams Charts: GTA 6 streaming records](https://streamscharts.com/news/gta-6-streaming-records),
[WIN.gg: GTA 6 streaming records](https://win.gg/gta-6-smashes-streaming-records/),
[Sportskeeda: Kai Cenat 100% marathon](https://sportskeeda.com/us/streamers/news-kai-cenat-hold-gta-6-100-playthrough-marathon-upon-game-s-release).

## Permission (read this before turning uploads on)

Reposting someone's stream on your own channel without permission is copyright infringement on
their content. In practice that means Content ID claims, copyright strikes (three on YouTube and
the channel is deleted), and TikTok bans, and it can happen weeks after you post. GTA 6 footage
is also being watched closely: in August 2026 Twitch had xQc pull a VOD over how Rockstar's GTA 6
footage was used ([Dexerto](https://www.dexerto.com/gaming/xqc-furious-after-being-forced-to-take-down-gta-6-vod-by-twitch-3403752/)).
A credit line in the video does not make it permitted.

That's why the bot has `permission:` per streamer and `require_permission_to_upload: true` by
default: clips are still made so you can see what the bot finds, but nothing from a streamer is
uploaded until you set them to `granted`.

Ways to get permission:

1. **Official clipping programs.** Many big streamers pay clippers through official programs
   (often run on platforms like Whop "Content Rewards" or announced in their Discord). Joining
   one gives you explicit permission and often pays per view. Check each streamer's socials and
   Discord for "clippers" or "clip program".
2. **Ask directly.** DM the streamer's editor or manager (business email is usually on their
   YouTube "About" page). Keep the reply as proof.
3. **Streamers who allow it publicly.** Some say on stream or in their channel rules that
   anyone can clip them. Save a link to where they said it in `permission_note`.

```yaml
  - name: CaseOh
    platforms: {twitch: caseoh_}
    permission: granted
    permission_note: "Joined official clip program 2026-11-20, see Discord #clippers"
```
