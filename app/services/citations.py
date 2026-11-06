"""The sources a chat's runs may have used, for the Citations tab. Pulled from
the stored answers and traces rather than asked of the model, so it also works
on old chats and can't cite something that never appeared in a run. It shows
that a source appeared and where, not that the agent relied on it."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_FOUND_RE = re.compile(
    r"""(?P<url>https?://[^\s<>"'`\\]+)
    | \b(?P<doi>10\.\d{4,9}/[^\s<>"'`\\]+)
    | \bPMID[:\s#]*(?P<pmid>\d{1,9})\b
    | \b(?P<pmc>PMC\d{3,9})\b
    | \barXiv[:\s]+(?P<arxiv>\d{4}\.\d{4,5}(?:v\d+)?)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_TAG_RE = re.compile(r"</?(execute|observation|solution)>")
_DOI_URL_RE = re.compile(r"^(?:dx\.)?doi\.org/(10\.\d{4,9}/.+)$", re.IGNORECASE)
_PUBMED_URL_RE = re.compile(r"^pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", re.IGNORECASE)
_PMC_URL_RE = re.compile(r"/(PMC\d+)", re.IGNORECASE)
_ARXIV_URL_RE = re.compile(r"^arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)

# Not sources: this machine, XML namespaces, package and code hosting.
_IGNORED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "www.w3.org", "pypi.org", "files.pythonhosted.org"}
# Endpoints the agent's code calls: where data came from, not something to read.
_DATA_HOST_RE = re.compile(r"^(api|rest|eutils|data|ftp|s3)[.-]|\.s3\.|\.amazonaws\.com$|/api/", re.IGNORECASE)

_WHERE = {
    "answer": "Cited in the answer",
    "execute": "Used in the agent's code",
    "observation": "Returned by a tool or code result",
    "reasoning": "Mentioned in the agent's reasoning",
}
_EXCERPT_CHARS = 240
_MAX_USES = 5  # per citation; a source a run loops over shouldn't flood its note


def _clean(found: str) -> str:
    """Drop what prose and markdown leave stuck to the end of a link, keeping
    a closing bracket only if the link opened it."""
    while found:
        last = found[-1]
        if last in ".,;:!?*_":
            found = found[:-1]
        elif last in ")]}" and found.count(last) > found.count({")": "(", "]": "[", "}": "{"}[last]):
            found = found[:-1]
        else:
            break
    return found


def _identifier(kind: str, value: str) -> dict:
    if kind == "doi":
        return {"id": f"doi:{value.lower()}", "kind": "DOI", "label": value, "url": f"https://doi.org/{value}"}
    if kind == "pmid":
        return {"id": f"pmid:{value}", "kind": "PubMed", "label": f"PMID {value}",
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{value}/"}
    if kind == "pmc":
        value = value.upper()
        return {"id": f"pmc:{value}", "kind": "PubMed Central", "label": value,
                "url": f"https://pmc.ncbi.nlm.nih.gov/articles/{value}/"}
    return {"id": f"arxiv:{value}", "kind": "arXiv", "label": f"arXiv {value}", "url": f"https://arxiv.org/abs/{value}"}


def _from_url(url: str) -> dict | None:
    """The citation a link points to: the paper's identifier when the link
    has one (so a DOI and its doi.org link are one entry), else the page."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host or host in _IGNORED_HOSTS:
        return None
    bare = f"{parts.netloc}{parts.path}"
    if m := _DOI_URL_RE.match(bare):
        return _identifier("doi", m[1])
    if m := _PUBMED_URL_RE.match(bare):
        return _identifier("pmid", m[1])
    if m := _ARXIV_URL_RE.match(bare):
        return _identifier("arxiv", m[1])
    if host.endswith("ncbi.nlm.nih.gov") and (m := _PMC_URL_RE.search(parts.path)):
        return _identifier("pmc", m[1])
    if _DATA_HOST_RE.search(host) or "/api/" in parts.path:
        # One entry per endpoint, however many queries the agent sent it.
        return {"id": f"data:{host}{parts.path}", "kind": "Data query", "label": f"{host}{parts.path}", "url": url}
    return {"id": f"web:{host}{parts.path}", "kind": "Web page", "label": f"{host}{parts.path}", "url": url}


def _excerpt(text: str, start: int, end: int) -> str:
    """The line the citation is on, cut down around it if the line is long."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    left = max(line_start, start - _EXCERPT_CHARS // 2)
    right = min(line_end, end + _EXCERPT_CHARS // 2)
    return ("…" if left > line_start else "") + text[left:right].strip() + ("…" if right < line_end else "")


def _section(text: str, pos: int) -> str:
    """Which part of a trace message a position is in, from the last tag
    opened before it."""
    open_tag = None
    for tag in _TAG_RE.finditer(text, 0, pos):
        open_tag = None if tag[0].startswith("</") else tag[1]
    return open_tag if open_tag in ("execute", "observation") else "reasoning"


def _scan(text: str, where: str | None):
    for m in _FOUND_RE.finditer(text):
        kind = m.lastgroup
        found = _clean(m[kind])
        citation = _from_url(found) if kind == "url" else _identifier(kind, found)
        if citation:
            end = m.start(kind) + len(found)
            yield citation, where or _section(text, m.start()), _excerpt(text, m.start(), end)


def collect(messages: list[dict]) -> list[dict]:
    """Every source in a chat's messages (chat_store.get_chat's, in order):
    [{id, kind, label, url, in_answer, uses: [{where, excerpt, prompt, ts}]}],
    those cited in an answer first, then by first appearance. The user's own
    messages are only the context for what follows, never citations."""
    citations: dict[str, dict] = {}
    prompt = ""
    for message in messages:
        role = message.get("role")
        if role == "user":
            prompt = message.get("content") or ""
            continue
        if role not in ("assistant", "trace"):
            continue
        for citation, where, excerpt in _scan(message.get("content") or "", "answer" if role == "assistant" else None):
            entry = citations.setdefault(citation["id"], {**citation, "in_answer": False, "uses": []})
            entry["in_answer"] = entry["in_answer"] or where == "answer"
            # One note per kind of use per prompt, so loops don't repeat it.
            same = any(u["where"] == _WHERE[where] and u["prompt"] == prompt for u in entry["uses"])
            if not same and len(entry["uses"]) < _MAX_USES:
                entry["uses"].append(
                    {"where": _WHERE[where], "excerpt": excerpt, "prompt": prompt, "ts": message.get("created")}
                )
    return sorted(citations.values(), key=lambda c: not c["in_answer"])  # stable: keeps first-seen order
