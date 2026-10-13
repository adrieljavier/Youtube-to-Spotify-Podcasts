# Running the pipeline on your own Mac

YouTube blocks GitHub's servers. Every request from a GitHub-hosted runner comes
back as *"Sign in to confirm you're not a bot"* — for every player client —
because YouTube blocks datacenter IP ranges wholesale. No amount of code can
work around it. From an ordinary internet connection the same request succeeds,
verified on this Mac.

So the download step runs on a machine you own. A **self-hosted runner** is a
small background program from GitHub that sits on your Mac, waits for jobs and
runs them locally. Everything else is unchanged: same repo, same hourly
schedule, same **Run workflow** button, same secrets stored in GitHub, same
commits pushed back. Only the machine doing the work is different.

**There is nothing to install by hand.** ffmpeg now comes from pip as part of
`requirements.txt`, so there is no Homebrew step and no admin password.

---

## Read this first: the one safety rule

A self-hosted runner executes workflow code **on your Mac**. On a public repo
that would be dangerous if strangers could trigger it — someone could open a
pull request that rewrites the workflow and have it run on your machine.

**This workflow cannot be triggered that way.** Look at the top of
[.github/workflows/publish.yml](.github/workflows/publish.yml):

```yaml
on:
  schedule:
  workflow_dispatch:
```

`schedule` and `workflow_dispatch` only. No `pull_request`, no `push`. Nothing
an outsider submits can start a run — only the clock, or you clicking the button.

**Never add a `pull_request` trigger to this workflow.** If you ever want one,
move the job back to `runs-on: ubuntu-latest` first.

As extra insurance, under **Settings → Actions → General**, leave *Fork pull
request workflows from outside collaborators* on its default,
**"Require approval for all outside collaborators."**

---

## Setup — two steps

### 1. Get a registration token

Open:

<https://github.com/adrieljavier/Youtube-to-Spotify-Podcasts/settings/actions/runners/new>

Choose **macOS** and **arm64**. The page shows a block of commands; you only
need one value from it. Find the line starting `./config.sh --url ...` and copy
the long string after `--token`.

That token expires in about an hour, so use it right away.

### 2. Run the setup script

In Terminal:

```bash
cd "/Users/adrieljavier/Desktop/Youtube to Spotify Podcast" && ./setup-runner.sh PASTE_TOKEN_HERE
```

That downloads GitHub's official runner, registers it against this repo,
installs it as a background service and starts it. Takes about a minute.

The runner is installed to **`~/actions-runner`**, deliberately outside this
project folder. macOS privacy protection (TCC) refuses background services
access to `~/Desktop`, `~/Documents` and `~/Downloads`, and this repo lives on
the Desktop — a runner installed here registers fine and then dies on startup
with `Operation not permitted`. The runner checks the repo out into its own
`_work` directory, so it has no reason to sit inside the project.

When it finishes, check **Settings → Actions → Runners**. Your Mac should show
a green **Idle** — connected and waiting for work.

---

## Keeping the Mac awake

A sleeping Mac cannot run the job. Missed hours are not fatal — the queue simply
waits and catches up — but a Mac that always sleeps never publishes anything.

This Mac was set to `sleep 1`: one minute of idle and it sleeps, on wall power
as well as battery. That would have limited publishing to moments when someone
was actively using it.

**This is already installed** —
[mac/com.newlifeoxnard.podcast-caffeinate.plist](mac/com.newlifeoxnard.podcast-caffeinate.plist),
a login item running `caffeinate -s`. It holds the machine awake **only while on
wall power**, so it can never flatten the battery, and the display is still free
to sleep. Changing `pmset` directly would have needed an admin password;
`caffeinate` does the same job from user space.

```bash
pmset -g assertions | grep caffeinate     # confirm it is holding
```

To remove it:

```bash
launchctl bootout gui/$(id -u)/com.newlifeoxnard.podcast-caffeinate
rm ~/Library/LaunchAgents/com.newlifeoxnard.podcast-caffeinate.plist
```

**Keep the Mac plugged in.** That is the one part no software can arrange: on
battery the agent deliberately does nothing, so an unplugged laptop still
sleeps and publishing pauses until it is powered again.

## Scheduled runs arrive late (this is GitHub, not the runner)

The workflow asks for a run every hour, but GitHub delivers `schedule:` triggers
best-effort, and for this repo it delivers only a fraction of them. Measured
from 2026-09-01 to 2026-10-12: about 4-6 runs a day against 24 requested, a
median gap of 4-5 hours, and two gaps of ~20 hours. A new sermon can wait most
of a day to be picked up. Nothing is lost - the pipeline is idempotent and each
run resumes where the last stopped - it is latency only.

**It is almost certainly not the runner.** The runner process stayed up for
weeks and logged "Listening for Jobs" throughout. We also tested the obvious
theory - that its long-poll connection to GitHub silently dies (there are open
upstream reports of that: github.com/actions/runner issues #3904, #4668,
#4703) - by installing a watchdog that restarted the listener whenever it had
been idle for 20 minutes. It fired 410 times in 8 days. Only 13 of those
restarts (3%) were followed by a run within 10 minutes, which is chance, and
the median gap got *worse* (252 -> 341 min). If a dead connection were the
cause, restarts would have been followed by jobs. The watchdog was retired on
2026-10-05 and removed from the repo; it is in git history if you ever want it
(`mac/podcast-runner-watchdog.sh`).

The earlier version of this section claimed the stuck connection was the cause
and that the watchdog fixed it. That was never verified - a restart was seen to
reconnect, but nobody saw one pick up a stalled job - and the data above
disproves it. Left here so nobody re-derives it.

**Mitigation (2026-10-12):** `publish.yml` now has two cron ticks, `:23` and
`:53`. If GitHub drops most ticks, more ticks means more runs. Overlap is
harmless: the `concurrency` group queues runs, and a run with nothing new
finishes in seconds and commits nothing. To check whether it helped, count runs
per day (baseline before the change: 4-6):

```bash
ls ~/actions-runner/_diag/Worker_*.log | sed -E 's/.*Worker_([0-9]{8})-.*/\1/' | sort | uniq -c | tail -14
```

**Need a run now** (new sermon just posted): Actions -> Publish sermon
episodes -> Run workflow.

**`svc.sh stop` / `svc.sh start` do not reliably restart the runner** in this
setup - `stop` (`launchctl unload`) leaves the service registered but
stopped, and the follow-up `start` (`launchctl load`) then fails with
`Load failed: 5: Input/output error` against an already-registered label.
Use `launchctl kickstart -k gui/$(id -u)/actions.runner.adrieljavier-Youtube-to-Spotify-Podcasts.MacBook_Pro`
if you ever do need to restart it.

---

## Then run it

**Actions → Publish sermon episodes → Run workflow.**

The first run creates a virtualenv and installs packages, so it takes a couple
of extra minutes. After that they are reused.

Measured on this Mac: a 34-minute sermon downloads and converts in about 20
seconds, producing a 16 MB mono mp3. Most of each run is the archive.org
upload. Four episodes per run, and your 29-episode backlog clears over roughly
8 hourly runs.

---

## Everyday life

Nothing. The runner sits idle, wakes each hour, publishes anything new.

```bash
cd ~/actions-runner
./svc.sh status     # is it running?
./svc.sh stop       # pause the automation
./svc.sh start      # resume
```

### If runs stop happening

Symptoms first, since they look alike from the Actions tab: jobs that sit
**queued** and then get **cancelled** an hour later mean no runner is available.
That is not an error in the pipeline — it is the job waiting for a machine.

- **Runner shows "Offline"** in Settings → Actions → Runners — the Mac is
  asleep, off, or logged out. Wake it, then check `./svc.sh status`.
- **The Mac restarted and nobody logged in** — the service starts at login, not
  at boot. Log in and it resumes by itself.
- **You moved or renamed `~/actions-runner`** — the service points at the old
  path. Run `./svc.sh uninstall`, delete the folder, and redo the two setup
  steps. (Moving the *project* folder is fine; the runner is independent of it.)
- **`Operation not permitted` in `~/Library/Logs/actions.runner.*/stderr.log`** —
  the runner ended up somewhere macOS shields from background services. Move it
  to `~/actions-runner`: `./svc.sh uninstall`, move the folder, then
  `./svc.sh install && ./svc.sh start`. Registration survives the move, so no
  new token is needed.

### Moving it to a different Mac

On the old machine: `cd actions-runner && ./svc.sh uninstall`, then remove the
runner under Settings → Actions → Runners. On the new one, clone the repo and
repeat the two setup steps. Nothing in the repo needs to change.
