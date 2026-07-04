"""lib/pdftext.page_paragraphs: the header-strip identifier.

Regression for a real bug found via the remisser vertical: an `identifier`
that happens to recur as ordinary self-reference inside body prose (an
organisation naming itself in its own letter, not a repeated running header)
must not be silently deleted from that prose. `None`/`""` means "no header to
strip", not "strip the empty pattern" (which used to mangle every line)."""

from accommodanda.lib.pdftext import Line, page_paragraphs


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
