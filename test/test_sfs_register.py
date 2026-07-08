"""Tests for the SFSR register parser and amendment mapping.

Unit tests for the pure helpers, plus golden-diff integration tests that
assert the full amendments section reproduces the frozen golden corpus for
known-good documents (using the same comparator the corpus run uses).
"""

import importlib.util
from pathlib import Path

import pytest

from accommodanda.sfs import load_inputs
from accommodanda.lib.datasets import NAMEDLAWS
from accommodanda.lib.lagrum import FORARBETEN, LagrumParser, load_namedlaws
from accommodanda.sfs.nf import to_normalform
from accommodanda.sfs.register import (amendment_properties,
                                  build_metadata, forarbete_identifier,
                                  forfattningstyp, lfragment, lookup_resource,
                                  omfattning_predicate, parse_forarbeten,
                                  register_from_source, resource_map,
                                  sanitize_departement, sfs_slug)

ROOT = Path(__file__).parent.parent

# the golden-diff tests read two data trees that only exist on a full dev
# checkout: the downloaded SFS JSON corpus and the old pipeline's parsed XHTML
# oracle in a sibling ferenda.old checkout. Skip (not fail) where either is
# absent; the pure-helper tests below run everywhere.
needs_json_corpus = pytest.mark.skipif(
    not (ROOT / "site/data/downloaded/sfs").is_dir(),
    reason="downloaded SFS JSON corpus (site/data/downloaded/sfs) not present")
needs_golden_corpus = pytest.mark.skipif(
    not (ROOT.parent / "ferenda.old/data/sfs/parsed").is_dir(),
    reason="golden corpus (../ferenda.old/data/sfs/parsed) not present")


def golden_module():
    spec = importlib.util.spec_from_file_location(
        "golden_sfs", ROOT / "tools" / "golden_sfs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def json_path(basefile):
    year, rest = basefile.split(":")
    return ROOT / "site/data/downloaded/sfs" / year / (rest.replace(" ", "_") + ".json")


def parsed_path(basefile):
    year, rest = basefile.split(":")
    # old-pipeline parsed XHTML oracle -- temporary scaffolding in the old checkout
    return (ROOT.parent / "ferenda.old/data/sfs/parsed"
            / year / (rest.replace(" ", "_") + ".xhtml"))


def inputs(basefile):
    """``(doc, register, sfst_header)`` from the new JSON ``_source`` -- the
    legacy SFSR/SFST HTML pages the old layout split this across are gone, so
    the register/metadata parsing now runs off the JSON throughout.

    The `needs_json_corpus` guard only checks the corpus *directory* exists; a
    partial checkout can have the dir but not this basefile's JSON. Skip per
    basefile so a partial corpus produces skips, not a None-path failure deep in
    extract_body."""
    path = json_path(basefile)
    if not path.exists():
        pytest.skip("SFS JSON %s not in this (partial) corpus" % path)
    return load_inputs(path, None, None, basefile)


def normalform(basefile):
    doc, register, sfst_header = inputs(basefile)
    return to_normalform(
        doc, basefile,
        refparser=LagrumParser(load_namedlaws(NAMEDLAWS), basefile),
        register=register, sfst_header=sfst_header)


def golden(basefile):
    """The old pipeline's parsed XHTML, normalized to NF on the fly -- the
    golden the corpus run compares against (there is no frozen golden tree)."""
    module = golden_module()
    gold = module.normalize(str(parsed_path(basefile)))
    module.canonicalize_node_texts(gold["structure"])
    return gold, module


# --- pure helpers -------------------------------------------------------

def test_sfs_slug_pagenumber():
    assert sfs_slug("1907:69 s.2") == ("1907", "69_s.2")
    assert sfs_slug("1902:71 s. 1") == ("1902", "71_s.1")
    assert sfs_slug("1990:100") == ("1990", "100")


def test_lfragment_slugifies():
    assert lfragment("1996:507") == "L1996:507"
    assert lfragment("1902:71 s.1") == "L1902:71_s.1"


def test_sanitize_departement_drops_suborg():
    assert sanitize_departement("Finansdepartementet BA") == "Finansdepartementet"
    assert sanitize_departement(
        "Justitiedepartementet DOM, L5 och Å") == "Justitiedepartementet"


def test_lookup_resource_known_labels_and_unknown_raise():
    # the labels the register builder itself hardcodes, plus a sanitized
    # departement (current and historical), must all be in the ported
    # resources.json -- and resolve to URIs, never echo the label back
    for label in ("Regeringskansliet", "SFS", "Justitiedepartementet",
                  "Klimat- och näringslivsdepartementet"):
        assert lookup_resource(label).startswith("http")
    # an unknown label is bad input data: raise at the per-document boundary,
    # never mint a non-URI value into the artifact
    with pytest.raises(ValueError, match="unknown org/series label"):
        lookup_resource("Fantasidepartementet")


def test_resource_map_values_are_uris():
    # the TTL->JSON port must yield only URIs; a label that slipped into a
    # value position would silently corrupt every artifact referencing it
    assert all(uri.startswith("http") for uri in resource_map().values())


def test_forfattningstyp():
    assert forfattningstyp("Lag (1902:71 s.1) om foo") == "rpubl:Lag"
    assert forfattningstyp("Miljöbalk (1998:808)") == "rpubl:Lag"
    assert forfattningstyp(
        "Förordning (1998:899) om miljöfarlig verksamhet") == "rpubl:Forordning"
    assert forfattningstyp(
        "Kungörelse (1951:25) angående foo") == "rpubl:Forordning"


def test_forarbete_identifier_normalizes_prop():
    assert forarbete_identifier("bet. 1980/81:JuU4") == "Bet. 1980/81:JuU4"
    assert forarbete_identifier("rskr. 1980/81:14") == "Rskr. 1980/81:14"
    assert forarbete_identifier("Prop.1992/93:90") == "Prop. 1992/93:90"
    assert forarbete_identifier("Prop. 2009/2010:87") == "Prop. 2009/10:87"


def test_parse_forarbeten_field():
    parser = LagrumParser(load_namedlaws(NAMEDLAWS), "1980:100",
                          parse_types=[FORARBETEN])
    got = parse_forarbeten(
        "Prop. 1980/81:18, bet. 1980/81:JuU4, rskr. 1980/81:14", parser)
    assert got == ["Bet. 1980/81:JuU4", "Prop. 1980/81:18",
                   "Rskr. 1980/81:14"]


def test_omfattning_predicate():
    assert omfattning_predicate("ändr. 1 §") == "rpubl:ersatter"
    assert omfattning_predicate("upph.") == "rpubl:upphaver"
    assert omfattning_predicate("ny 3 §") == "rpubl:inforsI"
    assert omfattning_predicate("tillägg 7 §") == "rpubl:inforsI"
    assert omfattning_predicate("nuvarande 2 § betecknas 3 §") is None


# --- register parsing ---------------------------------------------------

def test_register_from_source_sorts_changes_chronologically():
    # the API's andringsforfattningar carries no reliable order (2017:900
    # arrives oldest-first, most documents newest-first): the register sorts
    # by SFS number, the register page's oldest-first publication order
    reg = register_from_source({
        "beteckning": "2017:900", "rubrik": "Förvaltningslag (2017:900)",
        "andringsforfattningar": [
            {"beteckning": "2019:981", "rubrik": ""},
            {"beteckning": "2018:1210", "rubrik": ""},
            {"beteckning": "2025:582", "rubrik": ""},
            # numeric löpnummer order, not string order (900 < 1001)
            {"beteckning": "2018:900", "rubrik": ""}]})
    assert [c.sfsnr for c in reg.changes] == \
        ["2018:900", "2018:1210", "2019:981", "2025:582"]


@needs_json_corpus
def test_register_header_and_changes():
    reg = inputs("1951:25")[1]
    assert reg.sfsnr == "1951:25"
    assert reg.header["Departement"] == "Finansdepartementet BA"
    # the repeal date now comes from the JSON upphavdDateTime (it was lost on the
    # JSON path before; the SFSR HTML carried it in the register header)
    assert reg.header["Upphävd"].startswith("1994-03-01")
    assert [c.sfsnr for c in reg.changes] == ["1994:14"]
    # base act first, then change acts
    assert [a.sfsnr for a in reg.acts] == ["1951:25", "1994:14"]


@needs_json_corpus
def test_amendment_properties_base_act():
    reg = inputs("1951:25")[1]
    parser = LagrumParser(load_namedlaws(NAMEDLAWS), "1951:25")
    props = amendment_properties(reg.acts[0], "1951:25", parser,
                                 "https://lagen.nu/")
    assert props["dcterms:identifier"] == "SFS 1951:25"
    assert props["rpubl:departement"] == \
        "https://lagen.nu/org/2008/finansdepartementet"
    assert props["rpubl:forfattningssamling"] == "https://lagen.nu/dataset/sfs"
    assert props["rpubl:upphavandedatum"] == "1994-03-01"
    # title/rdf:type belong to document metadata, never the register entry
    assert "dcterms:title" not in props
    assert "rdf:type" not in props


@needs_json_corpus
def test_omfattning_resolves_to_paragraph_uris():
    reg = inputs("1998:808")[1]
    parser = LagrumParser(load_namedlaws(NAMEDLAWS), "1998:808")
    # "ändr. 17 kap. 1 §; ny 9 kap. 6 i §" -> the changed paragraf resolves to
    # rpubl:ersatter, the new one to rpubl:inforsI, both as paragraf URIs
    act = next(a for a in reg.changes if a.sfsnr == "2018:641")
    props = amendment_properties(act, "1998:808", parser, "https://lagen.nu/")
    assert props["rpubl:ersatter"] == ["https://lagen.nu/1998:808#K17P1"]
    assert props["rpubl:inforsI"] == ["https://lagen.nu/1998:808#K9P6i"]


@needs_json_corpus
def test_omfattning_whitespace_normalized():
    # the JSON Omfattning ("anteckningar") carries hard \r\n line breaks; the
    # register parser must collapse them so the raw rpubl:andrar matches the
    # golden's whitespace-collapsed form (and the lagrum scan resolves cleanly)
    reg = inputs("1998:808")[1]
    parser = LagrumParser(load_namedlaws(NAMEDLAWS), "1998:808")
    act = next(a for a in reg.changes if a.sfsnr == "2002:175")
    andrar = amendment_properties(act, "1998:808", parser,
                                  "https://lagen.nu/")["rpubl:andrar"]
    assert "\n" not in andrar and "\r" not in andrar
    assert "  " not in andrar  # no doubled spaces either


# --- golden-diff integration -------------------------------------------

@pytest.mark.parametrize("basefile", ["1990:100", "1951:25"])
@needs_json_corpus
@needs_golden_corpus
def test_amendments_match_golden(basefile):
    gold, module = golden(basefile)
    problems = []
    module.diff_amendments(gold["amendments"],
                           normalform(basefile)["amendments"], problems)
    assert problems == []


@needs_json_corpus
def test_sfst_header_parses_cutoff():
    header = inputs("1998:808")[2]
    # the cutoff drifts as the source is re-downloaded; assert the shape, not the
    # exact SFS number
    assert header["Ändring införd"].startswith("t.o.m. SFS ")
    assert header["Utfärdad"].startswith("1998-06-11")


@needs_json_corpus
def test_build_metadata_consolidation_envelope():
    _, reg, header = inputs("1998:808")
    cutoff = header["Ändring införd"].replace("t.o.m. SFS ", "")
    meta = build_metadata(header, reg, "1998:808")
    assert meta["uri"] == "https://lagen.nu/1998:808/konsolidering/%s" % cutoff
    p = meta["properties"]
    assert p["dcterms:identifier"] == \
        "SFS 1998:808 i lydelse enligt SFS %s" % cutoff
    assert p["dcterms:alternate"] == "MB"
    assert p["rpubl:konsoliderar"] == "https://lagen.nu/1998:808"
    assert "https://lagen.nu/1998:808" in p["rpubl:konsolideringsunderlag"]


# Exact metadata golden-match only holds for documents whose amendment set is
# frozen relative to the golden: 1990:100 (stable) and 1951:25 (repealed). A
# living, frequently-amended act (1962:700, 1998:808) accrues post-freeze
# amendments on re-download, drifting its consolidation cutoff / underlag past
# the golden -- new-is-right drift, covered by the corpus run's adjudicator, not
# assertable against a frozen golden here.
@pytest.mark.parametrize("basefile", ["1990:100", "1951:25"])
@needs_json_corpus
@needs_golden_corpus
def test_metadata_match_golden(basefile):
    # dcterms:title diverges on purpose: the JSON carries the modernised title
    # ("Kungörelse..."), the golden the historical one ("Kungl. Maj:ts
    # kungörelse..."); everything else must still match the frozen golden.
    gold, module = golden(basefile)
    nf = normalform(basefile)
    nf["metadata"]["properties"].pop("dcterms:title", None)
    gold["metadata"]["properties"].pop("dcterms:title", None)
    problems = []
    module.diff_metadata(gold, nf, problems)
    assert problems == []


@needs_json_corpus
def test_overgangsbestammelse_content_joined():
    """The base act's övergångsbestämmelse joins onto its amendment entry as
    content with the L-prefixed fragment ids."""
    base = next(a for a in normalform("1990:100")["amendments"]
                if a["uri"] == "https://lagen.nu/1990:100")
    assert base["content"][0]["id"] == "L1990:100"
    assert base["content"][0]["children"][0]["id"] == "L1990:100S1"
