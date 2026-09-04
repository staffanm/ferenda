"""The eurlex versions stage (ferenda/eurlex/source.py), the generalized
version endpoints' eurlex branches, and the konsolidering rendering."""

import json
import os

import pytest
from fastapi import HTTPException

from ferenda.api.app import _validate_version_id, _versioned_document
from ferenda.eurlex import render as eurlex_render
from ferenda.eurlex import source
from ferenda.eurlex.parse import parse_dir
from ferenda.lib import catalog, compress, layout, page
from ferenda.lib.errors import SkipDocument

# a minimal base act with a preamble (spliced into every consolidated wording)
BASE_XML = """<ACT>
  <BIB.INSTANCE><DATE ISO="20140723">20140723</DATE></BIB.INSTANCE>
  <TITLE><TI><P>F&#246;rordning (EU) nr 910/2014 om elektronisk
    identifiering</P></TI></TITLE>
  <PREAMBLE><GR.CONSID><CONSID><NP><NO.P>(1)</NO.P><TXT>Det f&#246;rsta
    sk&#228;let.</TXT></NP></CONSID></GR.CONSID></PREAMBLE>
  <ENACTING.TERMS>
    <ARTICLE IDENTIFIER="001"><TI.ART>Artikel 1</TI.ART>
      <ALINEA>Ursprunglig text.</ALINEA></ARTICLE>
  </ENACTING.TERMS>
</ACT>"""

# a consolidated wording: one untouched article, one inserted, one deleted,
# with the FAM.COMP register naming the amending act
CONS_XML = """<CONS.ACT>
 <INFO.CONSLEG START.DATE="20241018" LEG.VAL="REG"/>
 <CONS.DOC>
  <BIB.INSTANCE><DATE ISO="20241018">20241018</DATE></BIB.INSTANCE>
  <FAM.COMP LEG.VAL="REG">
   <BIB.DATA><NO.CELEX>32014R0910</NO.CELEX>
     <DATE ISO="20140723">20140723</DATE></BIB.DATA>
   <GR.MOD.ACT><MOD.ACT><BIB.DATA><NO.CELEX>32024R1183</NO.CELEX>
     <TITLE><TI><P>Europaparlamentets och r&#229;dets f&#246;rordning (EU)
       2024/1183</P></TI></TITLE></BIB.DATA></MOD.ACT></GR.MOD.ACT>
  </FAM.COMP>
  <TITLE><TI><P>F&#246;rordning (EU) nr 910/2014 om elektronisk
    identifiering</P></TI></TITLE>
  <PREAMBLE><PREAMBLE.INIT/><PREAMBLE.FINAL/></PREAMBLE>
  <ENACTING.TERMS>
   <ARTICLE IDENTIFIER="001"><TI.ART>Artikel 1</TI.ART>
     <ALINEA>Konsoliderad text.</ALINEA></ARTICLE>
   <?CLG.MDFO ID="O1" IDREF="C1" ACTION="INSERTED" LEVEL="STRUCTURE"
      ACTIVE.DOC="32024R1183" ACTIVE.LOC="AR:1;PT:5"?>
   <ARTICLE IDENTIFIER="005A"><TI.ART>Artikel 5a</TI.ART>
     <ALINEA>Den nya artikelns text.</ALINEA></ARTICLE>
   <?CLG.MDFC ID="C1" IDREF="O1"?>
   <?CLG.MDFO ID="O2" IDREF="C2" ACTION="DELETED" LEVEL="STRUCTURE"
      ACTIVE.DOC="32024R1183" ACTIVE.LOC="AR:1;PT:17"?>
   <ARTICLE IDENTIFIER="019"><TI.ART>Artikel 19</TI.ART>
     <ALINEA>Den upph&#228;vda artikelns text.</ALINEA></ARTICLE>
   <?CLG.MDFC ID="C2" IDREF="O2"?>
  </ENACTING.TERMS>
 </CONS.DOC>
</CONS.ACT>"""

NOTICE = (b'<x> <http://publications.europa.eu/ontology/cdm#'
          b'work_date_document> "2014-07-23" .\n')


def _version(doc_dir, date, xml):
    vdir = doc_dir / ".versions" / date
    vdir.mkdir(parents=True)
    (vdir / "swe.fmx4").write_bytes(xml.encode())
    (vdir / "notice.ttl").write_bytes(NOTICE)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Isolated eurlex download + artifact trees, with one act whose history
    holds an older consolidation, the (newer) one parse_dir presents, and a
    PDF-only pre-Formex version."""
    downloaded = tmp_path / "downloaded" / "eurlex"
    monkeypatch.setattr(layout, "EURLEX_DOWNLOADED", downloaded)
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    doc_dir = downloaded / "2014" / "32014R0910"
    doc_dir.mkdir(parents=True)
    (doc_dir / "swe.fmx4").write_bytes(BASE_XML.encode())
    (doc_dir / "notice.ttl").write_bytes(NOTICE)
    _version(doc_dir, "2024-10-18", CONS_XML)
    _version(doc_dir, "2014-09-17",
             CONS_XML.replace('START.DATE="20241018"',
                              'START.DATE="20140917"'))
    pdf_only = doc_dir / ".versions" / "2003-01-01"
    pdf_only.mkdir(parents=True)
    (pdf_only / "swe.pdf").write_bytes(b"%PDF-1.4 not formex")
    (pdf_only / "notice.ttl").write_bytes(NOTICE)
    return doc_dir


def _write_main_artifact(basefile, art):
    compress.write_json(layout.artifact("eurlex", basefile), art)


def _build(roots, basefile):
    """parse + the versions stage over one act, serially: the main artifact
    (which decides the wording the stage excludes), every fan-out key, then
    the sidecar hook -- what `lagen eurlex parse` / `versions` run. A key's
    SkipDocument leaves the driver's empty placeholder, as ensure() does.
    Returns the sidecar dict."""
    _write_main_artifact(basefile, parse_dir(roots, basefile))
    for key in source.eurlex_version_list():
        out = source.eurlex_version_output(key)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            source.eurlex_version_run(key)
        except SkipDocument:
            compress.write_bytes(out, b"", encodings=compress.ARTIFACT_ENCODINGS)
    source.eurlex_versions_rebuild_sidecars()
    return json.loads(layout.eurlex_versions_sidecar(basefile).read_text())


def test_versions_stage_builds_sidecar_and_lydelse_artifacts(roots):
    sidecar = _build(roots, "32014R0910")
    # the newest Formex version is the main artifact, not a lydelse; the
    # PDF-only tail is recorded, not parsed
    assert [e["version"] for e in sidecar["versions"]] == ["2014-09-17"]
    assert sidecar["versions"][0]["uri"] == (
        "https://lagen.nu/celex/32014R0910/konsolidering/2014-09-17")
    assert [s["version"] for s in sidecar["skipped"]] == ["2003-01-01"]
    art = compress.read_json(
        layout.eurlex_version_artifact("32014R0910", "2014-09-17"))
    assert art["version"] == "2014-09-17"
    assert layout.eurlex_versions_sidecar("32014R0910").exists()
    # the exclusion agrees with parse_dir's pick, or a lydelse page would
    # duplicate the act's own
    main = parse_dir(roots, "32014R0910")
    assert main["consolidation"]["date"] == "2024-10-18"


def test_version_endpoints_dispatch_by_uri():
    assert _versioned_document("https://lagen.nu/1998:204") == ("sfs", "1998:204")
    assert _versioned_document("https://lagen.nu/celex/32014R0910") \
        == ("eurlex", "32014R0910")
    with pytest.raises(HTTPException):
        _versioned_document("https://lagen.nu/dom/nja/2015s1")
    _validate_version_id("eurlex", "2024-10-18")
    with pytest.raises(HTTPException):
        _validate_version_id("eurlex", "../../etc/passwd")
    with pytest.raises(HTTPException):
        _validate_version_id("eurlex", "20241018")


def _rendered(tmp_path, monkeypatch, art):
    monkeypatch.setattr(eurlex_render, "_load_editorial", lambda celex: None)
    db = str(tmp_path / "catalog.sqlite")
    path = tmp_path / "act.json"
    path.write_text(json.dumps(art))
    catalog.rebuild(db, "eurlex", [path])
    return eurlex_render.render(art, page.Site.from_catalog(catalog.connect(db)))


def test_consolidated_page_renders_provenance(roots, tmp_path, monkeypatch):
    monkeypatch.setattr(eurlex_render.history, "versions",
                        lambda source, bf: [("2014-09-17", "u")])
    html = _rendered(tmp_path, monkeypatch, parse_dir(roots, "32014R0910"))
    assert "Konsoliderad version" in html            # the banner
    assert html.count("artikel-mod")                 # the "Ändrad genom" lines
    assert "Upphävd genom" in html                   # article 19's line
    assert 'class="artikel-upphavd"' in html         # ... and its subdued region
    assert "Konsoliderad t.o.m." in html
    assert "data-diff" in html                       # the compare panel


def test_lydelse_page_banner_matches_its_temporality(roots, tmp_path,
                                                     monkeypatch):
    _build(roots, "32014R0910")
    monkeypatch.setattr(eurlex_render.history, "versions",
                        lambda source, bf: [])
    art = compress.read_json(
        layout.eurlex_version_artifact("32014R0910", "2014-09-17"))
    html = _rendered(tmp_path, monkeypatch, art)
    assert "Äldre lydelse" in html and "äldre lydelse" in html
    # a forward-dated wording is coming, not old -- banner and eyebrow agree
    art["version"] = "2999-01-01"
    html = _rendered(tmp_path, monkeypatch, art)
    assert "Kommande lydelse" in html and "kommande lydelse" in html
    assert "Äldre lydelse" not in html


def test_a_broken_newest_version_is_skipped_and_the_older_serves(roots,
                                                                 tmp_path,
                                                                 monkeypatch):
    """CELLAR's scanned-era consolidations include zips whose .xml member
    holds TIFF bytes (31994L0062's 2015-05-26, among 13 of 172,043 measured).
    A broken version must cost neither the act's page (the next older wording
    stands in) nor its whole history (the versions stage records the skip) --
    and both paths pick the served wording by the same rule."""
    broken = roots / ".versions" / "2025-01-01"
    broken.mkdir(parents=True)
    (broken / "swe.fmx4").write_bytes(b"II*\x00 not xml at all")
    (broken / "notice.ttl").write_bytes(NOTICE)
    art = parse_dir(roots, "32014R0910")
    assert art["consolidation"]["date"] == "2024-10-18"    # the older serves
    sidecar = _build(roots, "32014R0910")
    assert [e["version"] for e in sidecar["versions"]] == ["2014-09-17"]
    errors = {s["version"]: s["error"] for s in sidecar["skipped"]}
    assert "XMLSyntaxError" in errors["2025-01-01"]
    assert errors["2003-01-01"].startswith("no Formex")


# --------------------------------------------------------------------------
# the fan-out dispatch (Stage.list_basefiles): one superseded consolidation
# per key instead of one job per act, the eurlex counterpart of sfs's. "Main"
# (the wording parse_dir already presents, excluded from the fan-out) is read
# back off the already-built main artifact, not re-derived by retrying
# parses -- that decision is parse_dir's alone, made before versions ever
# runs. `_build` above runs the two phases serially over one act.
# --------------------------------------------------------------------------

def test_version_list_excludes_the_main_wording(roots):
    _write_main_artifact("32014R0910", parse_dir(roots, "32014R0910"))
    keys = source.eurlex_version_list()
    # the newest (2024-10-18, main) is excluded; the older and the PDF-only
    # tail are both real dispatch keys -- the PDF-only one's own skip is the
    # per-item recipe's job, not the listing's
    assert keys == ["32014R0910@2003-01-01", "32014R0910@2014-09-17"]


def test_version_list_excludes_nothing_before_parse_has_ever_run(roots):
    # no main artifact yet (parse hasn't run) -- nothing to exclude, every
    # archived consolidation is a real key
    keys = source.eurlex_version_list()
    assert keys == ["32014R0910@2003-01-01", "32014R0910@2014-09-17",
                    "32014R0910@2024-10-18"]


def test_version_list_tolerates_an_empty_main_artifact(roots):
    # ensure() writes an empty placeholder artifact for ANY stage's
    # SkipDocument, unconditionally -- an UNCARRIED act or one with no
    # swe/eng content leaves parse's own output empty, not absent. An empty
    # file is not valid JSON; this must not crash reading it back, real
    # corpus data hit exactly this the first time this ran (2026-09-04)
    layout.artifact("eurlex", "32014R0910").parent.mkdir(parents=True, exist_ok=True)
    compress.write_bytes(layout.artifact("eurlex", "32014R0910"), b"",
                        encodings=compress.ARTIFACT_ENCODINGS)
    keys = source.eurlex_version_list()
    assert keys == ["32014R0910@2003-01-01", "32014R0910@2014-09-17",
                    "32014R0910@2024-10-18"]


def test_version_run_writes_the_predicted_output_path(roots):
    _write_main_artifact("32014R0910", parse_dir(roots, "32014R0910"))
    key = "32014R0910@2014-09-17"
    source.eurlex_version_run(key)
    out = source.eurlex_version_output(key)
    assert compress.exists(out)
    assert compress.read_json(out)["version"] == "2014-09-17"


def test_version_run_raises_skipdocument_for_pdf_only(roots):
    _write_main_artifact("32014R0910", parse_dir(roots, "32014R0910"))
    with pytest.raises(SkipDocument, match="no Formex"):
        source.eurlex_version_run("32014R0910@2003-01-01")


def test_version_run_raises_skipdocument_for_a_broken_consolidation(roots):
    broken = roots / ".versions" / "2025-01-01"
    broken.mkdir(parents=True)
    (broken / "swe.fmx4").write_bytes(b"II*\x00 not xml at all")
    (broken / "notice.ttl").write_bytes(NOTICE)
    _write_main_artifact("32014R0910", parse_dir(roots, "32014R0910"))
    with pytest.raises(SkipDocument, match="XMLSyntaxError"):
        source.eurlex_version_run("32014R0910@2025-01-01")


def test_rebuild_sidecars_records_a_failed_version_without_reparsing(
        roots, monkeypatch):
    # a key the fan-out raised on has no artifact at all (the driver holds
    # the traceback in the errors ledger); the hook must record it, not run
    # the same parse again outside the per-document isolation -- a second
    # raise there used to end the whole run before relate
    _build(roots, "32014R0910")
    compress.resolve(layout.eurlex_version_artifact("32014R0910",
                                                    "2014-09-17")).unlink()
    os.utime(layout.eurlex_versions_sidecar("32014R0910"), ns=(0, 0))

    def only_the_benign_skip(vdir, celex, version, preamble=()):
        assert version == "2003-01-01", "re-parsed a failed version"
        return None            # what the real parse says of the PDF-only one

    monkeypatch.setattr(source, "parse_consolidation", only_the_benign_skip)
    source.eurlex_versions_rebuild_sidecars()
    sidecar = json.loads(layout.eurlex_versions_sidecar("32014R0910").read_text())
    assert sidecar["versions"] == []
    errors = {s["version"]: s["error"] for s in sidecar["skipped"]}
    assert errors["2014-09-17"].startswith("no version artifact")
    assert errors["2003-01-01"].startswith("no Formex")


def test_rebuild_sidecars_skips_an_act_untouched_since_its_last_build(
        roots, monkeypatch):
    _write_main_artifact("32014R0910", parse_dir(roots, "32014R0910"))
    for key in source.eurlex_version_list():
        try:
            source.eurlex_version_run(key)
        except SkipDocument:
            pass
    source.eurlex_versions_rebuild_sidecars()
    before = layout.eurlex_versions_sidecar("32014R0910").stat().st_mtime_ns

    def boom(*a, **kw):
        raise AssertionError("rebuilt a sidecar nothing changed under")
    monkeypatch.setattr(source.util, "write_json_atomic", boom)
    source.eurlex_versions_rebuild_sidecars()
    after = layout.eurlex_versions_sidecar("32014R0910").stat().st_mtime_ns
    assert after == before
