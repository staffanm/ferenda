"""remisser parse stage: an ärende record + its answer PDF -> Remissvar.

Uses the real fixture PDF at test/files/remisser/instance.pdf (a genuine
one-page Kammarkollegiet remissvar) through the actual poppler extraction, so
this is a real end-to-end check of the pipeline shape -- no network needed,
poppler (pdftohtml) is a local binary dependency shared with the other
PDF-bodied verticals."""

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from accommodanda.lib import pdftext
from accommodanda.lib.errors import SkipDocument
from accommodanda.remisser import parse
from accommodanda.remisser.model import Remiss, Remissinstans, Remissvar, org_slug

FIXTURE_PDF = Path(__file__).parent / "files" / "remisser" / "instance.pdf"


@pytest.fixture
def corpus(tmp_path):
    # one tree, keyed on the remitted document: <typ>/<id-slug>.json beside the
    # <typ>/<id-slug>/ PDF dir
    root = tmp_path / "downloaded"
    (root / "sou" / "2025-99").mkdir(parents=True)
    shutil.copy(FIXTURE_PDF, root / "sou" / "2025-99" / "kammarkollegiet.pdf")
    remiss = Remiss(
        basefile="sou/2025:99",
        titel="Remiss av Ett testbetänkande",
        url="https://www.regeringen.se/remisser/2026/01/ett-testbetankande/",
        dnr="Fi2026/01234",
        remitterat=[{"typ": "sou", "basefile": "2025:99"}],
        svar=[Remissinstans(
            organisation="Kammarkollegiet",
            source_url="https://www.regeringen.se/.../kammarkollegiet.pdf",
            downloaded=True)])
    (root / "sou" / "2025-99.json").write_text(
        json.dumps(remiss.to_dict(), ensure_ascii=False, indent=2))
    return root


def test_parse_extracts_body_text(corpus):
    root = corpus
    result = parse.parse("sou/2025:99/kammarkollegiet", root)
    assert result.basefile == "sou/2025:99/kammarkollegiet"
    assert result.arende_basefile == "sou/2025:99"
    assert result.organisation == "Kammarkollegiet"
    assert result.arende_titel == "Remiss av Ett testbetänkande"
    assert result.remitterat == [{"typ": "sou", "basefile": "2025:99"}]
    assert result.source_url == "https://www.regeringen.se/.../kammarkollegiet.pdf"
    assert isinstance(result.full_text, list)
    assert all(isinstance(p, str) and p for p in result.full_text)
    # the real letter body -- not just header/footer noise
    assert any("remitterade förslagen" in p for p in result.full_text)
    # regression: page_paragraphs' header-strip used to be driven by the
    # organisation's own name, which silently deleted it out of ordinary
    # self-referencing prose too ("Kammarkollegiet har, utifrån ..." ->
    # "har, utifrån ...") -- parse must pass no identifier at all
    assert any("Kammarkollegiet har," in p for p in result.full_text)


def test_parse_to_dict_from_dict_roundtrip(corpus):
    root = corpus
    result = parse.parse("sou/2025:99/kammarkollegiet", root)
    again = Remissvar.from_dict(json.loads(json.dumps(result.to_dict(),
                                                       ensure_ascii=False)))
    assert again == result


def _line(text, size=12):
    """A minimal stand-in for a real extracted line. It must be a Line, not a
    bare str: the extraction reads `.size` to tell a footnote from body text."""
    return pdftext.Line(text=text, top=0, bold=False, lead_bold=False,
                        italic=False, size=size)


def test_pages_always_asks_for_hidden_text(corpus, monkeypatch):
    """Some remissvar carry a text layer poppler renders invisible (observed:
    Valmyndigheten's answer to SOU 2026:2, which extracts to nothing without
    ``-hidden`` even though pdftotext reads it fine). Every extraction asks for
    hidden text, so those are never silently empty."""
    seen = []
    monkeypatch.setattr(pdftext, "pdf_pages",
                        lambda path, key, hidden: seen.append(hidden) or [(1, [_line("x")])])
    monkeypatch.setattr(parse, "page_paragraphs", lambda lines, ident, no: [])
    parse.parse("sou/2025:99/kammarkollegiet", corpus)
    assert seen == [True]


def test_pages_falls_back_to_ocr_when_a_pdf_has_no_text_layer(corpus, monkeypatch):
    """A scanned answer -- no text layer at all, even hidden -- goes through
    ocrmypdf rather than parsing to an empty full_text (three of the first 94
    answers harvested were such scans)."""
    ocred = corpus / "ocr-of-it.pdf"
    monkeypatch.setattr(pdftext, "ocr_pdf", lambda path, lang: ocred)

    def fake_pages(path, key, hidden):
        assert hidden
        return ([(1, [_line("recovered")])] if path == str(ocred)
                else [(1, []), (2, [])])

    monkeypatch.setattr(pdftext, "pdf_pages", fake_pages)
    monkeypatch.setattr(parse, "page_paragraphs",
                        lambda lines, ident, no: [SimpleNamespace(text=l.text) for l in lines])
    result = parse.parse("sou/2025:99/kammarkollegiet", corpus)
    assert result.full_text == ["recovered"]


def test_pages_does_not_ocr_a_pdf_that_yielded_lines(corpus, monkeypatch):
    """OCR is the expensive path, and emptiness is judged on *lines* -- a PDF
    whose text all gets stripped as letterhead by `page_paragraphs` has a text
    layer and must not be re-OCR'd."""
    monkeypatch.setattr(pdftext, "ocr_pdf",
                        lambda path, lang: pytest.fail("OCR ran on a text PDF"))
    monkeypatch.setattr(pdftext, "pdf_pages",
                        lambda path, key, hidden: [(1, [_line("a letterhead line")])])
    monkeypatch.setattr(parse, "page_paragraphs", lambda lines, ident, no: [])
    assert parse.parse("sou/2025:99/kammarkollegiet", corpus).full_text == []


def test_parse_missing_instance_asserts(corpus):
    root = corpus
    with pytest.raises(AssertionError, match="no answer instance"):
        parse.parse("sou/2025:99/no-such-org", root)


def test_parse_not_yet_downloaded_asserts(corpus):
    root = corpus
    remiss = Remiss.from_dict(json.loads(
        (root / "sou" / "2025-99.json").read_text()))
    remiss.svar[0].downloaded = False
    (root / "sou" / "2025-99.json").write_text(
        json.dumps(remiss.to_dict(), ensure_ascii=False, indent=2))
    with pytest.raises(AssertionError, match="has not been downloaded"):
        parse.parse("sou/2025:99/kammarkollegiet", root)


def test_a_broken_answer_pdf_is_skipped_not_errored(corpus, monkeypatch):
    # El-Kretsen's real answer is corrupt on regeringen.se and always will be:
    # parse must refuse it as a SkipDocument (an empty artifact, built once) and
    # not raise the per-document error the driver reports on every build.
    monkeypatch.setitem(parse.BROKEN_PDFS, "sou/2025:99/kammarkollegiet",
                        "trasig PDF hos regeringen.se")
    with pytest.raises(SkipDocument):
        parse.parse("sou/2025:99/kammarkollegiet", corpus)


def test_every_broken_pdf_entry_is_well_formed():
    # keys are answer basefiles "<typ>/<document id>/<org-slug>"; a typo'd key
    # would silently never match and the document would keep failing
    for key, why in parse.BROKEN_PDFS.items():
        typ, _, rest = key.partition("/")
        assert typ in ("sou", "ds", "pm", "lr"), key
        arende, _, slug = rest.rpartition("/")
        assert arende and slug and slug == org_slug("x/" + slug + ".pdf"), key
        assert why and isinstance(why, str), key


# --------------------------------------------------------------------------
# what the stored file actually IS -- answers arrive as whatever a registrator
# uploaded, and the `.pdf` name on disk is the tree's convention, not evidence
# --------------------------------------------------------------------------

WORD_FIXTURE = Path(__file__).parent / "files" / "remisser" / "word-answer.docx"


def test_a_word_answer_stored_as_pdf_is_read_as_word(corpus):
    """Four of the 79 980 answers are Word documents saved under a `.pdf` name
    (regeringen.se serves the registrator's upload unchanged, and the org slug
    keeps the original extension: `transportstyrelsen.docx`). Handing those to
    poppler is what produced five per-build `pdftohtml` failures; `_body_text`
    dispatches on the magic bytes instead and reads them through lib.poi."""
    shutil.copy(WORD_FIXTURE, corpus / "sou" / "2025-99" / "kammarkollegiet.pdf")
    result = parse.parse("sou/2025:99/kammarkollegiet", corpus)
    assert "REMISSVAR" in result.full_text
    assert any("tillstyrker de remitterade förslagen" in p
               for p in result.full_text)


def test_an_answer_that_is_neither_pdf_nor_word_raises(corpus):
    """An HTML error page stored under a document name must fail, not be filed
    as the organisation's prose (rule:fail-fast).

    ValueError, not AssertionError: this is a recorded rejection of untrusted
    remote bytes, so it must survive -O (rule:errors-drive-retry-use-raise)."""
    (corpus / "sou" / "2025-99" / "kammarkollegiet.pdf").write_bytes(
        b"<!doctype html><title>502 Bad Gateway</title>")
    with pytest.raises(ValueError, match="its bytes are"):
        parse.parse("sou/2025:99/kammarkollegiet", corpus)


def test_a_pdf_with_an_unreadable_xref_is_repaired_not_lost(corpus):
    """Stockholms universitets answer on SOU 2020:58 carries `%PDF` magic and
    intact objects, but its `startxref` points at a blank linearizer
    placeholder and no trailer dictionary survives -- poppler refuses the whole
    file and `pdftohtml` exits non-zero. Ghostscript rebuilds the xref by
    scanning for objects, which recovers the text intact, so the answer is
    repaired rather than written off as broken.

    The fixture is the good PDF broken the same way, so the test does not
    depend on shipping a second corrupt binary."""
    answer = corpus / "sou" / "2025-99" / "kammarkollegiet.pdf"
    broken = re.sub(rb"startxref\s+\d+", b"startxref\r\n116",
                    answer.read_bytes()).replace(b"trailer", b"traiier")
    answer.write_bytes(broken)
    result = parse.parse("sou/2025:99/kammarkollegiet", corpus)
    assert any("remitterade förslagen" in p for p in result.full_text)
