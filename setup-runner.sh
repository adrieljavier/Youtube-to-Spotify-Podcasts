#!/usr/bin/env bash
#
# One-shot setup for the self-hosted GitHub Actions runner.
#
#   ./setup-runner.sh <REGISTRATION-TOKEN>
#
# Get the token from your repo:
#   Settings -> Actions -> Runners -> New self-hosted runner -> macOS
# It is on that page in the ./config.sh line, and expires after about an hour.
#
# This downloads GitHub's official runner, registers it against this repo,
# installs it as a background service and starts it. Nothing else is needed —
# ffmpeg comes from pip, so there is no Homebrew step and no admin password.

set -euo pipefail

TOKEN="${1:-}"
if [ -z "$TOKEN" ]; then
  cat >&2 <<'USAGE'
Usage: ./setup-runner.sh <REGISTRATION-TOKEN>

Get the token from:
  https://github.com/adrieljavier/Youtube-to-Spotify-Podcasts/settings/actions/runners/new

On that page, look for the line beginning "./config.sh --url ... --token".
Copy the value after --token and pass it to this script.
USAGE
  exit 1
fi

REPO_URL="$(git -C "$(dirname "$0")" remote get-url origin 2>/dev/null || true)"
REPO_URL="${REPO_URL%.git}"
if [ -z "$REPO_URL" ]; then
  REPO_URL="https://github.com/adrieljavier/Youtube-to-Spotify-Podcasts"
fi

case "$(uname -m)" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64)        ARCH="x64" ;;
  *) echo "Unsupported CPU architecture: $(uname -m)" >&2; exit 1 ;;
esac

# Deliberately OUTSIDE the project folder. This repo lives on the Desktop, and
# macOS privacy protection (TCC) refuses background launchd services access to
# ~/Desktop, ~/Documents and ~/Downloads. A runner installed there registers
# fine and then dies on startup with:
#   /bin/bash: .../runsvc.sh: Operation not permitted
# The runner checks the repo out into its own _work directory anyway, so it has
# no reason to sit inside the project.
RUNNER_DIR="$HOME/actions-runner"

echo "Repository   : $REPO_URL"
echo "Architecture : macOS $ARCH"
echo "Install into : $RUNNER_DIR"
echo

# .runner is only written once registration actually succeeds. Testing for it
# (rather than for the unpacked files) means a failed attempt — an expired
# token, say — can simply be re-run with a fresh token.
if [ -f "$RUNNER_DIR/.runner" ]; then
  echo "A runner is already registered here."
  echo "To start over:  cd \"$RUNNER_DIR\" && ./svc.sh uninstall && cd .. && rm -rf \"$RUNNER_DIR\""
  exit 1
fi

# Resolve the current runner release from GitHub's own API.
echo "Finding the latest runner release..."
VERSION="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["tag_name"].lstrip("v"))')"
TARBALL="actions-runner-osx-${ARCH}-${VERSION}.tar.gz"
URL="https://github.com/actions/runner/releases/download/v${VERSION}/${TARBALL}"

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

if [ -x "./config.sh" ]; then
  echo "Runner files already unpacked — reusing them."
else
  echo "Downloading runner ${VERSION}..."
  curl -fsSL -o "$TARBALL" "$URL"
  tar xzf "./$TARBALL"
  rm -f "./$TARBALL"
fi

echo
echo "Registering with GitHub..."
if ! ./config.sh \
  --url "$REPO_URL" \
  --token "$TOKEN" \
  --name "$(scutil --get ComputerName 2>/dev/null || hostname)" \
  --labels self-hosted \
  --work _work \
  --unattended \
  --replace
then
  cat >&2 <<'FAILED'

------------------------------------------------------------------
Registration failed.

A "404 Not Found" from runner-registration almost always means the
token has expired. They are only valid for about an hour, and each
one can be used once.

Get a fresh token and run this script again — the downloaded files
are kept, so the retry is quick:

  https://github.com/adrieljavier/Youtube-to-Spotify-Podcasts/settings/actions/runners/new

Choose macOS + arm64, then copy the value after --token.
------------------------------------------------------------------
FAILED
  exit 1
fi

echo
echo "Installing as a background service..."
./svc.sh install
./svc.sh start
sleep 3
./svc.sh status || true

cat <<'DONE'

------------------------------------------------------------------
Runner installed.

Check it: repo -> Settings -> Actions -> Runners. It should show a
green "Idle" within a few seconds.

One thing left, and it matters: stop this Mac from sleeping.
  System Settings -> Battery (or Displays) -> Options
  Turn ON "Prevent automatic sleeping when the display is off"
The screen may sleep. The machine must not.

Then run the pipeline:
  Actions -> Publish sermon episodes -> Run workflow

Useful later:
  cd ~/actions-runner && ./svc.sh status | stop | start
------------------------------------------------------------------
DONE
