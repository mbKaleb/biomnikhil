# biomnikhil

Flask wrapper around [Stanford Biomni](https://github.com/snap-stanford/Biomni),
running Claude on Amazon Bedrock with each user's Cognito sign-in. Built to run **fully native on Windows 11**
— no admin rights, no WSL, no Docker, no VM — inside a disposable folder you
can delete without a trace.

## Architecture

Three layers: browser UI → Flask app (REST + SSE, background-thread task
runner) → Biomni data lake / local storage / Bedrock. Model calls go to
Claude on Bedrock Mantle, SigV4-signed with the Cognito credentials of the
user who started the run (`app/auth.py`); there are no API keys. The models
offered are listed in `app/providers.py`.

## Install (lab members)

Needs a 64-bit Intel/AMD Windows PC, or an Apple Silicon Mac on macOS 15 or
newer; about 16 GB free; and internet access to PyPI, GitHub, the Biomni
data lake (S3), Bedrock and Cognito. Setup checks all of these first.

1. Download `biomnikhil-<version>.zip` from the
   [latest release](https://github.com/mbKaleb/biomnikhil/releases/latest)
   and extract it somewhere that isn't synced (not OneDrive or iCloud
   Desktop/Documents), e.g. `C:\biomnikhil` or `~/biomnikhil`.
2. Double-click **setup-windows** or **setup-mac**.
   - Windows may warn about a downloaded file: choose **Run** (or
     **More info → Run anyway**).
   - macOS blocks it the first time: open **System Settings → Privacy &
     Security** and click **Open Anyway** (or run `bash app/run-mac.sh` in
     Terminal from the folder).
3. A console window says "Preparing installer…" for a few seconds while it
   fetches [uv](https://docs.astral.sh/uv/) and the installer's own small
   Python environment. The **biomnikhil Setup** window then shows the
   install location and space needed; click **Install**. It uses your
   Python 3.11–3.13 if you have one, otherwise a private Python 3.12 in the
   folder, then downloads the app's packages (about 1 GB, a few minutes) and
   adds a **biomnikhil** shortcut (Desktop and Start menu on Windows;
   Applications and Desktop on Mac). Click **Open biomnikhil**: the app opens
   in its own window; sign in with your lab account.

   If something fails, the window says what to do and **Retry** picks up
   where it stopped. The full log is `data/install.log`. On a PC where the
   window can't open (no WebView2), setup continues as text in the console.

From then on, open biomnikhil from that shortcut. It runs in its own window,
with no Terminal or console; closing the window stops it. Each start checks
for a newer release and updates itself first. The server's output goes to
`data/server.log`.

The folder holds everything: `app/` (the program), `data/` (your settings,
chats, uploads and the data lake) and `runtime/` (Python, uv and its download
cache; safe to delete, setup rebuilds it). To uninstall, delete the folder and the biomnikhil
shortcut(s).

## Releasing

Every push to `main` runs the tests (`tests/`, on macOS and Windows, with the
exact versions in `app/requirements.lock`), and a release is only published
once they pass. Run them locally first:

```bash
uv venv .venv && uv pip install --python .venv -r app/requirements.lock -r tests/requirements.txt
.venv/bin/python -m pytest tests
```

Releases are signed. The workflow writes `release.txt` (the version and the
zip's SHA-256) and signs it with the `RELEASE_SIGNING_KEY` repository secret,
an Ed25519 private key in PEM form; installs only update to a release whose
signature matches `PUBLIC_KEY` in `app/installer/update.py`, and only to a
newer version. A release without the secret fails rather than shipping
unsigned. To set the secret from the key file:

```bash
gh secret set RELEASE_SIGNING_KEY -R mbKaleb/biomnikhil < ~/.config/biomnikhil/release-signing-key.pem
```

Keep a backup of that file somewhere safe: losing it means installs can't
update until they're reinstalled from a zip with a new `PUBLIC_KEY`. To
rotate the key, ship one release signed with the old key whose `PUBLIC_KEY`
is the new one, then switch the secret.

Commit and push to `main`, then tag a test build and try it:

```bash
git tag v0.2.0-rc1 && git push origin v0.2.0-rc1
```

`.github/workflows/release.yml` publishes it as a pre-release with
`biomnikhil-0.2.0-rc1.zip` (plus the same file as `biomnikhil.zip`, the name
installs update from, and the signed `release.txt`). Lab installs ignore pre-releases, and a test install
doesn't self-update; download the zip from that pre-release's page. Broken?
Fix, commit, tag `-rc2`. When it works, tag the release on the same commit:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

That becomes the latest release, and lab installs update to it the next
time they start. Release notes are the commit subjects since the previous
release.

After editing `app/requirements.txt`, regenerate the pinned versions installs
use (with [uv](https://docs.astral.sh/uv/)); the installer window's own
pywebview pins work the same way:

```bash
uv pip compile app/requirements.txt --universal --python-version 3.11 -o app/requirements.lock
uv pip compile app/installer/requirements.txt --universal --python-version 3.11 -o app/installer/requirements.lock
```

To try the installer without a release, build the zip from the working tree
(as the workflow does) and run `setup-mac.command` from an unpacked copy.
`BIOMNIKHIL_NO_DIALOGS=1` runs the same install as text in the console, and
`BIOMNIKHIL_REINSTALL=1` (`-Reinstall` on Windows) runs it again on an
existing install.

## Dev setup

Clone the repo and run the same launcher (`setup-mac.command` /
`setup-windows.cmd`). Git checkouts skip the self-update; pull with git
instead. Settings live in `data/.env`.

## Layout

```
setup-windows.cmd, setup-mac.command   double-click launchers (all users see)
LICENSE
app/
  run.ps1, run-mac.sh     launchers: fetch uv, start the installer, self-update, open the app window
  installer/              the install window (pywebview + ui/index.html), same on Mac and Windows:
                          system checks, Python, packages, data/.env, shortcuts;
                          window.py is the app window (runs waitress in the background);
                          update.py the signed self-update
  wsgi.py                 app entrypoint (app.wsgi:app)
  requirements.txt/.lock  dependencies / exact versions installs use
  .env.example            template for data/.env
  auth.py                 Cognito sign-in + per-user AWS credentials
  providers.py            the Bedrock models offered -> A1 kwargs
  config.py               env-driven config, paths inside the install folder
  api/routes.py           /api/chat, /api/tasks/<id>, /api/tasks/<id>/stream (SSE)
  services/tasks.py       thread-safe in-memory task registry
  services/biomni_runner.py  lazy A1 init + background-thread runs
  templates/, static/     UI (static/icons: the app icon)
data/                     created on first run: .env, chats, uploads, data lake
runtime/                  created on first run: uv, Python, the app's environment
.github/                  this README, the test and release workflows (not shipped)
tests/                    pytest suite: security guards, biomni patches, self-update (not shipped)
```

## Known seams

- biomni's agent imports `langchain-openai` at startup even though nothing
  uses OpenAI, so that package (and `openai`) stays installed.
- Live step streaming is coarse for now (status pings + full trace at the
  end). Hook biomni's step callback in `biomni_runner._run` — the TODO marks
  the spot.
- Windows-native means the full `biomni_e1` conda env (bash + bioconda) is
  skipped: tools that shell out to Linux-only binaries will error at runtime
  and surface in the UI as failed tasks.
