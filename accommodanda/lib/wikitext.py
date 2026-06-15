"""Parse MediaWiki dump pages (the hand-authored lagen.nu commentary + concept
wiki) into the same inline-run shape every other source uses.

The wiki's value-add links three ways, and this turns all three into the
artifact's `{predicate,uri,text}` link runs:

  * `[[Concept]]` / `[[Concept|label]]`  -> a `begrepp/<Concept>` link;
  * natural-language law/case citations in the prose ("2 kap 2 §
    tryckfrihetsförordningen", "RB 17 kap. 11 §", "NJA 1990 s. 510") -> run
    through the **same citation engine** the statutes and cases use;
  * `[[Kategori:X]]` -> a category on the page (not an inline link).

So commentary and concepts flow through the identical artifact -> catalog ->
inbound-graph -> render pipeline as SFS/DV/förarbete: a paragraph's commentary
shows up in its margin, a concept's page shows everything that references it.
"""

import re
import xml.etree.ElementTree as ET

from .lagrum import Ref, interleave

MW = "{http://www.mediawiki.org/xml/export-0.10/}"
BEGREPP = "https://lagen.nu/begrepp/"

RE_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
RE_CATEGORY = re.compile(r"\[\[Kategori:([^\]]+)\]\]")
RE_HEADING = re.compile(r"^(=+)\s*(.*?)\s*=+\s*$")
RE_BYLINE = re.compile(r"^''+\s*Huvudförfattare:?\s*(.+?)\s*''+$")
RE_REDIRECT = re.compile(r"^#\s*(?:REDIRECT|OMDIRIGERING)", re.IGNORECASE)
# wikitext noise stripped to plain text
RE_FORMAT = re.compile(r"'''''|'''|''")                 # bold/italic markers
RE_TEMPLATE = re.compile(r"\{\{[^}]*\}\}")              # {{templates}}
RE_TAG = re.compile(r"<[^>]+>")                         # stray html / <ref>
RE_WS = re.compile(r"[ \t]+")


def load_page(path):
    """(title, namespace, wikitext) from a MediaWiki XML export file. Takes the
    last revision (the current text)."""
    page = ET.parse(path).getroot()
    title = page.findtext(MW + "title") or ""
    ns = page.findtext(MW + "ns") or "0"
    revs = page.findall(MW + "revision")
    text = (revs[-1].findtext(MW + "text") if revs else "") or ""
    return title, ns, text


def is_redirect(wikitext):
    return bool(RE_REDIRECT.match(wikitext.lstrip()))


def begrepp_uri(name):
    """A concept name -> its begrepp URI. MediaWiki upper-cases the first
    letter of a page title, so `[[allmän handling]]` and the page "Allmän
    handling" resolve to the same URI."""
    name = name.strip()
    if name:
        name = name[0].upper() + name[1:]
    return BEGREPP + name.replace(" ", "_")


def _strip(text):
    text = RE_TEMPLATE.sub("", text)
    text = RE_TAG.sub("", text)
    text = RE_FORMAT.sub("", text)
    return RE_WS.sub(" ", text).strip()


def categories(wikitext):
    return [m.group(1).strip() for m in RE_CATEGORY.finditer(wikitext)]


def author(wikitext):
    for line in wikitext.splitlines():
        m = RE_BYLINE.match(line.strip())
        if m:
            return _strip(m.group(1))
    return None


def _wikilinks(text):
    """Replace `[[target|label]]` with its label, returning (plaintext, [Ref])
    for the concept links, with spans in plaintext coordinates. Category links
    are dropped (handled separately)."""
    out, refs, last, length = [], [], 0, 0
    for m in RE_WIKILINK.finditer(text):
        before = _strip_inline(text[last:m.start()])
        out.append(before)
        length += len(before)
        target = m.group(1).strip()
        label = _strip_inline((m.group(2) or m.group(1)).strip())
        last = m.end()
        if target.lower().startswith("kategori:"):
            continue
        out.append(label)
        refs.append(Ref(length, length + len(label), label,
                        "dcterms:references", begrepp_uri(target)))
        length += len(label)
    out.append(_strip_inline(text[last:]))
    return "".join(out), refs


def _strip_inline(text):
    """Strip formatting from a non-link run without touching link spans."""
    return RE_WS.sub(" ", RE_TAG.sub("", RE_TEMPLATE.sub(
        "", RE_FORMAT.sub("", text))))


def to_runs(text, refparser=None, **parse_kw):
    """One paragraph of wikitext -> inline runs: concept links from `[[...]]`
    plus law/case links from the citation engine, non-overlapping. `parse_kw`
    (e.g. `fragment=` or `context=`) is forwarded to the citation parser to set
    the base law for relative references."""
    plain, links = _wikilinks(text)
    refs = list(links)
    if refparser is not None:
        for r in refparser.parse_text(plain, **parse_kw):
            if not any(w.start < r.end and r.start < w.end for w in links):
                refs.append(r)
    return interleave(plain, refs)


def blocks(wikitext):
    """wikitext -> a flat list of *raw* blocks (link-parsing is left to the
    caller, which sets the citation context):
        ("rubrik", level, heading_text)
        ("stycke", raw_paragraph_text)
    Category lines and the author byline are removed."""
    out, para = [], []

    def flush():
        if para:
            out.append(("stycke", " ".join(para)))
            para.clear()

    for raw in wikitext.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        h = RE_HEADING.match(line)
        if h:
            flush()
            out.append(("rubrik", len(h.group(1)), _strip(h.group(2))))
        elif RE_CATEGORY.match(line) or RE_BYLINE.match(line):
            continue
        else:
            para.append(line)
    flush()
    return out
