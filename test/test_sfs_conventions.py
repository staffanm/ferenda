"""Corpus regressions for trilingual convention appendices beyond the ECHR."""

import json
from pathlib import Path

import pytest

from accommodanda.sfs import parse_sfs_source
from accommodanda.sfs.model import (
    Bilaga,
    Konventionsartikel,
    Konventionsavdelning,
    Konventionsbilaga,
)
from accommodanda.sfs.nf import to_normalform

ROOT = Path(__file__).parent.parent
CORPUS = ROOT / "site/data/downloaded/sfs"
needs_sfs_corpus = pytest.mark.skipif(
    not CORPUS.is_dir(),
    reason="downloaded SFS JSON corpus not present")


def _source(basefile):
    year, number = basefile.split(":")
    return json.loads((CORPUS / year / (number + ".json")).read_text())


@needs_sfs_corpus
@pytest.mark.parametrize(("basefile", "articles", "divisions", "rows"), [
    ("2018:1197", 54, 3, 178),
    ("2010:510", 57, 7, 153),
    ("2022:366", 28, 0, 286),
])
def test_parallel_convention_corpus_alignment(
        basefile, articles, divisions, rows):
    doc = parse_sfs_source(_source(basefile), basefile)
    appendix = next(node for node in doc.children if isinstance(node, Bilaga))
    parallel = appendix.children[0]
    assert isinstance(parallel, Konventionsbilaga)
    assert len(parallel.instruments) == 1
    instrument = parallel.instruments[0]
    assert sum(isinstance(node, Konventionsartikel)
               for node in instrument.children) == articles
    assert sum(isinstance(node, Konventionsavdelning)
               for node in instrument.children) == divisions
    assert sum(len(node.texter) for node in instrument.children
               if isinstance(node, Konventionsartikel)) == rows

    artifact = to_normalform(doc, basefile, suppress_temporal=False)
    projected = next(
        child for node in artifact["structure"] if node["type"] == "bilaga"
        for child in node["children"]
        if child["type"] == "konventionsbilaga")
    assert projected["languages"] == ["en", "fr", "sv"]
    first_article = next(
        child for child in projected["children"][0]["children"]
        if child["type"] == "konventionsartikel")
    assert [version["language"]
            for version in first_article["paragraphs"][0]["versions"]] == [
                "en", "fr", "sv"]
