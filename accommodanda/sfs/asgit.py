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
minutes; one `git commit` per event would take days). Every commit carries a
`Lagen-Event:` trailer; a re-run reads the trailers off the existing history
and appends only the events not yet present, so the command is idempotent and
a post-harvest run adds just the new history. The ledger is event-level: an
event already committed is never revisited, so a statute added to the run
later gets its history appended as of *then* (its older events were not part
of the committed ones) -- build the repo from the full corpus, or accept that
per-run chronology only holds within a run. Snapshot text is extracted
twice -- once at collect time (validating the snapshot, so a corrupt
decades-old archive file becomes a recorded skip, not a mid-stream abort) and
once lazily at emit time (the whole corpus never sits in memory at once).
"""

import heapq
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..lib import compress, git, layout
from ..lib.errors import SkipDocument
from . import register as register_mod
from .extract import extract_body, sniff_encoding
from .versions import archival_header, header_cutoff

BRANCH = "main"
RE_TRAILER = re.compile(r"^Lagen-Event: (.+)$", re.MULTILINE)
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

    def merge_dates(self, utfardad, ikraft):
        """Keep the earliest known date of each kind -- deterministic when an
        omnibus proposition's amendments carry slightly different dates."""
        for attr, val in (("utfardad", utfardad), ("ikraft", ikraft)):
            cur = getattr(self, attr)
            if val and (cur is None or val < cur):
                setattr(self, attr, val)


def snapshot_text(path):
    """The plaintext body of one downloaded consolidation -- the same text the
    parser consumes: `fulltext.forfattningstext` from the beta JSON,
    `extract_body` from the two legacy HTML generations."""
    if path.suffix == ".json":
        text = json.loads(compress.read_text(path))["fulltext"]["forfattningstext"]
        if text is None:
            raise SkipDocument("no forfattningstext")
    else:
        text = extract_body(path)
    return text.rstrip("\n") + "\n"


def snapshot_cutoff(path, basefile):
    """The consolidation cutoff ("t.o.m. SFS ...") the snapshot itself names,
    or the basefile for an un-amended act."""
    if path.suffix == ".json":
        header = register_mod.sfst_header_from_source(
            json.loads(compress.read_text(path)))
    elif sniff_encoding(compress.read_bytes(path)) == "latin-1":
        header = archival_header(path)
    else:
        header = register_mod.parse_sfst_header(path)
    return header_cutoff(header) or basefile


def statute_snapshots(basefile, skipped):
    """Every available consolidation of one statute, oldest first: the
    download archive plus the current download, each as (cutoff, path),
    deduplicated on the recovered cutoff. A snapshot that fails extraction is
    recorded in `skipped` and excluded -- mirroring the versions stage's
    per-version resilience -- so it can never abort the fast-import stream."""
    current = layout.sfs_source(basefile)
    if not compress.exists(current):
        current = layout.sfs_sfst(basefile)
    # explicitly-keyed archives first, so a counter-keyed duplicate of the
    # same consolidation loses to the authoritative key (as in versions.build)
    files = sorted(layout.sfs_version_downloads(basefile),
                   key=lambda vp: (":" not in vp[0], vp[0]))
    files = [path for _, path in files] + [current]
    snapshots, seen = [], set()
    for path in files:
        try:
            cutoff = snapshot_cutoff(path, basefile)
            snapshot_text(path)          # validate now, extract again at emit
        except SkipDocument as exc:
            skipped.append({"basefile": basefile, "file": str(path),
                            "error": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 — per-snapshot resilience point, mirroring versions.build's: a corrupt decades-old archive file becomes a recorded skip, not an aborted corpus export (rule:no-catch-log-continue)
            skipped.append({"basefile": basefile, "file": str(path),
                            "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        if cutoff in seen:
            continue
        seen.add(cutoff)
        snapshots.append((cutoff, path))
    snapshots.sort(key=lambda cp: layout.sfs_version_key(cp[0]))
    return snapshots


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
    nr), plus the skip records. Statutes without a parsed artifact are skipped
    and logged -- the export mirrors what the corpus knows, and the artifact is
    the source of truth for amendment metadata."""
    events, skipped, repeals = {}, [], []
    # global nr -> (utfärdad, ikraft, prop identifier, rskr identifier)
    amendment_meta: dict[str, tuple[str | None, str | None,
                                    str | None, str | None]] = {}
    for basefile in basefiles:
        art_path = layout.artifact("sfs", basefile)
        if not compress.exists(art_path):
            skipped.append({"basefile": basefile, "error": "no parsed artifact"})
            continue
        art = json.loads(compress.read_bytes(art_path))
        index = _amendment_index(art)
        for nr, meta in index.items():
            amendment_meta.setdefault(nr, meta)
        meta_props = art["metadata"]["properties"]
        title = meta_props.get("dcterms:title", "")
        # append, never with_suffix: "1827/60_s.1007" would lose its ".1007"
        rel = layout.relpath("sfs", basefile)
        path = str(rel.parent / (rel.name + ".txt"))
        prev = None
        for cutoff, src in statute_snapshots(basefile, skipped):
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
                                     add=prev is None))
            prev = cutoff
        if "rinfoex:upphavdAv" in meta_props:
            m = RE_SFS_NR.search(meta_props["rinfoex:upphavdAv"])
            if m:
                repeals.append((path, basefile, title, m.group(1),
                                meta_props.get("rpubl:upphavandedatum")))
    # repeals resolve against the *global* amendment index, so the deletion
    # joins the repealing act's own event whenever that act is in the run
    for path, basefile, _title, repealer, upphavd in repeals:
        utf, ikraft, prop, rskr = amendment_meta.get(
            repealer, (None, upphavd, None, None))
        key = prop or ("SFS " + repealer)
        ev = events.setdefault(key, Event(key=key, prop=prop, rskr=rskr))
        ev.merge_dates(utf, ikraft or upphavd)
        ev.deletes.append((path, basefile, repealer))
    return events, skipped


def email_slug(name):
    """A synthesized address on the clearly-non-real lagen.nu domain:
    "Stefan Löfven" -> "stefan.lofven@lagen.nu"."""
    ascii_name = unicodedata.normalize("NFKD", name).encode(
        "ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", ".", ascii_name.lower()).strip(".") + "@lagen.nu"


def _epoch(date):
    """A date-only string as a fast-import timestamp (noon UTC -- the sources
    carry no time of day)."""
    d = datetime.fromisoformat(date).replace(hour=12, tzinfo=timezone.utc)
    return "%d +0000" % int(d.timestamp())


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


def message(event, forarbete_meta):
    """The commit message: the proposition's own summary paragraph as body
    (its title as subject), the affected statutes listed, the granularity and
    date caveats spelled out, and the idempotency trailer last."""
    prop_meta = forarbete_meta(event.prop) if event.prop else None
    if prop_meta and prop_meta.get("title"):
        subject = "%s: %s" % (event.prop, prop_meta["title"])
    elif event.prop:
        subject = "%s: ändringar i %d författning%s" % (
            event.prop, len(event.changes) + len(event.deletes),
            "" if len(event.changes) + len(event.deletes) == 1 else "ar")
    else:
        subject = "%s: %s" % (event.key,
                              event.changes[0].title if event.changes
                              else "upphävande")
    lines = [subject]
    if prop_meta and prop_meta.get("ingress"):
        lines += ["", prop_meta["ingress"]]
    body = []
    for c in sorted(event.changes, key=lambda c: c.path):
        if c.add and c.cutoff != c.basefile:
            body.append("SFS %s: %s -- första kända konsolidering (i lydelse "
                        "enligt SFS %s), inte den ursprungliga lydelsen"
                        % (c.basefile, c.title, c.cutoff))
        elif c.add:
            body.append("SFS %s: %s" % (c.basefile, c.title))
        else:
            body.append("SFS %s: %s -- ändrad t.o.m. SFS %s"
                        % (c.basefile, c.title, c.cutoff))
        if c.folded:
            body.append("  innefattar även SFS %s (mellanliggande ändringar "
                        "utan arkiverad konsolidering)" % ", ".join(c.folded))
    for _path, basefile, repealer in sorted(event.deletes):
        body.append("SFS %s: upphävd genom SFS %s" % (basefile, repealer))
    if body:
        lines += [""] + body
    _, _, substituted = event_dates(event)
    if substituted:
        lines += ["", "Författardatum är ikraftträdandedatum (utfärdandedatum "
                      "saknas i registret)."]
    trailers = ["Lagen-Event: " + event.key]
    if prop_meta:
        for name in prop_meta.get("signers", [])[1:]:
            trailers.append("Co-authored-by: %s <%s>" % (name, email_slug(name)))
    return "\n".join(lines) + "\n\n" + "\n".join(trailers) + "\n"


def identities(event, forarbete_meta):
    """((author name, email), (committer name, email)) -- the proposition's
    first signer and the riksdagsskrivelse's first signer, with the corpus
    fallbacks when either förarbete is unavailable."""
    author = ("Regeringen", "regeringen@lagen.nu")
    committer = ("Riksdagen", "riksdagen@lagen.nu")
    prop_meta = forarbete_meta(event.prop) if event.prop else None
    if prop_meta and prop_meta.get("signers"):
        name = prop_meta["signers"][0]
        author = (name, email_slug(name))
    rskr_meta = forarbete_meta(event.rskr) if event.rskr else None
    if rskr_meta and rskr_meta.get("signers"):
        name = rskr_meta["signers"][0]
        committer = (name, email_slug(name))
    return author, committer


def _data(text):
    payload = text.encode() if isinstance(text, str) else text
    return b"data %d\n%s\n" % (len(payload), payload)


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


def stream(events, forarbete_meta, tip=None):
    """The fast-import byte stream for the events, in `ordered_events` order --
    a generator of chunks, so the whole corpus never sits in memory. `tip`
    chains the first commit onto an existing branch head."""
    ordered = ordered_events(events)
    first = True
    for ev in ordered:
        author_date, committer_date, _ = event_dates(ev)
        (a_name, a_mail), (c_name, c_mail) = identities(ev, forarbete_meta)
        yield ("commit refs/heads/%s\n"
               "author %s <%s> %s\n"
               "committer %s <%s> %s\n"
               % (BRANCH, a_name, a_mail, _epoch(author_date),
                  c_name, c_mail, _epoch(committer_date))).encode()
        yield _data(message(ev, forarbete_meta))
        if first and tip:
            yield b"from %s\n" % tip.encode()
        first = False
        for c in sorted(ev.changes, key=lambda c: c.path):
            yield b"M 644 inline %s\n" % c.path.encode()
            yield _data(snapshot_text(c.src))
        for path, _, _ in sorted(ev.deletes):
            yield b"D %s\n" % path.encode()


def emit(repodir, events, forarbete_meta):
    """Pipe the event stream into `git fast-import` and materialize the
    working tree. Returns the number of commits written."""
    tip = git.run(repodir, "rev-list", "-n1", "--all", capture=True)
    proc = subprocess.Popen(["git", "-C", str(repodir), "fast-import",
                             "--quiet"], stdin=subprocess.PIPE)
    out = proc.stdin
    assert out is not None, "Popen(stdin=PIPE) always yields a pipe"
    for chunk in stream(events, forarbete_meta, tip):
        out.write(chunk)
    out.close()
    if proc.wait() != 0:
        raise RuntimeError("git fast-import failed (exit %d)" % proc.returncode)
    if events:
        git.run(repodir, "checkout", "-f", BRANCH)
    return len(events)


def existing_events(repodir):
    """The Lagen-Event trailers already committed -- the idempotency ledger a
    re-run skips."""
    tip = git.run(repodir, "rev-list", "-n1", "--all", capture=True)
    if not tip:
        return set()
    return set(RE_TRAILER.findall(
        git.run(repodir, "log", "--format=%B", BRANCH, capture=True)))


def export(basefiles, repodir, *, forarbete_meta, log=print):
    """Build or update the history repo: collect every amendment event across
    `basefiles`, drop those already committed, and fast-import the rest.
    `forarbete_meta` resolves a "Prop. ..."/"Rskr. ..." identifier to
    {title, signers, ingress} from the förarbete corpus (or None). Returns
    (commits, skipped)."""
    repodir.mkdir(parents=True, exist_ok=True)
    if not (repodir / ".git").exists():
        git.run(repodir, "init", "-q", "-b", BRANCH)
    done = existing_events(repodir)
    events, skipped = collect(basefiles)
    fresh = {k: v for k, v in events.items() if k not in done}
    for skip in skipped:
        log("  asgit %s: skipped %s (%s)"
            % (skip["basefile"], skip.get("file", ""), skip["error"]))
    # the ledger is event-level: changes riding an already-committed event are
    # dropped, which is invisible per change -- say so in aggregate, so a
    # partial-corpus rebuild (a statute added after its events were committed)
    # is detectable rather than silent
    dropped = sum(len(events[k].changes) + len(events[k].deletes)
                  for k in events if k in done)
    if dropped:
        log("  asgit: %d event(s) already committed; %d file change(s) riding "
            "them were not revisited" % (len(events) - len(fresh), dropped))
    commits = emit(repodir, fresh, forarbete_meta)
    return commits, skipped
