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

* **KB scan sets** (128, `orig_url` on urn.kb.se) -- the files are the
  volumes KB catalogued under one SOU number: the report itself, its parts
  ("D. 1", "D. 2"), its appendix volumes ("Bil. 3", "Bilagedel D") and now and
  then an English version, in KB's index order, which is no order at all --
  sou/1996:158's 22 files are Bilaga 15, 21, 14, 16 … of the EMU-utredningen,
  and sou/1997:116 lists its appendix before Barnkommitténs huvudbetänkande.
  Taking the first file as the work picked an appendix, a later part or an
  English version for 88 of the 128. So each volume is read by its KB title (`kb_volume`, the titles
  `soukb` stores in the record's `volumes`): the report and its parts come
  first, then the appendices, and each volume after the first keeps its own
  pagination under its own label (`Block.bilaga`: "Bilaga 3", "Del 2"). An
  English version is dropped, as for a live record.
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
from collections import Counter
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

# What a KB volume is, from the part of its index title after the SOU's own
# name (KB separates the two with two spaces): "Bil. 3,", "Bil. 1-5,", "Bil.,",
# "Bilagedel D", "Bilagor II", "Expertbilaga", "Appendix"; "D. 2,", "D. Abetänk-
# ande" (KB drops the space), "Del 1", "Vol. 2,", "2,betänkande.". Measured
# over all 128 multi-volume SOUs in KB's index (2026-09).
RE_KB_APPENDIX = re.compile(
    r"(?i:bilag)\w*\s*(\d+(?:-\d+)?|[IVX]+\b|[A-Z](?![a-zåäö]))?"
    r"|\bBil\.\s*\[?(\d+(?:-\d+)?|[A-Z](?![a-zåäö]))?\]?|(?i:\bappendix\b)")
RE_KB_PART = re.compile(r"\b(?:D|Vol)\.\s*(\d+|[A-Z])|\bDel\s*(\d+|[A-Z](?![a-zåäö]))"
                        r"|^(\d+)(?=,|[a-zåäö])")
# the report says what it is ("huvudbetänkande", "slutbetänkande :")
RE_KB_BETANKANDE = re.compile(r"betänkande", re.I)
# an English version: an English title has no Swedish letter at all and some
# English function word ("Environment for sustainable health development  an
# action plan", "Swedish nuclear regulatory activities  Vol. 1,report.")
RE_KB_ENGLISH_WORD = re.compile(
    r"\b(?:the|and|for|of|to|in|on|an|report|summary)\b", re.I)



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
        # the record's `volumes` are KB's title for each file, in `files` order
        # (written by `soukb`); a record written before it stored them cannot
        # say which volume is the report (rule:fail-fast)
        assert "volumes" in record, (
            "%s/%s: a KB scan set without its volume titles -- run `lagen "
            "forarbete soukb-scans` to rewrite its record"
            % (record["type"], record["basefile"]))
        titles = dict(zip(record["files"], record["volumes"], strict=True))
        order, english = kb_order(pdfs, [titles[f] for f in pdfs])
        return [f for f, _label in order], dropped | english

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


def kb_volume(title):
    """What one KB volume is, from its index title: `("main", None)` for the
    report itself, `("main", "Del 2")` for one part of it, `("appendix",
    "Bilaga 3")` -- `"Bilaga"` for an unnumbered one -- or a volume that is
    not the report's text: `("english", None)`, `("kortversion", None)` (an
    easy-read or short version) and `("sammanfattning", None)`."""
    # after the SOU's name where KB's two spaces mark it; some titles run the
    # volume on without them ("…: betänkande D. A")
    rest = title.split("  ", 1)[1] if "  " in title else title
    if RE_ENGLISH.search(title) or (RE_KB_ENGLISH_WORD.search(title)
                                    and not re.search("[åäöÅÄÖ]", title)):
        return "english", None
    if RE_POPULAR.search(title) or re.search(r"\bi korthet\b", title, re.I):
        return "kortversion", None
    if RE_SUMMARY.search(title) or re.search(r"\bsärtryck\b", title, re.I):
        return "sammanfattning", None
    part = RE_KB_PART.search(rest)
    if m := RE_KB_APPENDIX.search(rest):
        number = m.group(1) or m.group(2)
        if number:
            return "appendix", "Bilaga %s" % number
        # one part of an appendix set (sou/1995:140: "D. 3slutbetänkande.
        # Underlagsbilagor", four parts and no other volume)
        return "appendix", ("Del %s" % next(g for g in part.groups() if g)
                            if part else "Bilaga")
    if part:
        return "main", "Del %s" % next(g for g in part.groups() if g)
    return "main", None


def kb_name(title):
    """The SOU's own name in a KB volume title: the part before KB's two
    spaces, without the trailing full stop some volumes carry."""
    return title.split("  ", 1)[0].strip().rstrip(".")


def _natural(label):
    return [(0, int(t)) if t.isdigit() else (1, t)
            for t in re.split(r"(\d+)", label or "") if t]


def kb_order(files, titles):
    """A KB scan set's files in reading order, each with the label its pages
    carry, and the files dropped: `([(file, label)], {file: reason})`.

    The report first -- its unlabelled volume, then its parts by number -- then
    the appendices by number. The first volume's pages need no label: they are
    the report's own, cited as "SOU 1997:116 s. 42". Every later volume keeps
    its own page numbers under its label, so its page 5 is "Bilaga 3 s. 5" and
    never a second "s. 5".

    The report is the unmarked volume under the name most of the set carries:
    sou/1987:3 files "Sveriges arbetskraft" and "Den framtida befolkningen"
    beside "Långtidsutredningen 1987" and its 25 appendices, and those two are
    reports of their own, so they follow the appendices under their own names.
    Where two volumes are the same unmarked report (sou/1990:14 lists
    "Långtidsutredningen 1990" twice) the later one is labelled by its place in
    the set ("Volym 2"): nothing else tells the two apart, and they restart
    their page numbers alike. An English, short or summary version is dropped,
    as for a live record."""
    assert len(files) == len(titles), "one KB title per file"
    kinds = [kb_volume(t) for t in titles]
    dropped = {f: kind for f, (kind, _) in zip(files, kinds, strict=True)
               if kind not in ("main", "appendix")}
    # the report's name: the one most volumes carry, and on a tie the one a
    # volume calling itself a betänkande carries (sou/1996:155 files its
    # slutbetänkande beside a report of another name, one volume each)
    names = Counter(kb_name(t) for t, (kind, _) in zip(titles, kinds, strict=True)
                    if kind in ("main", "appendix"))
    name = max(names, key=lambda n: (names[n], any(
        kb_name(t) == n and RE_KB_BETANKANDE.search(t) for t in titles)),
        default=None)

    def key(v):
        _f, kind, label, i = v
        own = kb_name(titles[i]) == name
        group = (0 if kind == "main" and own else 1 if kind == "appendix" else 2)
        return (group, label is not None,
                not RE_KB_BETANKANDE.search(titles[i]), _natural(label), i)

    kept = sorted(((f, kind, label, i) for i, (f, (kind, label))
                   in enumerate(zip(files, kinds, strict=True))
                   if f not in dropped), key=key)
    order = []
    for pos, (f, _kind, label, i) in enumerate(kept):
        if pos == 0:
            label = None
        elif label is None:
            label = (kb_name(titles[i]) if kb_name(titles[i]) != name
                     else "Volym %d" % (pos + 1))
        order.append((f, label))
    return order, dropped


def page_labels(record):
    """{file: label} for the volumes of a KB scan set after the first -- the
    label each one's pages carry (`kb_order`). Empty for any other record."""
    if population(record) != "kb" or len(record.get("files", [])) < 2:
        return {}
    order, _dropped = kb_order(record["files"], record["volumes"])
    return {f: label for f, label in order if label is not None}
