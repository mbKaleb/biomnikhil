# biomnikhil

A desktop app for the lab's biomedical research agent. It wraps
[Stanford Biomni](https://github.com/snap-stanford/Biomni) in a Flask app with
its own window, and runs Claude on Amazon Bedrock signed with each user's
Cognito sign-in, so there are no API keys to hand out.

It runs natively on Windows 11 and on Apple Silicon Macs: no admin rights, no
WSL, Docker or VM. Everything lives in one folder you can delete without a
trace.

## What it does

You describe a task in chat, attach your data files, and the agent plans,
writes and runs Python against them, streaming each step as it goes. The tabs
around the chat:

- **Task**: the chat and the live run. Pick the model and effort, attach
  files, stop a run, or rewind to an earlier message and retry. Three composer
  icons add optional stages: **lab context** (your protocols and thresholds,
  added to the prompt), **preprocessing** of the data before the main run,
  and an **adversarial review** of the result afterwards.
- **Outputs**: the files a run produced, to preview, download or zip.
- **Citations**: the papers, DOIs and links that appeared in a chat's runs.
- **Files**: your uploads.
- **Viewer**: web pages the agent generated, such as reports and plots.
- **Integrity**: whether the data lake, the agent and its saved recipes are
  healthy.
- **Spending**: the AWS account's costs, from Cost Explorer.
- **Settings**: your sign-in, the installed version, beta releases, and setup
  and cleanup.

The first task downloads Biomni's data lake (about 14 GB). The app checks each
file and re-downloads any that are missing or damaged.

## Install

You need a 64-bit Intel/AMD Windows PC or an Apple Silicon Mac on macOS 15 or
newer, about 16 GB free, and internet access to PyPI, GitHub, the Biomni data
lake (S3), Bedrock and Cognito. Setup checks all of these first.

1. Download `biomnikhil-<version>-installer.zip` from the
   [latest release](https://github.com/mbKaleb/biomnikhil/releases/latest)
   and extract it anywhere, OneDrive included.
2. Double-click **setup-windows** or **setup-mac**.
   - Windows may warn about a downloaded file: choose **Run** (or
     **More info → Run anyway**).
   - macOS blocks it the first time: open **System Settings → Privacy &
     Security** and click **Open Anyway**, or run `bash app/run-mac.sh` in
     Terminal from the folder.
3. Choose where to install. Setup suggests `%LOCALAPPDATA%\biomnikhil`
   (Windows) or `~/Library/biomnikhil` (Mac); any other folder you pick gets a
   `biomnikhil` folder inside it. It refuses cloud-synced folders, network
   folders (Windows), Documents, Desktop and Downloads (Mac), and folders that
   already hold other files. Setup copies the app there, so you can delete the
   zip and the extracted folder afterwards. To move an install, uninstall and
   set up again.
4. After a few seconds of "Preparing installer…" the **biomnikhil Setup**
   window opens. Click **Install**. It uses your Python 3.11–3.13 if you have
   one, otherwise it adds a private Python 3.12, then downloads about 1 GB of
   packages and adds a **biomnikhil** shortcut (Desktop and Start menu on
   Windows; Applications and Desktop on Mac). Click **Open biomnikhil** and
   sign in with your lab account.

If a step fails, the window says what to do and **Retry** picks up where it
stopped. The full log is `data/install.log` (and `setup.log` in the install
folder on Windows). On a PC that can't open the window (no WebView2), setup
carries on as text in the console.

## Using it day to day

Open biomnikhil from its shortcut. It runs in its own window with no console,
and closing the window stops it.

Each start checks for a newer release and updates first. If biomnikhil is
already open, the update waits until it's closed and started again. Updates
are signed, and an install only takes a newer version whose signature checks
out.

In the **Settings** tab:

- **Beta releases** updates to the newest release on each start, test builds
  included. Turning it off keeps your version until the next full release. It
  applies to the whole install.
- **Run setup again** closes biomnikhil and rebuilds its Python environment.
  Your chats, settings and data lake stay.
- **Clean up** closes biomnikhil, runs the cleanup described under
  [Uninstall](#uninstall), and reopens the app when you're done.

The install folder holds:

- `app/`: the program
- `data/`: your settings (`.env`, `settings.json`), chats, uploads, the data
  lake, and the logs (`server.log`, `install.log`, `diagnostics.log`)
- `runtime/`: Python, uv and its cache. It's safe to delete, and setup
  rebuilds it.

## Uninstall

Close biomnikhil, then double-click **uninstall-windows** or **uninstall-mac**
in the install folder or the extracted folder. It's also in the Start menu
(Windows) and `~/Applications` (Mac). It offers two choices:

- **Uninstall** lists every path and its size, then asks you to type
  `REMOVE`. It deletes the install, your settings, chats, uploads and logs, the
  shortcuts, and the data lakes the app owns. A shared lake or a folder you
  chose stays unless you type its full path at its own prompt, and then only
  the lake's own files go. A git checkout keeps its source.
- **Clean up** leaves the installed app alone and looks for biomnikhil files
  elsewhere: older copies, data lakes and downloaded installers. It searches
  your user folder and cloud folders (OneDrive, iCloud Drive), plus
  `/Applications` and `/Users/Shared` on a Mac, or the Public folder and
  top-level `biomni*` folders on each fixed drive on Windows. It skips network
  folders. It asks about each item, and only `y` deletes it. The lake the app
  is using is never offered.

## How it works

Browser UI → Flask app (REST plus server-sent events, tasks on background
threads) → Biomni's A1 agent, the data lake and local storage → Claude on
Bedrock.

Model calls are SigV4-signed with the Cognito credentials of the user who
started the run (`app/auth.py`). The model list comes from Bedrock and falls
back to the models in `app/providers.py`. Biomni is pinned to 0.0.8 because
`app/services/biomni_runner.py` patches its internals. The same goes for the
Anthropic and LangChain clients, whose request signing the app replaces.

Agent prompts get scoped hints (`app/hints/`): a core pack every time, domain
packs such as mass spec only when the task looks relevant, ground truth probed
from installed packages, and working code saved from earlier successful runs.

## Development

Clone the repo and run the same launcher (`setup-mac.command` or
`setup-windows.cmd`). Git checkouts skip the self-update and ignore beta
releases, so pull with git instead. Settings live in `data/.env` (template:
`app/.env.example`).

Run the tests:

```bash
uv venv .venv && uv pip install --python .venv -r app/requirements.lock -r tests/requirements.txt
.venv/bin/python -m pytest tests
```

CI runs the same tests on macOS and Windows on every push to `main` and every
pull request.

After editing `app/requirements.txt` or `app/installer/requirements.txt`,
regenerate the pinned versions installs use:

```bash
uv pip compile app/requirements.txt --universal --python-version 3.11 -o app/requirements.lock
uv pip compile app/installer/requirements.txt --universal --python-version 3.11 -o app/installer/requirements.lock
```

To try the installer without a release, build the zip from the working tree
the way the release workflow does, and run `setup-mac.command` from an
unpacked copy. These environment variables help:

- `BIOMNIKHIL_NO_DIALOGS=1` runs the install as text in the console.
- `BIOMNIKHIL_INSTALL_DIR=<folder>` installs there without asking.
- `BIOMNIKHIL_REINSTALL=1` (`-Reinstall` on Windows) runs it again on an
  existing install.

### Layout

```
setup-*.cmd/.command, uninstall-*   double-click launchers at the top of the zip
app/
  run.ps1, run-mac.sh       fetch uv, start the installer, self-update, open the app
  uninstall.*, maintenance.*, cleanup-mac.command   uninstall, setup and cleanup scripts
  installer/                the setup window (pywebview + ui/index.html), system checks,
                            Python, packages, shortcuts; window.py is the app window,
                            update.py the signed self-update, diagnostics.py the crash log
  wsgi.py                   entrypoint (app.wsgi:app, served by waitress)
  auth.py                   Cognito sign-in and per-user AWS credentials
  providers.py              the Bedrock models and effort levels
  config.py                 settings from data/.env; every path inside the install
  api/routes.py             the REST and SSE endpoints
  services/
    biomni_runner.py        builds A1 lazily, runs tasks, patches biomni
    tasks.py                in-memory task registry
    chat_store.py           SQLite chat history
    workflow.py             lab context, preprocessing and review stages
    hints.py                prompt hints, capability probes, saved recipes
    datalake.py             data lake download and integrity checks
    citations.py            sources found in a chat's runs
    spending.py             Cost Explorer figures
    settings.py, maintenance.py   the Settings tab
  hints/                    hint packs
  allowed_packages.txt      packages the agent may install mid-run
  templates/, static/       the UI
tests/                      pytest suite (not shipped)
.github/                    this README and the CI workflows (not shipped)
```

## Releasing

Releases are tags on `main`. The release workflow runs the tests first and
publishes nothing if they fail.

Tag a test build and try it:

```bash
git tag v0.2.0-rc1 && git push origin v0.2.0-rc1
```

That publishes a pre-release with `biomnikhil-0.2.0-rc1-installer.zip` (for
people to download), `biomnikhil.zip` (what installs update from) and the
signed `release.txt`. Installs ignore pre-releases unless **Beta releases** is
on. If it's broken, fix it, commit and tag `-rc2`. When it works, tag the
release on the same commit:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

That becomes the latest release, and installs update to it the next time they
start. The release notes are the commit subjects since the previous release.

### Signing

The workflow signs `release.txt` (the version and the zip's SHA-256) with the
`RELEASE_SIGNING_KEY` repository secret, an Ed25519 private key in PEM form.
Installs only update to a newer release whose signature matches `PUBLIC_KEY` in
`app/installer/update.py`. Without the secret, the release fails rather than
shipping unsigned. To set it:

```bash
gh secret set RELEASE_SIGNING_KEY -R mbKaleb/biomnikhil < ~/.config/biomnikhil/release-signing-key.pem
```

Back that key file up. If it's lost, installs can't update until they're
reinstalled from a zip with a new `PUBLIC_KEY`. To rotate the key, ship one
release signed with the old key that carries the new `PUBLIC_KEY`, then switch
the secret.

## Known limits

- The full `biomni_e1` conda environment (bash and bioconda) isn't installed,
  and there's no R. Tools that shell out to Linux-only binaries or R packages
  fail at runtime and show as failed tasks. The hints steer the agent to Python
  alternatives such as pyopenms.
- Heavy ML stacks (torch, transformers, scvi, cellpose) are left out on
  purpose. Tools that need them fail with a clear install message.
- Biomni imports `langchain-openai` at startup even though nothing uses
  OpenAI, so that package and `openai` stay installed.
- Running tasks live in memory, so restarting the app ends them. Chat history
  is kept.
