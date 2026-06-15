"""Tests for the SFSR register parser and amendment mapping.

Unit tests for the pure helpers, plus golden-diff integration tests that
assert the full amendments section reproduces the frozen golden corpus for
known-good documents (using the same comparator the corpus run uses).
"""

import importlib.util
import json
from pathlib import Path

import pytest

from accommodanda.sfs import parse_sfs
from accommodanda.lib.lagrum import LagrumParser, load_namedlaws
from accommodanda.sfs.nf import to_normalform
from accommodanda.lib.lagrum import FORARBETEN
from accommodanda.sfs.register import (amendment_properties, amendment_uri,
                                  build_metadata, forarbete_identifier,
                                  forfattningstyp, lfragment,
                                  omfattning_predicate, parse_forarbeten,
                                  parse_register, parse_sfst_header,
                                  sanitize_departement, sfs_slug)

ROOT = Path(__file__).parent.parent
NAMEDLAWS = ROOT / "lagen/nu/res/extra/sfs.ttl"


def golden_module():
    spec = importlib.util.spec_from_file_location(
        "golden_sfs", ROOT / "tools" / "golden_sfs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def paths(basefile):
    year, rest = basefile.split(":")
    slug = rest.replace(" ", "_")
    return (ROOT / "site/data/sfs/register" / year / (slug + ".html"),
            ROOT / "site/data/sfs/downloaded" / year / (slug + ".html"),
            ROOT / "site/data/sfs/golden" / year / (slug + ".json"))


def normalform(basefile):
    register_path, downloaded, _ = paths(basefile)
    return to_normalform(
        parse_sfs(str(downloaded), basefile), basefile,
        refparser=LagrumParser(load_namedlaws(NAMEDLAWS), basefile),
        register=parse_register(register_path),
        sfst_header=parse_sfst_header(downloaded))


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

def test_parse_register_header_and_changes():
    register, _, _ = paths("1951:25")
    reg = parse_register(register)
    assert reg.sfsnr == "1951:25"
    assert reg.header["Departement"] == "Finansdepartementet BA"
    assert reg.header["Upphävd"] == "1994-03-01"
    assert [c.sfsnr for c in reg.changes] == ["1994:14"]
    # base act first, then change acts
    assert [a.sfsnr for a in reg.acts] == ["1951:25", "1994:14"]


def test_amendment_properties_base_act():
    register, _, _ = paths("1951:25")
    reg = parse_register(register)
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


def test_omfattning_resolves_to_paragraph_uris():
    register, _, _ = paths("1902:71 s.1")
    reg = parse_register(register)
    parser = LagrumParser(load_namedlaws(NAMEDLAWS), "1902:71 s.1")
    # find the change act amending §§ 1-3 ("ändr. 1, 2, 3 §§")
    act = next(a for a in reg.changes if a.sfsnr == "1907:69 s.2")
    props = amendment_properties(act, "1902:71 s.1", parser,
                                 "https://lagen.nu/")
    assert props["rpubl:andrar"] == "ändr. 1, 2, 3 §§"
    targets = props["rpubl:ersatter"]
    assert len(targets) == 3 and all("#P" in t for t in targets)


# --- golden-diff integration -------------------------------------------

@pytest.mark.parametrize("basefile", ["1990:100", "1951:25"])
def test_amendments_match_golden(basefile):
    _, _, golden_path = paths(basefile)
    golden = json.loads(golden_path.read_text())
    problems = []
    golden_module().diff_amendments(golden["amendments"],
                                    normalform(basefile)["amendments"], problems)
    assert problems == []


def test_sfst_header_parses_cutoff():
    _, downloaded, _ = paths("1998:808")
    header = parse_sfst_header(downloaded)
    assert header["Ändring införd"] == "t.o.m. SFS 2025:976"
    assert header["Utfärdad"] == "1998-06-11"


def test_build_metadata_consolidation_envelope():
    reg = parse_register(paths("1998:808")[0])
    header = parse_sfst_header(paths("1998:808")[1])
    meta = build_metadata(header, reg, "1998:808")
    assert meta["uri"] == "https://lagen.nu/1998:808/konsolidering/2025:976"
    p = meta["properties"]
    assert p["dcterms:identifier"] == "SFS 1998:808 i lydelse enligt SFS 2025:976"
    assert p["dcterms:alternate"] == "MB"
    assert p["rpubl:konsoliderar"] == "https://lagen.nu/1998:808"
    assert "https://lagen.nu/1998:808" in p["rpubl:konsolideringsunderlag"]


@pytest.mark.parametrize("basefile", ["1998:808", "1990:100", "1962:700"])
def test_metadata_match_golden(basefile):
    _, _, golden_path = paths(basefile)
    golden = json.loads(golden_path.read_text())
    problems = []
    golden_module().diff_metadata(golden, normalform(basefile), problems)
    assert problems == []


def test_overgangsbestammelse_content_joined():
    """The base act's övergångsbestämmelse joins onto its amendment entry as
    content with the L-prefixed fragment ids."""
    register_path, downloaded, _ = paths("1990:100")
    reg = parse_register(register_path)
    doc = parse_sfs(str(downloaded), "1990:100")
    nf = to_normalform(doc, "1990:100",
                       refparser=LagrumParser(load_namedlaws(NAMEDLAWS), "1990:100"),
                       register=reg)
    base = next(a for a in nf["amendments"]
                if a["uri"] == "https://lagen.nu/1990:100")
    assert base["content"][0]["id"] == "L1990:100"
    assert base["content"][0]["children"][0]["id"] == "L1990:100S1"
