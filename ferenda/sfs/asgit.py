"""Export the SFS corpus as a git repository -- `lagen sfs history-as-git`.

One file per statute (`1998/204.txt`, the plaintext body the parser consumes),
one commit per amendment *event*: when one proposition amends several statutes,
all those file changes land in a single commit keyed by the proposition id in
the cutoff amendments' förarbeten. Author is the proposition's first signer
(the co-signers become Co-authored-by trailers), committer the
riksdagsskrivelse's first signer (the talman); both identities come through
the `forarbete_meta` callable build.py composes in (reading a sibling
vertical's artifacts is build's job, like ai-correspond), with the
`Regeringen`/`Riksdagen <...@lagen.nu>` fallbacks when the förarbete is not in
the corpus. E-mail addresses are name slugs on the clearly-non-real lagen.nu
domain, never real-looking government addresses.

Granularity is bounded by the download archive: a commit reflects the delta
between two *available* consolidations, attributed to the newer snapshot's
cutoff amendment, with any amendments folded in between named in the message
body. Author date is the amendment's utfärdandedatum where the register knows
it (it rarely does); the marked fallback is ikraftträdandedatum -- the
committer date -- and, lacking both, July 1 of the amendment's SFS year.
Repeals (`rinfoex:upphavdAv`) delete the file, folded into the repealing
act's own event when that act is in the run.

Emission is one `git fast-import` stream (tens of thousands of commits in
minutes; one `git commit` per event would take days). Every change also carries
a machine-readable `Lagen-Transition:` trailer with its immutable transition
identity, plaintext hash and metadata hash. A re-run appends only a strict
extension of that ledger. Corrections, backfilled snapshots, changed
attribution and partial proposition events require an explicit rebuild, which
recreates `main` atomically from a complete corpus. Snapshot text is extracted
twice -- once at collect time (validating and hashing it) and once lazily at
emit time (the whole corpus never sits in memory at once).
"""

import collections
import hashlib
import heapq
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from ..lib import compress, git, gitledger, layout
from ..lib.errors import RebuildRequired, SkipDocument
from . import register as register_mod
from .extract import extract_body, sniff_encoding
from .versions import archival_header, header_cutoff

BRANCH = gitledger.BRANCH
BRANCH_REF = gitledger.BRANCH_REF
STAGING_REF = "refs/lagen/history-as-git-staging"
FORMAT = "4"
# format 3 and earlier kept the ledger as `Lagen-Event:`/`Lagen-Transition:`
# commit-message trailers; format 4 moved it to `gitledger`'s sidecar file
# (`.git/lagen-ledger.json`), invisible to a plain `git log` the way
# Staffan asked ("not normally shown to a user, like an X- header in
# mime/http"). RE_EVENT still finds an old-format repo's trailers -- not to
# rebuild the ledger from them, only to tell "legacy, needs
# --rebuild-history" apart from "not an export repo at all".
RE_EVENT = re.compile(r"^Lagen-Event: (.+)$", re.MULTILINE)
RE_SFS_NR = re.compile(r"(\d+:\d+)")


@dataclass
class Change:
    """One statute's file modification within an event: replace `path` with
    the text of the consolidation at `src` (extracted lazily at emit time)."""
    path: str            # repo-relative, e.g. "1998/204.txt"
    src: Path            # the snapshot file (download JSON or SFST HTML)
    basefile: str
    title: str
    cutoff: str          # the transition's cutoff amendment ("2008:187")
    folded: list[str] = field(default_factory=list)  # amendments in between
    add: bool = False    # first known consolidation of the statute
    omtryck: bool = False  # this cutoff reprinted the whole act (rinfoex:omtryck)
    body_hash: str | None = None


@dataclass
class Event:
    """One commit: every change (and repeal deletion) attributed to the same
    proposition -- or, when no proposition is known, to one cutoff SFS nr."""
    key: str             # "Prop. 2020/21:194" or "SFS 2021:952"
    prop: str | None = None    # the "Prop. ..." identifier, when known
    rskr: str | None = None    # the "Rskr. ..." identifier, when known
    utfardad: str | None = None
    ikraft: str | None = None
    changes: list[Change] = field(default_factory=list)
    # (path, basefile, repealed_by)
    deletes: list[tuple[str, str, str]] = field(default_factory=list)
    # basefile -> the act's title, for every change and deletion
    titles: dict[str, str] = field(default_factory=dict)

    @property
    def lag(self):
        """Whether riksdagen enacted any act this event touches. A förordning,
        kungörelse or tillkännagivande is the government's alone, so nobody
        but its author stands behind that commit."""
        return any(is_lag(t) for t in self.titles.values())

    def merge_dates(self, utfardad, ikraft):
        """Keep the earliest known date of each kind -- deterministic when an
        omnibus proposition's amendments carry slightly different dates."""
        for attr, val in (("utfardad", utfardad), ("ikraft", ikraft)):
            cur = getattr(self, attr)
            if val and (cur is None or val < cur):
                setattr(self, attr, val)


# the head noun of an act's title in the definite form the subject line
# needs: "Lag (2022:1) om foo" -> "lagen (2022:1) om foo". Suffix-matched,
# longest first; a head no rule names takes the common -en/-n.
_DEFINITE = [
    ("tillkännagivande", "tillkännagivandet"), ("föreskrifter", "föreskrifterna"),
    ("förordning", "förordningen"), ("kungörelse", "kungörelsen"),
    ("instruktion", "instruktionen"), ("resolution", "resolutionen"),
    ("föreskrift", "föreskriften"), ("reglemente", "reglementet"),
    ("skrivelse", "skrivelsen"), ("cirkulär", "cirkuläret"),
    ("ordning", "ordningen"), ("stadgar", "stadgarna"), ("beslut", "beslutet"),
    ("stadga", "stadgan"), ("brev", "brevet"), ("form", "formen"),
    ("balk", "balken"), ("lag", "lagen"),
]
# the grundlagar and riksdagsordningen are riksdagen's although no title says "lag"
_RIKSDAG_HEADS = {"regeringsform", "riksdagsordning", "successionsordning",
                  "tryckfrihetsförordning"}


def _title_head(title):
    """The head noun of a title: the last word before the SFS number."""
    words = title.partition(" (")[0].split()
    return words[-1].lower() if words else ""


def is_lag(title):
    head = _title_head(title)
    return (head.endswith(("lag", "balk")) or head in _RIKSDAG_HEADS
            or RE_BESLUTAD_GRUNDLAG.search(title) is not None)


# "Kungörelse (1974:152) om beslutad ny regeringsform": a grundlag under a
# kungörelse title
RE_BESLUTAD_GRUNDLAG = re.compile(
    r"om beslutad ny (regeringsform|riksdagsordning|successionsordning|"
    r"tryckfrihetsförordning)")


def definite(title):
    """'Lag (2022:1) om foo' -> 'lagen (2022:1) om foo', 'Brottsbalk
    (1962:700)' -> 'brottsbalken (1962:700)'."""
    head, sep, rest = title.partition(" (")
    words = head.split()
    if not words:
        return title
    noun = words[-1].lower()
    for suffix, form in _DEFINITE:
        if noun.endswith(suffix):
            noun = noun[:len(noun) - len(suffix)] + form
            break
    else:
        noun += "n" if noun.endswith(("a", "e")) else "en"
    words[-1] = noun
    words[0] = words[0][:1].lower() + words[0][1:]
    return " ".join(words) + sep + rest


def snapshot_text(path):
    """The plaintext body of one downloaded consolidation -- the same text the
    parser consumes: `fulltext.forfattningstext` from the beta JSON,
    `extract_body` from the two legacy HTML generations."""
    if path.suffix == ".json":
        text = compress.read_json(path)["fulltext"]["forfattningstext"]
        if text is None:
            raise SkipDocument("no forfattningstext")
    else:
        text = extract_body(path)
    return text.rstrip("\n") + "\n"


def snapshot_header(path):
    """The SFST header of one downloaded consolidation, across the three raw
    generations the archive holds (beta JSON, utf-8 HTML, latin-1 HTML)."""
    if path.suffix == ".json":
        return register_mod.sfst_header_from_source(compress.read_json(path))
    if sniff_encoding(compress.read_bytes(path)) == "latin-1":
        return archival_header(path)
    return register_mod.parse_sfst_header(path)


def snapshot_cutoff(path, basefile):
    """The consolidation cutoff ("t.o.m. SFS ...") the snapshot itself names,
    or the basefile for an un-amended act."""
    return header_cutoff(snapshot_header(path)) or basefile


def _current_cutoff(path, basefile, repealer=None):
    """The current download's true cutoff.

    The header's own "Ändring införd" text is maintained by hand and is
    sometimes wrong -- a typo (2002:986's header names "20103:54", a
    five-digit year) or simply stale (2020:486's header still names
    2023:216 while its body already carries 2024:216's wording, confirmed
    directly against beta.rkrattsbaser.gov.se) -- and for most repealed
    acts it names no cutoff at all although the body is consolidated
    (1966:436's body carries 1986:176's wording under a bare header). The
    register's own `andringsforfattningar` list is the authoritative
    amendment chain (see sfs.source's cover-consolidation-gap), so prefer
    its newest usable entry over the header text whenever that entry is
    newer.

    Two kinds of entry consolidate nothing and are never a cutoff: the
    repealing act itself (`repealer`, the artifact's rinfoex:upphavdAv --
    5,684 register entries, spelled "upph.", "utgår", "uppgh." and worse, so
    matched by number, not wording), and an ikraftträdandeförfattning
    ("ikrafttr."), which brings an amendment into force without changing a
    word. A header that names the repealer as cutoff (57 repealed acts) is
    read as naming none: the wording at repeal is the last amendment's, and
    a cutoff equal to the repealer would put the file's only write and its
    deletion in one commit, so the text never entered any tree."""
    header_based = snapshot_cutoff(path, basefile)
    if repealer and header_based == repealer:
        header_based = basefile
    if path.suffix != ".json":
        return header_based
    source = compress.read_json(path)
    plausible = [act.get("beteckning", "") for act in
                 source.get("andringsforfattningar") or []
                 if RE_PLAUSIBLE_CUTOFF.match(act.get("beteckning", ""))
                 and act.get("beteckning") != repealer
                 and not act.get("borttagen")
                 and not (act.get("anteckningar") or "").lstrip().lower()
                 .startswith("ikrafttr")]
    if not plausible:
        return header_based
    newest = max(plausible, key=layout.sfs_version_key)
    return (newest if layout.sfs_version_key(newest)
            > layout.sfs_version_key(header_based) else header_based)


# the SFS number a snapshot's own Rubrik names -- "Förordning (1982:798) om
# kompensation i vissa fall" -- which says which act the file actually holds
RE_RUBRIK_SFS = re.compile(r"\((\d{4}:\s?\d+)\)")
# an SFS number's year has four digits. "20120:354" is the source's own typo
# for 2020:354 (1991:1128, which also holds the correctly keyed file), and
# sorting it as a year 20120 put it after every real consolidation.
RE_PLAUSIBLE_CUTOFF = re.compile(r"^\d{4}:\d+$")


def misfiled_as(header, basefile):
    """The SFS number this snapshot really holds, when that is not `basefile` --
    else None.

    Twenty archived consolidations hold a *different act's* text, in one shifted
    chain an old archive import left behind: 1982:787's newest archive is
    2008:313, 2008:313's is 1982:789, and so on down to 1998:1473's, which is
    1982:801. Nothing but the snapshot's own Rubrik says so, and exporting one
    would write another statute's wording into this statute's file."""
    m = RE_RUBRIK_SFS.search(header.get("Rubrik") or "")
    named = m.group(1).replace(" ", "") if m else None
    return named if named and named != basefile else None


def statute_snapshots(basefile, skipped, gaps, repealed=False, repealer=None):
    """Every usable consolidation of one statute, oldest first: the download
    archive plus the current download, each as ``(cutoff, path,
    plaintext_hash)``. The current download wins over an archive of the same
    cutoff: it is the source the downloader has just corrected.

    Two kinds of bad input are told apart, because they need different answers.

    An unusable *archived* consolidation is a **gap** (`gaps`): the archive is
    already known to be incomplete, and a transition that folds several
    amendments is the ordinary case the commit message names. It is recorded
    and dropped, the same answer the versions stage gives a corrupt archive file.
    Three shapes turn up: junk the old downloader saved instead of the document
    (a rkrattsbaser search-results page, a FELMEDDELANDE page), a snapshot whose
    own Rubrik names another act (`misfiled_as`), and a cutoff whose year is not
    a year (`RE_PLAUSIBLE_CUTOFF`).

    An unusable *current* download is **incompleteness** (`skipped`): the act
    has no text at all, so `export` refuses to write history rather than
    committing a hole that a later repair would have to append at the tip.

    Cutoff order is likewise required only of a live act. A repealed act's
    current page serves the wording as it stood at repeal and stops naming a
    cutoff -- the newest consolidation is then the last archived one, and
    demanding that the current file be newest rejected ten repealed acts.
    `repealer` (the repealing act's SFS number) is never a cutoff: see
    `_current_cutoff`."""
    current = layout.sfs_source(basefile)
    if not compress.exists(current):
        current = layout.sfs_sfst(basefile)
    archive = layout.sfs_version_downloads(basefile)
    snapshots = {}
    for _, path in archive:
        try:
            header = snapshot_header(path)
            other = misfiled_as(header, basefile)
            if other:
                gaps.append({"kind": "archive", "basefile": basefile,
                             "file": str(path),
                             "error": "archived consolidation holds SFS "
                                      + other})
                continue
            cutoff = header_cutoff(header) or basefile
            if not RE_PLAUSIBLE_CUTOFF.match(cutoff):
                gaps.append({"kind": "archive", "basefile": basefile,
                             "file": str(path),
                             "error": "archived cutoff %s is not an SFS number"
                                      % cutoff})
                continue
            if repealer and cutoff == repealer:
                # the wording "t.o.m." the repealing act (49 archived
                # consolidations) would share the deletion's commit, so it
                # can never enter a tree -- and when a later amendment to the
                # repeal's transitional provisions follows it (2022:1464),
                # the repealing act's commit would have to come both before
                # and after that amendment's
                gaps.append({"kind": "archive", "basefile": basefile,
                             "file": str(path),
                             "error": "archived consolidation is cut off at "
                                      "the repealing act SFS %s" % cutoff})
                continue
            text = snapshot_text(path)
        except SkipDocument as exc:
            gaps.append({"kind": "archive", "basefile": basefile,
                         "file": str(path), "error": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 — per-snapshot resilience point, mirroring the versions stage's sidecar hook: a corrupt decades-old archive file becomes a recorded gap, not an aborted corpus export (rule:no-catch-log-continue)
            gaps.append({"kind": "archive", "basefile": basefile,
                         "file": str(path),
                         "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        snapshots.setdefault(cutoff, (path, _hash(text)))
    try:
        cutoff = _current_cutoff(current, basefile, repealer)
        text = snapshot_text(current)
    except SkipDocument as exc:
        skipped.append({"basefile": basefile, "file": str(current),
                        "error": str(exc)})
        return []
    except Exception as exc:  # noqa: BLE001 — per-snapshot boundary: record all malformed snapshots before export rejects the incomplete history (rule:no-catch-log-continue)
        skipped.append({"basefile": basefile, "file": str(current),
                        "error": "%s: %s" % (type(exc).__name__, exc)})
        return []
    snapshots[cutoff] = (current, _hash(text))
    ordered = sorted(snapshots.items(), key=lambda cp: layout.sfs_version_key(cp[0]))
    if not repealed and ordered and ordered[-1][0] != cutoff:
        skipped.append({"basefile": basefile, "file": str(current),
                        "error": "current cutoff %s predates archived cutoff %s"
                                 % (cutoff, ordered[-1][0])})
    return [(version, path, body_hash)
            for version, (path, body_hash) in ordered]


def _amendment_index(art):
    """The artifact's amendments keyed by SFS nr: {nr: (utfärdandedatum,
    ikraftträdandedatum, prop identifier, rskr identifier)}."""
    index = {}
    for a in art["amendments"]:
        props = a["properties"]
        m = RE_SFS_NR.search(props.get("dcterms:identifier", ""))
        if not m:
            continue
        fa = a.get("forarbeten", [])
        index[m.group(1)] = (
            props.get("rpubl:utfardandedatum"),
            props.get("rpubl:ikrafttradandedatum"),
            next((f for f in fa if f.startswith("Prop.")), None),
            next((f for f in fa if f.startswith("Rskr.")), None))
    return index


def collect(basefiles):
    """All events across `basefiles`, keyed by proposition (else cutoff SFS
    nr), plus the two record lists `statute_snapshots` separates: `skipped`,
    the incomplete inputs `export` refuses to write history from, and `gaps`,
    the unusable archived consolidations it reports and exports around. Keeping
    both here lets one preflight report every problem at once."""
    events, skipped, gaps, repeals = {}, [], [], []
    # global nr -> (utfärdad, ikraft, prop identifier, rskr identifier)
    amendment_meta: dict[str, tuple[str | None, str | None,
                                    str | None, str | None]] = {}
    for basefile in basefiles:
        art_path = layout.artifact("sfs", basefile)
        if not compress.exists(art_path):
            skipped.append({"basefile": basefile, "error": "no parsed artifact"})
            continue
        # an empty artifact is the parse's SkipDocument placeholder: the act
        # stands in the register but carries no forfattningstext -- repealed
        # long ago, or published and withdrawn before it entered force (19
        # acts, 1942:937 to 2023:592). There is no body to put in a file, so
        # the statute stays out of the export altogether; that is a
        # deliberately empty document, not an incomplete input, so it is not a
        # skip record either.
        art = compress.read_json(art_path, empty=None)
        if art is None:
            continue
        index = _amendment_index(art)
        for nr, meta in index.items():
            amendment_meta.setdefault(nr, meta)
        meta_props = art["metadata"]["properties"]
        repealed = "rinfoex:upphavdAv" in meta_props
        m = RE_SFS_NR.search(meta_props["rinfoex:upphavdAv"]) if repealed else None
        repealer = m.group(1) if m else None
        # the amending act whose text was printed as a reprint of the whole
        # statute. The base act keeps its number, so this renames no file --
        # it marks the one transition that restated the act rather than
        # amending it. 47 of the corpus's 445 omtryck fall on a consolidation
        # the archive holds; the rest predate it.
        omtryck = RE_SFS_NR.search(meta_props.get("rinfoex:omtryck", ""))
        omtryck = omtryck.group(1) if omtryck else None
        title = meta_props.get("dcterms:title", "")
        # append, never with_suffix: "1827/60_s.1007" would lose its ".1007"
        rel = layout.relpath("sfs", basefile)
        path = str(rel.parent / (rel.name + ".txt"))
        prev = None
        for cutoff, src, body_hash in statute_snapshots(basefile, skipped, gaps,
                                                        repealed, repealer):
            utf, ikraft, prop, rskr = index.get(cutoff, (None, None, None, None))
            key = prop or ("SFS " + cutoff)
            ev = events.setdefault(key, Event(key=key, prop=prop, rskr=rskr))
            ev.merge_dates(utf, ikraft)
            folded = ([nr for nr in index
                       if layout.sfs_version_key(prev)
                       < layout.sfs_version_key(nr)
                       < layout.sfs_version_key(cutoff)]
                      if prev is not None else [])
            ev.changes.append(Change(path=path, src=src, basefile=basefile,
                                     title=title, cutoff=cutoff, folded=folded,
                                     add=prev is None, omtryck=cutoff == omtryck,
                                     body_hash=body_hash))
            ev.titles[basefile] = title
            prev = cutoff
        if repealer:
            repeals.append((path, basefile, title, repealer,
                            meta_props.get("rpubl:upphavandedatum")))
    # repeals resolve against the *global* amendment index, so the deletion
    # joins the repealing act's own event whenever that act is in the run
    for path, basefile, title, repealer, upphavd in repeals:
        utf, ikraft, prop, rskr = amendment_meta.get(
            repealer, (None, upphavd, None, None))
        key = prop or ("SFS " + repealer)
        ev = events.setdefault(key, Event(key=key, prop=prop, rskr=rskr))
        ev.merge_dates(utf, ikraft or upphavd)
        ev.deletes.append((path, basefile, repealer))
        ev.titles[basefile] = title
    return resolve_order_conflicts(events, amendment_meta, gaps), skipped, gaps


def cycle_members(evs):
    """The events *on* a precedence cycle: every member of a strongly connected
    component of more than one node.

    Tarjan rather than "what Kahn's algorithm left over", which also returns
    everything merely downstream of a cycle -- that read 2 093 propositions as
    conflicting where 52 are. Iterative, because the corpus has tens of
    thousands of events and the recursion would not fit the stack."""
    successors, _ = order_graph(evs)
    index: list[int | None] = [None] * len(evs)
    low = [0] * len(evs)
    on_stack = [False] * len(evs)
    stack: list[int] = []
    counter, out = 0, set()
    for root in range(len(evs)):
        if index[root] is not None:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True
        work = [(root, iter(sorted(successors[root])))]
        while work:
            v, pending = work[-1]
            for w in pending:
                if index[w] is None:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack[w] = True
                    work.append((w, iter(sorted(successors[w]))))
                    break
                if on_stack[w]:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    component = []
                    while True:
                        w = stack.pop()
                        on_stack[w] = False
                        component.append(w)
                        if w == v:
                            break
                    if len(component) > 1:
                        out.update(component)
    return out


def refinable(ev):
    """Whether ungrouping this event can still change anything: it groups more
    than one amending act.

    A part `ungroup` has already minted carries exactly one, so ungrouping it
    again returns the same single key and the same graph. Testing "has no
    proposition" instead sent the loop round forever, because `ungroup` keeps
    each part's proposition -- it is the attribution, not the grouping."""
    return len({change.cutoff for change in ev.changes}
               | {delete[2] for delete in ev.deletes}) > 1


def ungroup(ev, amendment_meta):
    """`ev` split into one event per amending SFS number -- the key an event
    already takes where no proposition is known -- keeping each part's own
    proposition attribution and dates."""
    parts: dict[str, Event] = {}

    def part(nr):
        utf, ikraft, prop, rskr = amendment_meta.get(
            nr, (ev.utfardad, ev.ikraft, ev.prop, ev.rskr))
        key = "SFS " + nr
        out = parts.setdefault(key, Event(key=key, prop=prop, rskr=rskr))
        out.merge_dates(utf, ikraft)
        return out

    for c in ev.changes:
        out = part(c.cutoff)
        out.changes.append(c)
        out.titles[c.basefile] = ev.titles.get(c.basefile, c.title)
    for delete in ev.deletes:
        out = part(delete[2])
        out.deletes.append(delete)
        if delete[1] in ev.titles:
            out.titles[delete[1]] = ev.titles[delete[1]]
    return parts


def resolve_order_conflicts(events, amendment_meta, gaps):
    """Ungroup the propositions whose commit cannot hold one position in every
    statute's timeline, and record why.

    Grouping by proposition assumes a proposition's amendments land in the same
    relative order in every statute they touch. Twenty-six pairs in the corpus
    break that: prop. 2005/06:148 amends 1985:1100 before prop. 2006/07:1 does
    and 1994:741 after it, so neither commit can precede the other; and a single
    proposition can produce two amending acts to the *same* statute (prop.
    2007/08:13 and prop. 2007/08:21 interleave twice in 1997:483). One commit
    per proposition cannot express either, so the conflicting propositions fall
    back to the per-SFS-number key -- the same key an amendment with no known
    proposition already takes. The commit still names the proposition and is
    still authored by its signers; it is simply not merged with that
    proposition's other statutes.

    Ungrouping strictly refines the order (each part carries one cutoff, which
    every statute's timeline agrees on), so the loop terminates -- but a part
    may merge into an existing per-SFS event and pull that one into a new
    conflict, which is why it repeats until the graph is acyclic. `refinable`
    is what makes that termination real: an event already down to one amending
    act is left alone, and a cycle of nothing but those is raised rather than
    ungrouped forever."""
    while (stuck := cycle_members(list(events.values()))):
        # a snapshot taken before this round mutates `events`: the indices
        # `cycle_members` returned are into it. A part that merges into an
        # event this round already visited reaches the live object, not this
        # copy, because `ungroup`'s parts are merged in rather than replacing.
        by_index = list(events.items())
        if not any(refinable(by_index[i][1]) for i in stuck):
            # every event on the cycle already carries a single amending act,
            # so there is nothing left to refine: the corpus itself disagrees
            # about a statute's order and the export must not paper over it
            raise ValueError("conflicting per-statute event order (cycle) "
                             "among: " + ", ".join(sorted(by_index[i][0]
                                                          for i in stuck)))
        for i in sorted(stuck):
            key, ev = by_index[i]
            if not refinable(ev):
                continue
            del events[key]
            gaps.append({"kind": "order", "basefile": ev.prop or key,
                         "error": "amends %d statute(s) in an order no single "
                                  "commit can hold; ungrouped per SFS number"
                                  % (len(ev.changes) + len(ev.deletes))})
            for part_key, part in ungroup(ev, amendment_meta).items():
                if part_key in events:
                    into = events[part_key]
                    into.changes.extend(part.changes)
                    into.deletes.extend(part.deletes)
                    into.titles.update(part.titles)
                    into.merge_dates(part.utfardad, part.ikraft)
                else:
                    events[part_key] = part
    return events


def email_slug(name):
    """A synthesized address on the clearly-non-real lagen.nu domain:
    "Stefan Löfven" -> "stefan.lofven@lagen.nu"."""
    ascii_name = unicodedata.normalize("NFKD", name).encode(
        "ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", ".", ascii_name.lower()).strip(".") + "@lagen.nu"


def event_dates(event):
    """(author_date, committer_date, substituted): utfärdandedatum ->
    ikraftträdandedatum -> July 1 of the event's SFS year, per the fallback
    chain; `substituted` says the author date is not a real utfärdandedatum
    (noted in the message body)."""
    year = re.search(r"(\d{4})", event.key)
    synthetic = "%s-07-01" % (year.group(1) if year else "1900")
    author = event.utfardad or event.ikraft or synthetic
    committer = event.ikraft or author
    return author, committer, event.utfardad is None


def _hash(value):
    """A stable SHA-256 over a plaintext string or canonical JSON value."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def _body_hash(change):
    """The collect-time plaintext hash -- never recomputed from `src`, so the
    emit-time comparison in `stream` actually detects a snapshot that changed
    on disk between collect and emit."""
    assert change.body_hash, "collect hashes every snapshot it admits"
    return change.body_hash


def _event_metadata(event, forarbete_meta):
    """Every mutable input that changes an event's rendered Git metadata."""
    return {
        "key": event.key,
        "prop": event.prop,
        "rskr": event.rskr,
        "utfardad": event.utfardad,
        "ikraft": event.ikraft,
        "prop_meta": forarbete_meta(event.prop) if event.prop else None,
        "rskr_meta": forarbete_meta(event.rskr) if event.rskr else None,
    }


def transition_records(event, forarbete_meta):
    """The event's immutable per-file ledger records.

    The proposition is presentation and grouping metadata, not the ledger key:
    a late statute joining an already-recorded proposition must be detectable
    rather than silently filtered out on the next run.
    """
    event_metadata = _event_metadata(event, forarbete_meta)
    records = []
    for change in sorted(event.changes, key=lambda c: c.path):
        metadata = {"event": event_metadata, "title": change.title,
                    "folded": change.folded, "add": change.add,
                    "omtryck": change.omtryck,
                    "replaces": replaced_by(event, change)}
        records.append({
            "id": "write:%s@%s" % (change.basefile, change.cutoff),
            "basefile": change.basefile,
            "cutoff": change.cutoff,
            "op": "write",
            "event": event.key,
            "body": _body_hash(change),
            "metadata": _hash(metadata),
        })
    for _path, basefile, repealer in sorted(event.deletes):
        records.append({
            "id": "delete:%s@%s" % (basefile, repealer),
            "basefile": basefile,
            "cutoff": repealer,
            "op": "delete",
            "event": event.key,
            "body": None,
            "metadata": _hash({"event": event_metadata}),
        })
    return records


def event_records(events, forarbete_meta):
    """All ledger records, indexed by transition identity."""
    records = {}
    for event in events.values():
        for record in transition_records(event, forarbete_meta):
            if record["id"] in records:
                raise ValueError("duplicate transition %s" % record["id"])
            records[record["id"]] = record
    return records


def scope_id(basefiles, *, full):
    """A history repo cannot silently change between full and partial scope."""
    if full:
        return "full"
    return "partial:" + _hash("\x1e".join(sorted(basefiles)))


def replaced_by(event, change):
    """The statutes this event repeals that name `change`'s act as the
    repealer -- the acts it succeeds.

    Git records no renames: a commit stores a tree, and `git log --follow`
    recovers a move by comparing content at read time. A replacement act is
    newly written text, so the similarity never reaches even `-M40%` (the new
    vapenlag, SFS 2026:408, against the 1996:67 it replaces). The succession is
    therefore stated in the message rather than expressed as a rename. It is
    known for 1 685 of the corpus's 5 887 repeals -- the ones whose successor
    also enters the corpus in the same event."""
    return sorted(basefile for _path, basefile, repealer in event.deletes
                  if repealer == change.basefile)


SUBJECT_MAX = 72


def subject(event, prop_meta):
    """The subject line: what happened to the act the commit is about --
    "ändring i lagen (2022:1) om foo", the title itself for a new act,
    "upphävande av ..." for a repeal -- "m.fl." when the event touches more
    acts, then in parentheses the proposition's title as far as it fits in
    `SUBJECT_MAX` columns (the full "Prop. ...: title" follows on the next
    line of the message), or the amending act's own SFS number when no
    proposition is known. A new act is the event's main act when it has one:
    a proposition that enacts a law and amends others is about the new law;
    otherwise a lag before a förordning (an event holding both is riksdagen's
    commit, and should read as one)."""
    changes = sorted(event.changes,
                     key=lambda c: (not c.add, not is_lag(c.title), c.path))
    deletes = sorted(event.deletes)
    if changes:
        c = changes[0]
        head = c.title if c.add else "ändring i " + definite(c.title)
    elif deletes:
        _path, basefile, _repealer = deletes[0]
        title = event.titles.get(basefile)
        head = "upphävande av " + (definite(title) if title
                                   else "SFS " + basefile)
    else:
        return event.key
    acts = {c.basefile for c in changes} | {d[1] for d in deletes}
    if len(acts) > 1:
        head += " m.fl."
    if event.prop:
        title = prop_meta.get("title") if prop_meta else None
    elif changes and changes[0].add and event.key == "SFS " + changes[0].basefile:
        title = None            # the act's own number is already in its title
    else:
        title = event.key       # the amending or repealing act
    if not title:
        return head
    room = SUBJECT_MAX - len(head) - 3
    if room < 12:
        return head
    if len(title) > room:
        title = title[:room - 1].rsplit(" ", 1)[0].rstrip(",.;:–-") + "…"
    return "%s (%s)" % (head, title)


def message(event, forarbete_meta, scope="full"):
    """The commit message: the `subject` line, the proposition (identifier
    and full title) on the next line, its own summary paragraph as body, the
    affected statutes listed, the granularity and date caveats spelled out,
    and the co-authors last."""
    prop_meta = forarbete_meta(event.prop) if event.prop else None
    lines = [subject(event, prop_meta)]
    if event.prop:
        lines += ["", ("%s: %s" % (event.prop, prop_meta["title"])
                       if prop_meta and prop_meta.get("title") else event.prop)]
    if prop_meta and prop_meta.get("ingress"):
        lines += ["", prop_meta["ingress"]]
    body = []
    for c in sorted(event.changes, key=lambda c: c.path):
        if c.add and c.cutoff != c.basefile:
            line = ("SFS %s: %s -- första kända konsolidering (i lydelse "
                    "enligt SFS %s), inte den ursprungliga lydelsen"
                    % (c.basefile, c.title, c.cutoff))
        elif c.add:
            line = "SFS %s: %s" % (c.basefile, c.title)
        else:
            line = ("SFS %s: %s -- ändrad t.o.m. SFS %s"
                    % (c.basefile, c.title, c.cutoff))
        body.append((line + ", omtryckt") if c.omtryck else line)
        if replaced := replaced_by(event, c):
            body.append("  ersätter SFS %s" % ", ".join(replaced))
        if c.folded:
            body.append("  innefattar även SFS %s (mellanliggande ändringar "
                        "utan arkiverad konsolidering)" % ", ".join(c.folded))
    for _path, basefile, repealer in sorted(event.deletes):
        body.append("SFS %s: upphävd genom SFS %s" % (basefile, repealer))
    if body:
        lines += [""] + body
    author_date, committer_date, substituted = event_dates(event)
    if substituted:
        lines += ["", "Författardatum är ikraftträdandedatum (utfärdandedatum "
                      "saknas i registret)."]
    # the git ident date clamps to 1970-01-01 for a pre-1970 event (GitHub's
    # receive-side fsck rejects a negative timestamp), so the true date must
    # survive in the message
    pre_epoch = [("Författardatum", author_date), ("Incheckningsdatum", committer_date)]
    pre_epoch = [(label, d) for label, d in pre_epoch if d < "1970-01-01"]
    if len(pre_epoch) == 2 and pre_epoch[0][1] == pre_epoch[1][1]:
        pre_epoch = pre_epoch[:1]
    if pre_epoch:
        lines += [""] + ["%s: %s" % (label, d) for label, d in pre_epoch]
    coauthors = ["Co-authored-by: %s <%s>" % (name, email_slug(name))
                for name in (prop_meta.get("signers", [])[1:] if prop_meta else [])]
    if coauthors:
        lines += [""] + coauthors
    return "\n".join(lines) + "\n"




def identities(event, forarbete_meta):
    """((author name, email), (committer name, email)) -- the proposition's
    first signer and the riksdagsskrivelse's first signer, with the corpus
    fallbacks when either förarbete is unavailable. An event with no lag
    among its acts is the government's alone, whatever proposition it
    follows on: author and committer are the same."""
    author = ("Regeringen", "regeringen@lagen.nu")
    committer = ("Riksdagen", "riksdagen@lagen.nu")
    prop_meta = forarbete_meta(event.prop) if event.prop else None
    if prop_meta and prop_meta.get("signers"):
        name = prop_meta["signers"][0]
        author = (name, email_slug(name))
    if not event.lag:
        # förordningar only: the government issues them alone, and commits
        # them, whatever proposition they follow on
        return author, author
    rskr_meta = forarbete_meta(event.rskr) if event.rskr else None
    if rskr_meta and rskr_meta.get("signers"):
        name = rskr_meta["signers"][0]
        committer = (name, email_slug(name))
    return author, committer


def order_graph(evs):
    """The per-statute precedence over `evs`: `(successors, indegree)`, an edge
    from each of a statute file's transitions to the next in cutoff order, and
    from its last transition to its deletion."""
    per_path: dict[str, list[tuple[tuple, int, int]]] = {}
    for i, ev in enumerate(evs):
        for c in ev.changes:
            per_path.setdefault(c.path, []).append(
                (layout.sfs_version_key(c.cutoff), 0, i))
        for path, _, repealer in ev.deletes:
            per_path.setdefault(path, []).append(
                (layout.sfs_version_key(repealer), 1, i))
    successors: list[set[int]] = [set() for _ in evs]
    indegree = [0] * len(evs)
    for entries in per_path.values():
        # deletes sort after every change (the 1 flag), changes by cutoff
        entries.sort(key=lambda e: (e[1], e[0]))
        for (_, _, a), (_, _, b) in zip(entries, entries[1:], strict=False):
            if a != b and b not in successors[a]:
                successors[a].add(b)
                indegree[b] += 1
    return successors, indegree


def ordered_events(events):
    """The emission order: globally by (author date, key), constrained so each
    statute's consolidations emit oldest-cutoff-first and its repeal last.
    The dates alone cannot carry this -- they come from a lossy fallback chain
    (utfärdad -> ikraft -> synthetic July 1) and ikraft is not monotonic in
    SFS-nr order (delayed entry into force is common) -- and a date inversion
    would silently overwrite a newer consolidation with older text, or
    resurrect a repealed statute at the tip. Kahn's algorithm over the
    per-statute precedence edges, ties broken by (date, key) so the global
    chronology holds wherever the constraints allow; a precedence cycle
    (conflicting orders through two statutes' shared events) is a data
    conflict the export must not paper over, raised as ValueError."""
    evs = list(events.values())
    successors, indegree = order_graph(evs)
    ready = [(event_dates(ev)[0], ev.key, i)
             for i, ev in enumerate(evs) if indegree[i] == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        _, _, i = heapq.heappop(ready)
        ordered.append(evs[i])
        for j in successors[i]:
            indegree[j] -= 1
            if indegree[j] == 0:
                heapq.heappush(ready, (event_dates(evs[j])[0], evs[j].key, j))
    if len(ordered) != len(evs):
        stuck = sorted(ev.key for i, ev in enumerate(evs) if indegree[i] > 0)
        raise ValueError("conflicting per-statute event order (cycle) among: "
                         + ", ".join(stuck))
    return ordered


def stream(events, forarbete_meta, tip=None, scope="full", ref=BRANCH_REF):
    """The fast-import byte stream for the events, in `ordered_events` order --
    a generator of chunks, so the whole corpus never sits in memory. `tip`
    chains the first commit onto an existing branch head."""
    ordered = ordered_events(events)
    first = True
    for ev in ordered:
        author_date, committer_date, _ = event_dates(ev)
        (a_name, a_mail), (c_name, c_mail) = identities(ev, forarbete_meta)
        yield ("commit %s\n"
               "author %s <%s> %s\n"
               "committer %s <%s> %s\n"
               % (ref, a_name, a_mail,
                  gitledger.epoch(author_date, clamp=True),
                  c_name, c_mail,
                  gitledger.epoch(committer_date, clamp=True))).encode()
        yield gitledger.data_payload(message(ev, forarbete_meta, scope))
        if first and tip:
            yield b"from %s\n" % tip.encode()
        first = False
        for c in sorted(ev.changes, key=lambda c: c.path):
            text = snapshot_text(c.src)
            if _hash(text) != _body_hash(c):
                raise RuntimeError("snapshot changed during history export: %s"
                                   % c.src)
            yield b"M 644 inline %s\n" % c.path.encode()
            yield gitledger.data_payload(text)
        for path, _, _ in sorted(ev.deletes):
            yield b"D %s\n" % path.encode()


def existing_ledger(repodir):
    """`(transitions, scope)` from the ledger file (`gitledger`), or
    `({}, None)` for a fresh repo, or one predating this format (format < 4
    -- `legacy_ledger` is the caller's way to tell those two apart).

    A malformed ledger is corruption, not an excuse to guess which source
    transition it meant."""
    ledger = gitledger.read(repodir)
    if ledger is None or ledger.get("format") != FORMAT:
        return {}, None
    if set(ledger) != {"format", "scope", "transitions"} \
            or not isinstance(ledger["transitions"], dict):
        raise ValueError("invalid ledger file: %s" % gitledger.path(repodir))
    records = ledger["transitions"]
    for record in records.values():
        if (not isinstance(record, dict)
                or set(record) != {"id", "basefile", "cutoff", "op", "event", "body",
                           "metadata"}
                or record["op"] not in ("write", "delete")):
            raise ValueError("invalid ledger transition: %s" % record)
        if (not all(isinstance(record[key], str)
                    for key in ("id", "basefile", "cutoff", "op", "event", "metadata"))
                or record["body"] is not None and not isinstance(record["body"], str)):
            raise ValueError("invalid ledger transition: %s" % record)
    return records, ledger["scope"]


def legacy_ledger(repodir):
    """Whether `main` carries the pre-ledger-file (format <= 3)
    `Lagen-Event:` commit-message trailers -- only to tell "needs
    --rebuild-history" apart from "not an export repository at all" when
    `existing_ledger` finds nothing."""
    messages = git.run(repodir, "log", "--format=%B", BRANCH_REF, capture=True)
    return bool(RE_EVENT.findall(messages))


def _transition_order(record):
    return (record["op"] == "delete",
            layout.sfs_version_key(record["cutoff"]))


def _append_reasons(existing, desired):
    """Why `desired` is not a strict append-only extension of `existing`."""
    reasons = []
    for ident, old in existing.items():
        new = desired.get(ident)
        if new is None:
            reasons.append("%s is absent from the current corpus" % ident)
        elif new != old:
            reasons.append("%s changed" % ident)
    existing_events = {record["event"] for record in existing.values()}
    by_basefile = {}
    for record in existing.values():
        by_basefile.setdefault(record["basefile"], []).append(record)
    for ident, record in desired.items():
        if ident in existing:
            continue
        if record["event"] in existing_events:
            reasons.append("%s joins already-committed %s" %
                           (ident, record["event"]))
        if any(_transition_order(old) >= _transition_order(record)
               for old in by_basefile.get(record["basefile"], [])):
            reasons.append("%s precedes an existing transition for %s" %
                           (ident, record["basefile"]))
    return reasons


def _require_complete(basefiles, events, skipped, gaps, log):
    for gap in gaps:
        log("  asgit %s: %s %s (%s)"
            % (gap["basefile"], gap["kind"], gap.get("file", ""), gap["error"]))
    kinds = collections.Counter(gap["kind"] for gap in gaps)
    if kinds["archive"]:
        log("  asgit: %d unusable archived consolidation(s) dropped; the "
            "amendments they would have separated are named as folded in the "
            "next commit" % kinds["archive"])
    if kinds["order"]:
        log("  asgit: %d proposition(s) ungrouped per SFS number (their "
            "amendments land in conflicting orders across statutes)"
            % kinds["order"])
    for skip in skipped:
        log("  asgit %s: incomplete %s (%s)"
            % (skip["basefile"], skip.get("file", ""), skip["error"]))
    # collect is the one owner of incompleteness: a missing artifact and an
    # unreadable *current* download both arrive as skip records. An unusable
    # archived consolidation does not -- it is a gap, reported above.
    missing = sum(1 for skip in skipped
                  if skip["error"] == "no parsed artifact")
    bad = len(skipped) - missing
    if skipped:
        details = ["%d parsed artifact(s) missing" % missing if missing else "",
                   "%d current download(s) unreadable or inconsistent" % bad
                   if bad else ""]
        raise ValueError("history-as-git needs a complete corpus (%s)" %
                         "; ".join(part for part in details if part))
    if basefiles and not events:
        raise ValueError("history-as-git complete corpus produced no events")


def _cached_meta(forarbete_meta):
    """Keep one export's signatures stable while avoiding repeated artifact I/O."""
    cache = {}

    def lookup(identifier):
        if identifier not in cache:
            cache[identifier] = forarbete_meta(identifier)
        return cache[identifier]

    return lookup


def _publish(repodir, events, forarbete_meta, scope, old_tip, parent, *,
            reclaim=False):
    """Import to a staging ref, then atomically move `main` on success
    (`gitledger.publish`). Does not touch the ledger file -- the caller
    writes that only once this has actually succeeded (see `gitledger`'s
    module docstring for why the order matters)."""
    if not events:
        return 0
    gitledger.publish(
        repodir, stream(events, forarbete_meta, tip=parent, scope=scope,
                        ref=STAGING_REF),
        branch_ref=BRANCH_REF, staging_branch_ref=STAGING_REF, old_tip=old_tip,
        reclaim=reclaim)
    return len(events)


def export(basefiles, repodir, *, forarbete_meta, scope="full", rebuild=False,
           log=print):
    """Build or safely update a history repository from a complete corpus;
    returns the number of commits written.

    Normal runs only append unseen, later transitions belonging to wholly new
    events. `rebuild=True` is the explicit, atomic answer to corrected text,
    backfills, attribution changes and legacy event-only repositories.
    """
    events, skipped, gaps = collect(basefiles)
    _require_complete(basefiles, events, skipped, gaps, log)
    forarbete_meta = _cached_meta(forarbete_meta)
    desired = event_records(events, forarbete_meta)
    tip = gitledger.prepare_repo(repodir, BRANCH)
    existing, existing_scope = existing_ledger(repodir)
    if tip and not existing:
        if legacy_ledger(repodir):
            if not rebuild:
                raise RebuildRequired(
                    "history-as-git ledger is legacy; rerun with --rebuild-history")
        else:
            raise ValueError("history-as-git target is not an export repository")
    if existing and existing_scope != scope and not rebuild:
        raise RebuildRequired("history-as-git scope changed; rerun with "
                              "--rebuild-history")
    if rebuild:
        commits = _publish(repodir, events, forarbete_meta, scope, tip, None,
                           reclaim=True)
        if commits:
            gitledger.write(repodir, {"format": FORMAT, "scope": scope,
                                      "transitions": desired})
        return commits
    reasons = _append_reasons(existing, desired)
    if reasons:
        raise RebuildRequired("history-as-git requires rebuild: %s; rerun with "
                              "--rebuild-history" % "; ".join(reasons[:5]))
    existing_event_keys = {record["event"] for record in existing.values()}
    fresh = {key: event for key, event in events.items()
             if key not in existing_event_keys}
    commits = _publish(repodir, fresh, forarbete_meta, scope, tip, tip)
    if commits:
        # `desired` already includes every unchanged existing record plus
        # the fresh ones just published (that is what `_append_reasons`
        # finding no reasons means), so it is the correct new ledger as-is
        gitledger.write(repodir, {"format": FORMAT, "scope": scope,
                                  "transitions": desired})
    return commits
