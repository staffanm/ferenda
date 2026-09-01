"""Tests for the eurlex history-as-git export (eurlex/asgit.py): CELEX-to-path
decomposition, first-recital extraction, commit-message/trailer composition,
and a golden fast-import stream (round-tripped through a real
`git fast-import`) built from the eIDAS regulation's real consolidation
history (32014R0910, the same fixture shape as test_eurlex_versions.py)."""

import json
import subprocess

import pytest

from ferenda.eurlex import versions as eurlex_versions
from ferenda.eurlex.asgit import (
    _act_name,
    _emit_commits,
    celex_path,
    export,
    message,
    stream,
)
from ferenda.eurlex.parse import parse_dir
from ferenda.lib import compress, gitledger, layout
from ferenda.lib.errors import RebuildRequired

BASE_XML = """<ACT>
  <BIB.INSTANCE><DATE ISO="20140723">20140723</DATE></BIB.INSTANCE>
  <TITLE><TI><P>F&#246;rordning (EU) nr 910/2014 om elektronisk
    identifiering</P></TI></TITLE>
  <PREAMBLE><GR.CONSID><CONSID><NP><NO.P>(1)</NO.P><TXT>Den inre marknaden
    kr&#228;ver tillf&#246;rlitlig elektronisk identifiering.</TXT></NP>
    </CONSID></GR.CONSID></PREAMBLE>
  <ENACTING.TERMS>
    <ARTICLE IDENTIFIER="001"><TI.ART>Artikel 1</TI.ART>
      <ALINEA>Ursprunglig text.</ALINEA></ARTICLE>
  </ENACTING.TERMS>
</ACT>"""

CONS_XML = """<CONS.ACT>
 <INFO.CONSLEG START.DATE="{date}" LEG.VAL="REG"/>
 <CONS.DOC>
  <BIB.INSTANCE><DATE ISO="20140723">20140723</DATE></BIB.INSTANCE>
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
     <ALINEA>Konsoliderad text {date}.</ALINEA></ARTICLE>
  </ENACTING.TERMS>
 </CONS.DOC>
</CONS.ACT>"""

NOTICE = (b'<x> <http://publications.europa.eu/ontology/cdm#'
          b'work_date_document> "2014-07-23" .\n')


def _version(doc_dir, date, xml):
    vdir = doc_dir / ".versions" / date
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "swe.fmx4").write_bytes(xml.encode())
    (vdir / "notice.ttl").write_bytes(NOTICE)


def _write_main_artifact(basefile, art):
    """The build's own write, minus write_artifact's source_url stamping
    (not exercised here)."""
    out = layout.artifact("eurlex", basefile)
    out.parent.mkdir(parents=True, exist_ok=True)
    compress.write_text(out, json.dumps(art, ensure_ascii=False,
                                        sort_keys=True),
                        encodings=compress.ARTIFACT_ENCODINGS)


def _corpus(tmp_path, monkeypatch):
    """eIDAS with two consolidations (an older and the newer, amending one)
    -- the same shape test_eurlex_versions.py uses, built here so this
    module does not import fixtures across test files."""
    downloaded = tmp_path / "downloaded" / "eurlex"
    monkeypatch.setattr(layout, "EURLEX_DOWNLOADED", downloaded)
    monkeypatch.setattr(layout, "ARTIFACT", tmp_path / "artifact")
    doc_dir = downloaded / "2014" / "32014R0910"
    doc_dir.mkdir(parents=True)
    (doc_dir / "swe.fmx4").write_bytes(BASE_XML.encode())
    (doc_dir / "notice.ttl").write_bytes(NOTICE)
    _version(doc_dir, "2014-09-17", CONS_XML.format(date="20140917"))
    _version(doc_dir, "2024-10-18", CONS_XML.format(date="20241018"))
    eurlex_versions.build("32014R0910")
    _write_main_artifact("32014R0910", parse_dir(doc_dir, "32014R0910"))
    return doc_dir


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          text=True, capture_output=True).stdout.strip()


def test_celex_path():
    assert celex_path("32016R0679") == "32016/R0679.md"
    assert celex_path("32022L2555") == "32022/L2555.md"
    assert celex_path("32016D2295") is None       # a decision, not R/L
    assert celex_path("32016R0679R(01)") is None   # a corrigendum
    assert celex_path("12019W/TXT") is None        # a treaty


URI = "https://lagen.nu/celex/32014R0910"


def test_message_subject_is_the_act_name_not_the_recital():
    art = {"celex": "32014R0910", "shortname": "eIDAS-förordningen",
          "uri": URI, "date": "2014-07-23",
          "structure": [{"type": "recital", "num": "1",
                        "text": "Den inre marknaden."}]}
    msg = message(art, "2014-07-23")
    assert msg == "eIDAS-förordningen\n\nDen inre marknaden.\n\n%s\n" % URI
    assert "(1)" not in msg              # the printed marker is dropped
    assert "Lagen-" not in msg           # no machine-trailer block, see docstring


def test_message_amendment_names_the_amending_act_in_prose():
    art = {"celex": "32014R0910", "uri": URI + "/konsolidering/2024-10-18",
          "structure": [],
          "consolidation": {"date": "2024-10-18",
                           "amending": [{"celex": "32024R1183"}],
                           "corrigenda": []}}
    msg = message(art, "2024-10-18")
    assert "Ändrad genom 32024R1183." in msg
    assert "Rättad" not in msg


def test_message_corrigendum_only_is_a_correction_not_an_amendment():
    art = {"celex": "32014R0910", "uri": URI + "/konsolidering/2014-08-01",
          "structure": [],
          "consolidation": {"date": "2014-08-01", "amending": [],
                           "corrigenda": ["32014R0910R(01)"]}}
    msg = message(art, "2014-08-01")
    assert "Rättad genom 32014R0910R(01)." in msg
    assert "Ändrad" not in msg


def test_message_dates_a_pre_1970_commit_explicitly():
    """The git ident date clamps to 1970 for a pre-1970 act (_epoch), so the
    true date must survive somewhere the reader can still find it."""
    art = {"celex": "31968R0805", "uri": "https://lagen.nu/celex/31968R0805",
          "title": "Förordning om nötkött", "structure": []}
    msg = message(art, "1968-06-27")
    assert "Ursprungsdatum 1968-06-27 (git visar 1970-01-01)." in msg


def test_message_falls_back_to_title_without_a_recital():
    art = {"celex": "32014R0910", "title": "Förordning (EU) nr 910/2014",
          "uri": URI, "structure": [{"type": "article",
                                    "text": "no recital here"}]}
    assert message(art, "2014-07-23") == (
        "Förordning (EU) nr 910/2014\n\n%s\n" % URI)


def test_act_name_shortens_a_long_title_at_a_word_boundary():
    long_title = "Europaparlamentets och rådets förordning om " + "x" * 60
    art = {"celex": "32014R0910", "title": long_title}
    name = _act_name(art)
    assert len(name) <= 72
    assert name.endswith("…")


def test_stream_and_export_roundtrip_through_git_fast_import(tmp_path,
                                                              monkeypatch):
    _corpus(tmp_path, monkeypatch)
    chunks = b"".join(stream(["32014R0910"]))
    assert chunks.count(b"commit refs/heads/main\n") == 2

    repo = tmp_path / "repo"
    export(repo, ["32014R0910"])
    log = _git(repo, "log", "--format=%ad %s", "--date=short", "main"
              ).splitlines()
    # newest (the amendment) first; author date is each wording's own; the
    # named-acts dataset recognizes this real CELEX, so the subject is its
    # curated short name rather than a fallback
    assert log == ["2024-10-18 eIDAS-förordningen",
                   "2014-09-17 eIDAS-förordningen"]
    show = _git(repo, "show", "main:32014/R0910.md")
    assert "Konsoliderad text 20241018" in show
    body = _git(repo, "show", "-s", "--format=%B", "main")
    assert "Den inre marknaden kräver tillförlitlig elektronisk " \
          "identifiering." in body
    assert "(1)" not in body
    assert "Lagen-" not in body           # no machine-trailer block
    assert "Ändrad genom 32024R1183." in body
    assert body.rstrip().endswith("https://lagen.nu/celex/32014R0910")

    # a second run on an unchanged corpus appends nothing new
    assert export(repo, ["32014R0910"]) == 0
    assert len(_git(repo, "log", "--format=%H", "main").splitlines()) == 2


def test_export_appends_only_a_genuinely_new_version(tmp_path, monkeypatch):
    doc_dir = _corpus(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    assert export(repo, ["32014R0910"]) == 2
    old_tips = _git(repo, "log", "--format=%H", "main").splitlines()

    # a new consolidation supersedes the old "main" (2024-10-18), which now
    # becomes an archived version -- the file content changes, but the two
    # already-committed wordings themselves do not
    _version(doc_dir, "2025-06-01", CONS_XML.format(date="20250601"))
    eurlex_versions.build("32014R0910")
    _write_main_artifact("32014R0910", parse_dir(doc_dir, "32014R0910"))

    assert export(repo, ["32014R0910"]) == 1
    new_log = _git(repo, "log", "--format=%H", "main").splitlines()
    assert len(new_log) == 3
    assert new_log[1:] == old_tips              # the first two are untouched
    assert "Konsoliderad text 20250601" in _git(repo, "show", "main:32014/R0910.md")


def test_export_scoped_to_one_act_leaves_others_in_a_wider_ledger_alone(
        tmp_path, monkeypatch):
    """A repo built with a wider scope (every requested basefile, not just
    one) holds ledger entries for acts a later, narrower run never looks
    at -- those are simply untouched, not "removed from the corpus"
    (`_append_reasons` is scoped to the run's own basefiles)."""
    _corpus(tmp_path, monkeypatch)
    other = {"celex": "31987R3027", "uri": "https://lagen.nu/celex/31987R3027",
            "title": "En annan förordning", "date": "1987-10-09",
            "structure": []}
    _write_main_artifact("31987R3027", other)
    repo = tmp_path / "repo"

    assert export(repo, ["32014R0910", "31987R3027"]) == 3
    full_tip = _git(repo, "rev-parse", "main")

    # a later run scoped to only one of the two acts must not treat the
    # other as absent and demand a rebuild
    assert export(repo, ["32014R0910"]) == 0
    assert _git(repo, "rev-parse", "main") == full_tip
    assert "31987/R3027.md" in _git(repo, "ls-tree", "-r", "--name-only", "main")

    # nor may a *rebuild* narrower than the existing ledger: that would
    # replace `main` with only what this run named, discarding (and, via
    # reclaim=True, pruning outright) 31987R3027's already-committed history
    with pytest.raises(ValueError, match="31987R3027"):
        export(repo, ["32014R0910"], rebuild=True)
    assert _git(repo, "rev-parse", "main") == full_tip
    assert "31987/R3027.md" in _git(repo, "ls-tree", "-r", "--name-only", "main")

    # rebuilding with every ledgered basefile named is the correct way in
    assert export(repo, ["32014R0910", "31987R3027"], rebuild=True) == 3


def test_export_requires_rebuild_when_a_committed_version_changes(tmp_path,
                                                                   monkeypatch):
    doc_dir = _corpus(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    export(repo, ["32014R0910"])

    # the archived 2014-09-17 wording's text changes underneath an
    # already-committed hash -- e.g. a rendering bug fix -- so a plain
    # append would silently rewrite history instead of extending it
    _version(doc_dir, "2014-09-17",
            CONS_XML.format(date="20140917").replace(
                "Konsoliderad text", "Rättad konsoliderad text"))
    eurlex_versions.build("32014R0910")
    _write_main_artifact("32014R0910", parse_dir(doc_dir, "32014R0910"))

    with pytest.raises(RebuildRequired, match="32014R0910 changed"):
        export(repo, ["32014R0910"])
    assert export(repo, ["32014R0910"], rebuild=True) == 2


def test_export_requires_rebuild_for_a_repo_predating_the_ledger(tmp_path,
                                                                  monkeypatch):
    _corpus(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.org")
    (repo / "unrelated.txt").write_text("no ledger file here\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "a repo history-as-git never built")

    assert not gitledger.path(repo).exists()
    with pytest.raises(RebuildRequired, match="missing or predates"):
        export(repo, ["32014R0910"])
    assert export(repo, ["32014R0910"], rebuild=True) == 2


def test_export_refuses_a_dirty_or_non_main_target(tmp_path, monkeypatch):
    """gitledger.prepare_repo's validation (shared with sfs.asgit) applies
    here too: an uncommitted change, or a checkout not on `main`, must
    refuse before `--rebuild-history` would otherwise discard it."""
    _corpus(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    export(repo, ["32014R0910"])

    (repo / "32014" / "R0910.md").write_text("lokal ändring", encoding="utf-8")
    with pytest.raises(ValueError, match="uncommitted changes"):
        export(repo, ["32014R0910"])
    _git(repo, "checkout", "--", ".")

    _git(repo, "checkout", "-qb", "side")
    with pytest.raises(ValueError, match="main checked out"):
        export(repo, ["32014R0910"])


def test_stream_raises_when_the_artifact_changed_since_the_ledger_decision():
    """`_emit_commits`'s emit-time reverification (mirrors
    sfs.asgit.stream's snapshot-changed check): a `desired` record whose
    hash no longer matches the freshly rendered markdown must abort loudly,
    not silently commit different text than the ledger will claim."""
    art = {"celex": "32014R0910", "uri": URI, "date": "2014-07-23",
          "structure": []}
    kept = [(art, "2014-07-23", "current text")]
    wrong = [{"id": "32014R0910@2014-07-23", "celex": "32014R0910",
             "date": "2014-07-23", "body": "0" * 64}]
    with pytest.raises(RuntimeError, match="artifact changed"):
        b"".join(_emit_commits("32014R0910", kept, "refs/heads/main", [None],
                               wrong))
