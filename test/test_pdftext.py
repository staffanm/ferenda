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

from types import SimpleNamespace

from accommodanda.lib import pdftext
from accommodanda.lib.pdftext import (
    PAGE_STRIDE,
    Line,
    _lines,
    flat_lines,
    page_paragraphs,
    pdf_pages,
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


def test_a_real_identifier_still_strips_every_occurrence():
    """An identifier is still honoured when one is actually known (the DV/JO/ARN
    case) -- this must keep working exactly as before, including its existing
    (out of scope for this fix) behaviour of stripping a mid-sentence
    recurrence, not just a standalone header line; only the None/"" path
    changed."""
    lines = [_line("Riksdagens ombudsmän 2026-01-01", 100),
             _line("Klagomålet rör Riksdagens ombudsmän i ett tidigare ärende", 120)]
    paras = page_paragraphs(lines, "Riksdagens ombudsmän", 1)
    joined = " ".join(p.text for p in paras)
    assert "Riksdagens ombudsmän" not in joined
    assert "2026-01-01" in joined and "tidigare ärende" in joined


PAGE_XML = (b"<pdf2xml>"
            b"<page number='1' height='1200'>"
            b"<text top='10' left='5' height='10'>first page</text></page>"
            b"<page number='2' height='1200'>"
            b"<text top='10' left='5' height='10'>second page</text></page>"
            b"</pdf2xml>")


def _fake_run(calls):
    def run(cmd, capture_output, check):
        calls.append(cmd)
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
    assert [c for c in calls[1] if c != "-hidden"] == calls[0]


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
    spans = [(10, 0, 30, "9", True, False, 20, 20),                      # big digit
             (20, 50, 30, "Författningskommentar", True, False, 250, 15)]  # smaller title
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
