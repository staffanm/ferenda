"""Tests for the frozen myndfs corpus importer (the legacy-corpus sweep)."""

import json

from accommodanda.foreskrift import legacy
from accommodanda.lib import compress

RDF = """<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:dcterms="http://purl.org/dc/terms/"
         xmlns:rpubl="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#">
  <rpubl:Paragraf rdf:about="https://lagen.nu/pmfs/2019:2#K1P1">
    <dcterms:title xml:lang="sv">En rubrik, inte dokumenttiteln</dcterms:title>
  </rpubl:Paragraf>
  <rpubl:Myndighetsforeskrift rdf:about="https://lagen.nu/pmfs/2019:2">
    <dcterms:title xml:lang="sv">Säkerhetspolisens föreskrifter om
      säkerhetsskydd</dcterms:title>
  </rpubl:Myndighetsforeskrift>
</rdf:RDF>"""


def _corpus(tmp_path, basefile_in_entry="pmfs/2019:2"):
    corpus = tmp_path / "pmfs"
    (corpus / "entries" / "pmfs-2019").mkdir(parents=True)
    (corpus / "downloaded" / "pmfs-2019" / "2").mkdir(parents=True)
    (corpus / "distilled" / "pmfs-2019").mkdir(parents=True)
    (corpus / "entries" / "pmfs-2019" / "2.json").write_text(json.dumps(
        {"basefile": basefile_in_entry,
         "orig_url": "https://polisen.se/pmfs-2019-2_web.pdf"}))
    (corpus / "downloaded" / "pmfs-2019" / "2" / "index.pdf").write_bytes(
        b"%PDF-1.4 fake body")
    (corpus / "distilled" / "pmfs-2019" / "2.rdf").write_text(RDF,
                                                              encoding="utf-8")
    return corpus


def test_doc_title_takes_the_document_resource_not_a_fragment(tmp_path):
    corpus = _corpus(tmp_path)
    title = legacy.doc_title(corpus / "distilled" / "pmfs-2019" / "2.rdf")
    assert title == "Säkerhetspolisens föreskrifter om säkerhetsskydd"


def test_import_writes_record_and_body_and_respects_live(tmp_path):
    corpus = _corpus(tmp_path)
    root = tmp_path / "new"
    root.mkdir()
    bases, amendments = set(), set()
    counts = legacy.import_corpus(corpus, root, bases, amendments)
    assert counts == (1, 1, 0, 0, 0, 0)
    rec = json.loads(compress.read_text(root / "pmfs" / "pmfs-2019-2.json"))
    assert rec["basefile"] == "pmfs/2019:2"
    assert rec["identifier"] == "PMFS 2019:2"
    assert rec["source"] == "myndfs-legacy"
    assert rec["title"].startswith("Säkerhetspolisens föreskrifter")
    assert rec["files"]["regulation"]["url"].endswith("pmfs-2019-2_web.pdf")
    assert compress.exists(root / "pmfs" / "pmfs-2019-2-regulation.pdf")
    # a second run: the freshly written record now counts as live-covered
    assert legacy.import_corpus(corpus, root, bases, amendments) \
        == (1, 0, 1, 0, 0, 0)
    # and a doc the live corpus carries as an amendment is never imported
    assert legacy.import_corpus(corpus, root, set(), {"PMFS2019:2"}) \
        == (1, 0, 1, 0, 0, 0)


def test_entry_without_basefile_recovers_it_from_the_path(tmp_path):
    corpus = _corpus(tmp_path, basefile_in_entry=None)
    docs = list(legacy.legacy_docs(corpus))
    assert [bf for bf, *_ in docs] == ["pmfs/2019:2"]


def test_import_corpus_uses_registry_designation(tmp_path):
    # RA-FS is not its upper-cased slug; the identifier must match the form
    # the live harvest mints (agencies.REGISTRY designation)
    corpus = tmp_path / "rafs"
    (corpus / "entries" / "rafs-1991").mkdir(parents=True)
    (corpus / "entries" / "rafs-1991" / "6.json").write_text(
        '{"basefile": "rafs/1991:6", "orig_url": "https://x/ra.pdf"}')
    (corpus / "downloaded" / "rafs-1991" / "6").mkdir(parents=True)
    (corpus / "downloaded" / "rafs-1991" / "6" / "index.pdf").write_bytes(
        b"%PDF-1.4 x")
    root = tmp_path / "root"
    legacy.import_corpus(corpus, root, set(), set())
    rec = json.loads(compress.read_text(
        compress.logical(next(iter(compress.glob(root / "rafs", "*.json"))))))
    assert rec["identifier"] == "RA-FS 1991:6"
    assert rec["files"]["regulation"]["identifier"] == "RA-FS 1991:6"


def test_legacy_docs_records_unrecognized_entries(tmp_path):
    corpus = tmp_path / "afs"
    (corpus / "entries" / "junk").mkdir(parents=True)
    (corpus / "entries" / "junk" / "x.json").write_text('{"basefile": null}')
    unrecognized = []
    assert list(legacy.legacy_docs(corpus, unrecognized)) == []
    assert len(unrecognized) == 1 and unrecognized[0].endswith("junk/x.json")
