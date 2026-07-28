"""eurlex/parse_pdf: the thin consumer of lib/pdftext.

Locks in the 2026-07 consolidation (parse_pdf had forked lib/pdftext's
extractor wholesale) and its behavioural fix: the OJ header date goes through
parse_html's shared `eu_date` rather than a duplicate regex. The OCR fallback
itself now lives in lib/pdftext (`ocr_pdf`) and is tested there."""

import pytest

from accommodanda.eurlex import parse_pdf as pp
from accommodanda.eurlex.parse_html import eu_date
from accommodanda.lib.pdftext import Line


def _line(text, top, bold=False):
    return Line(text, top, bold, bold, False)


def test_pdf_lines_uses_shared_extractor_with_hidden(monkeypatch):
    """pdf_lines delegates to lib's flat_lines with hidden=True (the ocrmypdf
    text layer is invisible) instead of a forked extractor."""
    seen = []
    monkeypatch.setattr(pp, "flat_lines",
                        lambda path, hidden: seen.append(hidden) or [_line("x", 10)])
    assert [l.text for l in pp.pdf_lines("doc.pdf")] == ["x"]
    assert seen == [True]


def test_paragraphs_reflow_bold_break_and_dehyphenation():
    """A bold line starts its own paragraph (the following body line rejoins it
    only across a normal line gap -- so a big gap after the title splits);
    hyphenated line breaks are healed on join."""
    lines = [_line("En bestäm-", 100),
             _line("melse i akten.", 112),
             _line("Den gäller alla.", 124),
             _line("Artikel 2", 160, bold=True),
             _line("Nästa stycke.", 220)]
    assert pp._paragraphs(lines) == [
        ("En bestämmelse i akten. Den gäller alla.", False),
        ("Artikel 2", True),
        ("Nästa stycke.", False)]


def test_parse_pdf_header_metadata_via_shared_eu_date(monkeypatch):
    """The OJ header date/number: one eu_date definition (parse_html's), no
    duplicate regex in parse_pdf."""
    lines = [_line("Europeiska unionens officiella tidning L 333/1", 10),
             _line("14.12.2022", 22),
             _line("Svensk utgåva", 34),
             _line("Artikel 1", 300, bold=True),
             _line("Denna förordning träder i kraft.", 360)]
    monkeypatch.setattr(pp, "pdf_lines", lambda path, lang: lines)
    doc = pp.parse_pdf("doc.pdf", "32022R9999", "swe")
    assert doc.date == "2022-12-14"
    assert doc.oj == "L 333"
    article = next(b for b in doc.body if b.kind == "article")
    assert article.num == "1"


def test_eu_date_searches_within_header_blob():
    """eu_date finds the date anywhere in the text (the PDF path hands it a
    joined header blob), not only at the start."""
    assert eu_date("officiella tidning 9.4.1968 L 88/1") == "1968-04-09"
    assert eu_date("9.4.1968") == "1968-04-09"
    assert eu_date("no date here") is None
