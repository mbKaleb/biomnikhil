"""The About Biomni panel's data: every tool, data file and package sorted into
a research area, and what this install can run. These read the pinned biomni's
own descriptions, so a biomni upgrade that adds something unsorted fails here
instead of quietly landing under "General"."""
import pytest

pytest.importorskip("biomni")

from app.services import about, research_areas  # noqa: E402


@pytest.fixture(scope="module")
def summary():
    return about.summary()


def _all_tools(summary):
    tools = []
    for group in summary["tools"]:
        for tool in group["tools"]:
            tools.append(tool)
    return tools


def test_every_item_has_a_known_area(summary):
    items = list(summary["lake"]) + _all_tools(summary) + list(summary["software"])
    for item in items:
        assert item["area"] in research_areas.AREA_IDS, item["name"]


def test_an_item_lists_its_main_area_first_then_others_it_serves(summary):
    items = list(summary["lake"]) + _all_tools(summary) + list(summary["software"])
    for item in items:
        assert item["areas"][0] == item["area"], item["name"]
        assert len(set(item["areas"])) == len(item["areas"]), item["name"]
        for area_id in item["areas"]:
            assert area_id in research_areas.AREA_IDS, item["name"]


def test_protein_data_is_listed_under_proteins(summary):
    """Protein interaction tables, the Protein Atlas and BindingDB are protein
    data even though their main area is another one."""
    names = []
    for file in summary["lake"]:
        if research_areas.PROTEINS in file["areas"]:
            names.append(file["name"])
    assert "proteinatlas.tsv" in names
    assert "BindingDB_All_202409.tsv" in names
    assert "two-hybrid.parquet" in names


def test_areas_have_the_fields_the_panel_shows(summary):
    for area in summary["areas"]:
        assert area["title"]
        assert area["summary"]
        assert area["example"]


def test_every_live_database_tool_is_sorted(summary):
    for tool in _all_tools(summary):
        if tool["live"]:
            assert tool["name"] in research_areas.DATABASE_TOOL_AREAS, tool["name"]


def test_every_data_file_is_sorted(summary):
    for file in summary["lake"]:
        assert file["area"] != research_areas.GENERAL, file["name"]


def test_every_package_is_sorted(summary):
    for package in summary["software"]:
        assert package["name"] in research_areas._software_area_by_name, package["name"]


def test_tables_only_name_things_biomni_has(summary):
    tool_names = set()
    for tool in _all_tools(summary):
        tool_names.add(tool["name"])
    for name in research_areas.TOOL_AREA_OVERRIDES:
        assert name in tool_names, name
    for name in research_areas.DATABASE_TOOL_AREAS:
        assert name in tool_names, name
    for name in research_areas.TOOL_ALSO_AREAS:
        assert name in tool_names, name

    package_names = set()
    for package in summary["software"]:
        package_names.add(package["name"])
    for name in research_areas._software_area_by_name:
        assert name in package_names, name
    for name in research_areas.SOFTWARE_ALSO_AREAS:
        assert name in package_names, name


def test_every_area_has_something_in_it(summary):
    used = set()
    items = list(summary["lake"]) + _all_tools(summary) + list(summary["software"])
    for item in items:
        used.add(item["area"])
    for area_id in research_areas.AREA_IDS:
        assert area_id in used, area_id


def test_software_has_a_kind_and_a_status(summary):
    kinds = {about.KIND_PYTHON, about.KIND_R, about.KIND_COMMAND_LINE}
    statuses = {
        about.STATUS_INSTALLED, about.STATUS_ON_REQUEST,
        about.STATUS_UNAVAILABLE, about.STATUS_UNKNOWN,
    }
    for package in summary["software"]:
        assert package["kind"] in kinds, package["name"]
        assert package["status"] in statuses, package["name"]
        if package["status"] == about.STATUS_UNAVAILABLE:
            assert package["note"], package["name"]
        assert not package["description"].startswith("["), package["name"]


def test_kind_comes_from_biomnis_label_or_the_table():
    assert about._software_kind("x", "[R Package] stats") == (about.KIND_R, "stats")
    assert about._software_kind("x", "[CLI Tool] aligns") == (about.KIND_COMMAND_LINE, "aligns")
    assert about._software_kind("diamond", "A sequence aligner") == (about.KIND_COMMAND_LINE, "A sequence aligner")
    assert about._software_kind("y", "[Python Package] a library")[0] == about.KIND_PYTHON


def test_status_follows_the_install(monkeypatch):
    monkeypatch.setattr(about.shutil, "which", lambda program: "/usr/bin/" + program if program == "samtools" else None)
    approved = {"scanpy"}

    assert about._software_status("samtools", about.KIND_COMMAND_LINE, approved)[0] == about.STATUS_INSTALLED
    assert about._software_status("bwa", about.KIND_COMMAND_LINE, approved)[0] == about.STATUS_UNAVAILABLE
    assert about._software_status("DESeq2", about.KIND_R, approved)[0] == about.STATUS_UNAVAILABLE
    assert about._software_status("numpy", about.KIND_PYTHON, approved)[0] == about.STATUS_INSTALLED
    assert about._software_status("not-a-real-package", about.KIND_PYTHON, approved)[0] == about.STATUS_UNAVAILABLE

    def not_installed(name):
        raise about.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(about.importlib.metadata, "version", not_installed)
    assert about._software_status("scanpy", about.KIND_PYTHON, approved)[0] == about.STATUS_ON_REQUEST
    assert about._software_status("igraph", about.KIND_PYTHON, {"python-igraph"})[0] == about.STATUS_ON_REQUEST


def test_approved_installs_are_read_from_the_list():
    approved = about._approved_installs()
    assert "scanpy" in approved  # a package line
    assert "spectrum-utils" in approved  # underscores read as dashes
    assert "#" not in "".join(approved)  # no comment text


def test_the_route_serves_the_summary(client, signed_in):
    body = client.get("/api/about").get_json()
    assert set(body) == {"areas", "lake", "tools", "software", "tool_availability_known"}
    assert len(body["areas"]) == len(research_areas.AREAS)
