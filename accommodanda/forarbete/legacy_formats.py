"""Format adapters for the frozen förarbete corpora (REWRITE.md §7g).

Several förarbete upstreams are dead or historic (data.riksdagen.se's old
dokumentstatus dumps, KB's ABBYY digitizations, the retired TRIPS site), so the
old downloaders are not ported -- the corpus is complete and a one-time import
materializes it into the förarbete vertical. Each upstream stored its bodies in
a different legacy format; this module turns each into the vertical's own
currency -- an import-record metadata ``dict`` or a :class:`lib.pdftext.Para`
stream the shared förarbete ``classify`` consumes -- as pure functions the
import verb (a later task) drives:

  * :func:`dokumentstatus_meta`  -- data.riksdagen.se dokumentstatus XML -> the
    import record fields (basefile/identifier/date/source_url/bilagor).
  * :func:`riksdagen_html_paras` -- the ``text/tml`` body (``<br>``-wrapped
    plaintext) -> a Para stream.
  * :func:`riksdagen_mso_paras`  -- the ``skanning2007`` body (riksdagen's own
    OCR text of the scanned original, as Word-export ``MsoNormal`` HTML) -> a
    Para stream.
  * :func:`abbyy_pages`          -- ABBYY FineReader OCR-XML (propkb, 1867-1970)
    -> ``(pageno, [Para])`` per page.
  * :func:`scanned_pdf_pages`    -- a scanned legacy PDF's OCR text layer (soukb,
    propkb's scan-only props) via ``pdftotext`` -> ``(pageno, [Para])`` per page,
    the fallback for the scans the font-aware ``pdftohtml`` path cannot read.
  * :func:`trips_paras`          -- a TRIPS ``div.body-text`` plaintext-HTML body
    -> a Para stream.

Three of the body formats carry no font/bold signal: the ``text/tml`` body is
``<br>``-hard-wrapped plaintext with no markup but the line break, the TRIPS
body is justified plaintext, and the ABBYY ``<formatting>`` element carries only
``lang`` (verified across the 1867/1908/1958 samples -- no ``ff``/``bold``/size
attributes anywhere) -- their Paras are plain and the förarbete ``classify``
recovers headings from numbering alone, exactly as for a text-inferred PDF. The
``skanning2007`` Word export is the exception: it wraps headings in ``<b>``, so
its Paras carry the bold heading signal.
"""

import re
import subprocess
from datetime import date

from bs4 import BeautifulSoup
from lxml import etree  # ty: ignore[unresolved-import]  # lxml ships no stubs
from lxml import html as lxml_html

# de-hyphenation is the same soft-hyphen rule the PDF verticals already use; the
# ABBYY loader reuses it rather than replicating it (rule:second-use-goes-to-lib)
from ..lib.pdftext import Para, dehyphenate
from ..lib.util import normalize_space

# the ABBYY export namespaces every element; Clark-notation prefix for lookups
ABBYY_NS = "{http://www.abbyy.com/FineReader_xml/FineReader10-schema-v1.xml}"

# a text/tml (and empty-htmlformat) body that is literally this string is a
# never-published proposition; the import verb turns the ValueError into a skip
EJ_UTGIVEN = "Propositionen ej utgiven"

# the ABBYY OCR renders the line-break soft hyphen as U+00AC (NOT SIGN) as often
# as a plain '-'; normalizing it lets the shared `dehyphenate` rule apply
OCR_HYPHEN = "¬"

# the skanning2007 Word export renders the OCR line-break hyphen as a real
# U+00AD SOFT HYPHEN ("depar\xadtementschefen"); removing it de-hyphenates
SOFT_HYPHEN = "\xad"

_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_ISODATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

# the dokumentstatus XML is remote-supplied (data.riksdagen.se): no DTD/entity
# expansion, matching the hardened parser eurlex/parse.py uses for its own
# remote XML (rule:second-use-goes-to-lib candidate once a third source needs it)
_XML_PARSER = etree.XMLParser(resolve_entities=False, load_dtd=False,
                              no_network=True, remove_comments=True,
                              remove_pis=True)


# --- Adapter 1: Riksdagen dokumentstatus XML -----------------------------

def _text(el, tag):
    """The normalized text of ``el``'s ``tag`` child, or None when the element
    is absent or empty (riksdagen uses empty ``<organ/>``-style tags freely)."""
    child = el.find(tag)
    return normalize_space(child.text) if child is not None and child.text else None


def _iso_date(datum):
    """The ISO date part of a ``datum`` ('1971-12-31 00:00:00' -> '1971-12-31'),
    or None when it is missing, malformed, or a placeholder ('0000-00-00',
    '1994-02-30'). Legacy input, so a bad value is rejected to None, not raised."""
    m = _ISODATE.match(datum) if datum else None
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:            # placeholder / impossible date -> recorded as None
        return None


def dokumentstatus_meta(xml_bytes):
    """A data.riksdagen.se dokumentstatus XML -> the förarbete import-record
    fields. ``basefile``/``identifier`` are minted from ``rm`` + ``beteckning``
    so the resulting URI agrees with a FORARBETEN citation by construction;
    ``source_url`` keeps the still-live data.riksdagen.se html endpoint;
    ``htmlformat`` + ``bilagor`` are what the precedence rule reads (a pdf bilaga
    is what lets a scanned/html-ec doc beat this html-only body)."""
    root = etree.fromstring(xml_bytes, parser=_XML_PARSER)
    dok = root.find("dokument")
    rm, beteckning = _text(dok, "rm"), _text(dok, "beteckning")
    if not (rm and beteckning):
        # untrusted remote XML -- a raise, never an assert: under `python -O`
        # an assert is stripped and a None:None basefile would be minted and
        # written to disk as a junk catalog document (rule:errors-drive-retry-use-raise)
        raise ValueError("dokumentstatus missing rm/beteckning (dok_id=%s)"
                         % _text(dok, "dok_id"))
    basefile = "%s:%s" % (rm, beteckning)
    return {
        "basefile": basefile,
        "identifier": "Prop. %s" % basefile,
        "title": _text(dok, "titel"),
        "date": _iso_date(_text(dok, "datum")),
        "dok_id": _text(dok, "dok_id"),
        "source_url": _text(dok, "dokument_url_html"),
        "htmlformat": _text(dok, "htmlformat"),
        "bilagor": [{"filnamn": _text(b, "filnamn"),
                     "filtyp": _text(b, "filtyp"),
                     "fil_url": _text(b, "fil_url")}
                    for b in root.findall("dokbilaga/bilaga")],
    }


# --- shared: hard-wrapped plaintext -> Para stream ------------------------

def _reflow_plaintext(text):
    """Hard-wrapped plaintext -> a Para stream: a blank line splits paragraphs,
    lines within a paragraph reflow onto one line joined with a space, and each
    Para's whitespace is collapsed. Shared by the text/tml and TRIPS bodies,
    which are both justified/wrapped plaintext with no other structure."""
    paras, cur = [], []
    for line in text.splitlines():
        if line.strip():
            cur.append(line)
        elif cur:
            paras.append(Para(normalize_space(" ".join(cur))))
            cur = []
    if cur:
        paras.append(Para(normalize_space(" ".join(cur))))
    return paras


# --- Adapter 2: Riksdagen text/tml HTML body ------------------------------

def riksdagen_html_paras(html_text):
    """A riksdagen ``text/tml`` body -> a Para stream. The body is CRLF
    plaintext whose only markup is ``<br>`` (verified: no other tag, no entity,
    no bold across the 1995/96-2000/01 window), so it reflows as plaintext once
    the line breaks become newlines. The literal body ``Propositionen ej
    utgiven`` is a never-published sentinel -- raised as a ValueError the import
    verb turns into a recorded skip."""
    # the <br> is the logical line break; the source file's own CRLF wrapping is
    # noise, so flatten it to spaces before the <br>s become newlines
    flat = html_text.replace("\r", " ").replace("\n", " ")
    paras = _reflow_plaintext(_BR.sub("\n", flat))
    if [p.text for p in paras] == [EJ_UTGIVEN]:
        raise ValueError("body is the %r never-published sentinel" % EJ_UTGIVEN)
    return paras


# --- Adapter 2b: Riksdagen skanning2007 Word-export HTML -------------------

def riksdagen_mso_paras(html_text):
    """A riksdagen ``skanning2007`` body -> a Para stream. The body is
    data.riksdagen.se's own 2007-scanning OCR text of the paper original,
    exported as Word HTML: one ``<p class=MsoNormal>`` per paragraph carrying
    ``<span>`` font runs, headings wrapped in ``<b>``, and the OCR line-break
    hyphen as U+00AD. Each ``<p>`` becomes one Para -- soft hyphens removed,
    whitespace collapsed, empties dropped -- bold (and lead_bold, matching the
    PDF extractor's "whole line bold implies bold lead" semantics) when the
    paragraph's entire text sits in ``<b>``. Only ``<p>`` elements carry body
    text, so the scanning-disclaimer banner (a bare ``div.brask``) never leaks
    in. Parsed with lxml.html, not BeautifulSoup: a budget prop runs to ~6 MB
    and 2,335 docs take this route, where lxml's 11x parse speed (0.1 s vs
    1.1 s on prop 1971:30) is material."""
    paras = []
    for p in lxml_html.fromstring(html_text).iter("p"):
        text = normalize_space(p.text_content().replace(SOFT_HYPHEN, ""))
        if not text:
            continue
        bold = text == normalize_space("".join(
            b.text_content() for b in p.iter("b")).replace(SOFT_HYPHEN, ""))
        paras.append(Para(text, bold=bold, lead_bold=bold))
    return paras


# --- Adapter 3: ABBYY FineReader OCR-XML ----------------------------------

def _join_ocr_lines(lines):
    """Join a ``<par>``'s OCR line texts into one Para string, de-hyphenating a
    line-break soft hyphen (rendered as '-' or U+00AC) via the shared rule."""
    acc = ""
    for line in lines:
        if not line:
            continue
        if line.endswith(OCR_HYPHEN):
            line = line[:-1] + "-"
        acc = dehyphenate(acc, line)
    return acc


def abbyy_pages(xml_path):
    """``[(pageno, [Para])]`` for an ABBYY FineReader OCR-XML export, pageno the
    1-based page order. Each ``<par>`` of a ``blockType="Text"`` block becomes
    one Para -- its ``<line>``s joined with de-hyphenation, whitespace collapsed;
    empty pars and non-Text blocks (separators, pictures, tables -- a table's
    cell text would otherwise leak in) are skipped. Streamed with ``iterparse``
    plus a per-page clear, since a budget prop's XML runs to tens of MB."""
    out = []
    for _event, page in etree.iterparse(str(xml_path), events=("end",),
                                        tag=ABBYY_NS + "page"):
        paras = []
        for block in page.iter(ABBYY_NS + "block"):
            if block.get("blockType") != "Text":
                continue
            for par in block.iter(ABBYY_NS + "par"):
                text = _join_ocr_lines(
                    normalize_space("".join(line.itertext()))
                    for line in par.iter(ABBYY_NS + "line"))
                if text:
                    paras.append(Para(text))
        out.append((len(out) + 1, paras))
        page.clear()                        # free the parsed page and its
        while page.getprevious() is not None:   # already-seen siblings -- bound
            del page.getparent()[0]             # memory across a huge document
    return out


# --- Adapter 3b: scanned-PDF OCR text layer (pdftotext) -------------------

def scanned_pdf_pages(pdf_path):
    """``[(pageno, [Para])]`` for a scanned legacy PDF, extracted with
    ``pdftotext``. The KB/older scans (soukb, propkb's scan-only props) carry an
    OCR text layer the font-aware ``pdftohtml -xml`` path renders empty -- and
    sometimes errors on -- while ``pdftotext`` reads it; the parse verb falls back
    here when the font path yields no blocks. Pages are split on the U+000C form
    feed ``pdftotext`` emits between pages, so pageno stays the printed page for
    ``#sid{N}`` anchors (same page ≈ printed-page assumption as the PDF route). No
    font signal survives, so the Paras are plain and the förarbete ``classify``
    recovers headings from numbering, as for a text-inferred body. OCR noise (the
    KB digitization banner, stray glyphs) rides along -- accepted against the
    alternative of an empty body for the whole 5,807-doc scanned corpus."""
    text = subprocess.run(["pdftotext", str(pdf_path), "-"], capture_output=True,
                          check=True).stdout.decode("utf-8", "replace")
    return [(pageno, _reflow_plaintext(page))
            for pageno, page in enumerate(text.split("\f"), 1)]


# --- Adapter 4: TRIPS plaintext-HTML --------------------------------------

def trips_paras(html_text):
    """A TRIPS ``div.body-text`` plaintext-HTML body -> a Para stream. The body
    is hard-wrapped justified plaintext inside that div; it reflows exactly like
    the text/tml body. ``html.parser`` tolerates the unescaped ``<``/``>`` the
    legacy pages are known to contain."""
    body = BeautifulSoup(html_text, "html.parser").find("div", class_="body-text")
    if body is None:
        # untrusted legacy html -- a raise, never an assert (a stripped assert
        # under `python -O` would fall through with body=None and crash on
        # .get_text() instead of failing this one document cleanly); the
        # import-time div.body-text guard (_pick_proptrips/import_dirtrips)
        # keeps this from firing in the ordinary case (rule:errors-drive-retry-use-raise)
        raise ValueError("TRIPS html has no div.body-text")
    return _reflow_plaintext(body.get_text())
