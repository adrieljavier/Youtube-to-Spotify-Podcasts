# Complete Runbook

Everything needed to build this system from nothing, in order, including every
obstacle we actually hit and why each one matters. If you were starting again —
another church, another channel, a rebuild — this is the document to follow.

---

## What it does

```
YouTube channel
      │  hourly, on a Mac you own
      ▼
1. read the channel's public Atom feed          (no API key, no account)
2. yt-dlp + ffmpeg  →  128 kbps mono mp3
3. upload to archive.org                        (free, permanent, unlimited)
4. splice a new <item> into docs/feed.xml       (incremental, never rebuilt)
5. commit state.json + feed.xml back to the repo
      │
      ▼  GitHub Pages
https://USER.github.io/REPO/feed.xml
      │
      ▼  crawled on their own schedule
Spotify · Apple Podcasts · anything else that takes RSS
```

Nothing in the chain costs money. No podcast host, no paid storage, no paid
compute.

---

# Part 1 — One-time setup

## Step 1: Create a public GitHub repository

**It must be public.** GitHub Pages on a private repo requires a paid plan, and
Actions minutes are only unlimited on public repos. Nothing sensitive lives in
the repo — credentials are GitHub Actions secrets, which stay encrypted.

Create it on github.com, then locally:

```bash
cd "/path/to/project"
git init && git add -A && git commit -m "Initial commit"
git remote add origin https://github.com/USER/REPO.git
git branch -M main
git push -u origin main
```

### Gotcha: your GitHub password will not work

GitHub disabled password authentication for git in 2021. When git prompts for
"Password" it wants a **Personal Access Token**.

Create one at <https://github.com/settings/tokens/new>. Tick **two** scopes:

- **`repo`** — to push code
- **`workflow`** — to push `.github/workflows/*.yml`

Miss `workflow` and the push uploads everything, then fails at the last moment
with *"refusing to allow a Personal Access Token to create or update workflow …
without `workflow` scope"*. The `repo` scope alone does not cover workflow
files; it is a deliberate separate permission, because a workflow file can run
code on your account.

Paste the token as the **password**, not your account password. macOS saves it
to the keychain, so this is a one-time step.

## Step 2: Find the YouTube channel ID

Go to <https://www.youtube.com/account_advanced> while signed in as the channel.
Copy the 24-character ID beginning `UC`.

An `@handle` will not work — the Atom feed only accepts the channel ID. Verify
by opening this in a browser; it should return XML:

```
https://www.youtube.com/feeds/videos.xml?channel_id=UC...
```

## Step 3: Create an archive.org account and generate S3 keys

1. Sign up at <https://archive.org/account/signup>
2. **Click the link in the verification email.** Uploads fail with `403` until
   you do, and the error does not say why.
3. Go to <https://archive.org/account/s3.php> and generate keys
4. Copy both the access key and the secret key — the secret is shown once

Free, no card, no practical storage limit, and items are permanent.

## Step 4: Add the repository secrets

**Settings → Secrets and variables → Actions → New repository secret.**

| Secret | Value |
| --- | --- |
| `IA_ACCESS_KEY` | archive.org access key |
| `IA_SECRET_KEY` | archive.org secret key |

### Gotcha: repository secrets, not environment secrets

That page offers both. **Repository secrets** are available to any workflow.
Environment secrets are scoped to a named deployment environment and are
invisible unless the workflow declares `environment:` — which this one does
not. Choose wrong and every run fails with a credentials error.

## Step 5: Fill in `config.yml`

Set the channel ID, show title, description, website link, author, owner name
and owner email, copyright, and `archive.identifier_prefix`.

Three that matter more than they look:

- **`podcast.owner_email`** — Spotify *and* Apple email their ownership
  verification codes to this address. It must be an inbox you can read.
- **`archive.identifier_prefix`** — archive.org identifiers are globally unique
  across the entire site. Use something distinctive (`nlo-sermon`), never
  something generic (`sermon`), or uploads collide with a stranger's.
- **`youtube.require_title_pattern`** — an allow-list regex deciding what counts
  as a sermon. See Step 6.

## Step 6: Work out your title convention

This is the single most important configuration decision, and it is worth
studying your actual channel before choosing.

New Life Oxnard titles sermons like this:

```
Refresh | Steve Abraham
No Name | Mark 5:1-20 | Steve Abraham
FAMILY MATTERS | House Rules | Genesis 25-26 | Steve Abraham
JOHN 8:31-32 | BEN PRESCOTT // FIRST THINGS FIRST 2026
```

Two things fall out of that automatically:

- **Sermon detection.** Shorts, "New Life Worship Experience" and "FIRST
  WEDNESDAY" never contain a pipe, so `require_title_pattern: "\\|"` excludes
  every one of them without needing a rule per case. **An allow-list is far more
  reliable than blacklisting** — you cannot enumerate every kind of non-sermon
  upload, but you can describe what a sermon looks like.
- **Speaker attribution.** The last pipe-separated segment becomes the episode's
  `itunes:author`. Conference titles are trimmed at the `//`, and all-caps names
  are normalised (`BEN PRESCOTT` → `Ben Prescott`). Anything that does not look
  like a person's name falls back to the show author.

Verified against all 300 videos on the channel: 29 sermons identified, 19
Shorts and worship videos excluded, every speaker parsed correctly.

Belt and braces on top: `skip_title_patterns` for known non-sermons, plus
`min_duration_seconds` (600) and `max_duration_seconds` (21600).

## Step 7: Make the cover art

Spotify and Apple both require square artwork, 1400×1400 minimum, 3000×3000
recommended, RGB JPEG or PNG.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m scripts.fetch_cover
```

That pulls the channel's own artwork and letterboxes it to a compliant square.
YouTube avatars are often only 800×800, so it warns when it upscales. For a
flat logo the upscale is invisible; for a photo, supply the original:

```bash
.venv/bin/python -m scripts.fetch_cover --source ~/path/to/logo.png
```

**If the show already exists elsewhere, use its existing artwork** so the switch
is invisible to listeners. See Step 10.

## Step 8: Enable GitHub Pages

**Settings → Pages → Build and deployment**

- Source: **Deploy from a branch**
- Branch: **main**, folder: **/docs**

The feed 404s until the first successful run creates it. That is expected.

## Step 9: Seed the state file

`state.json` records a decision about every video, so the first run does not
dump years of history into the feed.

```bash
# See the channel, newest first, with video IDs
.venv/bin/python -m scripts.backfill --list

# Preview a cutoff (nothing is written without --apply)
.venv/bin/python -m scripts.backfill --after-video-id VIDEO_ID

# Commit that decision
.venv/bin/python -m scripts.backfill --after-video-id VIDEO_ID --apply
git add state.json && git commit -m "Seed state" && git push
```

Use `--after-video-id`. Titles are not unique — "BOLD FAITH" matched six videos
in a single series — and a date cutoff needs a metadata request per candidate,
which YouTube rate-limits.

### Gotcha: the Videos tab is not in date order

There are two YouTube listings and they behave completely differently:

- The **uploads playlist** (`UU` + channel ID without `UC`) is reliably
  newest-first. **YouTube caps it at 100 entries.**
- The **Videos tab** returns the rest of the catalogue but in whatever order the
  channel owner picked — on this channel, the two listings were entirely
  disjoint, 100 and 200 videos with zero overlap.

So ordering decisions only ever use the uploads playlist; the Videos tab is used
solely to mark old videos as history. Getting this wrong publishes your
catalogue in the wrong order and cuts at the wrong episode.

## Step 10: If the show already exists somewhere — merge first

**This is the step that can destroy years of work.**

Pointing Spotify or Apple at a new RSS URL makes that feed the *entire* show.
Any episode not in it is dropped. This show had 300 episodes on Squarespace going
back to 2019; switching without merging would have deleted all of them.

```bash
# Preview
.venv/bin/python -m scripts.import_feed --url "https://OLD-HOST/feed?format=rss"
# Merge
.venv/bin/python -m scripts.import_feed --url "https://OLD-HOST/feed?format=rss" --apply
```

Episodes are copied **verbatim**, which matters twice over:

- The original `<guid>` is preserved, so apps recognise each episode as the one
  they already have. Rewriting GUIDs would notify every subscriber about
  hundreds of "new" episodes.
- The original `<enclosure>` is preserved, so the audio keeps serving from where
  it already lives. Nothing is re-hosted or re-uploaded.

Then **match the channel metadata to the existing show** — title, description
and artwork — so listeners see nothing change at all.

> **Keep the old host's account alive.** Those imported episodes still point at
> its audio files. Cancelling it breaks them. Migrating that audio to
> archive.org is possible but optional.

## Step 11: Set up the runner on your own Mac

### Gotcha: YouTube blocks GitHub's servers outright

Every request from a GitHub-hosted runner returns *"Sign in to confirm you're
not a bot"*, for every player client, because YouTube blocks datacenter IP
ranges wholesale. This is not a bug and no code fixes it. From an ordinary
internet connection the identical request succeeds.

So the job runs on a machine you own, via a **self-hosted runner** — a small
background program that waits for jobs and runs them locally. Everything else
stays on GitHub: same schedule, same secrets, same Run workflow button.

Get a token from **Settings → Actions → Runners → New self-hosted runner**
(choose macOS + your architecture), copy the value after `--token`, then:

```bash
./setup-runner.sh PASTE_TOKEN_HERE
```

**The token expires in about an hour and is single-use.** A stale one fails with
a bare `404 Not Found` from `runner-registration`.

### Gotcha: never install the runner in Desktop, Documents or Downloads

macOS privacy protection (TCC) refuses background services access to those
folders. A runner installed there registers perfectly, then dies on startup:

```
getcwd: cannot access parent directories: Operation not permitted
/bin/bash: .../runsvc.sh: Operation not permitted
```

The setup script installs to `~/actions-runner` for exactly this reason. If you
ever need to move an existing one, registration survives the move — uninstall
the service, move the folder, reinstall. No new token needed.

### Gotcha: a sleeping Mac publishes nothing

This Mac was set to `sleep 1` — one minute of idle, on wall power as well as
battery. That limits publishing to moments when somebody is actively using it.

`mac/com.newlifeoxnard.podcast-caffeinate.plist` is a login item running
`caffeinate -s`, which holds the machine awake **only on wall power** so it can
never flatten the battery. The display is still free to sleep. Changing `pmset`
directly needs an admin password; `caffeinate` does the same job from user space.

```bash
pmset -g assertions | grep caffeinate     # confirm it is holding
```

**Keep the Mac plugged in.** That part no software can arrange.

## Step 12: Run it

**Actions → Publish sermon episodes → Run workflow.**

Use the left sidebar to select the workflow first — the Run workflow button only
appears once a specific workflow is selected.

**"Re-run jobs" is not the same thing.** It replays the original commit and will
not pick up new code. For anything you have just pushed, start a fresh run.

Each run publishes up to 4 new episodes plus up to 12 already-uploaded ones, so
a backlog drains over several hours rather than one enormous job.

## Step 13: Submit to Spotify

1. <https://podcasters.spotify.com> → sign in
2. Existing show: **Settings → Update RSS feed and hosting provider**.
   New show: add a podcast that already has an RSS feed.
3. Hosting provider: pick the generic **Other** / not-listed option
4. Paste the feed URL
5. Enter the code emailed to `podcast.owner_email`

Ignore any "Host with us" offer — moving to Spotify hosting removes the RSS feed
this whole system depends on.

### Gotcha: one video episode rejects the entire feed

> We're unable to accept podcasts with videos.

One imported episode from 2020 was an 81 MB mp4. Spotify refuses the whole feed
over a single video enclosure — 322 good episodes blocked by one bad one.

`scripts/rehost.py` runs as a workflow step, finds any non-audio enclosure,
strips the video track, re-encodes, uploads to archive.org and rewrites the
enclosure in place — keeping the original GUID. It is a no-op unless something
needs fixing.

## Step 14: Submit to Apple Podcasts

The same feed. One feed serves every directory.

1. <https://podcastsconnect.apple.com> → sign in with an Apple ID
   (**two-factor authentication must be enabled**)
2. **+** → **New Show** → **Add a show with an RSS feed**
3. Paste the feed URL
4. Click the verification link emailed to `podcast.owner_email`
5. **Submit for review** — approval usually takes a few days

Free. (Apple Podcasts *Subscriptions*, for paid content, is the $19.99/year
product. You do not need it.)

### Gotcha: "An error has occurred. Try again later."

Apple's catch-all. It is not a validation message — genuine feed problems get
specific errors. When we hit it, the feed validated cleanly against every
documented Apple requirement, served in 0.12s as `application/xml`, and was not
a duplicate of an existing show.

**Retrying later worked.** Before assuming the feed is at fault: retry, check for
unaccepted agreements on the account, and try a different browser.

The same URL also works for Pocket Casts, Overcast, Amazon Music, iHeart and
YouTube Music. One submission each, nothing new to build.

---

# Part 2 — Design decisions worth keeping

## Audio quality

| | |
| --- | --- |
| YouTube source ceiling | 129 kbps AAC stereo |
| Existing Squarespace catalogue | 192 kbps stereo |
| Chosen setting | **128 kbps mono** |

64 kbps mono is a common podcast default and is fine for speech, but against a
192 kbps back catalogue the step down is audible. 128 kbps mono gives the single
channel more bits than the source spends on two, so it is effectively
transparent for preaching. A 45-minute sermon lands around 40 MB.

Episodes record what settings they were made at, so changing
`audio.bitrate_kbps` marks older ones stale and `scripts/reencode.py` brings
them up to date a few per run — replacing the archive.org file in place, keeping
the GUID, so nothing 404s and no subscriber is re-notified.

## Never block on archive.org

A fresh archive.org item takes **10–30 minutes** to reach the download nodes. An
early version waited 600 seconds and treated the timeout as a failure, which
discarded the record of four perfectly good uploads.

Uploads are now recorded the instant the S3 PUT succeeds. Availability is
checked briefly afterwards; if the file is not serving yet the episode sits in
`uploaded` and a later run writes its feed entry — no re-download, no re-upload.

## Livestreams renamed after the fact

Sunday services are streamed under a placeholder title and retitled to the
sermon afterwards. Discovery originally skipped anything it already knew, so the
correct skip made *during* the stream became permanent — and would have silently
swallowed every future sermon. No error, no failed run, just nothing appearing.

Title-based skips are now re-judged while the video is still in the channel
feed. Skips for length or the backfill cutoff are left settled: those facts do
not change when someone edits a title.

## Failure handling

A video that fails is never marked done. Progress is saved in stages so a retry
never repeats expensive work:

| State | Meaning | On retry |
| --- | --- | --- |
| `queued` | known and eligible | download → upload → publish |
| `uploaded` | audio on archive.org, feed entry pending | resumes at the feed step |
| `published` | live in the feed | never touched again |
| `skipped` | filtered out, or before the cutoff | re-judged only if renamed |
| `parked` | failed `max_attempts` times | ignored until `--retry` |

`state.json` is committed after every episode, so a cancelled job keeps
everything that succeeded. The feed is validated as well-formed XML before it is
committed — if validation fails, nothing is committed and the next run redoes
the work safely, because archive.org uploads are idempotent.

## Security

A self-hosted runner executes workflow code on your Mac. On a public repo that
would be dangerous if strangers could trigger it.

```yaml
on:
  schedule:
  workflow_dispatch:
```

`schedule` and `workflow_dispatch` only — no `pull_request`, no `push`. Nothing
an outsider submits can start a run. **Never add a `pull_request` trigger to
this workflow** without first moving the job back to `ubuntu-latest`.

---

# Part 3 — Everyday operation

Nothing. Post the sermon to YouTube; it appears on Spotify and Apple by itself.

**Keep the pipe in your sermon titles** — `Title | Scripture | Speaker`. That is
what marks a video as a sermon and identifies who preached it.

GitHub emails you automatically if any run fails, so there is nothing to watch.

```bash
cd ~/actions-runner
./svc.sh status | stop | start        # the runner

cd "/path/to/project"
.venv/bin/python -m scripts.run --dry-run          # what is queued
.venv/bin/python -m scripts.run --retry VIDEO_ID   # un-park a video
.venv/bin/python -m scripts.reencode               # what is out of date
```

## When things stop

**Jobs queued then cancelled** — no runner available. The Mac is asleep, off, or
logged out. This is not a pipeline error; the job is waiting for a machine.

**"Sign in to confirm you're not a bot"** — YouTube rotated which player clients
work. Reorder `youtube.player_clients` in `config.yml`. Measured on 2026-08-07,
`android_vr` was the only client serving audio; `tv_embedded` is retired,
`web_embedded` returns error 152, and the rest hit throttling. Expect to revisit
this every few months. It is the one foreseeable maintenance task, and the price
of the zero-budget constraint.

**`Operation not permitted` in the runner log** — it is in a TCC-protected
folder. Move it to `~/actions-runner`.

---

# Part 4 — Rebuilding from scratch with one prompt

Paste this to an AI coding assistant with filesystem and shell access. It
encodes the requirements *and* the obstacles, so the rebuild does not have to
rediscover them.

````text
Build a zero-cost pipeline that turns new YouTube videos from a channel into
episodes of a podcast RSS feed that Spotify and Apple Podcasts subscribe to.
Python. No paid services anywhere.

ARCHITECTURE
- Watch the channel's public Atom feed (youtube.com/feeds/videos.xml?channel_id=UC...),
  no API key. Track every video's decision in a committed state.json.
- Extract audio with yt-dlp + ffmpeg at 128 kbps mono mp3.
- Upload each mp3 to archive.org (free, permanent) via the internetarchive
  library. Identifier: <prefix>-<youtube_video_id>, so it is idempotent.
- Maintain docs/feed.xml as valid RSS 2.0 with iTunes tags. Update it
  INCREMENTALLY - never regenerate. Host it on GitHub Pages from /docs.
- GitHub Actions workflow, hourly + workflow_dispatch, commits state.json and
  docs/feed.xml back to the repo. Credentials only via Actions secrets.

CONSTRAINTS THE OBVIOUS IMPLEMENTATION GETS WRONG - handle all of these:

1. YouTube BLOCKS GitHub-hosted runners ("Sign in to confirm you're not a bot")
   because they are datacenter IPs. The job must run on a self-hosted runner on
   an ordinary connection. Provide a one-command setup script. Install it to
   ~/actions-runner, NEVER inside Desktop/Documents/Downloads - macOS TCC blocks
   background services there and the runner dies with "Operation not permitted".
2. Try multiple yt-dlp player clients in order and fall through on rejection;
   which ones work changes every few months. Make the list configurable.
3. archive.org takes 10-30 minutes to serve a fresh upload. NEVER block waiting
   for it. Record the upload the moment it succeeds, check availability briefly,
   and if it is not serving yet leave the episode in an "uploaded" state for a
   later run to finish. Never re-download or re-upload.
4. The channel's Videos tab is NOT in date order. Use the uploads playlist
   (UU + channel id minus UC) for anything order-dependent; it is capped at 100
   entries. Use the Videos tab only to mark old videos as history.
5. Detect sermons with an ALLOW-LIST title regex, not a blacklist. Parse the
   speaker from the title for per-episode itunes:author.
6. Livestreams are published under a placeholder title and renamed afterwards.
   A title-based skip must be RE-JUDGED if the video is later renamed, while it
   is still in the channel feed. Do not re-judge length or cutoff based skips.
7. If the show already exists on another host, import that feed FIRST, copying
   items verbatim so <guid> and <enclosure> are preserved - switching the feed
   URL otherwise DELETES the entire back catalogue from Spotify. Match the
   existing show's title and artwork so the switch is invisible.
8. Spotify rejects the whole feed if ANY episode has a video enclosure. Detect
   non-audio enclosures, strip the video track, re-encode, re-host, and rewrite
   the enclosure in place keeping the guid.
9. Record what bitrate/channels each episode was encoded at, so changing the
   setting marks older episodes stale and a bounded job brings them up to date
   in place - same URL, same guid.
10. Fail gracefully per episode: never mark a failed video done, never let one
    failure stop the run, commit partial progress, validate the feed as
    well-formed XML before committing.
11. The workflow must have NO pull_request trigger - a self-hosted runner on a
    public repo would otherwise execute strangers' code.

Also write: a seeding/backfill script with a cutoff so the first run does not
publish the whole channel history, a cover-art script producing a compliant
square 3000x3000 JPEG, and a README covering the manual steps (archive.org
account + S3 keys, GitHub Pages, repo secrets, PAT needing BOTH repo and
workflow scopes, Spotify and Apple submission).
````

---

## Final state of this build

| | |
| --- | --- |
| Episodes live | 324 — January 2019 to August 2026 |
| From YouTube, automated | 30 |
| Imported back catalogue | 294 |
| Audio | 128 kbps mono, all episodes |
| Errors | 0 |
| Directories | Spotify ✓ · Apple Podcasts ✓ |
| Ongoing cost | £0 |
