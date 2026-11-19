"""What the agent works with, for the About Biomni panel: the data lake's
files, the tools it can call and the software it can use, read from biomni's
own descriptions and sorted into research areas (see research_areas.py)."""
from __future__ import annotations

import importlib
import importlib.metadata
import shutil
from pathlib import Path

from . import biomni_runner, datalake, research_areas

# The order biomni.agent.a1.read_module2api lists them in.
TOOL_FIELDS = [
    "literature", "biochemistry", "bioimaging", "bioengineering", "biophysics",
    "glycoengineering", "cancer_biology", "cell_biology", "molecular_biology",
    "genetics", "genomics", "immunology", "microbiology", "pathology",
    "pharmacology", "physiology", "synthetic_biology", "systems_biology",
    "support_tools", "database", "lab_automation",
]

KIND_PYTHON = "python"
KIND_R = "r"
KIND_COMMAND_LINE = "cli"

# Biomni labels most software "[Python Package]", "[R Package]" or "[CLI Tool]";
# these few have no label, so the kind is given here.
UNLABELED_KINDS = {
    "ADFR": KIND_COMMAND_LINE,
    "diamond": KIND_COMMAND_LINE,
    "fcsparser": KIND_COMMAND_LINE,
}
KIND_LABELS = {
    "[Python Package]": KIND_PYTHON,
    "[R Package]": KIND_R,
    "[CLI Tool]": KIND_COMMAND_LINE,
}

STATUS_INSTALLED = "installed"
STATUS_ON_REQUEST = "on_request"  # the agent may install it when a task needs it
STATUS_UNAVAILABLE = "unavailable"
STATUS_UNKNOWN = "unknown"

# biomni's package name where the approved-install list spells it differently.
ALLOWED_LIST_ALIASES = {
    "igraph": "python-igraph",
    "opencv-python": "opencv-python-headless",
}


def lake_files() -> list[dict]:
    from biomni.env_desc import data_lake_dict

    lake_folder = datalake._lake_dir() / "data_lake"
    files = []
    for name, description in data_lake_dict.items():
        path = lake_folder / name
        present = path.is_file()
        size = path.stat().st_size if present else None
        files.append({
            "name": name,
            "description": description,
            "present": present,
            "size": size,
            "area": research_areas.lake_file_areas(name)[0],
            "areas": research_areas.lake_file_areas(name),
        })
    return files


def tool_groups() -> list[dict]:
    """Every tool biomni describes, grouped by field. `hidden` says why the
    app keeps a tool from the agent; it's only known once an agent has been
    built since the app started. `live` marks the lookups that query a
    database over the internet."""
    groups = []
    for field in TOOL_FIELDS:
        module = importlib.import_module(f"biomni.tool.tool_description.{field}")
        tools = []
        for tool in module.description:
            parameters = []
            for parameter in tool.get("required_parameters", []):
                parameters.append({"name": parameter["name"], "required": True})
            for parameter in tool.get("optional_parameters", []):
                parameters.append({"name": parameter["name"], "required": False})
            tools.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": parameters,
                "hidden": biomni_runner.hidden_tools.get(tool["name"]),
                "live": field == "database",
                "area": research_areas.tool_area(field, tool["name"]),
                "areas": research_areas.tool_areas(field, tool["name"]),
            })
        groups.append({"field": field, "tools": tools})
    return groups


# ---- software ---------------------------------------------------------------

def _normalized(package_name: str) -> str:
    return package_name.lower().replace("_", "-").replace(".", "-")


def _approved_installs() -> set[str]:
    """Packages the agent may pip install on its own (app/allowed_packages.txt)."""
    path = Path(__file__).resolve().parent.parent / "allowed_packages.txt"
    approved = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return approved
    for line in lines:
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        approved.add(_normalized(text.split()[0]))
    return approved


def _software_kind(name: str, description: str) -> tuple[str, str]:
    """The kind of software it is, and its description without the label."""
    for label, kind in KIND_LABELS.items():
        if description.startswith(label):
            return kind, description[len(label):].strip()
    return UNLABELED_KINDS.get(name, KIND_PYTHON), description.strip()


def _software_status(name: str, kind: str, approved: set[str]) -> tuple[str, str]:
    """Whether this install can run it, and a short reason when it can't."""
    if kind == KIND_COMMAND_LINE:
        if shutil.which(name):
            return STATUS_INSTALLED, ""
        return STATUS_UNAVAILABLE, "This program isn't installed on this computer."

    if kind == KIND_R:
        if shutil.which("Rscript"):
            return STATUS_UNKNOWN, ""
        return STATUS_UNAVAILABLE, "R isn't installed on this computer."

    try:
        importlib.metadata.version(name)
        return STATUS_INSTALLED, ""
    except importlib.metadata.PackageNotFoundError:
        pass
    wanted = ALLOWED_LIST_ALIASES.get(_normalized(name), _normalized(name))
    if wanted in approved:
        return STATUS_ON_REQUEST, "Not installed yet; the agent installs it when a task needs it."
    return STATUS_UNAVAILABLE, "Not installed, and not on the list of packages the agent installs by itself."


def software_list() -> list[dict]:
    from biomni.env_desc import library_content_dict

    approved = _approved_installs()
    software = []
    for name, raw_description in library_content_dict.items():
        kind, description = _software_kind(name, raw_description)
        status, note = _software_status(name, kind, approved)
        software.append({
            "name": name,
            "description": description,
            "kind": kind,
            "status": status,
            "note": note,
            "area": research_areas.software_area(name),
            "areas": research_areas.software_areas(name),
        })
    return software


def summary() -> dict:
    return {
        "areas": research_areas.AREAS,
        "lake": lake_files(),
        "tools": tool_groups(),
        "software": software_list(),
        # hidden_tools stays empty until an agent has been built, so an empty
        # one means "not known yet" (a real install always hides some).
        "tool_availability_known": bool(biomni_runner.hidden_tools),
    }
