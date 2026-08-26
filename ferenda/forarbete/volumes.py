"""Which of a förarbete record's PDFs are its body, and in what order.

A record's `files` is not a curated volume list -- it is every `/contentassets/`
PDF the regeringen.se landing page happened to link (`download.find_content_links`).
485 records carry more than one, and for about half of them the extras are not
the document: a one-page *Rättelseblad*, an English `Summary`, a *kortversion*,
the reprinted EU directive an act transposes, a *remisslista*. Reading all of
them as one body ingests every one of those as the proposition's own text;
reading only the first gets `sou/2016:77` wrong the other way, because there
`files[0]` is the rättelseblad and the 861-page betänkande is `files[1]`.

The discriminating evidence is already on disk and was being thrown away: the
landing page is stored beside the record, and each link's *text* says what it
is ("Nytt regelverk om upphandling, **del 3 av 4, bilaga 1-19**"). Where the
link count matches the file count the texts index-align with `files`, which
holds for 296 of the 306 records harvested from regeringen.se.

Provenance splits the 485 into five populations that need different handling
(the curated skip list, then KB scan sets, budget propositions, legacy `_N`
records and live records), and the record itself says which -- so 179 of them
are decided without opening a single PDF:

* **KB scan sets** (128, `orig_url` on urn.kb.se) -- the extra files are
  *sibling* volumes catalogued under the same SOU number, not later parts of
  one text: sou/1996:158's 22 files are Bilaga 15, 21, 14, 16 … of the
  EMU-utredningen, in no order. Only the first is the work.
* **Budget propositions** (11) -- 30-odd separately paginated volumes of
  tables; prop. 2016/17:1 is nine one-page fragments. Not a legal source, and
  skipped outright.
* **Legacy `_N` records** (40) -- genuine consecutive parts, but the file names
  do not give their order (ds/2001:15 runs pages 1, 14, 66, 72, 79, 19, 27 …),
  so they are ordered by the numbering the parser reads off the pages.
* **Everything else** (306) -- the link texts do the work.
"""

import functools
import json
import re
from html import unescape
from pathlib import Path

from ..lib import compress
from ..lib.util import basefile_slug

# a proposition's or SOU's own volumes: "del 2 av 4", "Del A", "volym 3",
# "band 2", "kapitel 6-12", "huvuddokument", "Bilagedel"
RE_PART = re.compile(r"\b(del(?:en)?|volym|band|kapitel|huvuddokument|"
                     r"bilagedel)\b", re.I)
# the whole thing in one file, published beside its own parts
RE_WHOLE = re.compile(r"\bhela dokumentet\b", re.I)
# never body, in link text or PDF title
RE_ERRATA = re.compile(r"(?<!under)\brättelse", re.I)
# definite and plural suffixes included: the link texts say "Remisslistan" and
# "Remissinstanserna" as often as the bare noun
RE_REMISS = re.compile(r"\bremiss(?:lista\w*|instans\w*|var\w*)|"
                       r"\bpressmeddelande\w*", re.I)
RE_POPULAR = re.compile(r"\bkortversion\b|\blättläst\b|\bkort presentation\b", re.I)
RE_ENGLISH = re.compile(r"\bsummary\b|\bengelsk|\(eng\)|\bin english\b", re.I)
RE_SUMMARY = re.compile(r"\bsammanfattning\b", re.I)
RE_ANNEX_DOC = re.compile(r"\bunderlagsrapport\b|\brapport \d|\bfaktablad\b|"
                          r"\bkonsekvensutredning\b", re.I)
# a reprinted EU act keeps its own Official Journal running header
RE_OJ = re.compile(r"Europeiska unionens officiella tidning|"
                   r"Official Journal of the European", re.I)

# Source PDFs regeringen.se serves as 200/application/pdf but which no reader
# can open: the bytes are permanently corrupt *upstream*, not truncated by our
# fetch (verified by re-downloading -- the delivered file is byte-identical).
# Every other file in the record is unaffected, so this drops the one file
# rather than the document; a record left with no body then takes the ordinary
# no-body path and `parse` raises SkipDocument, as for a record of pure errata.
#
# Distinct from `skip.json`, which is about documents that are not förarbeten,
# and from `lib.regeringen.is_misleading`, which is about pages we decline to
# harvest. Here we *want* the document and the publisher cannot deliver it.
# Keyed "<type>/<basefile>/<filename>", one entry per file with the evidence.
BROKEN_PDFS = {
    # Skr. 2000/01:38's Swedish volume. Exactly 65 536 bytes, no %%EOF, xref
    # entries 750 and 767 unresolvable, so poppler cannot even count its pages
    # ("Top-level pages object is wrong type (null)") -- pdfinfo exits 99 and
    # pdftotext yields zero bytes. regeringen.se has always held this copy: the
    # landing page's own link text advertises it as "(pdf 64 kB)". Its sibling
    # 2000-01-38-1.pdf is intact but is the English translation, dropped as
    # "engelsk" on its own evidence, so the skrivelse ends up metadata-only.
    "skr/2000/01:38/2000-01-38.pdf": "trasig hos källan",
}

SKIPLIST = Path(__file__).with_name("data") / "skip.json"
# the budget and vårproposition, skipped by rule rather than by list: they
# recur every year, so enumerating them would need an entry each spring
RE_BUDGET = re.compile(r"\d{4}/\d{2}:(1|100)$")
RE_CONTENT_LINK = re.compile(
    r'<a\b[^>]+href="[^"]*(?:contentassets|globalassets)[^"]*"[^>]*>(.*?)</a>',
    re.I | re.S)
RE_TAG = re.compile(r"<[^>]+>")


@functools.cache
def _skiplist():
    """The curated skip list (`data/skip.json`): '<type>/<basefile>' -> why.

    Documents that sit in the förarbete listings without being förarbeten -- an
    English-language summary published under a Ds number, a consultant's report
    with no författningsförslag. The curated source list supplies the entries.
    Ferenda does not use the broader historical ``metadataonly`` set. See the
    note in ``skip.json``."""
    return {k: v for k, v in
            json.loads(SKIPLIST.read_text(encoding="utf-8")).items()
            if not k.startswith("_")}


def population(record):
    """Which population a record belongs to: "skip", "kb", "budget", "legacy"
    or "live". Read off the record alone -- no PDF is opened."""
    if "%s/%s" % (record.get("type"), record.get("basefile")) in _skiplist():
        return "skip"
    if "urn.kb.se" in (record.get("orig_url") or ""):
        return "kb"
    if record.get("type") == "prop" and RE_BUDGET.search(record.get("basefile", "")):
        return "budget"
    if record.get("source"):
        return "legacy"
    return "live"


def link_texts(docdir, record):
    """The landing page's link text for each of the record's files, or None
    when they cannot be trusted to line up.

    The alignment is positional -- `download_document` names files in link
    order -- so it only holds when the page offers exactly as many content
    links as the record kept files. Ten live records link more than they kept
    (prop. 2024/25:1's two trailing `.xlsx`), and a handful have gaps in
    `files` that `download_document` cannot produce; both make the mapping
    guesswork, so it is refused rather than guessed."""
    page = docdir / (basefile_slug(record["basefile"]) + ".html")
    if not compress.exists(page):
        return None
    # entity-decoded: the stored pages write å/ä/ö as numeric entities
    # ("R&#xE4;ttelseblad"), and every pattern below that carries a diacritic
    # would silently never match
    texts = [re.sub(r"\s+", " ",
                    unescape(RE_TAG.sub(" ", m.group(1)))).strip()
             for m in RE_CONTENT_LINK.finditer(compress.read_text(page))]
    return texts if len(texts) == len(record["files"]) else None


def _role(label, title, first_page):
    """What one file is, from its link text, its PDF title and its first page.
    None means "could be body"."""
    tag = "%s || %s" % (label or "", title or "")
    if RE_ERRATA.search(tag) or RE_ERRATA.search(first_page[:400]):
        return "rättelse"
    for rx, role in ((RE_REMISS, "remisslista"), (RE_POPULAR, "kortversion"),
                     (RE_ENGLISH, "engelsk"), (RE_SUMMARY, "sammanfattning"),
                     (RE_ANNEX_DOC, "underlagsrapport")):
        if rx.search(tag):
            return role
    if first_page.startswith(("Summary", "Government Communication")):
        return "engelsk"
    if RE_OJ.search(first_page[:200]):
        return "eu-rättsakt"
    return None


def body_pdfs(record, probe):
    """The record's body PDFs, in reading order, and why each other file was
    dropped: `(names, {name: reason})`.

    `probe(name) -> (pages, title, first_page_text)` is injected so the rule is
    testable without poppler and so the caller controls how PDFs are opened.
    It is called only for the populations that need it, and never for a file
    listed in `BROKEN_PDFS` -- those are dropped unread.
    """
    pdfs = [f for f in record.get("files", []) if f.lower().endswith(".pdf")]
    kind = population(record)
    # the curated skip list fires regardless of file count: a skip-listed
    # document with a single PDF is still not a förarbete, and letting it
    # through ships exactly the wrong-rather-than-thin page skip.json exists
    # to prevent
    if kind == "skip":
        why = _skiplist()["%s/%s" % (record["type"], record["basefile"])]
        return [], {f: why for f in pdfs}
    # the unopenable files come out before anything counts or probes them: a
    # count-based rule must not weigh a file no reader can read, and `probe`
    # (poppler) raises on one rather than reporting it (see BROKEN_PDFS)
    key = "%s/%s/" % (record["type"], record["basefile"])
    dropped = {f: BROKEN_PDFS[key + f] for f in pdfs if key + f in BROKEN_PDFS}
    pdfs = [f for f in pdfs if f not in dropped]
    # "one PDF is the body" holds only for a record that *published* one: it is
    # the reason not to probe 97k single-file documents, not a judgement. A
    # record left with one file because the other is unreadable is still a
    # record whose publisher offered a choice, so it keeps being judged --
    # otherwise skr. 2000/01:38 silently ships its English translation as the
    # skrivelse's text, which is the exact failure this module exists to stop.
    if len(pdfs) < 2 and not dropped:
        return pdfs, dropped
    if kind == "budget":
        return [], dropped | {f: "budgetproposition" for f in pdfs}
    if kind == "kb":
        return pdfs[:1], dropped | {f: "syskonvolym i KB-skanningen"
                                    for f in pdfs[1:]}

    # the link texts align with `files`, which may hold .doc/.docx/.rtf beside
    # the PDFs, so a label is looked up by the file's position *there* -- not by
    # its index among the PDFs, which would shift every label after a non-PDF
    labels = record.get("_labels")     # link_texts(), threaded in by the caller
    label_of = ({f: labels[i] for i, f in enumerate(record["files"])}
                if labels else {})
    probed = {f: probe(f) for f in pdfs}
    keep = []
    for f in pdfs:
        pages, title, first = probed[f]
        role = _role(label_of.get(f), title, first)
        if role:
            dropped[f] = role
        else:
            keep.append(f)
    if not keep:
        # every file read as an extra. Returning pdfs[0] would hand back exactly
        # the file this module exists to distrust -- sou/2016:77's files[0] is
        # the rättelseblad -- so return no body at all. `dropped` already names
        # every file and why (an empty `keep` means each one got a role), which
        # is what keeps this from reading as a clean single-volume decision.
        return [], dropped

    # a "hela dokumentet" volume published beside its own parts: its page count
    # is the sum of theirs (lr/2007 ny lag om värdepappersmarknaden: 1009 =
    # 664 + 345), and keeping both would read the whole text twice
    for f in keep:
        rest = sum(probed[o][0] for o in keep if o != f)
        if len(keep) >= 3 and rest and abs(probed[f][0] - rest) <= max(3, rest // 100):
            for o in keep:
                if o != f:
                    dropped[o] = "ingår i hela dokumentet"
            return [f], dropped
    if labels:
        whole = [f for f in keep if RE_WHOLE.search(label_of.get(f, ""))]
        if len(whole) == 1:
            for o in keep:
                if o != whole[0]:
                    dropped[o] = "ingår i hela dokumentet"
            return whole, dropped

    if labels is None:
        # no landing page, so no link text: that is missing *evidence*, not
        # evidence the extras are not body. The legacy `_N` records are in
        # exactly this position and their files really are consecutive parts,
        # so everything not positively ruled out above is kept. (Their file
        # order is not always page order -- ds/2001:15 runs 1, 14, 66, 72, 79,
        # 19 -- which misorders the prose; each volume's page numbers are still
        # read from its own margins, so the page anchors stay right.)
        return keep, dropped

    # the primary is the volume that opens like the document type, not
    # necessarily files[0]; the rest join it only on positive evidence that
    # they are further parts of the same text
    primary = keep[0]
    body = [primary]
    for f in keep:
        if f == primary:
            continue
        same_title = probed[f][1] and probed[f][1] == probed[primary][1]
        if RE_PART.search(label_of.get(f, "")) or same_title:
            body.append(f)
        else:
            dropped[f] = "separat dokument"
    return body, dropped
