from app.services.citations import collect


def _msg(role, content):
    return {"role": role, "content": content, "created": 1.0}


def test_identifier_forms_merge_and_answers_come_first():
    found = collect([
        _msg("user", "What does TP53 do? See https://mine.example/paper"),
        _msg("trace", "<observation>PMID: 123 and https://en.wikipedia.org/wiki/P53.</observation>"),
        _msg("assistant", "TP53 is a suppressor (https://pubmed.ncbi.nlm.nih.gov/123/), doi:10.1038/nature1(2)."),
    ])
    assert [c["id"] for c in found] == ["pmid:123", "doi:10.1038/nature1(2)", "web:en.wikipedia.org/wiki/P53"]
    pmid = found[0]
    assert pmid["in_answer"] and pmid["url"] == "https://pubmed.ncbi.nlm.nih.gov/123/"
    assert [u["where"] for u in pmid["uses"]] == ["Returned by a tool or code result", "Cited in the answer"]
    assert pmid["uses"][1]["prompt"].startswith("What does TP53")
    assert "suppressor" in pmid["uses"][1]["excerpt"]


def test_where_it_appeared_in_a_trace():
    found = collect([_msg(
        "trace",
        "See arXiv:2401.12345\n<execute>get('https://rest.uniprot.org/uniprotkb/P04637?x=1')</execute>",
    )])
    assert found[0]["uses"][0]["where"] == "Mentioned in the agent's reasoning"
    assert found[1]["kind"] == "Data query"
    assert found[1]["uses"][0]["where"] == "Used in the agent's code"


def test_noise_is_left_out():
    assert collect([
        _msg("trace", "http://localhost:8000/x https://www.w3.org/2000/svg PMID 12 is not labelled by the user"),
        _msg("user", "PMID: 999"),
    ])[0]["id"] == "pmid:12"
    assert collect([_msg("trace", "http://localhost:8000/x")]) == []


def test_repeats_in_one_run_keep_one_note():
    found = collect([_msg("user", "q")] + [_msg("trace", "PMC12345") for _ in range(5)])
    assert len(found[0]["uses"]) == 1
