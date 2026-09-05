"""Tests for the SFS history-as-git export (sfs/asgit.py): identity/date
derivation, commit-message composition, and a golden fast-import stream for a
small synthetic corpus (round-tripped through a real `git fast-import`)."""

import hashlib
import json
import subprocess

import pytest

from ferenda.lib import gitledger, layout
from ferenda.sfs.asgit import (
    Change,
    Event,
    RebuildRequired,
    _current_cutoff,
    collect,
    cycle_members,
    definite,
    email_slug,
    event_dates,
    existing_ledger,
    export,
    identities,
    is_lag,
    message,
    misfiled_as,
    ordered_events,
    refinable,
    resolve_order_conflicts,
    scope_id,
    snapshot_text,
    stream,
    subject,
    transition_records,
    ungroup,
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
    ev = Event(key="SFS 1962:700", titles={"1962:700": "Brottsbalk (1962:700)"})
    assert identities(ev, _meta) == (("Regeringen", "regeringen@lagen.nu"),
                                     ("Riksdagen", "riksdagen@lagen.nu"))
    # a förordning is the government's alone: it authors and commits
    ev = Event(key="SFS 2024:216", titles={
        "2020:486": "Förordning (2020:486) om miljö- och trafiksäkerhetskrav"})
    assert identities(ev, _meta) == (("Regeringen", "regeringen@lagen.nu"),
                                     ("Regeringen", "regeringen@lagen.nu"))
    # the grundlagar are riksdagen's whatever their title says
    ev = Event(key="SFS 2018:1801", titles={
        "1949:105": "Tryckfrihetsförordning (1949:105)"})
    assert identities(ev, _meta)[1] == ("Riksdagen", "riksdagen@lagen.nu")


def test_definite_title_and_lag_detection():
    assert definite("Lag (2022:1) om foo") == "lagen (2022:1) om foo"
    assert definite("Brottsbalk (1962:700)") == "brottsbalken (1962:700)"
    assert definite("Förordning (2020:486) om bilar") == "förordningen (2020:486) om bilar"
    assert definite("Kungörelse (1966:436) om x") == "kungörelsen (1966:436) om x"
    assert definite("Tillkännagivande (2023:1) av y") == "tillkännagivandet (2023:1) av y"
    assert definite("Tryckfrihetsförordning (1949:105)") == "tryckfrihetsförordningen (1949:105)"
    assert definite("Riksdagsordning (2014:801)") == "riksdagsordningen (2014:801)"
    assert definite("Skattebrottslag (1971:69)") == "skattebrottslagen (1971:69)"
    assert is_lag("Lag (2022:1) om foo") and is_lag("Brottsbalk (1962:700)")
    assert is_lag("Tryckfrihetsförordning (1949:105)")
    assert not is_lag("Förordning (2020:486) om bilar")
    assert not is_lag("Kungörelse (1966:436) om x")


def test_subject_names_the_act_then_the_proposition_as_far_as_it_fits():
    lag = Change(path="2018/585.txt", src=None, basefile="2018:585",
                 title="Säkerhetsskyddslag (2018:585)", cutoff="2021:952")
    ev = Event(key="Prop. 2020/21:194", prop="Prop. 2020/21:194", changes=[lag])
    assert subject(ev, {"title": "Ett starkare skydd"}) \
        == "ändring i säkerhetsskyddslagen (2018:585) (Ett starkare skydd)"
    assert subject(ev, {"title": "Ett starkare skydd för Sveriges säkerhet"}) \
        == "ändring i säkerhetsskyddslagen (2018:585) (Ett starkare skydd för…)"
    # a title that does not fit is cut at a word, never past column 72
    long = "Ett starkare skydd för Sveriges säkerhet och för allting annat också"
    s = subject(ev, {"title": long})
    assert s.endswith("…)") and len(s) <= 72
    # no room for anything meaningful: the act alone
    wide = Change(path="2018/585.txt", src=None, basefile="2018:585",
                  title="Lag (2018:585) om " + "x" * 60, cutoff="2021:952")
    ev2 = Event(key="Prop. 2020/21:194", prop="Prop. 2020/21:194", changes=[wide])
    assert subject(ev2, {"title": long}) == "ändring i lagen (2018:585) om " + "x" * 60
    # a new act is the main act of its event, and m.fl. counts the rest
    new = Change(path="2021/1.txt", src=None, basefile="2021:1",
                 title="Lag (2021:1) om ny sak", cutoff="2021:1", add=True)
    ev3 = Event(key="Prop. 2020/21:194", prop="Prop. 2020/21:194",
                changes=[lag, new], deletes=[("1996/627.txt", "1996:627", "2021:1")])
    assert subject(ev3, {"title": "Ny sak"}) == "Lag (2021:1) om ny sak m.fl. (Ny sak)"
    # without a proposition the amending act's own number takes the slot
    ev4 = Event(key="SFS 2021:952", changes=[lag])
    assert subject(ev4, None) == "ändring i säkerhetsskyddslagen (2018:585) (SFS 2021:952)"
    ev5 = Event(key="SFS 2021:1", deletes=[("1996/627.txt", "1996:627", "2021:1")],
                titles={"1996:627": "Säkerhetsskyddslag (1996:627)"})
    assert subject(ev5, None) == "upphävande av säkerhetsskyddslagen (1996:627) (SFS 2021:1)"


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
    assert lines[0] == ("ändring i säkerhetsskyddslagen (2018:585) m.fl. "
                        "(Ett starkare skydd…)")
    assert len(lines[0]) <= 72
    assert lines[1] == ""
    assert lines[2] == ("Prop. 2020/21:194: Ett starkare skydd för Sveriges "
                        "säkerhet")
    assert "föreslår regeringen ändringar" in msg          # the ingress body
    assert ("SFS 2018:585: Säkerhetsskyddslag (2018:585) -- ändrad t.o.m. "
            "SFS 2021:952") in msg
    assert "innefattar även SFS 2021:498" in msg           # archive-gap caveat
    assert "SFS 1998:204: upphävd genom SFS 2018:218" in msg
    assert "Författardatum är ikraftträdandedatum" in msg  # date substitution
    assert "Lagen-" not in msg      # the ledger lives in a sidecar file, not here
    assert ("Co-authored-by: Mikael Damberg <mikael.damberg@lagen.nu>"
            in msg)
    records = transition_records(ev, _meta)
    assert {r["id"] for r in records} == {
        "write:2018:585@2021:952", "delete:1998:204@2018:218"}


def test_message_dates_a_pre_1970_commit_explicitly():
    """The git ident date clamps to 1970-01-01 for a pre-1970 event (GitHub
    rejects a negative timestamp), so the true date must survive in the
    message -- one line when author and committer dates agree, two otherwise."""
    ev = Event(key="SFS 1686:0903", ikraft="1686-07-01",
               changes=[Change(path="1686/0903.txt", src=None,
                               basefile="1686:0903", title="Kyrkolag (1686:0903)",
                               cutoff="1686:0903", add=True, body_hash="0" * 64)])
    msg = message(ev, _meta)
    assert msg.endswith("saknas i registret).\n\nFörfattardatum: 1686-07-01\n")
    assert "Incheckningsdatum" not in msg
    ev = Event(key="SFS 1969:78", utfardad="1969-03-21", ikraft="1970-01-01")
    msg = message(ev, _meta)
    assert msg.endswith("\nFörfattardatum: 1969-03-21\n")
    assert "Incheckningsdatum" not in msg
    ev = Event(key="SFS 1969:78", utfardad="1969-03-21", ikraft="1969-07-01")
    msg = message(ev, _meta)
    assert msg.endswith("\nFörfattardatum: 1969-03-21\nIncheckningsdatum: 1969-07-01\n")
    # the stream itself never emits a negative ident timestamp
    ev = Event(key="SFS 1686:0903", ikraft="1686-07-01",
               titles={"1686:0903": "Kyrkolag (1686:0903)"})
    header = next(stream({ev.key: ev}, _meta))
    assert b"author Regeringen <regeringen@lagen.nu> 0 +0000\n" in header
    assert b"committer Riksdagen <riksdagen@lagen.nu> 0 +0000\n" in header


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


def test_message_marks_an_omtryck_and_the_act_a_new_one_replaces():
    # an omtryck reprints the whole act under its own unchanged SFS number, so
    # it renames no file -- it is named on the transition it falls on. A
    # replacement act's succession is stated for the same reason: git records
    # no renames, and a new act's text is too unlike the one it replaces for
    # rename detection to recover the move by similarity.
    ev = Event(key="Prop. 2025/26:141", prop="Prop. 2025/26:141",
               ikraft="2026-07-01",
               changes=[Change(path="1987/10.txt", src=None,
                               basefile="1987:10",
                               title="Plan- och bygglag (1987:10)",
                               cutoff="1992:1769", omtryck=True,
                               body_hash="0" * 64),
                        Change(path="2026/408.txt", src=None,
                               basefile="2026:408",
                               title="Vapenlag (2026:408)",
                               cutoff="2026:408", add=True,
                               body_hash="1" * 64)],
               deletes=[("1996/67.txt", "1996:67", "2026:408")])
    msg = message(ev, _meta)
    assert ("SFS 1987:10: Plan- och bygglag (1987:10) -- ändrad t.o.m. "
            "SFS 1992:1769, omtryckt") in msg
    assert "SFS 2026:408: Vapenlag (2026:408)" in msg
    assert "  ersätter SFS 1996:67" in msg
    assert "SFS 1996:67: upphävd genom SFS 2026:408" in msg


def _order_event(key, prop, *changes):
    return Event(key=key, prop=prop, ikraft="2020-01-01",
                 changes=[_change(path, cutoff) for path, cutoff in changes])


def test_conflicting_propositions_are_ungrouped_per_sfs_number():
    # prop A amends 1985:1100 before prop B does and 1994:741 after it, so
    # neither commit can precede the other. One commit per proposition cannot
    # express that; the per-SFS-number key -- the key an amendment with no
    # known proposition already takes -- can.
    a = _order_event("Prop. 2005/06:148", "Prop. 2005/06:148",
                     ("1985/1100.txt", "2006:1"), ("1994/741.txt", "2006:9"))
    b = _order_event("Prop. 2006/07:1", "Prop. 2006/07:1",
                     ("1985/1100.txt", "2006:5"), ("1994/741.txt", "2006:3"))
    events, gaps = {a.key: a, b.key: b}, []
    assert cycle_members([a, b]) == {0, 1}

    out = resolve_order_conflicts(events, {}, gaps)
    assert sorted(out) == ["SFS 2006:1", "SFS 2006:3",
                           "SFS 2006:5", "SFS 2006:9"]
    # the proposition is attribution, not grouping: each part keeps it
    assert out["SFS 2006:1"].prop == "Prop. 2005/06:148"
    assert [g["kind"] for g in gaps] == ["order", "order"]
    assert not cycle_members(list(out.values()))


def test_ungrouping_stops_at_a_single_amending_act():
    # `ungroup` keeps each part's proposition, so a "has no proposition" guard
    # never fires on a part and the loop re-ungroups it into the identical
    # single key forever. Refinability is about the grouping, not the
    # attribution: an event carrying one amending act is already as fine as it
    # gets, and a cycle of nothing but those has to raise.
    part = _order_event("SFS 2006:1", "Prop. 2005/06:148",
                        ("1985/1100.txt", "2006:1"))
    assert not refinable(part)
    assert refinable(_order_event("Prop. X", "Prop. X",
                                  ("a.txt", "2006:1"), ("b.txt", "2006:2")))
    assert list(ungroup(part, {})) == ["SFS 2006:1"]


def test_an_unresolvable_cycle_raises_rather_than_ungrouping_forever():
    # a repeal that outranks a later amendment of the same statute: neither
    # event groups more than one amending act, so there is nothing left to
    # refine and the corpus itself disagrees about the order. This is the
    # branch that makes the loop's termination argument sound.
    a = Event(key="SFS 2006:1", ikraft="2020-01-01",
              changes=[_change("1994/741.txt", "2006:1")],
              deletes=[("1985/1100.txt", "1985:1100", "2006:1")])
    b = Event(key="SFS 2006:5", ikraft="2020-01-01",
              changes=[_change("1985/1100.txt", "2006:5"),
                       _change("1994/741.txt", "2006:5")])
    assert not refinable(a) and not refinable(b)
    with pytest.raises(ValueError, match="cycle"):
        resolve_order_conflicts({a.key: a, b.key: b}, {}, [])


def test_an_unusable_archived_consolidation_is_a_gap_not_incompleteness(
        export_corpus):
    # the archive is already known to be incomplete, so a snapshot it cannot
    # read is a gap the export reports and works around; the amendments it
    # would have separated are named as folded in the next commit. Only an
    # unreadable *current* download refuses the corpus.
    basefile, repo = "1999:175", export_corpus / "repo"
    _write_current(basefile, "2003:1", "1 § Nu gällande lydelse.")
    _write_archive(basefile, "2001:1", "1 § Äldre lydelse.")
    # an archived snapshot holding another act's text
    path = layout.sfs_archive_version_download(layout.SFS_DOWNLOADED,
                                               basefile, "2002:1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_source("1998:204", "2002:1", "1 § Fel akt.")),
                    encoding="utf-8")
    _write_artifact(basefile, ("2001:1", None), ("2002:1", None),
                    ("2003:1", "Prop. 2020/21:194"))

    events, skipped, gaps = collect([basefile])
    assert skipped == []
    assert [(g["kind"], g["error"]) for g in gaps] == [
        ("archive", "archived consolidation holds SFS 1998:204")]
    # the dropped snapshot's amendment is named as folded, not lost
    assert any("2002:1" in change.folded
               for event in events.values() for change in event.changes)
    assert export([basefile], repo, forarbete_meta=_meta) == 2


def test_an_unreadable_current_download_still_refuses_the_corpus(export_corpus):
    basefile, repo = "1999:175", export_corpus / "repo"
    layout.sfs_source(basefile).parent.mkdir(parents=True, exist_ok=True)
    layout.sfs_source(basefile).write_text(
        json.dumps(_source(basefile, "2001:1", None)), encoding="utf-8")
    _write_artifact(basefile, ("2001:1", None))

    with pytest.raises(ValueError, match="current download"):
        export([basefile], repo, forarbete_meta=_meta)
    assert not repo.exists()


def test_current_cutoff_prefers_the_register_but_never_the_repealer(tmp_path):
    def cutoff(header, register, repealer=None, basefile="1966:436"):
        path = tmp_path / "436.json"
        path.write_text(json.dumps(_source(basefile, header, "1 § Text.",
                                           register)), encoding="utf-8")
        return _current_cutoff(path, basefile, repealer)

    # a stale (or mistyped) header loses to a newer register entry
    assert cutoff("2023:216", [("2023:69", "ändr. 13 §"),
                               ("2024:216", "ändr. 3 a §")]) == "2024:216"
    # the header wins when it is the newer of the two
    assert cutoff("2024:216", [("2023:69", "ändr. 13 §")]) == "2024:216"
    # a bare header on a repealed act: the newest amendment, never the
    # repealing act (matched by number -- its wording varies)
    register = [("1986:176", "ändr. 8, 11 §§"), ("1990:717", "utgår")]
    assert cutoff("1966:436", register, repealer="1990:717") == "1986:176"
    # a header that names the repealer names no cutoff at all
    assert cutoff("1990:717", register, repealer="1990:717") == "1986:176"
    # an ikraftträdandeförfattning changes no word; a withdrawn entry is gone
    assert cutoff("1991:854", [("1991:878", "ikrafttr. av 1991:854")]) \
        == "1991:854"
    assert cutoff("1991:854", [("1991:900", "ändr. 1 §", True)]) == "1991:854"
    # nothing usable in the register: the header (or the act itself) stands
    assert cutoff("1990:717", [("1990:717", "upph.")], repealer="1990:717") \
        == "1966:436"


def test_a_repealed_act_whose_header_names_the_repealer_keeps_its_text(
        export_corpus):
    # 57 repealed acts name their own repeal as "t.o.m." cutoff. Read
    # literally, the file's only write and its deletion share one commit and
    # the wording never enters any tree.
    basefile, repo = "1966:436", export_corpus / "repo"
    _write_current(basefile, "1990:717", "8 § Lydelse vid upphävandet.",
                   register=[("1986:176", "ändr. 8, 11 §§"),
                             ("1990:717", "upph.")])
    _write_artifact(basefile, ("1986:176", None), repealed_by="1990:717")

    assert export([basefile], repo, forarbete_meta=_meta) == 2
    subjects = _git(repo, "log", "--reverse", "--format=%s",
                    gitledger.BRANCH).splitlines()
    assert subjects == ["Testlag (1966:436) (SFS 1986:176)",
                        "upphävande av testlagen (1966:436) (SFS 1990:717)"]
    assert _git(repo, "show", gitledger.BRANCH + "~1:1966/436.txt") \
        == "8 § Lydelse vid upphävandet."
    assert "1966/436.txt" not in _git(repo, "ls-tree", "-r", "--name-only",
                                      gitledger.BRANCH)


def test_a_consolidation_cut_off_at_the_repeal_is_a_gap_not_a_cycle(
        export_corpus):
    # 2022:1464: repealed by 2023:657, whose transitional provisions 2025:1236
    # later amended, so the current text postdates the repeal. Read literally
    # the archived "t.o.m. 2023:657" wording puts the repealing act's commit
    # both before 2025:1236's (a change) and after it (the deletion).
    basefile, repo = "2022:1464", export_corpus / "repo"
    _write_archive(basefile, "2023:584", "1 § Äldre lydelse.")
    _write_archive(basefile, "2023:657", "1 § Lydelse vid upphävandet.")
    _write_current(basefile, "2025:1236", "1 § Lydelse med ny p 3.",
                   register=[("2023:584", "ändr. 1 §"), ("2023:657", "upph."),
                             ("2025:1236", "ny p 3 övergångsbest.")])
    _write_artifact(basefile, ("2023:584", None), ("2023:657", None),
                    ("2025:1236", None), repealed_by="2023:657")

    events, skipped, gaps = collect([basefile])
    assert skipped == []
    assert [g["error"] for g in gaps] == [
        "archived consolidation is cut off at the repealing act SFS 2023:657"]
    assert export([basefile], repo, forarbete_meta=_meta) == 3
    subjects = _git(repo, "log", "--reverse", "--format=%s",
                    gitledger.BRANCH).splitlines()
    assert subjects == ["Testlag (2022:1464) (SFS 2023:584)",
                        "ändring i testlagen (2022:1464) (SFS 2025:1236)",
                        "upphävande av testlagen (2022:1464) (SFS 2023:657)"]
    assert _git(repo, "show", gitledger.BRANCH + "~1:2022/1464.txt") \
        == "1 § Lydelse med ny p 3."
    # the dropped consolidation's amendment is named as folded, not lost
    assert "innefattar även SFS 2023:657" in _git(
        repo, "log", "-1", "--format=%b", gitledger.BRANCH + "~1")


def test_misfiled_archive_snapshot_is_read_off_its_own_rubrik():
    # 20 archived consolidations hold another act's text, in one shifted chain
    # an old import left behind. Nothing but the snapshot's own Rubrik says so.
    assert misfiled_as({"Rubrik": "Förordning (1998:1473) om "
                        "miljöskadeförsäkring"}, "1982:798") == "1998:1473"
    assert misfiled_as({"Rubrik": "Förordning (1982:798) om kompensation"},
                       "1982:798") is None
    assert misfiled_as({}, "1982:798") is None


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
            titles={"1999:175": "Testlag (1999:175)"},
            changes=[Change(path="1999/175.txt", src=add, basefile="1999:175",
                            title="Testlag (1999:175)", cutoff="1999:175",
                            add=True, body_hash=_body_sha(add))]),
        "SFS 2001:9": Event(
            key="SFS 2001:9", utfardad="2001-01-11", ikraft="2001-02-01",
            titles={"1999:175": "Testlag (1999:175)"},
            changes=[Change(path="1999/175.txt", src=amended,
                            basefile="1999:175", title="Testlag (1999:175)",
                            cutoff="2001:9", body_hash=_body_sha(amended))]),
        "SFS 2005:100": Event(
            key="SFS 2005:100", ikraft="2005-03-01",
            titles={"1999:175": "Testlag (1999:175)"},
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
data 134
Testlag (1999:175)

SFS 1999:175: Testlag (1999:175)

Författardatum är ikraftträdandedatum (utfärdandedatum saknas i registret).

M 644 inline 1999/175.txt
data 26
1 § Ursprunglig lydelse.

commit refs/heads/main
author Regeringen <regeringen@lagen.nu> 979214400 +0000
committer Riksdagen <riksdagen@lagen.nu> 981028800 +0000
data 108
ändring i testlagen (1999:175) (SFS 2001:9)

SFS 1999:175: Testlag (1999:175) -- ändrad t.o.m. SFS 2001:9

M 644 inline 1999/175.txt
data 22
1 § Ändrad lydelse.

commit refs/heads/main
author Regeringen <regeringen@lagen.nu> 1109678400 +0000
committer Riksdagen <riksdagen@lagen.nu> 1109678400 +0000
data 175
upphävande av testlagen (1999:175) (SFS 2005:100)

SFS 1999:175: upphävd genom SFS 2005:100

Författardatum är ikraftträdandedatum (utfärdandedatum saknas i registret).

D 1999/175.txt
"""


def test_stream_roundtrips_through_git_fast_import(tmp_path):
    """The stream is what git itself accepts: import it, and the history has
    the three events in order, the file exists after the amendment and is
    gone at the tip."""
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
    assert log == ["2005-03-01 upphävande av testlagen (1999:175) (SFS 2005:100)",
                   "2001-01-11 ändring i testlagen (1999:175) (SFS 2001:9)",
                   "1999-07-01 Testlag (1999:175)"]
    show = subprocess.run(["git", "-C", repo, "show", "main~1:1999/175.txt"],
                          check=True, capture_output=True, text=True).stdout
    assert show == "1 § Ändrad lydelse.\n"
    tip_tree = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "main"],
                              check=True, capture_output=True,
                              text=True).stdout
    assert tip_tree == ""                     # repealed: the file is deleted


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


def _source(basefile, cutoff, text, register=()):
    """A beta-API download; `register` is its andringsforfattningar list as
    (beteckning, anteckningar) pairs, or (beteckning, anteckningar, borttagen)."""
    return {"beteckning": basefile, "rubrik": "Testlag (%s)" % basefile,
            "fulltext": {"andringInford": "t.o.m. SFS %s" % cutoff,
                         "forfattningstext": text},
            "andringsforfattningar": [
                {"beteckning": e[0], "anteckningar": e[1],
                 "borttagen": e[2] if len(e) > 2 else False}
                for e in register]}


def _write_current(basefile, cutoff, text, register=()):
    path = layout.sfs_source(basefile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_source(basefile, cutoff, text, register)),
                    encoding="utf-8")


def _write_archive(basefile, cutoff, text):
    path = layout.sfs_archive_version_download(layout.SFS_DOWNLOADED,
                                               basefile, cutoff)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_source(basefile, cutoff, text)), encoding="utf-8")


def _write_artifact(basefile, *amendments, repealed_by=None):
    entries = []
    for cutoff, prop in amendments:
        entries.append({"properties": {"dcterms:identifier": "SFS " + cutoff,
                                        "rpubl:ikrafttradandedatum": "2020-01-01"},
                        "forarbeten": [prop] if prop else []})
    props = {"dcterms:title": "Testlag (%s)" % basefile}
    if repealed_by:
        props["rinfoex:upphavdAv"] = "SFS " + repealed_by
        props["rpubl:upphavandedatum"] = "2021-01-01"
    path = layout.artifact("sfs", basefile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"metadata": {"properties": props},
                                "amendments": entries}), encoding="utf-8")


def test_export_passes_over_an_empty_artifact_placeholder(export_corpus):
    # a zero-byte artifact is the parse's SkipDocument placeholder: the act is
    # in the register but carries no forfattningstext (repealed long ago, or
    # withdrawn before entering force). Reading it as JSON aborted the whole
    # export with a JSONDecodeError; it has no body to export and is not an
    # incomplete input either, so the statute is simply not in the repository.
    empty, live, repo = "1942:937", "1999:175", export_corpus / "repo"
    _write_current(empty, "1942:937", "1 § Text.")
    path = layout.artifact("sfs", empty)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    _write_current(live, "2001:1", "1 § Text.")
    _write_artifact(live, ("2001:1", "Prop. 2020/21:194"))

    assert export([empty, live], repo, forarbete_meta=_meta) == 1
    assert _git(repo, "ls-files") == "1999/175.txt"


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
    assert {r["event"] for r in existing_ledger(repo)[0].values()} == {
        "Prop. 2020/21:194"}


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


def test_export_appends_a_new_event_and_ledger_file_stays_invisible(export_corpus):
    """The normal, non-rebuild path: a genuinely new, later amendment is
    just appended (one more commit, not a rewrite of the first), and the
    ledger the append decision reads back lives at gitledger.path -- a file
    inside .git/ untouched by git add/commit, invisible in the worktree."""
    basefile, repo = "1999:175", export_corpus / "repo"
    _write_current(basefile, "2001:1", "1 § Ursprunglig text.")
    _write_artifact(basefile, ("2001:1", "Prop. 2020/21:194"))
    assert export([basefile], repo, forarbete_meta=_meta) == 1
    first_tip = _git(repo, "rev-parse", "main")

    ledger_path = gitledger.path(repo)
    assert ledger_path.exists()
    assert ledger_path.is_relative_to(repo / ".git")
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "ls-files") == "1999/175.txt"

    _write_archive(basefile, "2001:1", "1 § Ursprunglig text.")
    _write_current(basefile, "2002:1", "1 § Senare text.")
    _write_artifact(basefile, ("2001:1", "Prop. 2020/21:194"),
                    ("2002:1", "Prop. 2021/22:1"))
    assert export([basefile], repo, forarbete_meta=_meta) == 1
    assert _git(repo, "rev-parse", "main^") == first_tip
    assert _git(repo, "show", "main:1999/175.txt") == "1 § Senare text."
    transitions, scope = existing_ledger(repo)
    assert {r["event"] for r in transitions.values()} == {
        "Prop. 2020/21:194", "Prop. 2021/22:1"}
    assert scope == "full"


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
    transitions, scope = existing_ledger(repo)
    assert set(transitions) == {"write:1999:175@2001:1"}
    assert scope == "full"


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
    assert existing_ledger(repo)[1] == partial


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
