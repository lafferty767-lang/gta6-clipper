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

## Streamers with paid GTA 6 clipping campaigns

Checked 2026-11-20 on [Content Rewards](https://contentrewards.com/articles/gta-6-clipping) and
[Clyper](https://www.clyper.io/clipping-programs). These are run by clipping agencies, joining is free
with no application, and being in the campaign is your permission to post. Each one wants you to
submit every post's link on Content Rewards (Reach: within 30 minutes of posting); the bot doesn't do that.

| Streamer | Campaign | Pay | Notes |
|---|---|---|---|
| Kai Cenat | [$10,000 GTA 6 pool](https://contentrewards.com/c/campaigns/1d2bcb00-c904-415f-aa82-f95b96baad86) (Clipped & Shipped) | split by views, 750K-view minimum | 18+, ad disclosure |
| Lacy | [$12,500 GTA VI marathon pool](https://contentrewards.com/c/campaigns/a5c63fad-cd53-4b81-9305-4a2d1696872a) (Reach) | split by views | name + #Lacy, no Kick casino streams |
| Clix | [$5,000 GTA VI marathon pool](https://contentrewards.com/c/campaigns/985dc674-c75e-44fc-ac87-1b1506324389) (Reach) | split by views, 400K minimum | name + #Clix |
| YourRAGE | [$5,000 GTA 6 pool](https://contentrewards.com/c/campaigns/be9b123e-ea47-4305-b6c5-af81f503b247) (Reach) | split by views, 500K minimum | |
| AsianJeff | [clipping campaign](https://contentrewards.com/c/campaigns/bbd3a963-9a23-457c-bc15-6697971d9902) (Reach) | $1 per 1,000 views | |
| KreekCraft | [GTA 6 clipping](https://contentrewards.com/c/campaigns/2eaa14fb-6927-4136-897e-b096e0d907d6) (ClipLaunch) | $1 per 1,000 views | his stream overlay must show |

Pools pay only once your approved views pass the minimum. Campaigns close when budgets run out or
marathons end, so re-check before relying on one.

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
