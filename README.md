# bionikhil

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

## Windows quickstart (the lab machine)

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
mkdir $env:USERPROFILE\biomni-box; cd $env:USERPROFILE\biomni-box
git clone <repo-url> bionikhil
cd bionikhil
powershell -ExecutionPolicy Bypass -File scripts\setup-box.ps1
# open .env, add your API key, then:
.\run.ps1
```

Open http://127.0.0.1:8000. First task triggers the ~11 GB data-lake
download into the box. Teardown: `Remove-Item -Recurse -Force
$env:USERPROFILE\biomni-box` — everything (python, packages, data, app)
lives in that one folder.

Every new shell after setup: `. scripts\activate.ps1`

## Mac / dev quickstart

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add a key
flask --app wsgi run --port 8000 --debug
```

## Switching providers

Edit two lines in `.env`:

```
BIOMNI_PROVIDER=ollama        # anthropic | openai | gemini | azure | ollama | custom
BIOMNI_MODEL=                 # optional override, else profile default
```

No code changes. On-device inference = `ollama` profile pointed at a local
Ollama (`OLLAMA_MODELS` inside the box keeps weights disposable too).

## Layout

```
app/
  providers.py            provider profiles -> A1 kwargs (the adapter)
  config.py               env-driven config, box-relative paths
  api/routes.py           /api/chat, /api/tasks/<id>, /api/tasks/<id>/stream (SSE)
  services/tasks.py       thread-safe in-memory task registry
  services/biomni_runner.py  lazy A1 init + background-thread runs
  templates/, static/     minimal test UI
scripts/setup-box.ps1     builds the disposable box around this clone
scripts/activate.ps1      per-session env (dot-source it)
run.ps1                   waitress-serve one-shot
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
