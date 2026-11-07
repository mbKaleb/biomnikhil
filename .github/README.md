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

1. Download `biomnikhil-<version>-installer.zip` from the
   [latest release](https://github.com/mbKaleb/biomnikhil/releases/latest)
   and extract it anywhere, OneDrive included.
2. Double-click **setup-windows** or **setup-mac**.
   - Windows may warn about a downloaded file: choose **Run** (or
     **More info → Run anyway**).
   - macOS blocks it the first time: open **System Settings → Privacy &
     Security** and click **Open Anyway** (or run `bash app/run-mac.sh` in
     Terminal from the folder).
3. Setup asks where to install. It suggests `%LOCALAPPDATA%\biomnikhil`
   (Windows) or `~/Library/biomnikhil` (Mac); anything else you choose gets a
   `biomnikhil` folder inside it. It won't use a cloud-synced folder, a network
   folder (Windows), Documents, Desktop or Downloads (Mac, where an app opened
   from its icon needs permission for those) or a folder that already has other
   files. Setup copies
   biomnikhil there and runs it from there, so you can delete the zip and the
   extracted folder afterwards. It doesn't ask again once an install is
   finished. To move it, uninstall and set up again. On Windows, setup's log is
   `setup.log` in that folder.
4. A console window says "Preparing installer…" for a few seconds while it
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
with no Terminal or console; closing the window stops it. Each start from the
shortcut checks for a newer release and updates itself first; the Settings tab
shows the last check. While biomnikhil is already open, the update waits until
it's closed and started again. The server's output goes to `data/server.log`.

The folder holds everything: `app/` (the program), `data/` (your settings,
chats, uploads and the data lake) and `runtime/` (Python, uv and its download
cache; safe to delete, setup rebuilds it).

The **Settings** tab shows who you're signed in as and the installed version, and has
three controls:

- **Beta releases** makes each start update to the newest release, test builds included.
  Turning it off keeps your version until a newer full release comes out. It applies to
  this whole install, and a git checkout ignores it.
- **Run setup again** closes biomnikhil and rebuilds its Python environment in the setup
  window, which has a button to open biomnikhil again. Your chats, settings and data lake
  stay.
- **Clean up** closes biomnikhil, opens the cleanup described below in a console or
  Terminal window, and reopens biomnikhil when you're done.

## Uninstall

Close biomnikhil, then double-click **uninstall-windows** or **uninstall-mac** in the
install folder or the extracted folder. **Uninstall biomnikhil** is also in the Start
menu (Windows) and `~/Applications` (Mac) after a reinstall.

It asks which of two things to do:

- **Uninstall** lists every path and size, then asks you to type `REMOVE`. It deletes the
  install, your settings, chats, uploads and logs, the shortcuts, and the data lakes the
  app owns. A shared lake or a folder you chose stays unless you type its full path at
  its own prompt, and then only the lake's own files go. A git checkout keeps its source.
- **Clean up** leaves the installed app alone and looks for biomnikhil files outside it:
  older copies, data lakes and downloaded installers. It searches your user folder and
  cloud folders (OneDrive, iCloud Drive), `/Applications` and `/Users/Shared` on a Mac, and
  the Public folder and top-level `biomni*` folders on each fixed drive on Windows. It
  doesn't search network folders, or anything else on other drives. It asks about each
  one, and only `y` deletes it. A shared lake still needs its full path typed, the lake
  the app is set to use isn't offered, and a git checkout stays with everything in it.

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
`biomnikhil-0.2.0-rc1-installer.zip`, which unpacks to `biomnikhil-installer/` (plus
the same files as `biomnikhil.zip`, unpacking to `biomnikhil/`, the names installs update from, and the signed `release.txt`). Lab installs ignore pre-releases unless
**Beta releases** is on in their Settings tab. To try one without that, download the
zip from the pre-release's page. Broken? Fix, commit, tag `-rc2`. When it works, tag the release on the same commit:

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
`BIOMNIKHIL_NO_DIALOGS=1` runs the same install as text in the console,
`BIOMNIKHIL_INSTALL_DIR=<folder>` installs in that folder without asking, and
`BIOMNIKHIL_REINSTALL=1` (`-Reinstall` on Windows) runs it again on an
existing install.

## Dev setup

Clone the repo and run the same launcher (`setup-mac.command` /
`setup-windows.cmd`). Git checkouts skip the self-update; pull with git
instead. Settings live in `data/.env`.

## Layout

```
setup-windows.cmd, setup-mac.command   double-click launchers (all users see)
uninstall-windows.cmd, uninstall-mac.command   double-click uninstallers
LICENSE                 in the release zip, this is app/LICENSE
app/
  run.ps1, run-mac.sh     launchers: fetch uv, start the installer, self-update, open the app window
  uninstall.ps1, uninstall-mac.sh  the uninstallers the root launchers start
  maintenance.ps1, maintenance-mac.sh, cleanup-mac.command  setup and cleanup from the Settings tab
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
