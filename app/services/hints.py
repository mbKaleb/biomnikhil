"""Scoped, composable execution hints for agent prompts — replaces one
ever-growing hardcoded hint string with three pieces:

  A. Capability probes — actually introspect the installed environment
     (e.g. pyopenms's real class names and method signatures) once, cache
     the result to disk, and hand the agent ground truth instead of it
     re-discovering the same facts by trial and error every single run.

  B. Domain-scoped hint packs (app/hints/*.txt) — only injected into a
     prompt when it actually looks relevant (keyword match), so an
     unrelated task doesn't pay the token cost of mass-spec troubleshooting
     notes it'll never use. `core.txt` is the only one always included.

  C. A verified-recipe cache — once a domain's pipeline actually succeeds
     (real files, not just "no exception"), the working code that got it
     there is saved and pointed at for future runs in that domain, so the
     next mzML task doesn't re-derive the whole pipeline from scratch.
"""
from __future__ import annotations

import json
import re
import time

from pathlib import Path

from ..config import Config


def _hints_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "hints"


def _read_hint(name: str) -> str:
    path = _hints_dir() / name
    return path.read_text() if path.exists() else ""


# ---- A. capability probes -------------------------------------------------

_CAPABILITIES_CACHE: dict = {}  # in-process cache, avoid re-reading disk every run


def _capabilities_path(domain: str):
    d = Config.HARNESS_PATH / "capabilities"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{domain}.json"


def probe_pyopenms() -> dict:
    """Introspect the real, installed pyopenms — class names relevant to
    LC-MS feature finding, and real docstrings (signatures) for the
    methods a pipeline actually needs to call. This is what should back
    every claim in the pyopenms hint pack, not hand-guessed text."""
    import pyopenms as oms

    keywords = [
        "FeatureFinder", "FeatureGrouping", "MapAlignment", "MassTrace",
        "ElutionPeak", "PeakPicker", "FeatureLinker", "MapAligner",
    ]
    classes = sorted(n for n in dir(oms) if any(k in n for k in keywords))

    key_methods = [
        ("PeakPickerHiRes", "pickExperiment"),
        ("MassTraceDetection", "run"),
        ("ElutionPeakDetection", "detectPeaks"),
        ("FeatureFindingMetabo", "run"),
        ("MapAlignmentAlgorithmPoseClustering", "align"),
        ("FeatureGroupingAlgorithmQT", "group"),
        # RT-alignment application — the actual next wall after grouping,
        # per real error traces from this environment (transformRetentionTimes
        # rejecting raw data, TransformationDescription construction failing).
        ("TransformationDescription", "setDataPoints"),
        ("MapAlignmentTransformer", "transformRetentionTimes"),
    ]
    docs = {}
    for cls_name, method_name in key_methods:
        cls = getattr(oms, cls_name, None)
        method = getattr(cls, method_name, None) if cls else None
        if method is None:
            continue
        doc = (method.__doc__ or "").strip()
        docs[f"{cls_name}.{method_name}"] = doc[:500]

    return {
        "domain": "pyopenms",
        "version": oms.__version__,
        "probed_at": time.time(),
        "relevant_classes": classes,
        "method_signatures": docs,
    }


_PROBES = {"pyopenms": probe_pyopenms}


def get_capabilities(domain: str) -> dict | None:
    """Cached probe result — regenerated only if missing or if the
    relevant package's version has changed since it was captured."""
    if domain in _CAPABILITIES_CACHE:
        return _CAPABILITIES_CACHE[domain]

    probe_fn = _PROBES.get(domain)
    if probe_fn is None:
        return None

    path = _capabilities_path(domain)
    cached = None
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            cached = None

    # Regenerate if missing, or if the installed package version drifted
    # since the cache was captured (e.g. after a pip upgrade).
    needs_refresh = cached is None
    if cached is not None:
        try:
            import pyopenms as oms  # cheap once biomni's already imported it

            needs_refresh = cached.get("version") != oms.__version__
        except ImportError:
            needs_refresh = False  # can't check — trust the cache

    if needs_refresh:
        try:
            cached = probe_fn()
            path.write_text(json.dumps(cached, indent=2))
        except Exception:  # noqa: BLE001 — probing must never break a run
            cached = cached or None

    _CAPABILITIES_CACHE[domain] = cached
    return cached


def _format_capabilities(info: dict) -> str:
    lines = [f"pyopenms {info['version']} — relevant classes found:"]
    lines.append(", ".join(info["relevant_classes"]))
    lines.append("")
    for sig, doc in info["method_signatures"].items():
        lines.append(f"{sig}:")
        lines.append(doc)
        lines.append("")
    return "\n".join(lines)


# ---- B. domain-scoped hint packs ------------------------------------------

# Each domain: keywords that trigger it (matched case-insensitively against
# the user's prompt), the hint file to load, and optionally a capabilities
# domain to fill into that file's {capabilities} placeholder.
_DOMAINS = [
    {
        "name": "mzml",
        "keywords": ["mzml", "mass spec", "lc-ms", "lcms", "metabolomic"],
        "hint_file": "mzml.txt",
        "capabilities": None,
    },
    {
        "name": "pyopenms",
        "keywords": ["pyopenms", "mzml", "lc-ms", "lcms", "feature finding",
                     "retention-time alignment", "retention time alignment"],
        "hint_file": "pyopenms.txt",
        "capabilities": "pyopenms",
    },
]


def _recipe_note(domain: str) -> str:
    path = _recipe_path(domain)
    if not path.exists():
        return ""
    return (
        f"\nA previously verified working code pattern for this exact "
        f"environment exists at {path} — read it first "
        f"(open(...).read()) and adapt it rather than starting from "
        f"scratch; it's known to actually produce real output here, not "
        f"just plausible-looking code.\n"
    )


def build_hints(prompt: str) -> str:
    """Compose the hint text for this prompt: core guidance always, plus
    any domain packs whose keywords match — keeps unrelated tasks from
    paying the token cost of, say, mass-spec troubleshooting notes."""
    parts = ["\n\n" + _read_hint("core.txt").strip()]

    text = prompt.lower()
    seen_files = set()
    for domain in _DOMAINS:
        if domain["hint_file"] in seen_files:
            continue
        if not any(kw in text for kw in domain["keywords"]):
            continue
        seen_files.add(domain["hint_file"])

        pack = _read_hint(domain["hint_file"]).strip()
        if domain["capabilities"]:
            info = get_capabilities(domain["capabilities"])
            cap_text = _format_capabilities(info) if info else (
                "(capability probe unavailable — pyopenms may not be "
                "importable yet; import it yourself and use help() to "
                "check signatures before calling.)"
            )
            pack = pack.replace("{capabilities}", cap_text)
            pack = pack.replace("{recipe_note}", _recipe_note(domain["name"]))
        if pack:
            parts.append(pack)

    return "\n\n".join(parts)


# ---- C. verified-recipe cache ----------------------------------------------

_EXECUTE_RE = re.compile(r"<execute>(.*?)</execute>", re.DOTALL)
# Heuristics for "this observation looks like a failure" — steps whose code
# produced one of these should not be saved as a known-good recipe.
_ERROR_MARKERS = ("Traceback (most recent call last)", "Error:", "error:")


def _recipe_path(domain: str):
    d = Config.HARNESS_PATH / "recipes"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{domain}.py"


def matched_domains(prompt: str) -> list[str]:
    text = prompt.lower()
    return [d["name"] for d in _DOMAINS if any(kw in text for kw in d["keywords"])]


def save_recipe_if_verified(prompt: str, steps: list[dict], files: list[dict]) -> None:
    """After a run finishes, save its executed code as a reusable recipe —
    but only if there's real evidence it worked: actual output files on
    disk, not just an exception-free run (which we've seen isn't the same
    thing — see the fabrication incidents this session)."""
    if not files:
        return  # no verified evidence this run actually produced anything

    domains = matched_domains(prompt)
    if not domains:
        return

    # Pull every <execute> block whose *next* agent step's text doesn't
    # look like a traceback — a rough but effective "this one didn't
    # immediately error" filter.
    agent_texts = [s["text"] for s in steps if s.get("kind") == "agent"]
    good_blocks = []
    for i, text in enumerate(agent_texts):
        for code in _EXECUTE_RE.findall(text):
            nxt = agent_texts[i + 1] if i + 1 < len(agent_texts) else ""
            if any(marker in nxt for marker in _ERROR_MARKERS):
                continue
            good_blocks.append(code.strip())

    if not good_blocks:
        return

    recipe = (
        f'"""Auto-saved recipe — verified to have produced real output '
        f"files at least once in this environment. Captured "
        f'{time.strftime("%Y-%m-%d %H:%M:%S")}. Adapt as needed; this is '
        f'a starting point, not a guarantee for every prompt."""\n\n'
        + "\n\n# ---- next step ----\n\n".join(good_blocks)
        + "\n"
    )
    for domain in domains:
        try:
            _recipe_path(domain).write_text(recipe)
        except OSError:
            pass  # best-effort — never let recipe-saving break a run
