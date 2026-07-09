"""Tests for the SFS history-as-git export (sfs/asgit.py): identity/date
derivation, commit-message composition, and a golden fast-import stream for a
small synthetic corpus (round-tripped through a real `git fast-import`)."""

import hashlib
import json
import subprocess

import pytest

from accommodanda.lib import layout
from accommodanda.sfs.asgit import (
    Change,
    Event,
    RebuildRequired,
    email_slug,
    event_dates,
    existing_ledger,
    export,
    identities,
    message,
    ordered_events,
    scope_id,
    snapshot_text,
    stream,
)

PROP_META = {
    "Prop. 2020/21:194": {
        "title": "Ett starkare skydd för Sveriges säkerhet",
        "ingress": "För att stärka skyddet för Sveriges säkerhet föreslår "
                   "regeringen ändringar i säkerhetsskyddslagen.",
        "signers": ["Stefan Löfven", "Mikael Damberg"]},
    "Rskr. 2020/21:387": {"title": "Riksdagsskrivelse 2020/21:387",
                          "ingress": None,
                          "signers": ["Andreas Norlén", "Kristina Svartz"]},
}


def _meta(identifier):
    return PROP_META.get(identifier)


def test_email_slug_is_ascii_on_the_fake_domain():
    assert email_slug("Stefan Löfven") == "stefan.lofven@lagen.nu"
    assert email_slug("Lars-Erik Lövdén") == "lars.erik.lovden@lagen.nu"
    assert email_slug("Åsa Lindestam") == "asa.lindestam@lagen.nu"


def test_event_dates_fallback_chain():
    # utfärdandedatum wins as author date; committer date is always ikraft
    ev = Event(key="SFS 2021:952", utfardad="2021-10-07", ikraft="2021-12-01")
    assert event_dates(ev) == ("2021-10-07", "2021-12-01", False)
    # no utfärdandedatum (the common case): ikraft substitutes, marked
    ev = Event(key="SFS 2021:952", ikraft="2021-12-01")
    assert event_dates(ev) == ("2021-12-01", "2021-12-01", True)
    # neither: July 1 of the event's SFS year, still marked
    ev = Event(key="SFS 2021:952")
    assert event_dates(ev) == ("2021-07-01", "2021-07-01", True)


def test_identities_from_forarbete_signers_and_fallbacks():
    ev = Event(key="Prop. 2020/21:194", prop="Prop. 2020/21:194",
               rskr="Rskr. 2020/21:387")
    author, committer = identities(ev, _meta)
    assert author == ("Stefan Löfven", "stefan.lofven@lagen.nu")
    assert committer == ("Andreas Norlén", "andreas.norlen@lagen.nu")
    # unknown förarbeten -> the corpus fallbacks, never a guessed identity
    ev = Event(key="SFS 1962:700")
    assert identities(ev, _meta) == (("Regeringen", "regeringen@lagen.nu"),
                                     ("Riksdagen", "riksdagen@lagen.nu"))


def test_message_composition():
    ev = Event(key="Prop. 2020/21:194", prop="Prop. 2020/21:194",
               ikraft="2021-12-01",
               changes=[Change(path="2018/585.txt", src=None,
                               basefile="2018:585",
                               title="Säkerhetsskyddslag (2018:585)",
                               cutoff="2021:952", folded=["2021:498"],
                               body_hash="0" * 64)],
               deletes=[("1998/204.txt", "1998:204", "2018:218")])
    msg = message(ev, _meta)
    lines = msg.splitlines()
    assert lines[0] == ("Prop. 2020/21:194: Ett starkare skydd för Sveriges "
                        "säkerhet")
    assert "föreslår regeringen ändringar" in msg          # the ingress body
    assert ("SFS 2018:585: Säkerhetsskyddslag (2018:585) -- ändrad t.o.m. "
            "SFS 2021:952") in msg
    assert "innefattar även SFS 2021:498" in msg           # archive-gap caveat
    assert "SFS 1998:204: upphävd genom SFS 2018:218" in msg
    assert "Författardatum är ikraftträdandedatum" in msg  # date substitution
    assert "Lagen-Event: Prop. 2020/21:194" in msg
    assert ("Co-authored-by: Mikael Damberg <mikael.damberg@lagen.nu>"
            in msg)


def test_message_add_commit_notes_consolidation_caveat():
    ev = Event(key="SFS 2003:466",
               changes=[Change(path="1998/204.txt", src=None,
                               basefile="1998:204",
                               title="Personuppgiftslag (1998:204)",
                               cutoff="2003:466", add=True,
                               body_hash="0" * 64)])
    msg = message(ev, _meta)
    assert "första kända konsolidering (i lydelse enligt SFS 2003:466)" in msg
    assert "inte den ursprungliga lydelsen" in msg


def _change(path, cutoff):
    return Change(path=path, src=None, basefile=path[:-4].replace("/", ":"),
                  title="Testlag", cutoff=cutoff)


def test_ordered_events_enforces_per_statute_cutoff_order():
    # delayed entry into force: the 2024:1214 amendment's ikraft (2031) is
    # LATER than the 2025:1015 amendment's (2026), so a pure date sort would
    # emit the older consolidation last and silently leave stale text at tip
    e1 = Event(key="SFS 2024:1214", ikraft="2031-01-01",
               changes=[_change("1998/899.txt", "2024:1214")])
    e2 = Event(key="SFS 2025:1015", ikraft="2026-01-01",
               changes=[_change("1998/899.txt", "2025:1015")])
    order = [e.key for e in ordered_events({e.key: e for e in (e1, e2)})]
    assert order == ["SFS 2024:1214", "SFS 2025:1015"]
    # unconstrained events still sort purely by date around the chain
    e3 = Event(key="SFS 2020:1", ikraft="2020-01-01",
               changes=[_change("2019/5.txt", "2020:1")])
    order = [e.key for e in
             ordered_events({e.key: e for e in (e1, e2, e3)})]
    assert order == ["SFS 2020:1", "SFS 2024:1214", "SFS 2025:1015"]


def test_ordered_events_repeal_emits_after_last_change():
    # a repeal whose date sorts before the statute's last change must still
    # emit last -- otherwise the delete is overwritten and the repealed
    # statute is resurrected at the tip
    change = Event(key="SFS 2005:900", ikraft="2031-01-01",
                   changes=[_change("1999/175.txt", "2005:900")])
    repeal = Event(key="SFS 2006:1", ikraft="2007-01-01",
                   deletes=[("1999/175.txt", "1999:175", "2006:1")])
    order = [e.key for e in
             ordered_events({e.key: e for e in (change, repeal)})]
    assert order == ["SFS 2005:900", "SFS 2006:1"]


def _snapshot(tmp_path, name, text):
    """A beta-API-shaped snapshot file whose forfattningstext is `text`."""
    p = tmp_path / name
    p.write_text(json.dumps({"fulltext": {"forfattningstext": text}}),
                 encoding="utf-8")
    return p


def _body_sha(path):
    """The collect-time hash `Change.body_hash` always carries in production."""
    return hashlib.sha256(snapshot_text(path).encode()).hexdigest()


def _events(tmp_path):
    add = _snapshot(tmp_path, "add.json", "1 § Ursprunglig lydelse.")
    amended = _snapshot(tmp_path, "amended.json", "1 § Ändrad lydelse.")
    return {
        "SFS 1999:175": Event(
            key="SFS 1999:175", ikraft="1999-07-01",
            changes=[Change(path="1999/175.txt", src=add, basefile="1999:175",
                            title="Testlag (1999:175)", cutoff="1999:175",
                            add=True, body_hash=_body_sha(add))]),
        "SFS 2001:9": Event(
            key="SFS 2001:9", utfardad="2001-01-11", ikraft="2001-02-01",
            changes=[Change(path="1999/175.txt", src=amended,
                            basefile="1999:175", title="Testlag (1999:175)",
                            cutoff="2001:9", body_hash=_body_sha(amended))]),
        "SFS 2005:100": Event(
            key="SFS 2005:100", ikraft="2005-03-01",
            deletes=[("1999/175.txt", "1999:175", "2005:100")]),
    }


def test_stream_golden(tmp_path):
    """The exact fast-import stream for a three-event corpus: an add, an
    amendment, a repeal -- ordered by date, snapshot text inlined, byte counts
    right. Locks the emission format (rule:lock-in-with-fixture)."""
    got = b"".join(stream(_events(tmp_path), _meta)).decode()
    # `data N` counts utf-8 BYTES (å/ä/ö/§ are two each); the blank line after
    # each payload is fast-import's optional LF separator, not part of the data
    assert got == """\
commit refs/heads/main
author Regeringen <regeringen@lagen.nu> 930830400 +0000
committer Riksdagen <riksdagen@lagen.nu> 930830400 +0000
data 498
SFS 1999:175: Testlag (1999:175)

SFS 1999:175: Testlag (1999:175)

Författardatum är ikraftträdandedatum (utfärdandedatum saknas i registret).

Lagen-History-Format: 2
Lagen-Scope: full
Lagen-Event: SFS 1999:175
Lagen-Transition: {"basefile":"1999:175","body":"3ac7bd549a70b015cb19de21fb124eeb12339f5e2bc98e6fcfdcc4baef3ededc","cutoff":"1999:175","event":"SFS 1999:175","id":"write:1999:175@1999:175","metadata":"8a7677bb17b09ab9e4fb68ca59133909866ddaf4ee5579e59b669c27a2f24ca4","op":"write"}

M 644 inline 1999/175.txt
data 26
1 § Ursprunglig lydelse.

commit refs/heads/main
author Regeringen <regeringen@lagen.nu> 979214400 +0000
committer Riksdagen <riksdagen@lagen.nu> 981028800 +0000
data 436
SFS 2001:9: Testlag (1999:175)

SFS 1999:175: Testlag (1999:175) -- ändrad t.o.m. SFS 2001:9

Lagen-History-Format: 2
Lagen-Scope: full
Lagen-Event: SFS 2001:9
Lagen-Transition: {"basefile":"1999:175","body":"91b338fb696624b474f8a79b73cb529b5b668b7412a453310d9fe62e71890087","cutoff":"2001:9","event":"SFS 2001:9","id":"write:1999:175@2001:9","metadata":"64ca7aa14045074955960f85ac241bdd8a45134b21ad8384f0840da06c64556f","op":"write"}

M 644 inline 1999/175.txt
data 22
1 § Ändrad lydelse.

commit refs/heads/main
author Regeringen <regeringen@lagen.nu> 1109678400 +0000
committer Riksdagen <riksdagen@lagen.nu> 1109678400 +0000
data 440
SFS 2005:100: upphävande

SFS 1999:175: upphävd genom SFS 2005:100

Författardatum är ikraftträdandedatum (utfärdandedatum saknas i registret).

Lagen-History-Format: 2
Lagen-Scope: full
Lagen-Event: SFS 2005:100
Lagen-Transition: {"basefile":"1999:175","body":null,"cutoff":"2005:100","event":"SFS 2005:100","id":"delete:1999:175@2005:100","metadata":"4d4ef825ea307a82a4e0886ec3d7ce81ff76a2954bdaf3b294648e0e191ef3a6","op":"delete"}

D 1999/175.txt
"""


def test_stream_roundtrips_through_git_fast_import(tmp_path):
    """The stream is what git itself accepts: import it, and the history has
    the three events in order, the file exists after the amendment and is
    gone at the tip, and the trailers read back as the idempotency ledger."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", repo, "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", repo, "fast-import", "--quiet"],
                   input=b"".join(stream(_events(tmp_path), _meta)),
                   check=True, stdout=subprocess.DEVNULL)
    log = subprocess.run(["git", "-C", repo, "log", "--format=%ad %s",
                          "--date=short", "main"], check=True,
                         capture_output=True, text=True).stdout.splitlines()
    # git log shows the AUTHOR date: the amendment's utfärdandedatum
    # (2001-01-11), not its ikraftträdandedatum
    assert log == ["2005-03-01 SFS 2005:100: upphävande",
                   "2001-01-11 SFS 2001:9: Testlag (1999:175)",
                   "1999-07-01 SFS 1999:175: Testlag (1999:175)"]
    show = subprocess.run(["git", "-C", repo, "show", "main~1:1999/175.txt"],
                          check=True, capture_output=True, text=True).stdout
    assert show == "1 § Ändrad lydelse.\n"
    tip_tree = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "main"],
                              check=True, capture_output=True,
                              text=True).stdout
    assert tip_tree == ""                     # repealed: the file is deleted
    assert existing_ledger(repo)[1] == {"SFS 1999:175", "SFS 2001:9",
                                        "SFS 2005:100"}


def test_snapshot_text_normalizes_trailing_newline(tmp_path):
    p = _snapshot(tmp_path, "s.json", "1 § Text.\n\n")
    assert snapshot_text(p) == "1 § Text.\n"
    with pytest.raises(Exception, match="forfattningstext"):
        snapshot_text(_snapshot(tmp_path, "none.json", None))


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          text=True, capture_output=True).stdout.strip()


@pytest.fixture
def export_corpus(tmp_path, monkeypatch):
    """One isolated raw/artifact corpus for real two-run export tests."""
    downloaded, artifact = tmp_path / "downloaded", tmp_path / "artifact"
    monkeypatch.setattr(layout, "SFS_DOWNLOADED", downloaded)
    monkeypatch.setattr(layout, "ARTIFACT", artifact)
    monkeypatch.setattr(layout, "SFS_ARTIFACT", artifact / "sfs")
    return tmp_path


def _source(basefile, cutoff, text):
    return {"beteckning": basefile, "rubrik": "Testlag (%s)" % basefile,
            "fulltext": {"andringInford": "t.o.m. SFS %s" % cutoff,
                         "forfattningstext": text}}


def _write_current(basefile, cutoff, text):
    path = layout.sfs_source(basefile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_source(basefile, cutoff, text)), encoding="utf-8")


def _write_archive(basefile, cutoff, text):
    path = layout.sfs_archive_version_download(layout.SFS_DOWNLOADED,
                                               basefile, cutoff)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_source(basefile, cutoff, text)), encoding="utf-8")


def _write_artifact(basefile, *amendments):
    entries = []
    for cutoff, prop in amendments:
        entries.append({"properties": {"dcterms:identifier": "SFS " + cutoff,
                                        "rpubl:ikrafttradandedatum": "2020-01-01"},
                        "forarbeten": [prop] if prop else []})
    path = layout.artifact("sfs", basefile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"metadata": {"properties": {
                        "dcterms:title": "Testlag (%s)" % basefile}},
                        "amendments": entries}), encoding="utf-8")


def test_export_requires_every_selected_artifact(export_corpus):
    basefile, repo = "1999:175", export_corpus / "repo"
    _write_current(basefile, "2001:1", "1 § Text.")

    with pytest.raises(ValueError, match="parsed artifact"):
        export([basefile], repo, forarbete_meta=_meta)

    assert not repo.exists()


def test_export_rebuilds_same_cutoff_correction(export_corpus):
    basefile, repo = "1999:175", export_corpus / "repo"
    _write_current(basefile, "2001:1", "1 § Fel text.")
    _write_artifact(basefile, ("2001:1", "Prop. 2020/21:194"))
    assert export([basefile], repo, forarbete_meta=_meta) == 1

    _write_current(basefile, "2001:1", "1 § Rättad text.")
    with pytest.raises(RebuildRequired, match="changed"):
        export([basefile], repo, forarbete_meta=_meta)
    assert _git(repo, "show", "main:1999/175.txt") == "1 § Fel text."

    assert export([basefile], repo, forarbete_meta=_meta, rebuild=True) == 1
    assert _git(repo, "show", "main:1999/175.txt") == "1 § Rättad text."


def test_export_rebuilds_changed_proposition_attribution(export_corpus):
    basefile, repo = "1999:175", export_corpus / "repo"
    _write_current(basefile, "2001:1", "1 § Text.")
    _write_artifact(basefile, ("2001:1", None))
    export([basefile], repo, forarbete_meta=_meta)

    _write_artifact(basefile, ("2001:1", "Prop. 2020/21:194"))
    with pytest.raises(RebuildRequired, match="changed"):
        export([basefile], repo, forarbete_meta=_meta)

    export([basefile], repo, forarbete_meta=_meta, rebuild=True)
    assert existing_ledger(repo)[1] == {"Prop. 2020/21:194"}


def test_export_rebuilds_late_transition_joining_existing_event(export_corpus):
    basefile, repo = "1999:175", export_corpus / "repo"
    prop = "Prop. 2020/21:194"
    _write_current(basefile, "2001:1", "1 § Ursprunglig text.")
    _write_artifact(basefile, ("2001:1", prop))
    export([basefile], repo, forarbete_meta=_meta)

    _write_archive(basefile, "2001:1", "1 § Ursprunglig text.")
    _write_current(basefile, "2002:1", "1 § Senare text.")
    _write_artifact(basefile, ("2001:1", prop), ("2002:1", prop))
    with pytest.raises(RebuildRequired, match="joins already-committed"):
        export([basefile], repo, forarbete_meta=_meta)

    assert export([basefile], repo, forarbete_meta=_meta, rebuild=True) == 1
    assert _git(repo, "show", "main:1999/175.txt") == "1 § Senare text."


def test_export_rebuilds_historical_backfill_without_regressing_tip(export_corpus):
    basefile, repo = "1999:175", export_corpus / "repo"
    _write_current(basefile, "2003:1", "1 § Nyaste text.")
    _write_artifact(basefile, ("2003:1", "Prop. 2020/21:194"))
    export([basefile], repo, forarbete_meta=_meta)

    _write_archive(basefile, "2002:1", "1 § Mellanliggande text.")
    _write_artifact(basefile, ("2002:1", None),
                    ("2003:1", "Prop. 2020/21:194"))
    with pytest.raises(RebuildRequired, match="precedes an existing transition"):
        export([basefile], repo, forarbete_meta=_meta)

    assert export([basefile], repo, forarbete_meta=_meta, rebuild=True) == 2
    assert _git(repo, "show", "main:1999/175.txt") == "1 § Nyaste text."


def test_export_refuses_dirty_target_and_ignores_side_branch_tip(export_corpus):
    basefile, repo = "1999:175", export_corpus / "repo"
    _write_current(basefile, "2001:1", "1 § Ursprunglig text.")
    _write_artifact(basefile, ("2001:1", None))
    export([basefile], repo, forarbete_meta=_meta)
    old_main = _git(repo, "rev-parse", "main")

    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.org")
    _git(repo, "checkout", "-qb", "side")
    (repo / "side.txt").write_text("unrelated", encoding="utf-8")
    _git(repo, "add", "side.txt")
    _git(repo, "commit", "-qm", "unrelated")
    _git(repo, "checkout", "-q", "main")

    _write_archive(basefile, "2001:1", "1 § Ursprunglig text.")
    _write_current(basefile, "2002:1", "1 § Senare text.")
    _write_artifact(basefile, ("2001:1", None), ("2002:1", None))
    export([basefile], repo, forarbete_meta=_meta)
    assert _git(repo, "rev-parse", "main^") == old_main
    assert "side.txt" not in _git(repo, "ls-tree", "-r", "main")

    (repo / "1999" / "175.txt").write_text("lokal ändring", encoding="utf-8")
    with pytest.raises(ValueError, match="uncommitted changes"):
        export([basefile], repo, forarbete_meta=_meta)
    assert (repo / "1999" / "175.txt").read_text(encoding="utf-8") == "lokal ändring"


def test_export_migrates_legacy_event_only_ledger(export_corpus):
    """A repo written by the pre-transition-ledger exporter (Lagen-Event
    trailers only) must demand --rebuild-history, and the rebuild must leave a
    v2 transition ledger behind."""
    basefile, repo = "1999:175", export_corpus / "repo"
    _write_current(basefile, "2001:1", "1 § Text.")
    _write_artifact(basefile, ("2001:1", None))
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.org")
    (repo / "1999").mkdir()
    (repo / "1999" / "175.txt").write_text("1 § Text.\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm",
         "SFS 2001:1: Testlag (1999:175)\n\nLagen-Event: SFS 2001:1")

    with pytest.raises(RebuildRequired, match="legacy"):
        export([basefile], repo, forarbete_meta=_meta)

    assert export([basefile], repo, forarbete_meta=_meta, rebuild=True) == 1
    transitions, _events_seen, scopes = existing_ledger(repo)
    assert set(transitions) == {"write:1999:175@2001:1"}
    assert scopes == {"full"}


def test_export_refuses_foreign_repository(export_corpus):
    """A repo with history the exporter never wrote (no Lagen trailers at all)
    is not a target we may move refs in, rebuild or not."""
    basefile, repo = "1999:175", export_corpus / "repo"
    _write_current(basefile, "2001:1", "1 § Text.")
    _write_artifact(basefile, ("2001:1", None))
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.org")
    (repo / "unrelated.txt").write_text("egna filer", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "unrelated work")

    with pytest.raises(ValueError, match="not an export repository"):
        export([basefile], repo, forarbete_meta=_meta)
    with pytest.raises(ValueError, match="not an export repository"):
        export([basefile], repo, forarbete_meta=_meta, rebuild=True)


def test_export_rebuilds_on_scope_change(export_corpus):
    basefile, repo = "1999:175", export_corpus / "repo"
    _write_current(basefile, "2001:1", "1 § Text.")
    _write_artifact(basefile, ("2001:1", None))
    export([basefile], repo, forarbete_meta=_meta)          # scope "full"

    partial = scope_id([basefile], full=False)
    with pytest.raises(RebuildRequired, match="scope changed"):
        export([basefile], repo, forarbete_meta=_meta, scope=partial)

    assert export([basefile], repo, forarbete_meta=_meta, scope=partial,
                  rebuild=True) == 1
    assert existing_ledger(repo)[2] == {partial}


def test_export_refuses_bare_and_non_main_targets(export_corpus):
    basefile, repo = "1999:175", export_corpus / "repo"
    _write_current(basefile, "2001:1", "1 § Text.")
    _write_artifact(basefile, ("2001:1", None))
    export([basefile], repo, forarbete_meta=_meta)

    _git(repo, "checkout", "-qb", "side")
    with pytest.raises(ValueError, match="main checked out"):
        export([basefile], repo, forarbete_meta=_meta)
    _git(repo, "checkout", "-q", "main")

    _git(repo, "config", "core.bare", "true")
    with pytest.raises(ValueError, match="must have a worktree"):
        export([basefile], repo, forarbete_meta=_meta)
