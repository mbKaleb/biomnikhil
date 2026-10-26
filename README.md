# biomnikhil

Flask wrapper around [Stanford Biomni](https://github.com/snap-stanford/Biomni)
with a swappable AI-provider layer. Built to run **fully native on Windows 11**
— no admin rights, no WSL, no Docker, no VM — inside a disposable folder you
can delete without a trace.

## Architecture

Three layers: browser UI → Flask app (REST + SSE, background-thread task
runner, provider adapter) → Biomni data lake / local storage / model
endpoints. See `docs/` for the diagram. Providers (Claude, OpenAI, Gemini,
Azure, Ollama on-device, custom) are profiles in `app/providers.py`; pick one
with a single env var.

## Install (lab members)

Needs a 64-bit Intel/AMD Windows PC, or an Apple Silicon Mac on macOS 15 or
newer; about 13 GB free; and internet access to PyPI, GitHub, the Biomni
data lake (S3), Bedrock and Cognito. Setup checks all of these first.

1. Download
   [biomnikhil.zip](https://github.com/mbKaleb/biomnikhil/releases/latest/download/biomnikhil.zip)
   and extract it somewhere that isn't synced (not OneDrive or iCloud
   Desktop/Documents), e.g. `C:\biomnikhil` or `~/biomnikhil`.
2. Double-click **Start biomnikhil** (`.cmd` on Windows, `.command` on Mac).
   - Windows may warn about a downloaded file: choose **Run** (or
     **More info → Run anyway**).
   - macOS blocks it the first time: open **System Settings → Privacy &
     Security** and click **Open Anyway** (or run `bash run-mac.sh` in
     Terminal from the folder).
3. The first run explains each step: it uses your Python 3.11–3.13 if you
   have one, otherwise installs a private copy in the folder, then installs
   the app's packages (5–15 minutes). Your browser then opens the app; sign
   in with your lab account.

Everything lives in that folder (app, Python environment, settings, data);
deleting it removes biomnikhil. Keep the launcher window open while you use
the app. Each start checks for a newer release and updates itself first.

## Releasing

Commit and push to `main`, then tag a test build and try it:

```bash
git tag v0.2.0-rc1 && git push origin v0.2.0-rc1
```

`.github/workflows/release.yml` publishes it as a pre-release with
`biomnikhil.zip`. Lab installs ignore pre-releases, and a test install
doesn't self-update; download the zip from that pre-release's page. Broken?
Fix, commit, tag `-rc2`. When it works, tag the release on the same commit:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

That becomes the latest release, and lab installs update to it the next
time they start. Release notes are the commit subjects since the previous
release.

After editing `requirements.txt`, regenerate the pinned versions installs
use (with [uv](https://docs.astral.sh/uv/)):

```bash
uv pip compile requirements.txt --universal --python-version 3.11 -o requirements.lock
```

## Dev setup

Clone the repo and run the same launcher (`run.ps1` / `run-mac.sh`). Git
checkouts skip the self-update; pull with git instead.

## Switching providers

Edit two lines in `.env`:

```
BIOMNI_PROVIDER=ollama        # bedrock | anthropic | openai | gemini | azure | ollama | custom
BIOMNI_MODEL=                 # optional override, else profile default
```

No code changes. On-device inference = `ollama` profile pointed at a local
Ollama.

## Layout

```
app/
  auth.py                 Cognito sign-in + per-user AWS credentials
  providers.py            provider profiles -> A1 kwargs (the adapter)
  config.py               env-driven config, paths inside the app folder
  api/routes.py           /api/chat, /api/tasks/<id>, /api/tasks/<id>/stream (SSE)
  services/tasks.py       thread-safe in-memory task registry
  services/biomni_runner.py  lazy A1 init + background-thread runs
  templates/, static/     minimal test UI
Start biomnikhil.cmd/.command  double-click launchers
run.ps1, run-mac.sh       first-run setup, self-update, then waitress-serve
requirements.lock         exact versions installs use (from requirements.txt)
wsgi.py                   app entrypoint
```

## Known seams

- Provider `source` strings in `app/providers.py` follow biomni's llm layer;
  verify against your installed biomni version if A1 init rejects one.
- Live step streaming is coarse for now (status pings + full trace at the
  end). Hook biomni's step callback in `biomni_runner._run` — the TODO marks
  the spot.
- Windows-native means the full `biomni_e1` conda env (bash + bioconda) is
  skipped: tools that shell out to Linux-only binaries will error at runtime
  and surface in the UI as failed tasks.
