"""lib/pdftext: header-strip identifier, extraction flags, page flattening.

Regression for a real bug found via the remisser vertical: an `identifier`
that happens to recur as ordinary self-reference inside body prose (an
organisation naming itself in its own letter, not a repeated running header)
must not be silently deleted from that prose. `None`/`""` means "no header to
strip", not "strip the empty pattern" (which used to mangle every line).

Also locks in the `hidden` flag and `flat_lines` page flattening added when
eurlex/parse_pdf was folded into this module (it had forked the extractor,
including the top-only span-grouping bug `_lines` documents as fixed), and the
baseline span-grouping itself."""

import os
import pathlib
import re
import subprocess
from types import SimpleNamespace

import brotli
import pytest

from accommodanda.lib import layout, pdftext
from accommodanda.lib.pdftext import (
    FIGURE_MIN,
    PAGE_STRIDE,
    Figure,
    Line,
    Para,
    Run,
    _lines,
    classify_letterhead,
    flat_lines,
    is_figure,
    is_italic_subheading,
    is_page_number,
    letterhead_footnotes,
    line_from_runs,
    page_paragraphs,
    pdf_pages,
    points_from_pdftohtml,
)


def _line(text, top, bold=False):
    return Line(text, top, bold, bold, False)


def test_none_identifier_does_not_touch_body_text():
    lines = [_line("Ale kommun välkomnar utredningens ambition", 100)]
    paras = page_paragraphs(lines, None, 1)
    assert paras[0].text == "Ale kommun välkomnar utredningens ambition"


def test_empty_string_identifier_does_not_touch_body_text():
    lines = [_line("Ale kommun välkomnar utredningens ambition", 100)]
    paras = page_paragraphs(lines, "", 1)
    assert paras[0].text == "Ale kommun välkomnar utredningens ambition"


def test_a_real_identifier_strips_headers_but_not_body_text():
    """An identifier is honoured when one is actually known (the DV/JO/ARN
    case): a running-header line -- the identifier plus at most a page
    number/date -- is stripped, but a body line that merely *contains* the
    identifier keeps it (A5: 'Allmänna reklamationsnämnden gjorde följande
    bedömning' must not lose its subject)."""
    lines = [_line("Riksdagens ombudsmän 2026-01-01", 100),
             _line("Klagomålet rör Riksdagens ombudsmän i ett tidigare ärende", 120)]
    paras = page_paragraphs(lines, "Riksdagens ombudsmän", 1)
    joined = " ".join(p.text for p in paras)
    assert "2026-01-01" in joined                          # header residue kept
    assert not joined.startswith("Riksdagens")             # header itself gone
    assert "Klagomålet rör Riksdagens ombudsmän i ett tidigare ärende" in joined


PAGE_XML = (b"<pdf2xml>"
            b"<page number='1' height='1200'>"
            b"<text top='10' left='5' height='10'>first page</text></page>"
            b"<page number='2' height='1200'>"
            b"<text top='10' left='5' height='10'>second page</text></page>"
            b"</pdf2xml>")


def _fake_run(calls):
    """As `_fake_pdftohtml`, for the tests that only inspect the command: the
    converter is given an output base in a temp directory and reads the XML back
    from it, so the stub has to write the file rather than return stdout."""
    def run(cmd, capture_output, check):
        calls.append(cmd)
        if cmd[0] == "pdftohtml":
            pathlib.Path(cmd[-1] + ".xml").write_bytes(PAGE_XML)
        return SimpleNamespace(stdout=PAGE_XML)
    return run


def test_pdf_pages_hidden_flag(monkeypatch):
    """`hidden=True` adds -hidden (the invisible ocrmypdf text layer); the
    default command is unchanged."""
    calls = []
    monkeypatch.setattr(pdftext.subprocess, "run", _fake_run(calls))
    list(pdf_pages("doc.pdf"))
    list(pdf_pages("doc.pdf", hidden=True))
    assert "-hidden" not in calls[0]
    assert "-hidden" in calls[1]
    # the trailing output base is a fresh temp directory each call (which is
    # what keeps poppler's extracted images out of the corpus), so the commands
    # are compared without it: -hidden is the only difference that means anything
    assert [c for c in calls[1][:-1] if c != "-hidden"] == calls[0][:-1]


def test_flat_lines_offsets_pages(monkeypatch):
    """flat_lines turns page breaks into large vertical gaps: line tops are
    strictly increasing across the page boundary, far beyond any body gap."""
    calls = []
    monkeypatch.setattr(pdftext.subprocess, "run", _fake_run(calls))
    lines = flat_lines("doc.pdf")
    assert [l.text for l in lines] == ["first page", "second page"]
    assert lines[1].top - lines[0].top == PAGE_STRIDE


def test_lines_groups_spans_on_shared_baseline():
    """The span-grouping fix eurlex/parse_pdf now inherits: a large heading
    number beside its smaller-font title shares a baseline but not a top; a
    top-only grouping split them and reflowed '9 Författningskommentar' to
    'Författningskommentar 9'."""
    spans = [(10, 0, 30, "9", True, False, 20, 20, "times"),           # big digit
             (20, 50, 30, "Författningskommentar", True, False, 250, 15,
              "times")]                                                # smaller title
    out = _lines(spans)
    assert [l.text for l in out] == ["9 Författningskommentar"]
    assert out[0].top == 10 and out[0].bold
    assert out[0].size == 20                     # the line takes the largest run's size
    assert [r.text for r in out[0].runs] == ["9", "Författningskommentar"]


def test_wrapped_heading_folds_into_one_paragraph():
    # prop 2013/14:116 ch 5: a large (not bold) chapter heading wraps over two
    # lines -- one logical heading, not a rubrik + an orphan stycke
    lines = [Line("brödtext i normal storlek.", 385, False, False, False, 15),
             Line("5 Mer fokuserad nedsättning av", 456, False, False, False, 23),
             Line("socialavgifterna för de yngsta", 482, False, False, False, 23),
             Line("Regeringens förslag: För personer", 528, False, False, False, 15)]
    out = page_paragraphs(lines, None, 19)
    assert [p.text for p in out] == [
        "brödtext i normal storlek.",
        "5 Mer fokuserad nedsättning av socialavgifterna för de yngsta",
        "Regeringens förslag: För personer"]
    assert out[1].size == 23


def test_adjacent_headings_of_different_size_do_not_fold():
    # a chapter heading directly followed by its first subsection heading
    lines = [Line("brödtext body body body body.", 100, False, False, False, 15),
             Line("7 Konsekvensanalys", 160, False, False, False, 23),
             Line("7.1 Offentligfinansiella effekter", 186, True, True, False, 17),
             Line("Mer brödtext följer här nedan.", 220, False, False, False, 15)]
    out = page_paragraphs(lines, None, 25)
    assert [p.text for p in out][1:3] == [
        "7 Konsekvensanalys", "7.1 Offentligfinansiella effekter"]


def test_numbered_continuation_does_not_fold_into_previous_heading():
    # two stacked same-size numbered headings stay separate (the continuation
    # guard: a wrapped line never opens its own numbered heading). The page is
    # body-dominated, as real pages are -- the body size is the *mode* of the
    # page's line sizes.
    lines = [Line("body text at normal size here.", 66, False, False, False, 15),
             Line("more body text at normal size.", 83, False, False, False, 15),
             Line("yet more body at normal size..", 100, False, False, False, 15),
             Line("6 Ikraftträdande- och", 160, False, False, False, 23),
             Line("övergångsbestämmelser", 186, False, False, False, 23),
             Line("7 Konsekvensanalys", 212, False, False, False, 23),
             Line("body text at normal size again.", 250, False, False, False, 15)]
    out = page_paragraphs(lines, None, 30)
    assert [p.text for p in out][1:3] == [
        "6 Ikraftträdande- och övergångsbestämmelser", "7 Konsekvensanalys"]


# --- the pdftohtml output cache ---------------------------------------------
#
# `pdftohtml` is the dominant cost of parsing a PDF-bodied document, and a
# downloaded PDF never changes, so its output is cached brotli-compressed and
# the converter is not run again.

def _fake_pdftohtml(monkeypatch, xml=b"<pdf2xml><page number=\"1\"/></pdf2xml>"):
    """Stand in for the converter, counting how often it actually runs.

    Writes its output to `<base>.xml` the way poppler does when given an output
    base, rather than to stdout: that base is a temporary directory, which is
    what keeps the images poppler extracts alongside it out of the corpus."""
    calls = []

    class Done:
        stdout = xml

    def run(args, **kw):
        calls.append(args)
        if args[0] == "pdftohtml":
            pathlib.Path(args[-1] + ".xml").write_bytes(xml)
        return Done()

    monkeypatch.setattr(pdftext.subprocess, "run", run)
    return calls


def _xml_cache(pdf, hidden=False):
    """Where `pdftohtml_xml` caches its conversion of `pdf` -- one stable entry
    per format; the command it was produced by is recorded inside it."""
    return layout.pdf_conversion(pdf, "hidden.xml" if hidden else "xml")


def test_pdftohtml_xml_converts_once_then_serves_the_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(layout, "DATA", tmp_path)
    monkeypatch.setattr(layout, "PDFCONV", tmp_path / "cache" / "pdfconv")
    pdf = tmp_path / "downloaded" / "x" / "a.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")
    calls = _fake_pdftohtml(monkeypatch)

    first = pdftext.pdftohtml_xml(pdf)
    second = pdftext.pdftohtml_xml(pdf)
    assert first == second                      # byte-identical either way
    assert len(calls) == 1                      # converted once only
    stored = brotli.decompress(_xml_cache(pdf).read_bytes())
    assert stored.partition(b"\n")[2] == first     # digest line, then payload


def test_pdftohtml_xml_reconverts_when_the_pdf_is_newer(monkeypatch, tmp_path):
    # a re-downloaded PDF moves its mtime past the cache entry's, which is what
    # makes the entry stale -- otherwise a refetched document would keep serving
    # the old conversion forever
    monkeypatch.setattr(layout, "DATA", tmp_path)
    monkeypatch.setattr(layout, "PDFCONV", tmp_path / "cache" / "pdfconv")
    pdf = tmp_path / "downloaded" / "x" / "a.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")
    calls = _fake_pdftohtml(monkeypatch)
    pdftext.pdftohtml_xml(pdf)
    cache_mtime = _xml_cache(pdf).stat().st_mtime_ns
    os.utime(pdf, ns=(cache_mtime + 10**9, cache_mtime + 10**9))
    pdftext.pdftohtml_xml(pdf)
    assert len(calls) == 2


def test_pdftohtml_xml_keys_the_hidden_variant_separately(monkeypatch, tmp_path):
    # -hidden is a different conversion (it pulls in the invisible OCR layer),
    # so it must not be served from the plain entry
    monkeypatch.setattr(layout, "DATA", tmp_path)
    monkeypatch.setattr(layout, "PDFCONV", tmp_path / "cache" / "pdfconv")
    pdf = tmp_path / "downloaded" / "x" / "a.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")
    calls = _fake_pdftohtml(monkeypatch)
    pdftext.pdftohtml_xml(pdf)
    pdftext.pdftohtml_xml(pdf, hidden=True)
    assert len(calls) == 2
    assert "-hidden" in calls[1] and "-hidden" not in calls[0]
    assert (layout.pdf_conversion(pdf, "xml")
            != layout.pdf_conversion(pdf, "hidden.xml"))


def test_a_pdf_outside_the_data_root_is_simply_not_cached(monkeypatch, tmp_path):
    # an ad-hoc path has no stable place in the cache tree; it converts directly
    monkeypatch.setattr(layout, "DATA", tmp_path / "data")
    (tmp_path / "data").mkdir()
    pdf = tmp_path / "loose.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert layout.pdf_conversion(pdf, "xml") is None
    calls = _fake_pdftohtml(monkeypatch)
    pdftext.pdftohtml_xml(pdf)
    pdftext.pdftohtml_xml(pdf)
    assert len(calls) == 2


def test_pdftotext_text_is_cached_too(monkeypatch, tmp_path):
    # a scanned document pays for BOTH converters -- the font path runs first and
    # finds nothing -- and there are 5 807 of them, so the text route is cached
    # on the same terms
    monkeypatch.setattr(layout, "DATA", tmp_path)
    monkeypatch.setattr(layout, "PDFCONV", tmp_path / "cache" / "pdfconv")
    pdf = tmp_path / "downloaded" / "x" / "scan.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")
    calls = _fake_pdftohtml(monkeypatch, xml="sidan ett\x0csidan två".encode())

    assert pdftext.pdftotext_text(pdf) == "sidan ett\x0csidan två"
    assert pdftext.pdftotext_text(pdf) == "sidan ett\x0csidan två"
    assert len(calls) == 1 and calls[0][0] == "pdftotext"
    # and it does not collide with the xml entry for the same PDF
    assert (layout.pdf_conversion(pdf, "txt")
            != layout.pdf_conversion(pdf, "xml"))


# ---- OCR fallback (shared by eurlex/parse_pdf and remisser/parse) ----------

def test_ocr_missing_binary_raises(tmp_path, monkeypatch):
    """A missing ocrmypdf is a broken environment, not a bad document: it must
    propagate (rule:fail-fast), never turn into an empty artifact."""
    def no_binary(cmd, check, capture_output):
        raise FileNotFoundError("ocrmypdf")
    monkeypatch.setattr(pdftext.subprocess, "run", no_binary)
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(FileNotFoundError):
        pdftext.ocr_pdf(pdf, "swe")


def test_ocr_per_document_failure_propagates(tmp_path, monkeypatch):
    """A per-document OCR failure raises CalledProcessError for the build
    driver's per-document boundary to record -- not swallowed here."""
    def fails(cmd, check, capture_output):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(pdftext.subprocess, "run", fails)
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(subprocess.CalledProcessError):
        pdftext.ocr_pdf(pdf, "swe")


def test_ocr_cached_sidecar_skips_subprocess(tmp_path, monkeypatch):
    monkeypatch.setattr(pdftext.subprocess, "run",
                        lambda *a, **kw: pytest.fail("subprocess ran"))
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    cached = tmp_path / ".scan.ocr.pdf"
    cached.write_bytes(b"%PDF-1.4")
    assert pdftext.ocr_pdf(pdf, "swe") == cached


# --------------------------------------------------------------------------
# letterhead_footnotes -- what classify_letterhead deliberately drops
# --------------------------------------------------------------------------

NO_MARGIN = re.compile(r"^$")
NO_MASTHEAD = re.compile(r"(?!)")


def _stream(*small):
    """A realistic Para stream: body prose at 17pt (the mode, and so the
    running size) plus the small paragraphs under test."""
    return [Para(text="Löpande text vid brödtextstorlek.", size=17)] * 6 + [
        Para(text=t, size=9) for t in small]


def test_letterhead_footnotes_returns_what_the_classifier_dropped():
    notes = letterhead_footnotes(
        _stream("12 Se riktlinjer 05/2020 om samtycke, punkt 42."),
        NO_MARGIN, NO_MASTHEAD)
    assert notes == [("12", "Se riktlinjer 05/2020 om samtycke, punkt 42.")]
    # and the block stream is untouched -- the two readings are independent
    assert not any(kind == "fotnot" for kind, _t, _l in classify_letterhead(
        _stream("12 Se riktlinjer 05/2020."), NO_MARGIN, NO_MASTHEAD))


def test_letterhead_footnotes_drops_the_furniture_that_shares_the_size():
    masthead = re.compile(r"\s*Postadress:.*")
    notes = letterhead_footnotes(
        _stream("Postadress: Box 8114, 104 20 Stockholm",   # the masthead
                "7",                                        # a page number
                "3 (12)",                                   # a page mark
                "En fotnot som är tillräckligt lång för att vara prosa."),
        NO_MARGIN, masthead)
    assert notes == [("", "En fotnot som är tillräckligt lång för att vara prosa.")]


def test_a_note_without_a_marker_keeps_an_empty_mark():
    assert letterhead_footnotes(
        _stream("Ingen markör inleder denna anmärkning."),
        NO_MARGIN, NO_MASTHEAD) == [("", "Ingen markör inleder denna anmärkning.")]


def _sized(text, top, size, bold=False):
    return Line(text, top, bold, bold, False, size)


def test_a_bold_heading_does_not_swallow_the_body_that_follows():
    # D8: `heading()` also calls a line heading-fonted when it is merely larger
    # than the page's dominant body size -- and a JO decision's opening
    # paragraph is. With only size and leading checked, the wrap rule folded
    # that paragraph into the bold heading above it, so "Anmälan" arrived as a
    # 40-word rubrik and the innehåll panel read as three paragraphs.
    lines = [_sized("Anmälan", 100, 17, bold=True),
             _sized("AA klagade, i en anmälan som kom in till JO", 120, 17),
             _sized("den 4 april 2005.", 140, 17),
             _sized("Utredning", 200, 17, bold=True),
             _sized("BB:s akt lånades in och granskades.", 220, 17)]
    paras = page_paragraphs(lines, None, 1)
    assert [(p.text, p.bold) for p in paras] == [
        ("Anmälan", True),
        ("AA klagade, i en anmälan som kom in till JO den 4 april 2005.", False),
        ("Utredning", True),
        ("BB:s akt lånades in och granskades.", False)]


def test_a_wrapped_heading_still_folds_when_the_weight_agrees():
    # the case the wrap rule exists for: a title set across two lines, both
    # bold and the same size, a heading's leading apart
    lines = [_sized("Kritik mot en överförmyndarnämnd för handläggningen", 100,
                    19, bold=True),
             _sized("av en begäran om byte av god man", 120, 19, bold=True),
             _sized("Anmälan", 200, 17, bold=True)]
    paras = page_paragraphs(lines, None, 1)
    assert paras[0].text == ("Kritik mot en överförmyndarnämnd för "
                             "handläggningen av en begäran om byte av god man")
    assert paras[1].text == "Anmälan"


def test_a_size_only_heading_still_wraps():
    # a prop's numbered chapter headings are large but not bold, so a wrap with
    # neither line bold must keep folding -- the weights agree, they are just
    # both False
    lines = [_sized("Överväganden och förslag om en ny", 100, 16),
             _sized("ordning för prövningen", 118, 16)]
    # the body has to dominate for 16pt to read as *larger than* body size
    body = [_sized("Regeringen föreslår att detta ska gälla.", 200, 12),
            _sized("Skälen för regeringens förslag är dessa.", 218, 12),
            _sized("Remissinstanserna tillstyrker.", 236, 12)]
    paras = page_paragraphs(lines + body, None, 1)
    assert paras[0].text == ("Överväganden och förslag om en ny ordning för "
                             "prövningen")


def test_a_parenthesised_page_number_line_is_dropped():
    # D7: only the bare number was dropped, so every föreskrift page carried
    # its "1 (3)" footer into the body -- as a heading, into the innehåll
    # panel, and splitting the sentence that ran across the page break
    lines = [_line("unionens institutioner, organ och", 900),
             _line("1 (3)", 1095)]
    assert [p.text for p in page_paragraphs(lines, None, 1)] == [
        "unionens institutioner, organ och"]


def test_a_page_number_line_must_be_this_page_s_number():
    # anchored to the page's own number, so a line of body text that happens to
    # read like a footer survives
    lines = [_line("1 (3)", 100), _line("brödtext", 200)]
    assert "1 (3)" in " ".join(p.text for p in page_paragraphs(lines, None, 2))
    # ...and is dropped on the page it does number
    assert "1 (3)" not in " ".join(p.text
                                   for p in page_paragraphs(lines, None, 1))


def test_page_number_forms():
    assert is_page_number("4/12", 4)
    assert is_page_number("Sida 4 av 12", 4)
    assert is_page_number("sid 4", 4)
    assert not is_page_number("4 § Om ansökan", 4)
    assert not is_page_number("(3)", 3)


def test_an_unnumbered_page_has_no_page_number_line():
    """`printed_pages` leaves a page past a numbering restart that names no
    bilaga without a printed number (prop. 2008/09:1's separately paginated
    utgiftsområden). Nothing on such a page can be its page number, and the
    patterns only hold because they are anchored to the page's own -- formatting
    one against None raised instead, which took 4,586 förarbeten down with it
    the first time every one of them was reparsed."""
    assert not is_page_number("4/12", None)
    assert not is_page_number("1 (3)", None)
    assert not is_page_number("Sida 4 av 12", None)


# --------------------------------------------------------------------------
# what a proposition's typography marks, and how extraction loses it
# --------------------------------------------------------------------------

def _span(top, left, right, text, *, size=15, bold=False, italic=False,
          height=13, font="timesnewroman"):
    """One pdftohtml <text> fragment, in the tuple shape `_lines` consumes."""
    return (top, left, top + height, text, bold, italic, right, size, font)


def test_a_margin_fragment_does_not_split_a_heading():
    """A proposition prints its running header in the left margin at a `top`
    between a chapter number and its title, while number and title share a
    baseline. Walked in `top` order the header lands between them and each span
    is only compared with the group last opened, so the heading came apart --
    the title became a stycke and the number a paragraph of its own."""
    lines = _lines([
        _span(61, 251, 507, "Ärendet och dess beredning ", size=23, height=25),
        _span(63, 55, 163, "Prop. 2017/18:89 ", height=13),
        _span(65, 183, 194, "3", size=23, height=20),
    ])
    assert [l.text for l in lines] == ["Prop. 2017/18:89",
                                       "3 Ärendet och dess beredning"]


def test_a_font_change_is_not_a_word_boundary():
    """poppler splits a line at every font change, so an italic phrase inside a
    sentence arrives as three runs and the punctuation after it starts its own.
    Joining those on a space wrote "bilaga 1 ." -- a space before every period
    that trails an italic phrase, throughout the corpus. Whether the seam
    carried a space is recorded only in the runs' own edge whitespace: the
    geometry cannot say, both seams here leaving the runs touching."""
    line, = _lines([
        _span(194, 183, 286, "lagförslag finns i "),
        _span(194, 286, 335, "bilaga 2", italic=True),
        _span(194, 335, 629, ". Utredningens betänkande har remissbehandlats. "),
    ])
    assert line.text == ("lagförslag finns i bilaga 2. Utredningens "
                         "betänkande har remissbehandlats.")


def test_an_indented_first_line_starts_a_paragraph():
    """Swedish government typography marks a new paragraph by indenting its
    first line, not by leaving space above it -- the ordinary line-height runs
    between. A gap rule alone ran whole sections together, every paragraph after
    the first."""
    lines = _lines([
        _span(228, 183, 629, "ställning av remissvaren finns tillgänglig i "),
        _span(245, 183, 310, "Justitiedepartementet (Ju2015/02740/L4). "),
        _span(263, 196, 629, "När det gäller utkontraktering av "),
        _span(280, 183, 629, "säkerhetskänslig verksamhet har händelser. "),
    ])
    paras = page_paragraphs(lines, None, 32, indent_breaks=True)
    assert len(paras) == 2
    assert paras[1].text.startswith("När det gäller")
    # ...and only where the caller says the document is set that way. It is a
    # claim about a förarbete's typography, not about PDFs, so the other
    # PDF-bodied sources (whose segmentation it moves by +8% to +50%, some of
    # it through the middle of a sentence) keep the gap rule alone.
    assert len(page_paragraphs(lines, None, 32)) == 1


def test_an_italic_line_of_its_own_is_a_subheading():
    """A förarbete sets its unnumbered subheadings in italics at body size --
    "Lagrådet", "Skälen för regeringens förslag" -- not in bold. Nothing else
    about them says heading, so they read as the first line of the paragraph
    beneath and the two ran together."""
    assert is_italic_subheading("Lagrådet", True)
    assert is_italic_subheading("Skälen för regeringens förslag", True)
    # an italic clause inside prose is not one: it carries its terminator, or
    # runs on past a heading's length
    assert not is_italic_subheading("Lagrådet", False)
    assert not is_italic_subheading("En ny säkerhetsskyddslag.", True)
    assert not is_italic_subheading("x" * 80, True)


def test_the_italic_subheading_breaks_the_paragraph_after_it():
    lines = [
        Line("förfaranden inte får genomföras.", 677, False, False, False, 15,
             [Run(183, 386, "förfaranden inte får genomföras.", False, False, 15)]),
        Line("Lagrådet", 711, False, False, True, 15,
             [Run(183, 242, "Lagrådet", False, True, 15)]),
        Line("Regeringen beslutade den 16 november 2017 att inhämta", 734,
             False, False, False, 15,
             [Run(183, 629, "Regeringen beslutade den 16 november 2017 att "
                            "inhämta", False, False, 15)]),
    ]
    assert [p.text for p in page_paragraphs(lines, None, 32)] == [
        "förfaranden inte får genomföras.",
        "Lagrådet",
        "Regeringen beslutade den 16 november 2017 att inhämta"]


def test_a_poppler_flag_change_reconverts_in_place(monkeypatch, tmp_path):
    """The cache keys on the PDF's mtime, which a flag change does not move, so
    an entry naming only the output format kept serving bytes produced by the
    old command: dropping `-i` ("ignore images"), which had been discarding
    every figure in the corpus, appeared to do nothing at all.

    The command's digest is recorded *in* the entry, so a change reconverts and
    overwrites. Keying the filename on it instead would leave the superseded
    file on disk forever -- 4.5 GB of orphans the first time, and the whole
    cache again on every future flag change."""
    monkeypatch.setattr(layout, "DATA", tmp_path)
    monkeypatch.setattr(layout, "PDFCONV", tmp_path / "cache" / "pdfconv")
    pdf = tmp_path / "downloaded" / "x" / "a.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")
    calls = _fake_pdftohtml(monkeypatch)

    pdftext.pdftohtml_xml(pdf)
    assert len(calls) == 1
    entries = list(_xml_cache(pdf).parent.glob("*.br"))
    assert len(entries) == 1

    # the same PDF, converted by a different command
    real = pdftext.pdftohtml_xml
    monkeypatch.setattr(pdftext, "command_digest", lambda args: "deadbeef")
    real(pdf)
    assert len(calls) == 2                       # reconverted, not served
    assert list(_xml_cache(pdf).parent.glob("*.br")) == entries   # in place


def test_which_rasters_count_as_an_illustration():
    """The shape filter that separates a figure from page furniture, pinned to
    the measurements it was tuned against: prop. 2017/18:89 sets its text
    77-523 px and prints its säkerhetsskydd pyramid 145 px wide inside that
    measure, while 2,798 of the 3,637 placements over 40 förarbeten are bullet
    glyphs and hairline rules under 60 px. Loosening this drops illustrations
    or drags letterhead marks into the reading text, in neither case visibly."""
    margins = (77, 523)                      # a 446 px measure
    pyramid = Figure(40, 190, 338, 145, 114)
    assert is_figure(pyramid, margins)
    # a bullet glyph: inside the margins, far too small in both dimensions
    assert not is_figure(Figure(40, 90, 200, 8, 8), margins)
    # a letterhead mark: big enough, but set outside the text margins
    assert not is_figure(Figure(1, 20, 30, 200, 60), margins)
    # ...and one that starts inside but runs past the right margin
    assert not is_figure(Figure(1, 400, 30, 200, 60), margins)
    # a tall narrow rule counts: the test is either dimension, not both
    assert is_figure(Figure(5, 80, 100, 20, int(FIGURE_MIN * 446) + 1), margins)
    # a scan has no placed text, so no margins and nothing to judge against
    assert not is_figure(pyramid, (None, None))


def test_pdftohtml_geometry_converts_to_pdf_points():
    """poppler reports its XML geometry in its own pixel space (1.5 px per point
    at its default resolution), not in points, so the figure coordinates it
    gives cannot be handed to the crop renderer as they stand -- prop.
    2017/18:89's illustration sits at pdftohtml (331, 338) on a 701 px wide page
    that measures 467.76 pt, and cropping at those raw numbers lands on body
    text a third of the way past it."""
    box = points_from_pdftohtml(701, 467.76, (331, 338, 145, 114))
    assert [round(v, 1) for v in box] == [220.9, 225.5, 317.6, 301.6]
    # a page whose two measurements agree needs no scaling
    assert points_from_pdftohtml(600, 600, (10, 20, 30, 40)) == [10, 20, 40, 60]


def test_a_margin_header_sharing_a_baseline_is_still_stripped():
    """Grouping spans in baseline order (which is what stopped a header from
    splitting a heading) also merges a margin header onto the first line of
    prose it shares a baseline with. `_strip_header_runs` reads its offsets
    from `_join_runs`, the function that actually assembles the line -- deriving
    them as "each run plus a joining space" was right only while that was the
    join, and once runs were butted together and spaced by geometry no boundary
    lined up, so the identifier appeared inside the body text of every such
    page."""
    lines = _lines([
        _span(63, 55, 163, "Prop. 2017/18:89 "),
        _span(60, 183, 190, "•"),
        _span(64, 204, 628, "Ett bortfall av eller en svår störning"),
    ])
    paras = page_paragraphs(lines, "Prop. 2017/18:89", 40)
    assert paras[0].text == "• Ett bortfall av eller en svår störning"


def test_a_line_that_is_only_the_header_drops_out():
    lines = _lines([_span(63, 55, 163, "Prop. 2017/18:89 ")])
    assert page_paragraphs(lines, "Prop. 2017/18:89", 40) == []


# ---- running page furniture, discovered by shape ---------------------------
# Regression for a real remisser defect: sou/2026-20/ydre-kommun's letterhead
# footer is spliced into the middle of a sentence ("...i kommuner Datum Sida
# Ydre kommun 2026-05-19 KS 2026/130 4(5) med ett redan högt skattetryck..."),
# so a verbatim quote spanning the page break matched nothing in the artifact.

def _page(pageno, rows):
    """(pageno, [Line]) from (text, top, size) triples."""
    return (pageno, [pdftext.Line(text=t, top=top, bold=False, lead_bold=False,
                                  italic=False, size=size)
                     for t, top, size in rows])


def _furniture_pages():
    """Four pages whose footer repeats with a changing page number and date."""
    return [_page(n, [("Ydre kommun %d(4)" % n, 10, 8),
                      ("Datum 2026-05-1%d KS 2026/130" % n, 20, 8),
                      ("brödtext på sidan %d som fortsätter" % n, 500, 11),
                      ("Ydre kommun %d(4)" % n, 990, 8)])
            for n in range(1, 5)]


def test_strip_page_furniture_drops_a_repeating_numbered_footer():
    out = pdftext.strip_page_furniture(_furniture_pages())
    kept = [l.text for _pageno, lines in out for l in lines]
    # the page number differs on every page, so only digit-masked matching finds it
    assert not [t for t in kept if "Ydre kommun" in t]
    assert not [t for t in kept if t.startswith("Datum")]
    assert len(kept) == 4 and all(t.startswith("brödtext") for t in kept)


def test_strip_page_furniture_spares_a_single_page():
    """One page cannot establish that anything recurs, and leaving a masthead in
    is much cheaper than deleting a real sentence. The floor is two rather than
    three deliberately: a third of these answers are one or two pages, and at
    three they were never stripped at all. At two, a line must appear on *both*
    pages, which is still evidence -- the digit masking is what makes "1(2)" and
    "2(2)" the same line."""
    pages = _furniture_pages()[:1]
    assert pdftext.strip_page_furniture(pages) == pages


def test_strip_page_furniture_uses_a_two_page_document():
    kept = [l.text for _pageno, lines in
            pdftext.strip_page_furniture(_furniture_pages()[:2]) for l in lines]
    assert not [t for t in kept if "Ydre kommun" in t]
    assert all(t.startswith("brödtext") for t in kept)


def test_strip_page_furniture_spares_body_text_that_repeats():
    """A refrain in the body is not furniture: it is body-positioned and
    body-sized, so two of the three signals are absent."""
    pages = [_page(n, [("Ydre kommun %d(4)" % n, 10, 8),
                       ("Kommunen avstyrker förslaget", 500, 11),
                       ("annan brödtext %d" % n, 600, 11)])
             for n in range(1, 5)]
    kept = [l.text for _pageno, lines in pdftext.strip_page_furniture(pages)
            for l in lines]
    assert kept.count("Kommunen avstyrker förslaget") == 4
    assert not [t for t in kept if "Ydre kommun" in t]


def test_join_across_pages_rejoins_a_split_sentence():
    joined = pdftext.join_across_pages([
        ["Risken är att både invånare och företag i kommuner"],
        ["med ett redan högt skattetryck blir dubbelbestraffade."]])
    assert joined == ["Risken är att både invånare och företag i kommuner "
                      "med ett redan högt skattetryck blir dubbelbestraffade."]


def test_join_across_pages_leaves_real_paragraph_boundaries_alone():
    # the first page ends a sentence, and the next starts with a capital
    assert pdftext.join_across_pages([
        ["Kommunen tillstyrker förslaget."],
        ["Vad gäller finansieringen har kommunen synpunkter."]]) == [
        "Kommunen tillstyrker förslaget.",
        "Vad gäller finansieringen har kommunen synpunkter."]
    # a mid-page paragraph is never joined to the one before it
    assert pdftext.join_across_pages([
        ["slutar utan punkt", "börjar med gemen men är inte sidans första"]]) == [
        "slutar utan punkt", "börjar med gemen men är inte sidans första"]


# ---- footnotes, for a source that does not want them ------------------------
# Regression for sou/2026-9/kommerskollegium: the note and its marker land
# inside the body sentence citing them, so the text read "...de som registreras
# 7 EU-kommissionens vägledning, C (2023)1392 slutlig tilldelas ett
# samordningsnummer" and no quote spanning the splice matched.

def _run(text, size):
    return pdftext.Run(left=0, right=100, text=text, bold=False, italic=False, size=size)


def _footnote_page():
    """One page: body at size 17, a marker run and a note line at 14 (exactly
    FOOTNOTE_DROP below), matching the measured Kommerskollegium PDF."""
    body = pdftext.Line(text="de som registreras 7 tilldelas ett samordningsnummer",
                        top=100, bold=False, lead_bold=False, italic=False, size=17,
                        runs=[_run("de som registreras ", 17), _run("7", 14),
                              _run(" tilldelas ett samordningsnummer", 17)])
    note = pdftext.Line(text="Se EU-kommissionens vägledning, kapitel 5.3.",
                        top=900, bold=False, lead_bold=False, italic=False, size=14)
    other = pdftext.Line(text="Kommerskollegium tillstyrker i huvudsak förslaget.",
                         top=200, bold=False, lead_bold=False, italic=False, size=17)
    return [(1, [body, note, other])]


def test_drop_footnotes_removes_the_note_and_its_marker():
    (_pageno, lines), = pdftext.drop_footnotes(_footnote_page())
    assert [l.text for l in lines] == [
        "de som registreras tilldelas ett samordningsnummer",
        "Kommerskollegium tillstyrker i huvudsak förslaget."]


def test_drop_footnotes_keeps_a_number_set_at_body_size():
    """Size is the signal, not the digit: "3" in the body is a paragraph number,
    an amount or a chapter, and dropping those would eat real text."""
    line = pdftext.Line(text="kapitel 3 anger att", top=100, bold=False,
                        lead_bold=False, italic=False, size=17,
                        runs=[_run("kapitel ", 17), _run("3", 17),
                              _run(" anger att", 17)])
    (_pageno, lines), = pdftext.drop_footnotes([(1, [line, line])])
    assert [l.text for l in lines] == ["kapitel 3 anger att"] * 2


def test_drop_footnotes_leaves_a_page_without_font_info_alone():
    """The OCR and legacy routes carry no sizes, so there is no signal -- and
    guessing from the digits alone would delete real numbers."""
    pages = [(1, [pdftext.Line(text="7 kap. 2 § anger", top=10, bold=False,
                               lead_bold=False, italic=False, size=0)])]
    assert pdftext.drop_footnotes(pages) == pages


# ---- the letter's addressing apparatus --------------------------------------
# `strip_page_furniture` finds what repeats; a masthead is printed once. Over 400
# reparsed remissvar that left 48% carrying a contact block and 44% a Datum/Dnr
# line, so composition rather than repetition has to catch these.

APPARATUS = [
    "Företagarförbundet Tel: 020-760 761 Org.nr: 802488-8805 Box 1132, "
    "262 22 Ängelholm E-post: info@ff.se Hemsida: www.ff.se",
    "Justitiedepartementet ju.remissvar@regeringskansliet.se",
    "Ert dnr Ju2021/00658",
    "Diarienummer: Ju2022/02173",
    "Yttrande Diarienummer 31 januari 2024 2023/04213",
    "• VÄSTMANLANDS TINGSRÄTT Datum Diarienummer",
    "Box 913, 391 29 Kalmar E-post: registrator@ehalsomyndigheten.se "
    "Besök: Södra Långgatan 60, Kalmar",
]

PROSE = [
    # cites a diarienummer but is plainly a sentence -- the token rule alone
    # would have eaten the reference, and an earlier phone pattern really did
    "Utredningen (dnr Ju2021/00658) föreslår att kravet på tillstånd tas bort, "
    "vilket kommunen anser är en rimlig avvägning mellan tillgänglighet och kontroll.",
    "Kommunen avstyrker förslaget om skattebroms eftersom det inskränker det "
    "kommunala självstyret på ett sätt som inte har utretts tillräckligt.",
    # a bare amount that looks like a phone number until you require 0/+46
    "Beloppet uppgår till 020 000 kronor enligt 3 kap. 2 § och bör enligt "
    "kommunen räknas upp årligen med index för att behålla sitt värde.",
    "Tillstyrks",
    "Kommunen tillstyrker förslaget.",
]


def test_strip_addressing_drops_the_masthead_and_reference_lines():
    assert pdftext.strip_addressing(APPARATUS) == []


def test_strip_addressing_keeps_prose_untouched():
    assert pdftext.strip_addressing(PROSE) == PROSE


def test_strip_addressing_leaves_surviving_prose_byte_identical():
    """Removing address tokens is how apparatus is *recognised*, never what is
    stored. An earlier cut emitted the token-stripped text for kept paragraphs
    too, which silently deleted a cited URL out of the artifact -- and the
    artifact is the source of truth for the text. The cost of the fix is that a
    recipient's e-mail spliced into a sentence stays; that is noise a reader can
    skip, unlike a missing citation, which they cannot recover."""
    cited = ("Se vidare Datainspektionens vägledning på www.imy.se/vagledningar "
             "som kommunen anser bör beaktas i det fortsatta lagstiftningsarbetet.")
    spliced = ("Remiss av betänkandet Ett effektivt straffrättsligt skydd för "
               "statliga johanna.johansson@regeringskansliet.se stöd till företag, "
               "och de förslag som lämnas där, bör enligt förbundet omarbetas.")
    assert pdftext.strip_addressing([cited, spliced]) == [cited, spliced]


def test_strip_addressing_drops_a_page_marker_line():
    """On a one-page letter nothing repeats, so `strip_page_furniture` cannot see
    the printed page marker; shape has to catch it. Bounded by length, because
    prose cites "artikel 8 (3) i Infosoc-direktivet" and must survive."""
    assert pdftext.strip_addressing(
        ["Lantmäterimyndigheten 1 (1)", "Remissvar 1(2)", "1(2) Tillväxtverket",
         "Medicinska fakulteten sid 3 (3)"]) == []
    prose = ("Detta gällde särskilt implementeringen av artikel 8 (3) i "
             "Infosoc-direktivet liksom lagstiftningen om tillfälliga kopior.")
    assert pdftext.strip_addressing([prose, "Kapitel 5 Huvudmannen i fokus"]) == [
        prose, "Kapitel 5 Huvudmannen i fokus"]


def test_join_across_pages_closes_a_hyphen_the_reflow_left_open():
    """A paragraph ending in a hyphen is never a real paragraph end -- the hyphen
    is a line break `page_paragraphs` could not close, which happens wherever the
    line spacing is too irregular to group lines at all (a scanned answer becomes
    one paragraph per line). Regression for sou/2018-82/forsvarets-radioanstalt,
    whose text carried "kostnadseffektivt säkerhets- skydd" and so rejected the
    model's correct quote."""
    assert pdftext.join_across_pages([
        ["bidrar till ett balanserat och kostnadseffektivt säkerhets-",
         "skydd. FRA har i huvudsak följande synpunkter."]]) == [
        "bidrar till ett balanserat och kostnadseffektivt säkerhetsskydd. "
        "FRA har i huvudsak följande synpunkter."]


def test_join_across_pages_keeps_a_hanging_hyphen():
    """Correct Swedish, not an artifact: "studie- och yrkesvägledare",
    "fri- och rättigheter". Closing these up would produce "studieoch"."""
    assert pdftext.join_across_pages([
        ["Där fyller studie-", "och yrkesvägledarna en viktig roll."]]) == [
        "Där fyller studie- och yrkesvägledarna en viktig roll."]
    assert pdftext.join_across_pages([
        ["arbeta för att människors grundläggande fri-",
         "och rättigheter skyddas."]]) == [
        "arbeta för att människors grundläggande fri- och rättigheter skyddas."]


# ---- superscript references and the ruled box -------------------------------
# Both read the run geometry pdftohtml reports, so both are built from real
# measurements off SOU 2025:115 rather than round numbers.

def _geo(text, top, size, left, right, bold=False):
    """A line with run geometry -- what the margin, measure and box rules read."""
    return line_from_runs(
        [Run(left=left, right=right, text=text, bold=bold, italic=False, size=size)],
        top)


def test_a_reference_marker_leaves_the_paragraph_it_marks_whole():
    """SOU 2025:115 p. 84, body 17 and references 10. A raised reference has a
    baseline of its own, so it arrives as a line holding one digit, sorted ahead
    of the line it belongs to. That line used to open the paragraph -- which then
    carried the *marker's* size, so classify filed running text as `fotnot`, and
    sat far right of the margin, so indent_breaks cut the sentence in two."""
    lines = [
        _geo("4", 312, 10, 594, 599),
        _geo("I strategin framhålls genomförandet av direktiv (EU) 2022/2555",
             312, 17, 149, 594),
        _geo("5", 333, 10, 483, 488),
        _geo("(NIS 2-direktivet) och direktiv (EU) 2022/2557 (CER-direktivet)",
             333, 17, 149, 617),
        _geo("som viktiga delar att uppnå ökad cybersäkerhet.", 353, 17, 149, 619),
    ]
    paras = page_paragraphs(lines, None, 84, indent_breaks=True)
    assert len(paras) == 1
    assert paras[0].size == 17
    # the citation is intact: neither split by the marker nor glued to it
    assert "direktiv (EU) 2022/2555 (NIS 2-direktivet)" in paras[0].text


def test_a_footnote_keeps_its_own_number():
    """The note's leading number is raised off its text exactly like a reference
    is, and is a line of its own for the same reason -- but it is the note's
    label, not chrome, and the line it sits beside is footnote-sized, not body."""
    lines = [
        _geo("Strategin framhåller att samhällsviktiga funktioner måste skyddas.",
             312, 17, 149, 594),
        _geo("Strategin innehåller också initiativ för att förebygga angrepp.",
             333, 17, 149, 617),
        _geo("Strategin erkänner den roll som privata aktörer har.", 353, 17, 149, 619),
        _geo("4", 824, 8, 149, 153),
        _geo("Europaparlamentets och rådets direktiv (EU) 2022/2555.",
             824, 12, 153, 606),
    ]
    paras = page_paragraphs(lines, None, 84, indent_breaks=True)
    assert paras[-1].text.startswith("4 Europaparlamentets")
    # and is still one, by the test classify applies (body 17)
    assert paras[-1].size <= 17 - pdftext.FOOTNOTE_DROP


# The box's own lines are the ones that must not vote on where the body's
# measure ends. Measured off SOU 2025:115 p. 307: body 85-555, box 96-543. The
# page-wide modal right was 539 -- the *box's* edge -- so every box line failed
# box_right, and since a box line is inset from the body margin by definition,
# indent_breaks then started a paragraph at each of them in turn.
BOX_PAGE = [
    _geo("Utredningens bedömning: Av artikel 64.5 i EU:s cyberresiliens-",
         162, 17, 96, 537),
    _geo("förordning framgår vilka omständigheter som ska beaktas vid",
         182, 17, 96, 530),
    _geo("fastställande av sanktionsavgift.", 203, 17, 96, 441),
    _geo("Av artikel 64.5 i EU:s cyberresiliensförordning framgår att vid beslut",
         453, 17, 85, 555),
    _geo("om storleken på den administrativa sanktionsavgiften i varje enskilt",
         474, 17, 85, 554),
    _geo("fall ska alla relevanta omständigheter i den specifika situationen",
         494, 17, 85, 550),
    _geo("beaktas och vederbörlig hänsyn ska tas till följande.", 514, 17, 85, 347),
]


def test_a_ruta_is_one_boxed_paragraph():
    paras = page_paragraphs(BOX_PAGE, None, 307, indent_breaks=True)
    assert len(paras) == 2
    assert paras[0].boxed
    assert paras[0].text.startswith("Utredningens bedömning:")
    assert paras[0].text.endswith("fastställande av sanktionsavgift.")
    assert not paras[1].boxed


def test_a_block_quotation_is_not_a_ruta_but_still_reflows():
    """Same page, further down: a recital quoted from the förordning, inset by 21
    where the box is inset by 11. Geometry cannot tell the two apart -- the size
    can, since the box carries running text and is set like it. The narrower
    measure is real either way, so the quotation still reflows against its own
    margin instead of coming out as one paragraph per line."""
    quote = [
        _geo("(120) För att säkerställa en effektiv efterlevnadskontroll av de",
             813, 15, 106, 549),
        _geo("skyldigheter som fastställs i denna förordning bör varje",
             830, 15, 106, 548),
        _geo("marknadskontrollmyndighet ha befogenhet att påföra sanktions-",
             846, 15, 106, 548),
        _geo("avgifter.", 863, 15, 106, 226),
    ]
    paras = page_paragraphs(BOX_PAGE + quote, None, 307, indent_breaks=True)
    assert paras[0].boxed                             # the ruta is unaffected
    assert paras[-1].text.startswith("(120) För att")
    assert paras[-1].text.endswith("påföra sanktionsavgifter.")
    assert not paras[-1].boxed


def test_one_inset_short_line_is_not_a_ruta():
    """A lead-in ("Strategins huvudmål är:") is inset at the left and short, so
    it clears both box tests on its own. Only a *run* of lines at one narrower
    measure is a box -- with a run of one, a page's short lines each became a
    one-line ruta and took two paragraph breaks, entering and leaving, with
    them."""
    lines = [
        _geo("kraft mot cyberattacker och säkerställa att medborgare och företag",
             103, 17, 149, 615),
        _geo("kan använda tillförlitliga digitala tjänster.", 123, 17, 149, 603),
        _geo("Strategins huvudmål är:", 184, 17, 170, 337),
        _geo("– att stärka cyberresiliens och teknologiskt självbestämmande,",
             213, 17, 149, 592),
        _geo("– öka operativ förmåga att förebygga och hantera attacker.",
             243, 17, 149, 603),
    ]
    paras = page_paragraphs(lines, None, 84, indent_breaks=True)
    assert not any(p.boxed for p in paras)


def _multi(runs, top):
    """A line assembled from several runs -- what a running head is: the
    identifier and the chapter title set beside each other on one baseline."""
    return line_from_runs([Run(left=a, right=b, text=t, bold=False, italic=False,
                               size=s) for a, b, t, s in runs], top)


def test_the_running_heads_other_half_goes_with_it():
    """Stripping the identifier leaves the chapter title standing, and it is a
    real line of real text -- so it survived as a paragraph on nearly every page
    of a förarbete (598 of SOU 2025:115's), each one classified `fotnot` for
    being set smaller than the body."""
    lines = [_multi([(85, 200, "SOU 2025:115", 12), (205, 638, "Sanktioner", 12)], 52),
             _geo("Av artikel 64.5 i EU:s cyberresiliensförordning framgår att vid",
                  453, 17, 85, 555),
             _geo("beslut om storleken på den administrativa sanktionsavgiften ska",
                  474, 17, 85, 554),
             _geo("alla relevanta omständigheter beaktas.", 494, 17, 85, 350)]
    paras = page_paragraphs(lines, "SOU 2025:115", 307, indent_breaks=True)
    assert len(paras) == 1
    assert "Sanktioner" not in paras[0].text


def test_the_running_head_goes_even_where_it_outsizes_the_page():
    """Size is not what identifies it. A förarbete sets its annexes smaller than
    its body but its running head at one size throughout, so on SOU 2025:115's
    bilaga 2 the head is *larger* than the text under it -- and sparing it there
    only moved the damage, from 138 pages of "Bilaga 2" as a footnote to 138 of it
    as a heading, in the table of contents."""
    lines = [_multi([(117, 200, "SOU 2025:115", 12), (205, 664, "Bilaga 2", 12)], 52),
             _geo("1. Förvaltare av programvara med fri och öppen källkod ska",
                  140, 10, 147, 610),
             _geo("anta och dokumentera en verifierbar cybersäkerhetspolicy så",
                  158, 10, 147, 610),
             _geo("att det utvecklas en säker produkt med digitala element.",
                  176, 10, 147, 560)]
    paras = page_paragraphs(lines, "SOU 2025:115", 544, indent_breaks=True)
    assert "Bilaga 2" not in " ".join(p.text for p in paras)


def test_body_prose_naming_the_identifier_is_not_a_running_head():
    """What identifies the head is that the identifier stood as a run of its own.
    Prose carries it *inside* a run, which `_strip_header_runs` leaves alone --
    so the line is never a candidate, wherever on the page it falls."""
    lines = [_geo("Se närmare SOU 2025:115 och de förslag som lämnas där.",
                  100, 17, 85, 540),
             _geo("Utredningen har övervägt frågan i ett tidigare sammanhang.",
                  120, 17, 85, 555)]
    paras = page_paragraphs(lines, "SOU 2025:115", 307, indent_breaks=True)
    assert "SOU 2025:115" in " ".join(p.text for p in paras)


def test_a_centred_heading_pair_is_not_a_ruta():
    """A heading centred over its centred title is inset at both margins and
    consecutive, so it clears every geometric test a box has -- but the two start
    at different lefts, where a box holds one edge. SOU 2025:115 reproduces the
    cyberresiliensförordning as bilaga 2, which sets a pair like this over every
    article."""
    lines = [_geo("Artikel 24", 100, 10, 359, 398),
             _geo("Skyldigheter för förvaltare av programvara med fri källkod",
                  118, 10, 231, 525),
             _geo("1. Förvaltare av programvara med fri och öppen källkod ska",
                  140, 10, 147, 610),
             _geo("anta och dokumentera en verifierbar cybersäkerhetspolicy så",
                  158, 10, 147, 610),
             _geo("att det utvecklas en säker produkt med digitala element.",
                  176, 10, 147, 560)]
    paras = page_paragraphs(lines, None, 544, indent_breaks=True)
    assert not any(p.boxed for p in paras)


def test_a_column_of_chart_labels_is_not_a_ruta():
    """Inset at both margins, consecutive, and agreeing on nothing else: a bar
    chart's axis labels are a column of fragments 36 units wide against a body
    measure of 472. SOU 2024:50's charts made 1,211 boxes this way."""
    lines = [_geo("det är något färre kommuner som får tillägg och fler som",
                  100, 17, 85, 553),
             _geo("får avdrag jämfört med i dag.", 120, 17, 85, 371),
             _geo("Med förslaget får Habo det största tillägget i modellen.",
                  140, 17, 85, 557),
             _geo("4 000", 200, 12, 127, 151),
             _geo("3 000", 216, 12, 127, 151),
             _geo("2 000", 232, 12, 127, 151),
             _geo("1 000", 248, 12, 127, 151)]
    paras = page_paragraphs(lines, None, 325, indent_breaks=True)
    assert not any(p.boxed for p in paras)


# The page geometry that motivates each of the box rules, one page per rule.
# They are separated because each is satisfied by the others' fixtures for the
# wrong reason -- a lone lead-in is rejected by its *width* before the
# run-length rule is ever consulted, so only a wide one tests that rule.

def test_a_page_the_box_dominates_still_finds_the_body_margin():
    """`margin` is the leftmost start a real share of the lines agree on, not the
    commonest. Here the box's 12 lines outnumber the body's 8, so the mode is the
    box's own inset -- which leaves the box not inset from anything, and the body
    outdented past it."""
    box = [_geo("Utredningens förslag rad %d som fyller hela satsytan här." % i,
                100 + 20 * i, 17, 96, 540) for i in range(12)]
    body = [_geo("Av artikel 64.5 framgår att alla relevanta omständigheter i "
                 "den enskilda situationen ska beaktas vid beslut %d." % i,
                 400 + 20 * i, 17, 85, 555) for i in range(8)]
    paras = page_paragraphs(box + body, None, 307, indent_breaks=True)
    assert [p.boxed for p in paras][0]
    assert not paras[-1].boxed


def test_a_box_is_broken_from_the_prose_that_follows_it():
    """Crossing out of a box is a paragraph break in itself. Without it the box's
    closing line runs on into the prose that resumes after it -- the vertical gap
    cannot be relied on to do it, since a box set at ordinary leading has none."""
    lines = [_geo("Utredningens förslag: myndigheten får avstå från att ta ut",
                  100, 17, 96, 540),
             _geo("en sanktionsavgift när överträdelsen bedöms som ringa.",
                  120, 17, 96, 535),
             _geo("Av artikel 64.5 i förordningen framgår att alla relevanta",
                  140, 17, 85, 555),
             _geo("omständigheter ska beaktas vid beslut om avgiftens storlek.",
                  160, 17, 85, 554)]
    paras = page_paragraphs(lines, None, 307, indent_breaks=True)
    assert len(paras) == 2
    assert paras[0].boxed and not paras[1].boxed
    assert paras[1].text.startswith("Av artikel 64.5")


def test_one_wide_inset_line_is_not_a_ruta():
    """The run-length rule, on the only shape that isolates it: a *wide* lone
    inset line clears both the alignment and the measure test on its own, so
    nothing but "a box is at least two lines" rejects it."""
    lines = [_geo("Av artikel 64.5 framgår att alla relevanta omständigheter",
                  100, 17, 85, 555),
             _geo("ska beaktas vid beslut om sanktionsavgiftens storlek.",
                  120, 17, 85, 554),
             _geo("Kommissionen offentliggör vägledning om tillämpningen här.",
                  140, 17, 96, 543),
             _geo("Bestämmelsen kräver ingen kompletterande författning alls.",
                  160, 17, 85, 550)]
    paras = page_paragraphs(lines, None, 307, indent_breaks=True)
    assert not any(p.boxed for p in paras)


def test_a_stripped_header_stops_reporting_its_own_width():
    """The header line survives when its residue is set at body size (it is only
    dropped when the style differs), and then its *runs* have to lose the
    identifier with its text: left standing they report the head's full width as
    the body's measure, which is wide enough to call the page's inset lines a
    box."""
    lines = [_multi([(85, 200, "SOU 2025:115", 17), (205, 638, "Sanktioner", 17)],
                    52),
             _geo("Av artikel 64.5 framgår att alla relevanta omständigheter",
                  100, 17, 85, 555),
             _geo("ska beaktas vid beslut om sanktionsavgiftens storlek här.",
                  120, 17, 85, 554),
             _geo("a) överträdelsens art, allvarlighetsgrad och varaktighet samt",
                  140, 17, 96, 600),
             _geo("dess konsekvenser för de berörda ekonomiska aktörerna i stort",
                  160, 17, 96, 600),
             _geo("Bestämmelsen kräver ingen kompletterande författning alls.",
                  200, 17, 85, 550)]
    paras = page_paragraphs(lines, "SOU 2025:115", 307, indent_breaks=True)
    assert "Sanktioner" in " ".join(p.text for p in paras)   # residue kept
    assert not any(p.boxed for p in paras)


def test_a_page_with_no_shared_line_start_has_no_margin():
    """`margin` is the leftmost start a real share of the lines agree on, so a
    page where every line starts somewhere different has none -- and then there
    is no indent to measure against and no box to find. Reflow falls back to the
    leading alone, which is the honest answer for a page with no measurable
    typography (a chart, a scanned form)."""
    lines = [_geo("fragment %d" % i, 100 + 20 * i, 17, 100 + 17 * i, 300 + 20 * i)
             for i in range(10)]
    paras = page_paragraphs(lines, None, 1, indent_breaks=True)
    assert not any(p.boxed for p in paras)
    assert " ".join(p.text for p in paras).count("fragment") == 10
