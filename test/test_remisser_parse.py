"""remisser parse stage: a case record + its answer PDF -> Remissvar.

Uses the real fixture PDF at test/files/remisser/instance.pdf (a genuine
one-page Kammarkollegiet remissvar) through the actual poppler extraction, so
this is a real end-to-end check of the pipeline shape -- no network needed,
poppler (pdftohtml) is a local binary dependency shared with the other
PDF-bodied verticals."""

import json
import shutil
from pathlib import Path

import pytest

from accommodanda.remisser.model import Remiss, Remissinstans, Remissvar
from accommodanda.remisser.parse import parse_record

FIXTURE_PDF = Path(__file__).parent / "files" / "remisser" / "instance.pdf"


@pytest.fixture
def corpus(tmp_path):
    cases_root = tmp_path / "cases"
    downloaded_root = tmp_path / "downloaded"
    cases_root.mkdir()
    (downloaded_root / "test-case").mkdir(parents=True)
    shutil.copy(FIXTURE_PDF, downloaded_root / "test-case" / "kammarkollegiet.pdf")
    remiss = Remiss(
        basefile="test-case",
        titel="Remiss av Ett testbetänkande",
        url="https://www.regeringen.se/remisser/2026/01/test-case/",
        dnr="Fi2026/01234",
        remitterat=[{"typ": "sou", "basefile": "2025:99"}],
        svar=[Remissinstans(
            organisation="Kammarkollegiet",
            source_url="https://www.regeringen.se/.../kammarkollegiet.pdf",
            downloaded=True)])
    (cases_root / "test-case.json").write_text(
        json.dumps(remiss.to_dict(), ensure_ascii=False, indent=2))
    return cases_root, downloaded_root


def test_parse_record_extracts_body_text(corpus):
    cases_root, downloaded_root = corpus
    result = parse_record("test-case/kammarkollegiet", cases_root, downloaded_root)
    assert result.basefile == "test-case/kammarkollegiet"
    assert result.case_basefile == "test-case"
    assert result.organisation == "Kammarkollegiet"
    assert result.case_titel == "Remiss av Ett testbetänkande"
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
    cases_root, downloaded_root = corpus
    result = parse_record("test-case/kammarkollegiet", cases_root, downloaded_root)
    again = Remissvar.from_dict(json.loads(json.dumps(result.to_dict(),
                                                       ensure_ascii=False)))
    assert again == result


def test_parse_record_missing_instance_asserts(corpus):
    cases_root, downloaded_root = corpus
    with pytest.raises(AssertionError, match="no answer instance"):
        parse_record("test-case/no-such-org", cases_root, downloaded_root)


def test_parse_record_not_yet_downloaded_asserts(corpus):
    cases_root, downloaded_root = corpus
    remiss = Remiss.from_dict(json.loads(
        (cases_root / "test-case.json").read_text()))
    remiss.svar[0].downloaded = False
    (cases_root / "test-case.json").write_text(
        json.dumps(remiss.to_dict(), ensure_ascii=False, indent=2))
    with pytest.raises(AssertionError, match="has not been downloaded"):
        parse_record("test-case/kammarkollegiet", cases_root, downloaded_root)
