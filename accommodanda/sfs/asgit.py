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

import hashlib
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
BRANCH_REF = "refs/heads/" + BRANCH
STAGING_REF = "refs/lagen/history-as-git-staging"
FORMAT = "2"
RE_EVENT = re.compile(r"^Lagen-Event: (.+)$", re.MULTILINE)
RE_TRANSITION = re.compile(r"^Lagen-Transition: (.+)$", re.MULTILINE)
RE_SCOPE = re.compile(r"^Lagen-Scope: (.+)$", re.MULTILINE)
RE_SFS_NR = re.compile(r"(\d+:\d+)")


class RebuildRequired(ValueError):
    """The current corpus changes history rather than extending it."""


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
    download archive plus the current download, each as
    ``(cutoff, path, plaintext_hash)``. Explicitly keyed archive files win over
    counter-keyed duplicates, but the current download wins over an archive of
    the same cutoff: it is the source the downloader has just corrected.

    A bad snapshot is recorded here so `collect` can report every problem in
    one pass. `export` deliberately refuses to write history from that
    incomplete collection; skipping it and appending a later repair would put
    historical text at the branch tip."""
    current = layout.sfs_source(basefile)
    if not compress.exists(current):
        current = layout.sfs_sfst(basefile)
    # explicitly-keyed archives first, so a counter-keyed duplicate of the
    # same consolidation loses to the authoritative key (as in versions.build)
    archive = sorted(layout.sfs_version_downloads(basefile),
                     key=lambda vp: (":" not in vp[0], vp[0]))
    snapshots = {}
    for _, path in archive:
        try:
            cutoff = snapshot_cutoff(path, basefile)
            text = snapshot_text(path)
        except SkipDocument as exc:
            skipped.append({"basefile": basefile, "file": str(path),
                            "error": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 — per-snapshot resilience point, mirroring versions.build's: a corrupt decades-old archive file becomes a recorded skip, not an aborted corpus export (rule:no-catch-log-continue)
            skipped.append({"basefile": basefile, "file": str(path),
                            "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        snapshots.setdefault(cutoff, (path, _hash(text)))
    try:
        cutoff = snapshot_cutoff(current, basefile)
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
    if ordered and ordered[-1][0] != cutoff:
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
    nr), plus incomplete-input records. The caller must reject an incomplete
    collection before it writes history; keeping the records here lets one
    preflight report every bad snapshot and missing artifact at once."""
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
        for cutoff, src, body_hash in statute_snapshots(basefile, skipped):
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
                                     add=prev is None, body_hash=body_hash))
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
                    "folded": change.folded, "add": change.add}
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


def message(event, forarbete_meta, scope="full"):
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
    trailers = ["Lagen-History-Format: " + FORMAT,
                "Lagen-Scope: " + scope,
                "Lagen-Event: " + event.key]
    if prop_meta:
        for name in prop_meta.get("signers", [])[1:]:
            trailers.append("Co-authored-by: %s <%s>" % (name, email_slug(name)))
    trailers.extend("Lagen-Transition: " + json.dumps(
        record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        for record in transition_records(event, forarbete_meta))
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
               % (ref, a_name, a_mail, _epoch(author_date),
                  c_name, c_mail, _epoch(committer_date))).encode()
        yield _data(message(ev, forarbete_meta, scope))
        if first and tip:
            yield b"from %s\n" % tip.encode()
        first = False
        for c in sorted(ev.changes, key=lambda c: c.path):
            text = snapshot_text(c.src)
            if _hash(text) != _body_hash(c):
                raise RuntimeError("snapshot changed during history export: %s"
                                   % c.src)
            yield b"M 644 inline %s\n" % c.path.encode()
            yield _data(text)
        for path, _, _ in sorted(ev.deletes):
            yield b"D %s\n" % path.encode()


def emit(repodir, events, forarbete_meta, *, tip=None, scope="full",
         ref=BRANCH_REF):
    """Pipe the event stream into `git fast-import` and return commit count.

    Callers materialize the worktree only after a successful append or atomic
    replacement of `main`; import itself must never choose a parent from an
    unrelated ref.
    """
    proc = subprocess.Popen(["git", "-C", str(repodir), "fast-import",
                             "--quiet"], stdin=subprocess.PIPE)
    out = proc.stdin
    assert out is not None, "Popen(stdin=PIPE) always yields a pipe"
    try:
        for chunk in stream(events, forarbete_meta, tip, scope, ref):
            out.write(chunk)
    except BaseException:
        out.close()
        proc.wait()
        raise
    out.close()
    if proc.wait() != 0:
        raise RuntimeError("git fast-import failed (exit %d)" % proc.returncode)
    return len(events)


def _branch_tip(repodir):
    """`main`'s tip, or ``""`` for an unborn branch, never another ref."""
    return git.run(repodir, "rev-parse", "--verify", "-q", BRANCH_REF,
                   capture=True, check=False)


def existing_ledger(repodir):
    """``(transitions, events, scopes)`` reachable from the export's main.

    A malformed trailer is a corrupted ledger, not an excuse to guess which
    source transition it meant.
    """
    if not _branch_tip(repodir):
        return {}, set(), set()
    messages = git.run(repodir, "log", "--format=%B", BRANCH, capture=True)
    records = {}
    for raw in RE_TRANSITION.findall(messages):
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid Lagen-Transition trailer: %s" % raw) from exc
        if (not isinstance(record, dict)
                or set(record) != {"id", "basefile", "cutoff", "op", "event", "body",
                           "metadata"}
                or record["op"] not in ("write", "delete")):
            raise ValueError("invalid Lagen-Transition trailer: %s" % raw)
        if (not all(isinstance(record[key], str)
                    for key in ("id", "basefile", "cutoff", "op", "event", "metadata"))
                or record["body"] is not None and not isinstance(record["body"], str)):
            raise ValueError("invalid Lagen-Transition trailer: %s" % raw)
        old = records.setdefault(record["id"], record)
        if old != record:
            raise ValueError("conflicting ledger records for %s" % record["id"])
    return (records, set(RE_EVENT.findall(messages)),
            set(RE_SCOPE.findall(messages)))


def _prepare_repo(repodir):
    """Validate a clean dedicated worktree before a history ref can move."""
    if repodir.exists() and not repodir.is_dir():
        raise ValueError("history-as-git target is not a directory: %s" % repodir)
    if not repodir.exists():
        repodir.mkdir(parents=True)
    dotgit = repodir / ".git"
    if not dotgit.exists():
        if any(repodir.iterdir()):
            raise ValueError("history-as-git target is not an empty directory: %s"
                             % repodir)
        git.run(repodir, "init", "-q", "-b", BRANCH)
    if git.run(repodir, "rev-parse", "--is-bare-repository", capture=True) == "true":
        raise ValueError("history-as-git target must have a worktree: %s" % repodir)
    head = git.run(repodir, "symbolic-ref", "-q", "--short", "HEAD",
                   capture=True, check=False)
    if head != BRANCH:
        raise ValueError("history-as-git target must have %s checked out" % BRANCH)
    dirty = git.run(repodir, "status", "--porcelain", capture=True)
    if dirty:
        raise ValueError("history-as-git target has uncommitted changes")
    return _branch_tip(repodir)


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


def _require_complete(basefiles, events, skipped, log):
    for skip in skipped:
        log("  asgit %s: incomplete %s (%s)"
            % (skip["basefile"], skip.get("file", ""), skip["error"]))
    # collect is the one owner of incompleteness: missing artifacts and bad
    # snapshots both arrive as skip records
    missing = sum(1 for skip in skipped
                  if skip["error"] == "no parsed artifact")
    bad = len(skipped) - missing
    if skipped:
        details = ["%d parsed artifact(s) missing" % missing if missing else "",
                   "%d snapshot(s) unreadable or inconsistent" % bad
                   if bad else ""]
        raise ValueError("history-as-git needs a complete corpus (%s)" %
                         "; ".join(part for part in details if part))
    if basefiles and not events:
        raise ValueError("history-as-git complete corpus produced no events")


def _materialize(repodir):
    """The checked-out branch is known clean before its ref has been moved."""
    git.run(repodir, "reset", "--hard", BRANCH)


def _cached_meta(forarbete_meta):
    """Keep one export's signatures stable while avoiding repeated artifact I/O."""
    cache = {}

    def lookup(identifier):
        if identifier not in cache:
            cache[identifier] = forarbete_meta(identifier)
        return cache[identifier]

    return lookup


def _publish(repodir, events, forarbete_meta, scope, old_tip, parent):
    """Import to a staging ref, then atomically move `main` on success."""
    git.run(repodir, "update-ref", "-d", STAGING_REF)
    commits = emit(repodir, events, forarbete_meta, tip=parent, scope=scope,
                   ref=STAGING_REF)
    if not commits:
        return 0
    new_tip = git.run(repodir, "rev-parse", "--verify", STAGING_REF,
                      capture=True)
    args = ["update-ref", BRANCH_REF, new_tip]
    if old_tip:
        args.append(old_tip)
    git.run(repodir, *args)
    git.run(repodir, "update-ref", "-d", STAGING_REF)
    _materialize(repodir)
    return commits


def export(basefiles, repodir, *, forarbete_meta, scope="full", rebuild=False,
           log=print):
    """Build or safely update a history repository from a complete corpus;
    returns the number of commits written.

    Normal runs only append unseen, later transitions belonging to wholly new
    events. `rebuild=True` is the explicit, atomic answer to corrected text,
    backfills, attribution changes and legacy event-only repositories.
    """
    events, skipped = collect(basefiles)
    _require_complete(basefiles, events, skipped, log)
    forarbete_meta = _cached_meta(forarbete_meta)
    desired = event_records(events, forarbete_meta)
    tip = _prepare_repo(repodir)
    existing, legacy_events, scopes = existing_ledger(repodir)
    if tip and not existing:
        if legacy_events:
            if not rebuild:
                raise RebuildRequired(
                    "history-as-git ledger is legacy; rerun with --rebuild-history")
        else:
            raise ValueError("history-as-git target is not an export repository")
    if existing and scopes != {scope} and not rebuild:
        raise RebuildRequired("history-as-git scope changed; rerun with "
                              "--rebuild-history")
    if rebuild:
        return _publish(repodir, events, forarbete_meta, scope, tip, None)
    reasons = _append_reasons(existing, desired)
    if reasons:
        raise RebuildRequired("history-as-git requires rebuild: %s; rerun with "
                              "--rebuild-history" % "; ".join(reasons[:5]))
    existing_event_keys = {record["event"] for record in existing.values()}
    fresh = {key: event for key, event in events.items()
             if key not in existing_event_keys}
    return _publish(repodir, fresh, forarbete_meta, scope, tip, tip)
