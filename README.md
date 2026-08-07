# Sermon Podcast Pipeline

Turns new YouTube sermon videos into a Spotify-ready podcast, automatically,
on a schedule, at zero cost.

```
YouTube channel  ──▶  hourly GitHub Actions run
                        │
                        ├─ 1. read the channel's public Atom feed (no API key)
                        ├─ 2. yt-dlp + ffmpeg → 64 kbps mono mp3
                        ├─ 3. upload to archive.org (free, permanent)
                        ├─ 4. splice a new <item> into docs/feed.xml
                        └─ 5. commit state.json + feed.xml back to the repo
                                    │
                        GitHub Pages ▼
                        https://USER.github.io/REPO/feed.xml
                                    │
                              Spotify ▼  (crawls the feed, publishes the episode)
```

Every moving part is free: GitHub Actions (unlimited minutes on public repos),
GitHub Pages, and archive.org's free unlimited storage for openly-licensed
media. There is no podcast host and no paid tier anywhere in the chain.

---

> **The download step runs on your own Mac, not GitHub's servers.**
> YouTube blocks GitHub Actions runners outright — every player client is
> answered with "Sign in to confirm you're not a bot", because the whole
> datacenter IP range is blocked. Verified, not assumed: from a normal
> connection the same request succeeds. Everything else still lives on GitHub.
> Setup is in **[SETUP-RUNNER.md](SETUP-RUNNER.md)** and takes about 15 minutes.

## Before you start: two things worth knowing

**The repo must be public.** GitHub Pages on private repos requires a paid
plan, and Actions minutes are only unlimited on public repos. A public repo
keeps the whole thing free. Nothing sensitive lives in it — credentials are
GitHub Actions secrets, which stay encrypted and are never in the code.

**Spotify cannot take video episodes from an RSS feed.** Its RSS ingestion is
audio-only. Video episodes have to be uploaded directly in Spotify for
Creators, or come in through Spotify's separate YouTube-linking program.
Nothing a self-hosted feed can do will change that, so this pipeline publishes
audio.

---

## Setup

Nine steps, one time. Budget about 30 minutes, most of it waiting.

### 1. Put this project in a public GitHub repo

```bash
cd "/Users/adrieljavier/Desktop/Youtube to Spotify Podcast"
git add -A
git commit -m "Initial commit: sermon podcast pipeline"
```

Create a new **public** repository on GitHub, then:

```bash
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git
git branch -M main
git push -u origin main
```

### 2. Your YouTube channel ID — already done

`youtube.com/@newlifeoxnard` resolves to **`UCJd0tF3I0zKrN73_YNNIHqg`**, and
that is already set in [config.yml](config.yml). Verified against the live feed:

```
https://www.youtube.com/feeds/videos.xml?channel_id=UCJd0tF3I0zKrN73_YNNIHqg
```

You can still add it as a `YOUTUBE_CHANNEL_ID` secret in step 4 if you would
rather keep it out of the repo — the secret overrides the config file.

### 3. Create a free archive.org account and generate S3 keys

1. Sign up at <https://archive.org/account/signup>.
2. **Confirm the verification email.** Uploads fail with `403` until you do.
3. Go to <https://archive.org/account/s3.php> and click to generate keys.
4. Copy the **access key** and **secret key**. The secret is shown once.

Free, no card, no storage limit for this kind of use. Items stay up
permanently.

### 4. Add the repo secrets

In the repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add three:

| Secret name | Value |
| --- | --- |
| `IA_ACCESS_KEY` | archive.org access key from step 3 |
| `IA_SECRET_KEY` | archive.org secret key from step 3 |
| `YOUTUBE_CHANNEL_ID` | your `UC…` channel ID from step 2 |

Secrets are encrypted, are not readable after saving, and are not exposed to
pull requests from forks. Never put these values in `config.yml`.

### 5. Check `config.yml`

It is already filled in from what you gave me — channel ID, show title,
`newlifeoxnard.com` as the link, "New Life Oxnard" as the show author,
`adrieljavier@newlifeoxnard.com` as the owner, and `nlo-sermon` as the
archive.org identifier prefix. Read it over and adjust the show description to
taste.

Three settings worth understanding:

- **`podcast.owner_email`** — Spotify emails a verification code here to prove
  you own the show. It is set to your address; make sure that inbox is reachable.
- **`archive.identifier_prefix`** — archive.org identifiers are globally unique
  across the entire site, so this needs to be distinctive to you. `nlo-sermon`
  is, `sermon` would not be.
- **`youtube.require_title_pattern`** — set to `\|`, meaning only titles
  containing a pipe are treated as sermons. See below.

You can leave `site.base_url` blank — in Actions it is derived from the repo
name automatically. Set it if you want correct URLs when running locally.

#### How your titles are read

Your sermon titles follow a consistent shape and the pipeline leans on it:

```
Refresh | Steve Abraham
No Name | Mark 5:1-20 | Steve Abraham
FAMILY MATTERS | House Rules | Genesis 25-26 | Steve Abraham
JOHN 8:31-32 | BEN PRESCOTT // FIRST THINGS FIRST 2026
```

Two things fall out of that automatically:

- **Speaker attribution.** The last pipe-separated segment becomes the
  episode's `itunes:author`, so each episode is credited to whoever preached
  it — Steve Abraham, Phillip Trank, Ryan Abraham, Tony Lyons, Bernie
  Federmann, Ben Prescott, Tim Ross. Conference titles are trimmed at the `//`,
  and all-caps names are normalised to `Ben Prescott`. Anything that does not
  look like a name falls back to "New Life Oxnard".
- **Sermon detection.** Shorts, "New Life Worship Experience" and "FIRST
  WEDNESDAY" never contain a pipe, so `require_title_pattern` excludes them
  without needing a rule per case.

Checked against all 200 videos on your channel: 29 sermons identified, 19
Shorts and worship videos excluded, every speaker parsed correctly. If you ever
change how you title sermons, this is the setting to revisit.

### 6. Make the cover art

Spotify requires square artwork, 1400×1400 minimum, 3000×3000 recommended.
Build it from your channel's own artwork:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m scripts.fetch_cover
```

That writes `docs/cover.jpg`, padded to a compliant square. YouTube avatars are
often only 800×800, so it will warn if it had to upscale — if you have the
original logo at a higher resolution, use it instead:

```bash
.venv/bin/python -m scripts.fetch_cover --source ~/path/to/logo.png
```

### 7. Enable GitHub Pages

**Settings → Pages → Build and deployment**

- Source: **Deploy from a branch**
- Branch: **main**, folder: **/docs**
- Save.

After a minute, <https://YOUR-USERNAME.github.io/YOUR-REPO/> serves a landing
page, and your feed will live at:

```
https://YOUR-USERNAME.github.io/YOUR-REPO/feed.xml
```

It 404s until the first pipeline run creates it. That is expected.

### 8. Seed the state file

This is the step that stops the first run from dumping years of history into
your feed. `state.json` records every video the pipeline has decided about;
seeding it writes those decisions up front.

Your cutoff episode is on the channel: **`n2D6a5WNy84`** — "BOLD FAITH | Three
Keys to Victory | Joshua 3:1-17 | Steve Abraham", the 48th-newest upload.
Cutting there queues **29 sermons** and marks everything older as history.

Preview it first — nothing is written without `--apply`:

```bash
.venv/bin/python -m scripts.backfill --after-video-id n2D6a5WNy84
```

Read the output. It lists exactly what would be queued and what would be
marked as history. When it looks right:

```bash
.venv/bin/python -m scripts.backfill --after-video-id n2D6a5WNy84 --apply
git add state.json && git commit -m "Seed state from Jan 27 2026" && git push
```

Use the video ID rather than a date or title. Titles are not unique — "BOLD
FAITH" matches six videos across the Joshua series — and a date cutoff has to
fetch metadata for every candidate, which YouTube rate-limits.

Other ways to pick the cutoff:

```bash
# See the whole channel with its video IDs first.
.venv/bin/python -m scripts.backfill --list

# Cut at a date instead.
.venv/bin/python -m scripts.backfill --after-date 2026-01-27

# Publish nothing historic — start fresh from your next upload.
.venv/bin/python -m scripts.backfill --seed-only --apply
```

The backfill takes a couple of seconds. It decides purely from titles and
upload order; dates and durations are collected later, at publish time, where
they are needed anyway. Add `--fetch-metadata` if you want them recorded up
front, but be aware it makes one request per video and YouTube rate-limits
bursts, which can stretch the run out considerably.

#### Where the video list comes from

Two YouTube listings, used for different things:

- The **uploads playlist** is reliably newest-first, so it decides the cutoff
  and the publishing order. YouTube caps it at the **100 most recent** videos,
  which comfortably covers your cutoff at number 48. A cutoff further back than
  100 uploads is not possible; the script says so rather than guessing.
- The **Videos tab** returns the rest of the catalogue but in no dependable
  order, so it is used *only* to mark old videos as history — never to decide
  what to publish.

Together those cover all 300 videos on your channel: 29 queued, 19 filtered as
Shorts or worship, 252 recorded as history.

The hourly watcher, by contrast, uses YouTube's Atom feed — which only ever
exposes the latest 15 videos. That is ample once you are caught up, and it is
exactly why catching up from January needs this separate step.

### 9. Run it, then point Spotify at the feed

Trigger the first run by hand: **Actions → Publish sermon episodes → Run
workflow**. Try it with **dry run** ticked first to see what it would do, then
run it for real.

Each run publishes up to `run.max_episodes_per_run` episodes (default 4), so
your 29-episode backlog drains over roughly 8 hourly runs rather than in one
very long job. Watch the run summary for what it did.

Episodes are published oldest-first, so the backfill enters the feed in the
order the sermons were preached.

Once at least one episode is live in the feed:

1. Go to <https://podcasters.spotify.com> and sign in.
2. If the show already exists there: **Settings → Update RSS feed and hosting
   provider**. If it is new: add a podcast and choose the option for one that
   already has an RSS feed.
3. When asked who hosts it, pick the generic **Other** / not-listed option.
4. Paste your feed URL:
   `https://YOUR-USERNAME.github.io/YOUR-REPO/feed.xml`
5. Spotify emails a verification code to the `podcast.owner_email` address from
   step 5. Enter it.

Spotify re-crawls the feed on its own schedule — new episodes typically show up
within a few hours, sometimes faster.

---

## Everyday operation

Nothing. Publish a sermon to YouTube; within the hour it becomes an episode.

The pipeline commits `state.json` and `docs/feed.xml` only when something
actually changed, so the repo stays quiet between sermons.

Useful manual controls, all from **Actions → Publish sermon episodes → Run
workflow**:

- **dry run** — show what would be published without touching anything
- **limit** — publish more (or fewer) episodes in one run than the default

And locally:

```bash
# What is queued right now?
.venv/bin/python -m scripts.run --dry-run

# Retry a video that gave up after repeated failures.
.venv/bin/python -m scripts.run --retry VIDEO_ID

# Rewrite feed.xml's channel metadata after editing config.yml.
.venv/bin/python -m scripts.run --init
```

### What gets skipped automatically

`config.yml` filters out anything that is not a sermon:

- titles that do not match `youtube.require_title_pattern` (no pipe → Shorts,
  worship sets, "FIRST WEDNESDAY")
- titles matching `youtube.skip_title_patterns` (worship, Carpool Karaoke, …)
- videos shorter than `youtube.min_duration_seconds` (10 minutes)
- videos longer than `youtube.max_duration_seconds` (6 hours)
- livestreams still in progress — deferred, then picked up once they end

**If you change how you title sermons, update `require_title_pattern` too** —
it is the main thing keeping Shorts out of the feed.

---

## How failures are handled

A video that fails is never marked as done. It keeps its place in the queue and
is retried on the next run, and one bad video never stops the others.

Progress is saved in stages, so a retry never repeats expensive work:

| State | Meaning | On retry |
| --- | --- | --- |
| `queued` | known and eligible, not yet processed | download → upload → publish |
| `uploaded` | audio is on archive.org, feed entry not written | resumes at the feed step |
| `published` | live in the feed | done, never touched again |
| `skipped` | filtered out or before the backfill cutoff | done, never touched again |
| `parked` | failed `run.max_attempts` times in a row | ignored until you `--retry` it |

`state.json` is committed after every episode, so even a cancelled Actions job
keeps everything that had already succeeded. The feed is validated as
well-formed XML before it is committed — if it ever failed validation, nothing
is committed at all and the next run redoes the work safely (archive.org
uploads are idempotent, keyed on the video ID).

---

## Costs and limits

| Piece | Cost | Limit that matters |
| --- | --- | --- |
| GitHub Actions | free | self-hosted runners consume **no** minutes at all |
| Self-hosted runner | free | needs a Mac that stays awake and logged in |
| GitHub Pages | free | 100 GB/month bandwidth — the feed is a few KB |
| archive.org | free | no practical storage cap; audio is served from there |
| yt-dlp / ffmpeg | free | — |

Listener audio downloads come from archive.org, not GitHub, so Pages bandwidth
stays negligible no matter how many people subscribe.

Two scheduling realities worth knowing:

- GitHub's cron is best-effort. An "hourly" run can start 5–30 minutes late
  under load, and occasionally a run is dropped. The next one catches up.
- GitHub disables scheduled workflows after 60 days without repository
  activity. The workflow writes a heartbeat commit if the repo has been quiet
  for 45 days, so a long break between services will not silently switch the
  automation off.

---

## Troubleshooting

**`403` from archive.org.** The keys are wrong, or the account email was never
verified. Re-check both secrets and confirm the account at
<https://archive.org/account/s3.php>.

**`YouTube returned 404 for channel …`.** `YOUTUBE_CHANNEL_ID` is a handle or a
username, not the `UC…` ID. Get it from
<https://www.youtube.com/account_advanced>.

**Episodes are missing from the feed but marked published.** Check
`docs/feed.xml` in the repo, then your Pages URL. If the repo file is right and
the URL is stale, Pages is still deploying — give it a few minutes.

**Spotify will not accept the feed.** Confirm all four: the feed URL loads in a
browser, `docs/cover.jpg` loads and is square and ≥1400px, there is at least
one `<item>`, and `podcast.owner_email` is an address you can read.

**yt-dlp fails on every video.** YouTube changed something; yt-dlp fixes these
quickly. `requirements.txt` floats the version, so simply re-running the
workflow installs the current release.

**A run failed but published nothing.** Look at the run summary — each failed
video is listed with its error. Those videos are still queued.

---

## Project layout

```
config.yml                     all non-secret settings
state.json                     what has been processed (committed each run)
requirements.txt
docs/                          published by GitHub Pages
  index.html                   landing page
  feed.xml                     the podcast feed (created by the first run)
  cover.jpg                    cover art (created by scripts.fetch_cover)
scripts/
  run.py                       pipeline entry point
  backfill.py                  one-time state seeding / catch-up
  fetch_cover.py               channel artwork → compliant cover art
  config.py                    config + credential loading
  state.py                     the state machine
  youtube.py                   Atom feed + full-channel discovery
  audio.py                     yt-dlp/ffmpeg extraction
  archive_upload.py            archive.org upload + availability check
  feed.py                      incremental RSS 2.0 generation
.github/workflows/publish.yml  the hourly automation
```

Credentials appear in exactly one place: `IA_ACCESS_KEY`, `IA_SECRET_KEY` and
`YOUTUBE_CHANNEL_ID` as environment variables, read in
[scripts/config.py](scripts/config.py). Nothing is written to disk, and
`.gitignore` blocks the usual archive.org credential files from being committed
by accident.
