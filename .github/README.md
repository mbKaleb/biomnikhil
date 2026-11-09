# biomnikhil

A small desktop app for running [Stanford Biomni](https://github.com/snap-stanford/Biomni)
(a biomedical research agent) with Claude on Amazon Bedrock. You sign in with
your lab account, so there are no API keys to pass around.

It runs on Windows 11 and Apple Silicon Macs. No admin rights, WSL or Docker,
and everything lives in one folder you can delete.

It's a wrapper around Biomni, and it's rough in places. See
[Known limits](#known-limits).

## What you can do

Describe a task in chat, attach some data, and the agent writes and runs
Python against it while you watch. Around the chat there are a few tabs:

- **Task**: the chat and live run. Pick a model, stop a run, or rewind and retry.
- **Outputs**: files a run produced.
- **Citations**: papers and links that came up.
- **Files**: your uploads.
- **Viewer**: web pages the agent made, like reports and plots.
- **Integrity**: checks on the data lake and the agent.
- **Spending**: AWS costs, from Cost Explorer.
- **Settings**: sign-in, version, beta releases, setup and cleanup.

There are also optional extras on the composer: lab context added to the
prompt, a preprocessing step, and a review pass after the run.

The first task downloads Biomni's data lake (around 14 GB).

## Install

You need Windows 11 (64-bit Intel/AMD) or an Apple Silicon Mac on macOS 15+,
about 16 GB free, and internet access. Setup checks the details for you.

1. Grab `biomnikhil-<version>-installer.zip` from the
   [latest release](https://github.com/mbKaleb/biomnikhil/releases/latest)
   and unzip it anywhere.
2. Double-click **setup-windows** or **setup-mac**.
   - Windows may warn about a download: choose **Run** (or **More info → Run
     anyway**).
   - macOS blocks it the first time: **System Settings → Privacy & Security →
     Open Anyway**.
3. Pick an install folder. Setup refuses cloud-synced folders and a few other
   bad spots, and tells you why.
4. Click **Install** and wait. It sets up Python, downloads about 1 GB of
   packages and adds a shortcut. Then open biomnikhil and sign in.

If something fails, the window says what to do and **Retry** picks up where it
stopped. Logs are in `data/install.log`.

## Day to day

Open it from the shortcut. Closing the window stops it. On each start it checks
for a newer release and updates first (updates are signed and verified).

Under **Settings** you can turn on beta releases, rebuild the Python
environment (chats and data stay), or clean up stray files.

The install folder has `app/` (the program), `data/` (settings, chats, uploads,
data lake, logs) and `runtime/` (Python and uv, safe to delete).

## Uninstall

Close the app, then run **uninstall-windows** or **uninstall-mac**. You get two
options:

- **Uninstall** lists everything it will remove, then asks you to type
  `REMOVE`. Shared data lakes and folders you chose are kept unless you
  confirm them separately.
- **Clean up** leaves the app alone and hunts for leftovers elsewhere (old
  copies, data lakes, installers). It asks about each item.

## How it works

Browser UI → Flask app (REST + server-sent events, tasks on background
threads) → Biomni's A1 agent → Claude on Bedrock.

Model calls are signed with the Cognito credentials of whoever started the run.
Biomni is pinned to 0.0.8 because `app/services/biomni_runner.py` patches its
internals. Prompts also get small hint packs from `app/hints/`.

## Development

Clone the repo and run the same launcher. Git checkouts skip self-update, so
use git to pull. Settings live in `data/.env` (template: `app/.env.example`).

```bash
uv venv .venv && uv pip install --python .venv -r app/requirements.lock -r tests/requirements.txt
.venv/bin/python -m pytest tests
```

CI runs the tests on macOS and Windows for every push to `main` and every PR.

After editing `app/requirements.txt` or `app/installer/requirements.txt`,
regenerate the lock files:

```bash
uv pip compile app/requirements.txt --universal --python-version 3.11 -o app/requirements.lock
uv pip compile app/installer/requirements.txt --universal --python-version 3.11 -o app/installer/requirements.lock
```

Handy env vars when testing the installer:

- `BIOMNIKHIL_NO_DIALOGS=1` runs the install as text in the console.
- `BIOMNIKHIL_INSTALL_DIR=<folder>` installs there without asking.
- `BIOMNIKHIL_REINSTALL=1` (`-Reinstall` on Windows) reruns setup on an
  existing install.

### Layout

```
setup-*, uninstall-*        launchers at the top of the zip
app/
  installer/                setup window, checks, self-update
  api/routes.py             REST and SSE endpoints
  services/                 agent runner, tasks, chats, workflow, data lake, spending...
  hints/                    prompt hint packs
  auth.py, providers.py     Cognito sign-in, Bedrock models
  templates/, static/       the UI
tests/                      pytest suite (not shipped)
.github/                    this README and CI (not shipped)
```

## Releasing

Releases are tags on `main`, and the workflow runs the tests first.

```bash
git tag v0.2.0-rc1 && git push origin v0.2.0-rc1   # test build (pre-release)
git tag v0.2.0 && git push origin v0.2.0           # real release
```

Installs ignore pre-releases unless **Beta releases** is on.

Releases are signed with the `RELEASE_SIGNING_KEY` repo secret (an Ed25519 key
in PEM form), and installs only accept updates matching `PUBLIC_KEY` in
`app/installer/update.py`. Back that key up: if it's lost, installs can't
update until they're reinstalled from a zip with a new `PUBLIC_KEY`.

## Known limits

- No full `biomni_e1` conda environment and no R. Tools that need Linux-only
  binaries or R packages fail at runtime. The hints nudge the agent toward
  Python alternatives.
- Heavy ML stacks (torch, transformers, scvi, cellpose) are left out. Tools
  that need them fail with an install message.
- Biomni imports `langchain-openai` at startup, so that and `openai` stay
  installed even though nothing uses OpenAI.
- Running tasks live in memory, so restarting the app ends them. Chat history
  is kept.
