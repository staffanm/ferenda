"""remisser parse stage: an ärende record + its answer PDF -> Remissvar.

Uses the real fixture PDF at test/files/remisser/instance.pdf (a genuine
one-page Kammarkollegiet remissvar) through the actual poppler extraction, so
this is a real end-to-end check of the pipeline shape -- no network needed,
poppler (pdftohtml) is a local binary dependency shared with the other
PDF-bodied verticals."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from accommodanda.lib import pdftext
from accommodanda.lib.errors import SkipDocument
from accommodanda.remisser import parse
from accommodanda.remisser.model import Remiss, Remissinstans, Remissvar, org_slug
from accommodanda.remisser.parse import parse_record

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


def test_parse_record_extracts_body_text(corpus):
    root = corpus
    result = parse_record("sou/2025:99/kammarkollegiet", root)
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
    # "har, utifrån ...") -- parse_record must pass no identifier at all
    assert any("Kammarkollegiet har," in p for p in result.full_text)


def test_parse_record_to_dict_from_dict_roundtrip(corpus):
    root = corpus
    result = parse_record("sou/2025:99/kammarkollegiet", root)
    again = Remissvar.from_dict(json.loads(json.dumps(result.to_dict(),
                                                       ensure_ascii=False)))
    assert again == result


def test_pages_always_asks_for_hidden_text(corpus, monkeypatch):
    """Some remissvar carry a text layer poppler renders invisible (observed:
    Valmyndigheten's answer to SOU 2026:2, which extracts to nothing without
    ``-hidden`` even though pdftotext reads it fine). Every extraction asks for
    hidden text, so those are never silently empty."""
    seen = []
    monkeypatch.setattr(pdftext, "pdf_pages",
                        lambda path, key, hidden: seen.append(hidden) or [(1, ["x"])])
    monkeypatch.setattr(parse, "page_paragraphs", lambda lines, ident, no: [])
    parse.parse_record("sou/2025:99/kammarkollegiet", corpus)
    assert seen == [True]


def test_pages_falls_back_to_ocr_when_a_pdf_has_no_text_layer(corpus, monkeypatch):
    """A scanned answer -- no text layer at all, even hidden -- goes through
    ocrmypdf rather than parsing to an empty full_text (three of the first 94
    answers harvested were such scans)."""
    ocred = corpus / "ocr-of-it.pdf"
    monkeypatch.setattr(pdftext, "ocr_pdf", lambda path, lang: ocred)

    def fake_pages(path, key, hidden):
        assert hidden
        return [(1, ["recovered"])] if path == str(ocred) else [(1, []), (2, [])]

    monkeypatch.setattr(pdftext, "pdf_pages", fake_pages)
    monkeypatch.setattr(parse, "page_paragraphs",
                        lambda lines, ident, no: [SimpleNamespace(text=t) for t in lines])
    result = parse.parse_record("sou/2025:99/kammarkollegiet", corpus)
    assert result.full_text == ["recovered"]


def test_pages_does_not_ocr_a_pdf_that_yielded_lines(corpus, monkeypatch):
    """OCR is the expensive path, and emptiness is judged on *lines* -- a PDF
    whose text all gets stripped as letterhead by `page_paragraphs` has a text
    layer and must not be re-OCR'd."""
    monkeypatch.setattr(pdftext, "ocr_pdf",
                        lambda path, lang: pytest.fail("OCR ran on a text PDF"))
    monkeypatch.setattr(pdftext, "pdf_pages",
                        lambda path, key, hidden: [(1, ["a letterhead line"])])
    monkeypatch.setattr(parse, "page_paragraphs", lambda lines, ident, no: [])
    assert parse.parse_record("sou/2025:99/kammarkollegiet", corpus).full_text == []


def test_parse_record_missing_instance_asserts(corpus):
    root = corpus
    with pytest.raises(AssertionError, match="no answer instance"):
        parse_record("sou/2025:99/no-such-org", root)


def test_parse_record_not_yet_downloaded_asserts(corpus):
    root = corpus
    remiss = Remiss.from_dict(json.loads(
        (root / "sou" / "2025-99.json").read_text()))
    remiss.svar[0].downloaded = False
    (root / "sou" / "2025-99.json").write_text(
        json.dumps(remiss.to_dict(), ensure_ascii=False, indent=2))
    with pytest.raises(AssertionError, match="has not been downloaded"):
        parse_record("sou/2025:99/kammarkollegiet", root)


def test_a_broken_answer_pdf_is_skipped_not_errored(corpus, monkeypatch):
    # El-Kretsen's real answer is corrupt on regeringen.se and always will be:
    # parse must refuse it as a SkipDocument (an empty artifact, built once) and
    # not raise the per-document error the driver reports on every build.
    monkeypatch.setitem(parse.BROKEN_PDFS, "sou/2025:99/kammarkollegiet",
                        "trasig PDF hos regeringen.se")
    with pytest.raises(SkipDocument):
        parse_record("sou/2025:99/kammarkollegiet", corpus)


def test_every_broken_pdf_entry_is_well_formed():
    # keys are answer basefiles "<typ>/<document id>/<org-slug>"; a typo'd key
    # would silently never match and the document would keep failing
    for key, why in parse.BROKEN_PDFS.items():
        typ, _, rest = key.partition("/")
        assert typ in ("sou", "ds", "pm", "lr"), key
        arende, _, slug = rest.rpartition("/")
        assert arende and slug and slug == org_slug("x/" + slug + ".pdf"), key
        assert why and isinstance(why, str), key
