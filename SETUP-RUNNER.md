# Running the pipeline on your own Mac

YouTube blocks GitHub's servers. Every request from a GitHub-hosted runner comes
back as *"Sign in to confirm you're not a bot"*, for every player client, because
YouTube blocks datacenter IP ranges wholesale. That is not something the code can
work around.

The fix is to run the job on a machine with an ordinary internet connection. A
**self-hosted runner** is a small background program from GitHub that sits on
your Mac, waits for jobs, and runs them locally. Everything else stays exactly
as it is — same repo, same hourly schedule, same **Run workflow** button, same
secrets stored in GitHub, same commits pushed back. The only difference is
which machine does the downloading.

---

## Read this first: why a public repo + your Mac needs one precaution

A self-hosted runner executes whatever the workflow says on **your machine**. On
a public repo that would be dangerous if strangers could trigger it — someone
could open a pull request that changes the workflow and have it run code on your
Mac.

**This workflow cannot be triggered that way.** Look at the top of
[.github/workflows/publish.yml](.github/workflows/publish.yml):

```yaml
on:
  schedule:
  workflow_dispatch:
```

`schedule` and `workflow_dispatch` only. There is no `pull_request` or `push`
trigger, so nothing an outsider submits can start a run — only the clock, or you
clicking the button.

**The one rule: never add a `pull_request` trigger to this workflow.** If you
ever want that, move the job back to `runs-on: ubuntu-latest` first.

As extra insurance, go to **Settings → Actions → General** and under *Fork pull
request workflows from outside collaborators* leave it on the default,
**"Require approval for all outside collaborators."**

---

## Step 1 — Install ffmpeg

ffmpeg does the audio conversion. Your Mac doesn't have it yet.

Open Terminal and install Homebrew (a package installer for macOS):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

It will ask for your Mac password and take a few minutes. Then:

```bash
brew install ffmpeg
```

Check it worked:

```bash
ffmpeg -version
```

You want a version line, not "command not found."

---

## Step 2 — Register the runner

1. In your repo, go to **Settings → Actions → Runners**
2. Click **New self-hosted runner**
3. Choose **macOS**, and the architecture matching your Mac — **arm64** for
   Apple Silicon (M1/M2/M3/M4), **x64** for older Intel Macs. If unsure:
   Apple menu → About This Mac. "Apple M-something" means arm64.

GitHub then shows a block of commands **containing a registration token unique
to you**. Copy them from that page — don't use any you find elsewhere, the token
is what proves it's your repo.

They look roughly like this:

```
mkdir actions-runner && cd actions-runner
curl -o actions-runner-osx-arm64-X.XXX.X.tar.gz -L https://github.com/actions/runner/releases/download/...
tar xzf ./actions-runner-osx-arm64-X.XXX.X.tar.gz
./config.sh --url https://github.com/adrieljavier/Youtube-to-Spotify-Podcasts --token XXXXXXXX
```

Run them in Terminal in order. `config.sh` asks three questions — **press Enter
for all three** to accept the defaults:

- runner group → Enter
- name of runner → Enter
- work folder → Enter

When it finishes you'll see "Connected to GitHub".

---

## Step 3 — Make it run in the background, permanently

Still in the `actions-runner` folder:

```bash
./svc.sh install
./svc.sh start
```

That registers it as a macOS background service, so it starts on its own when
you log in and keeps running after you close Terminal.

Check it:

```bash
./svc.sh status
```

Back in **Settings → Actions → Runners**, your runner should now show a green
**Idle**. Idle means connected and waiting for work — that's what you want.

---

## Step 4 — Stop the Mac from sleeping

A sleeping Mac can't run the job. Missed hours aren't fatal — the queue just
waits — but a Mac that's always asleep will never publish anything.

**System Settings → Displays → Advanced**, or **Battery → Options** on a laptop:

- Turn **off** "Put hard disks to sleep when possible"
- Set **"Prevent automatic sleeping when the display is off"** to on (this is
  the important one — the screen can sleep, the Mac must not)
- On a laptop, keep it plugged in; these settings usually only apply on power

The display going dark is fine. The machine going to sleep is not.

---

## Step 5 — Run it

**Actions → Publish sermon episodes → Run workflow.**

The first run installs Python packages into the runner's folder and takes a
couple of extra minutes. After that they're reused.

Expect roughly 3–6 minutes per sermon — download, convert, upload to
archive.org. Four episodes per run, so 15–25 minutes, and your 29-episode
backlog clears over about 8 hourly runs.

---

## Everyday life

Nothing. The runner sits idle, wakes each hour, publishes anything new.

Useful commands, all from inside the `actions-runner` folder:

```bash
./svc.sh status     # is it running?
./svc.sh stop       # pause the automation
./svc.sh start      # resume
```

### If runs stop happening

- **Runner shows "Offline"** in Settings → Actions → Runners — the Mac is
  asleep, off, or logged out. Wake it and check `./svc.sh status`.
- **The Mac was restarted and nobody logged in** — the service starts at login,
  not at boot. Log in and it comes back on its own.
- **You moved or renamed the `actions-runner` folder** — the service points at
  the old path. Easiest fix is `./svc.sh uninstall` and redo steps 2–3.

### Moving it to a different Mac

Run `./svc.sh uninstall` on the old machine, remove the runner in Settings →
Actions → Runners, then repeat steps 1–3 on the new one. Nothing in the repo
needs to change.
