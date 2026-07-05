"""`lagen kommentar ai-annotate <basefile>` -- the Step-4 AI guidance linker:
read the official external guidance PDFs an annotation declares and propose, per
article, which guidance section explains it.

A commentary markdown file declares its guidance documents in frontmatter:

    ---
    annotates: 32023R2854
    guidance:
      - title: Frågor och svar om dataakten
        url: https://digital-strategy.ec.europa.eu/.../faq-data-act
        pdf: https://ec.europa.eu/newsroom/dae/redirection/document/108144
    ---

The direct `pdf:` link is authored by hand (a guidance document is short-lived
and the URL is not derivable from the act), so this pass never has to guess it.
For each declared source we download + cache the PDF, flatten it to page-marked
text, and ask the configured Berget model to map guidance sections to the act's
targets -- not just whole articles but the *fine-grained* nodes the act divides
into (a single definition `2.21`, a numbered paragraph `6.2`, a recital
`recital-15`), so a FAQ answer about two definitions links to exactly those two,
not to article 2 as a whole. The validated mapping is written, wrapped in
`{"guidanceLinks": ...}`, as a `.ann` sidecar next to the kommentar artifact --
the AI-created, then human-corrected layer, kept **separate** from the
hand-edited markdown (which carries the editorial prose and the curated
`## Externa länkar`). Its shape -- `{anchor: [{label, href, desc, section}]}` --
is the per-node guidance shape the rail already renders, so promoting an accepted
link costs nothing.

Like every ai-* action the LLM is called only here, on an explicit annotate of a
named basefile -- never from a corpus-wide parse/relate/generate.
"""

import hashlib
import json
import re
import subprocess
from pathlib import Path

import requests
from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs

from ..lib import layout, llm, markdown
from ..lib.eu_structure import anchored_blocks
from ..lib.text import runs_text
from ..lib.util import normalize_space
from . import parse as wiki_parse

PROMPT = Path(__file__).with_name("guidance_linker_prompt.txt")
ACT_PLACEHOLDER = "[ACT MAP]"
# the act map lists articles, sub-articles and recitals, one per line, each led by
# the exact anchor token the model must copy; the label is truncated so 600+ lines
# stay a small fraction of the prompt (the guidance PDF is the bulk)
LABEL_MAX = 160
GUIDANCE_PLACEHOLDER = "[GUIDANCE TEXT]"
# the prompt carries only the act's article list (number + heading), never the
# full act text -- the model maps guidance sections to articles, it does not need
# the article bodies -- so the bulk is the guidance PDF. gpt-oss reasons over the
# whole FAQ before emitting the JSON, so the completion budget must cover a long
# chain-of-thought plus the answer (the endpoint default of 4096 truncates it).
MAX_TOKENS = 32000
RE_PAGE = re.compile(r"\[Sida (\d+)\]\n")
USER_AGENT = "ferenda/lagen.nu guidance linker"
CACHE = layout.DOWNLOADED / "kommentar" / "guidance"


def fetch_pdf(url):
    """Download a guidance PDF, cached under kommentar/guidance/ keyed on the url
    (a guidance document outlives a build but not the act, so caching it spares a
    re-fetch and survives the source going dark). `requests` transparently undoes
    the gzip transfer-encoding the Commission's redirector serves it with."""
    cached = CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf")
    if cached.exists():
        return cached
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
    resp.raise_for_status()
    assert resp.content[:4] == b"%PDF", \
        "%s did not return a PDF (got %r)" % (url, resp.content[:8])
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(resp.content)
    return cached


def guidance_text(pdf_path):
    """The PDF flattened to page-marked plain text -- each page's visual lines in
    reading order under a `[Sida N]` marker, so the model can cite the page a
    section starts on (the `#page=N` deep link the rail renders). Uses the same
    pdftohtml -xml extraction as the eurlex PDF parser, page-aware here."""
    xml = subprocess.run(
        ["pdftohtml", "-xml", "-i", "-nodrm", "-stdout", str(pdf_path)],
        capture_output=True, check=True).stdout
    root = etree.fromstring(xml, etree.XMLParser(recover=True, load_dtd=False,
                                                 no_network=True))
    out = []
    for n, page in enumerate(root.findall("page"), 1):
        spans = []
        for t in page.findall("text"):
            text = normalize_space("".join(t.itertext()))
            if text:
                spans.append((int(t.get("top")), int(t.get("left")), text))
        lines = [text for _, _, text in sorted(spans)]
        if lines:
            out.append("[Sida %d]\n%s" % (n, "\n".join(lines)))
    return "\n\n".join(out)


def _pages(text):
    """`[(page_number, page_text)]` parsed back from the `[Sida N]` markers
    `guidance_text` emits, for locating the page a section actually starts on."""
    parts = RE_PAGE.split(text)
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts), 2)]


def _page_of(pages, title):
    """The physical page a guidance section appears on, found by matching its title
    in the page text (alnum-normalised, so the model's straight quotes match the
    PDF's curly ones) -- the model miscounts pages, but the title it returns is
    verbatim, so this makes the `#page=N` deep link exact. None if not found."""
    needle = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    if needle:
        for num, body in pages:
            if needle in re.sub(r"[^a-z0-9]+", " ", body.lower()):
                return num
    return None


def act_map(host_art):
    """`(map_text, valid_anchors)` for the host act: the *fine-grained* target list
    spliced into the prompt and the set of anchors the model may cite. One line per
    targetable node -- article, sub-article (a definitions point, a numbered
    paragraph) and recital -- as `[<anchor>] <label>`, the anchor being the exact
    token the model copies into `targets` (validated against `anchors` on return).
    The anchors are the ones the renderer mints (`anchored_blocks`), so an accepted
    link lands on the right node: `2` a whole article, `2.21` a single definition,
    `recital-15` a recital. Labels are truncated -- the model needs to recognise a
    node, not read it."""
    lines, anchors = [], set()
    for anchor, b in anchored_blocks(host_art["structure"]):
        anchors.add(anchor)
        lines.append("[%s] %s" % (anchor, runs_text(b["text"]).strip()[:LABEL_MAX]))
    return "\n".join(lines), anchors


def _validate(content, anchors):
    """Parse and shape-check the model's reply: `{"links": [...]}` where every link
    has a title and at least one target, and every cited target is a real anchor in
    the act. Raises on anything else (the message is fed back on the retry) so a
    hallucinated anchor never reaches the `.ann`."""
    data = json.loads(llm.strip_fence(content))
    # ValueError, not assert: the retry loop load-bears on the raise, which
    # -O would strip
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    links = data.get("links")
    if not isinstance(links, list):
        raise ValueError("response lacks a links list")
    bad = set()
    for link in links:
        if not isinstance(link, dict):
            raise ValueError("a link is not an object")
        if not link.get("title"):
            raise ValueError("a link has no title")
        targets = link.get("targets")
        if not (isinstance(targets, list) and targets):
            raise ValueError("link %r has no targets list" % link.get("title"))
        bad |= {a for a in targets if a not in anchors}
    if bad:
        raise ValueError("links cite anchors not in the act: %s"
                         % ", ".join(sorted(bad)))
    return links


def _author(prompt, anchors):
    """Call the model and validate; on a malformed or hallucinated reply, retry
    once with the failure fed back (the call is temperature 0, so a bare re-prompt
    would repeat the answer). Raises if the second answer is bad too."""
    for attempt in range(2):
        try:
            return _validate(llm.complete(prompt, max_tokens=MAX_TOKENS), anchors)
        except ValueError as exc:
            if attempt:
                raise
            prompt += ("\n\nDITT FÖREGÅENDE SVAR UNDERKÄNDES: %s\n"
                       "Rätta detta och följ alla regler ovan exakt." % exc)


def _source_link(source, link, page):
    """One model link -> the per-anchor rail item. The rendered link reads
    `<source>, <section>: <section title>` -- e.g.
    "Frågor och svar om dataakten, question 25: Does a data holder have to share
    data if there are safety/security concerns?". The link *text* (`label`) names
    the guidance document and its own section reference (the durable,
    human-dereferenceable locator -- a FAQ question number, a chapter id -- that
    survives a FAQ revision where the `#page=N` deep link drifts); the section
    title follows as `desc`. `section` is kept first-class so review and rendering
    can rely on it. The `#page=N` deep link is a convenience located
    deterministically (the model miscounts pages)."""
    href = source["pdf"]
    if page:
        href += "#page=%d" % page
    section = link.get("section")
    # drop a leading enumerator the model sometimes echoes into the title ("4.",
    # "5a)") so the section title isn't doubled with the section reference
    title = re.sub(r"^\s*\d+[a-z]?\s*[.)]\s+", "", link["title"]).strip()
    name = source.get("title") or "Vägledning"
    label = "%s, %s" % (name, section) if section else name
    item = {"label": label, "href": href, "desc": title}
    if section:
        item["section"] = section
    return item


def annotate(basefile, wiki_root):
    """Author and write the `.ann` guidance-link layer for one kommentar basefile;
    returns the written path. The basefile's markdown declares the host act
    (`annotates:`) and its guidance PDFs (`guidance:`); the host act's parsed
    artifact supplies the article list + valid anchors."""
    src = Path(wiki_parse.kommentar_index(str(wiki_root))[basefile])
    meta, _ = markdown.frontmatter(src.read_text(encoding="utf-8"))
    celex = str(meta["annotates"])
    sources = meta.get("guidance", [])
    assert sources, \
        "%s declares no `guidance:` sources in frontmatter -- nothing to link" % src
    host_path = layout.artifact("eurlex", celex)
    assert host_path.exists(), \
        ("%s: no parsed host artifact at %s -- run `lagen eurlex parse %s` first"
         % (basefile, host_path, celex))
    host_art = json.loads(host_path.read_text())
    act, anchors = act_map(host_art)
    assert anchors, "%s host act %s has no anchors to link against" % (basefile, celex)

    out = {}
    for source in sources:
        assert source.get("pdf"), "a guidance source for %s has no `pdf:` url" % basefile
        text = guidance_text(fetch_pdf(source["pdf"]))
        pages = _pages(text)
        prompt = (PROMPT.read_text().replace(ACT_PLACEHOLDER, act)
                  .replace(GUIDANCE_PLACEHOLDER, text))
        for link in _author(prompt, anchors):
            page = _page_of(pages, link["title"]) or link.get("page")
            item = _source_link(source, link, page)
            for anchor in link["targets"]:
                out.setdefault(anchor, []).append(item)

    path = layout.artifact("kommentar", basefile).with_suffix(".ann")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"guidanceLinks": out}, ensure_ascii=False, indent=2))
    return path
