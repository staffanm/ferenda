"""A harvested kkvdomar record + its decision PDF -> :class:`Avgorande` -> JSON
artifact.

The PDF is what the court sent Konkurrensverket, and that is more than the
decision. Three kinds of document share one file:

  * the kammarrätt's own decision, first. Page 1 opens with the court's
    letterhead (court, doktyp, målnummer, date, "Meddelat i …") and the party
    block (KLAGANDE, MOTPART, ÖVERKLAGAT AVGÖRANDE, SAKEN); every later page
    repeats a short running header ("KAMMARRÄTTEN I BESLUT Sida 2 GÖTEBORG Mål
    nr 1137-26"); page 1 ends in the court's address footer ("Dok.Id 763400
    Postadress …").
  * the appeal instructions the court attaches ("Bilaga A / Hur man
    överklagar KR-09", "SVERIGES DOMSTOLAR / Bilaga / HUR MAN ÖVERKLAGAR"): a
    form, the same in every case, set in two columns that no reflow reads in
    order. Removed.
  * sometimes the lower court's decision, attached as a bilaga ("Förvaltnings-
    rätten i Linköpings beslut den 30 maj 2017 i mål nr 3370-17, se bilaga A").
    Kept, under a "Bilaga" heading: the kammarrätt's decision often only says
    that it agrees with it.

Until about 2022 the courts sent scans, and some PDFs from as late as 2026 are
scans too. `lib.pdftext.pages_with_ocr` reads a PDF with no text layer through
ocrmypdf. A PDF can also carry a scanned page 1 in front of text pages (the
signed first page, kgg/4404-22/2022-11-18); page 1 always holds the letterhead,
so an empty page 1 sends the whole PDF through OCR as well.

A decision that Högsta förvaltningsdomstolen reviewed and published as a
referat or a notis is not parsed: `download.superseded` names it, and the dv
source's page for the HFD case is where a reader finds it.

The listing and the case page are authoritative for the metadata they state
(the parties, the dates, the outcome). The PDF supplies only what they do not:
whether the decision is a dom or a beslut, and the text.
"""

import re
from typing import NamedTuple

from ..lib import compress, layout
from ..lib.errors import SkipDocument
from ..lib.lagrum import ALL_PARSE_TYPES, sfs_parser
from ..lib.pdftext import (
    join_across_pages,
    ocr_pdf,
    page_paragraphs,
    pages_with_ocr,
    pdf_pages,
)
from ..lib.util import approximate_date, normalize_space, store_relpath
from .download import body_path, record_json, superseded
from .model import Avgorande, Block

# the court's section labels, longest first so that "SKÄLEN FÖR KAMMARRÄTTENS
# AVGÖRANDE" is not read as "KAMMARRÄTTENS AVGÖRANDE". Upper case only: in prose
# these are ordinary words. OCR glues a label to the text beside it ("KLAGANDE
# Stanley Security Sverige AB", "… Aktbil MOTPARTER 1. Landskrona kommun"), so a
# label is split out wherever it stands.
LABELS = ("SKÄLEN FÖR KAMMARRÄTTENS AVGÖRANDE", "SKÄLEN FÖR AVGÖRANDET",
          "KAMMARRÄTTENS AVGÖRANDE", "FÖRVALTNINGSRÄTTENS AVGÖRANDE",
          "ÖVERKLAGADE AVGÖRANDEN", "ÖVERKLAGAT AVGÖRANDE", "YRKANDEN M.M.",
          "YRKANDEN", "KLAGANDE OCH MOTPART", "MOTPART OCH KLAGANDE",
          "MOTPARTER", "MOTPART", "KLAGANDE", "SÖKANDE", "SAKEN")
RE_LABEL = re.compile(r"(?<![\wÅÄÖ])(%s)(?![\wÅÄÖ])"
                      % "|".join(re.escape(label) for label in LABELS))

# the court's address footer on page 1 (and on a lower court's page 1). It is
# the last thing on the page, so everything from it on goes.
RE_FOOTER = re.compile(r"(?:Avgörandet är elektroniskt undertecknat\s*)?"
                       r"\bDok\s?\.\s?[Il]d\b|^Postadress\b")   # "ld": OCR

# the tokens a running header is made of. A leading paragraph built from
# nothing else is the header; the first paragraph that holds anything more is
# the body, so a body paragraph that happens to open on a year or a målnummer
# ("2015 ref. 55). Förvaltningsrätten …") is never cut.
_SEATS = ("STOCKHOLM|GÖTEBORG|JÖNKÖPING|SUNDSVALL|FALUN|HÄRNÖSAND|KARLSTAD"
          "|LINKÖPING|LULEÅ|MALMÖ|UMEÅ|UPPSALA|VÄXJÖ")
_HEADER_TOKENS = (
    r"(?:KAMMARR|FÖRVALTNINGSR|LÄNSR)[ÄA]TTEN",
    r"I?(?:%s)" % _SEATS,           # OCR glues the "I" on: "IJÖNKÖPING"
    r"I", r"D\s?O\s?M", r"B\s?E\s?S\s?L\s?U\s?T",
    r"Sida\s+[\dIl]+(?:\s*\([\dIl]+\))?",
    r"Mål\s*(?:nr|ar)",             # "ar": OCR's "nr"
    r"\d{1,6}(?:\s?[—–-]+\s?\d{1,6})*-\d{2}", r"m\.fl\.", r"och",
    r"\d{4}\s?-\s?\d{2}\s?-\s?\d{2}",
    r"Avdelning\s+[\dI:]+", r"Enhet\s+\d+",
    r"Meddelad?\s+i\s+\w+",
    r"[-–—\"”'|.,»«:;]+")
RE_HEADER = re.compile(r"(?:%s|\s)+" % "|".join(_HEADER_TOKENS))

# what the first three paragraphs of a page that starts an attachment hold: the
# "Bilaga A" line, the Domstolsverket masthead of the appeal form, or the form's
# own code ("KR-09") where the Bilaga line is not text at all (kst/2660-24)
RE_ATTACHMENT = re.compile(r"^Bilaga\b|\bSVERIGES DOMSTOLAR\b"
                           r"|^(?:Hur man överklagar\s+)?[KF]R-\d\d$")
# ... or the appeal form's heading as the page's very first paragraph. Further
# down a page it is a lower court's own closing section (kjo/1747-17/2017-07-27)
RE_APPEAL_HEADING = re.compile(r"^HUR MAN ÖVERKLAGAR$")
# an attachment that is the appeal form: its heading or its code within its
# first four paragraphs. Not the decision's own "HUR MAN ÖVERKLAGAR, se bilaga
# A", which is the sentence that points to it.
RE_APPEAL = re.compile(r"^HUR MAN ÖVERKLAGAR$|^Hur man överklagar\b(?!.*se bilaga)"
                       r"|^[KF]R-\d\d$|^Sid \d+ av \d+$")
# a lower court's letterhead within a page's first three paragraphs starts the
# lower court's decision
RE_LOWER_COURT = re.compile(r"\b(?:FÖRVALTNINGSR|LÄNSR)[ÄA]TTEN\b")
RE_BILAGA_LABEL = re.compile(r"^Bilaga\s+([A-Z1-9])$")

# the rules the court draws under the party block
RE_NOISE = re.compile(r"_{3,}")
# Konkurrensverkets receipt stamp on a scanned page 1 ("KONKURRENSVERKET … Avd
# Dnr KSnr Aktbil"), read into the party block beside it. Removed from page 1
# only: in the reasons "Dnr" and "Aktbil" are words the court uses.
RE_STAMP = re.compile(r"\b(?:KONKURRENSVERKET|Avd|Dnr|KSnr|Aktbil)\b(?!\.)")

RE_DOM = re.compile(r"(?<!\w)D\s?O\s?M(?!\w)|\bMeddelad\b")
RE_BESLUT = re.compile(r"(?<!\w)B\s?E\s?S\s?L\s?U\s?T(?!\w)|\bMeddelat\b")

# a bold or italic paragraph this short, with no full stop, is a subheading
# ("Vad målet gäller", "Kammarrättens bedömning" -- set in italics in kgg/2857-22,
# in bold in kst/2804-23). A scan carries neither, and loses these.
HEADING_MAX = 80


def pages(path, patch_key):
    """[(pageno, [Line])], read through OCR when the PDF is a scan -- or when
    only its page 1 is, which `pages_with_ocr` does not look for: it OCRs a PDF
    only when no page has text."""
    read = pages_with_ocr(path, patch_key)
    if read and not read[0][1]:
        read = list(pdf_pages(str(ocr_pdf(path, "swe")), patch_key,
                              hidden=True))
    return read


class Paragraph(NamedTuple):
    text: str
    emphasised: bool     # set in bold or in italics
    page: int            # the PDF page it starts on, for the facsimile tabs


def paragraphs(read):
    """Per page, its paragraphs, noise removed."""
    out = []
    for pageno, lines in read:
        page = []
        for para in page_paragraphs(lines, None, pageno):
            text = normalize_space(RE_NOISE.sub(" ", para.text))
            if text:
                page.append(Paragraph(text, para.bold or para.italic, pageno))
        out.append(page)
    return out


def _cut_footer(page):
    for i, para in enumerate(page):
        match = RE_FOOTER.search(para.text)
        if match:
            kept = para.text[:match.start()].strip()
            return page[:i] + ([para._replace(text=kept)] if kept else [])
    return page


def _drop_header(page):
    for i, para in enumerate(page):
        if not RE_HEADER.fullmatch(para.text):
            return page[i:]
    return []


def _drop_letterhead(page):
    """Page 1 of a document, from its first section label on, and the
    letterhead in front of it as one string. On a scan the letterhead also holds
    whatever the stamps and the signature scrawl read as, and the receipt stamp
    reads into the party block beside it."""
    for i, para in enumerate(page):
        match = RE_LABEL.search(para.text)
        if match:
            head = (" ".join(p.text for p in page[:i]) + " "
                    + para.text[:match.start()])
            rest = [p._replace(text=normalize_space(RE_STAMP.sub(" ", p.text)))
                    for p in [para._replace(text=para.text[match.start():])]
                    + page[i + 1:]]
            return head, [p for p in rest if p.text]
    return "", page


def segments(per_page):
    """The PDF's pages as the documents it holds: [(kind, label, [page])] with
    kind "decision", "bilaga" or "appeal", the decision first. A page that does
    not start a document continues the one before it."""
    out: list[tuple[str, str | None, list]] = [("decision", None, [])]
    for i, page in enumerate(per_page):
        texts = [para.text for para in page]
        if i and (any(RE_ATTACHMENT.search(t) for t in texts[:3])
                  or any(RE_APPEAL_HEADING.match(t) for t in texts[:1])):
            label = next((m.group(1) for t in texts[:3]
                          if (m := RE_BILAGA_LABEL.match(t))), None)
            kind = ("appeal" if any(RE_APPEAL.match(t) for t in texts[:4])
                    else "bilaga")
            out.append((kind, label, [page]))
        elif (i and out[-1][0] != "bilaga"
              and any(RE_LOWER_COURT.search(t) for t in texts[:3])):
            out.append(("bilaga", None, [page]))
        else:
            out[-1][2].append(page)
    return out


def _document(document_pages):
    """One document's pages -> (letterhead, [Paragraph]) with letterhead,
    running headers and footers removed, and the paragraphs a page break -- or,
    on a scan, an uneven line spacing -- split joined again.

    Every paragraph boundary goes through `join_across_pages`, not only the
    ones at a page break: OCR spaces lines unevenly, so a scan's paragraph
    arrives cut in two ("Förvaltningsrättens" / "avgörande står därför fast."),
    and a paragraph that opens in lower case after one that ends no sentence is
    the second half of it. A joined paragraph keeps the page its first half
    starts on, and is prose, not a subheading."""
    letterhead, paras = "", []
    for n, page in enumerate(document_pages):
        page = _cut_footer(page)
        if n == 0:
            letterhead, page = _drop_letterhead(page)
        else:
            page = _drop_header(page)
        paras.extend(page)
    out = []
    for para in paras:
        joined = (join_across_pages([[out[-1].text], [para.text]]) if out
                  else [])
        if len(joined) == 1:
            out[-1] = out[-1]._replace(text=joined[0], emphasised=False)
        else:
            out.append(para)
    return letterhead, out


def _is_heading(text, emphasised):
    letters = [c for c in text if c.isalpha()]
    if (len(letters) >= 4 and len(text) <= HEADING_MAX
            and not any(c.islower() for c in letters)):
        return 1
    if (emphasised and len(text) <= HEADING_MAX
            and not text.endswith((".", ":"))):
        return 2
    return None


def blocks(paras, shift=0):
    out = []
    for para in paras:
        for i, part in enumerate(RE_LABEL.split(para.text)):
            part = part.strip(" ,;")
            if not part:
                continue
            level = 1 if i % 2 else _is_heading(part, para.emphasised)
            out.append(Block("rubrik", part, level + shift, para.page) if level
                       else Block("stycke", part, page=para.page))
    return out


def domslut(body):
    """The text under the decision's "… AVGÖRANDE" heading -- what the court
    decided, the listing's snippet."""
    heading = next((i for i, block in enumerate(body) if block.kind == "rubrik"
                    and block.text == "KAMMARRÄTTENS AVGÖRANDE"), None)
    if heading is None:
        return None
    texts = []
    for block in body[heading + 1:]:
        if block.kind == "rubrik":
            break
        texts.append(block.text)
    return " ".join(texts) or None


def doktyp(letterhead):
    if RE_DOM.search(letterhead):
        return "dom"
    if RE_BESLUT.search(letterhead):
        return "beslut"
    return None


def document_blocks(per_page):
    """Per-page paragraphs -> the decision and its kept bilagor as blocks, and
    the doktyp page 1 states."""
    parts = segments(per_page)
    letterhead, decision = _document(parts[0][2])
    out = blocks(decision)
    for kind, label, document_pages in parts[1:]:
        if kind != "bilaga":
            continue
        _letterhead, paras = _document(document_pages)
        # the "Bilaga A" line itself becomes the heading
        paras = [p for p in paras
                 if not (RE_ATTACHMENT.match(p.text) or RE_BILAGA_LABEL.match(p.text))]
        out.append(Block("rubrik", "Bilaga %s" % label if label else "Bilaga",
                         page=document_pages[0][0].page if document_pages[0] else None))
        out.extend(blocks(paras, shift=1))
    return out, doktyp(letterhead)


def body(path, patch_key):
    return document_blocks(paragraphs(pages(path, patch_key)))


def parse(basefile, root):
    """One basefile ("kst/6426-25/2026-03-12") -> artifact dict, body
    citation-scanned."""
    hfd = superseded(root).get(basefile)
    if hfd:
        # the case as HFD decided and published it is the dv source's page
        raise SkipDocument("%s: överklagad; Högsta förvaltningsdomstolen har "
                           "publicerat målet (%s)" % (basefile, hfd))
    record = compress.read_json(record_json(root, basefile))
    path = body_path(root, basefile)
    court, malnummer, date = basefile.split("/")
    if compress.exists(path):
        body_blocks, typ = body(path, ("kkvdomar", basefile))
    else:
        # the invariant `download.resolve` keeps: a record names a PDF only
        # once the PDF is on disk
        assert not record.get("beslut_url"), (
            "%s names a PDF (%s) that is not on disk at %s -- re-run `lagen "
            "kkvdomar download --only %s`" % (basefile, record["beslut_url"],
                                              path, basefile))
        body_blocks, typ = [], None
    return Avgorande(
        court=court.upper(), domstol=record["domstol"],
        malnummer=malnummer, malnummer_lista=record["malnummer"],
        avgorandedatum=date, instans=record["instans"], doktyp=typ,
        arendemening=record.get("arendemening"),
        arendetyp=record.get("arendetyp"), utgang=record.get("avgorande"),
        kortreferat=record.get("kortreferat"), sokande=record.get("sokande"),
        motpart=record.get("motpart"), body=body_blocks,
        domslut=domslut(body_blocks), source_url=record["source_url"],
        document_url=record.get("beslut_url"),
        facsimile_pdf=(str(store_relpath(path, layout.DATA))
                       if compress.exists(path) else None),
        # the decision's own date, so a bare law name resolves to the act in
        # force when the court wrote it
    ).to_artifact(sfs_parser("kkvdomar", ALL_PARSE_TYPES,
                             written=approximate_date(date)))
