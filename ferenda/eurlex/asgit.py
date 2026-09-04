"""Export eurlex acts' consolidated-version history as a git repository --
`lagen eurlex history-as-git`.

One file per act (`32016/R0679.md`: sector+year directory, type+number
filename), one commit per consolidated wording CELLAR has already published
under `.versions/{date}/` -- the eurlex counterpart of `sfs.asgit`, reading
the artifacts the `parse` and `versions` build stages already produced
(`eurlex.source`) rather than parsing Formex/xhtml/PDF again. A
wording the versions stage could not parse (the pre-2005 PDF-only tail) is
already recorded as skipped there and produces no commit here: this export
never synthesizes a consolidated version CELLAR did not itself publish.

Unlike SFS there is no proposition/riksdagsskrivelse signer to attribute a
commit to, so both author and committer are a fixed, clearly-not-real
`EUR-Lex <eurlex@lagen.nu>`. In its place, the subject line is the act's own
distinctive name and the body its first recital -- an EU act's recitals are
its stated reasons, the nearest thing to SFS's proposition ingress -- closing
on what amended or corrected this wording (from the Formex `FAM.COMP`
register, `lib.formex.cons_register`, which keeps a substantive amendment
apart from a corrigendum that only corrects the text) and a link to the
lagen.nu page it renders as. Plain sentences, not `Lagen-*:` trailers -- see
`message` and `gitledger`'s own docstring for why the ledger this export
does need (below) lives in a sidecar file instead.

Like SFS: a normal run only appends the new tail entries of an otherwise
unchanged corpus (`gitledger`, shared with `sfs.asgit`); `rebuild=True` is
the explicit, atomic answer to a changed rendering, a corrected artifact, or
a repo predating the ledger -- an eurlex-specific case, since format 1 is
this feature's first ledgered version (v1 shipped rebuild-only, no ledger at
all, so an old eurlex history-as-git repo always needs one rebuild to gain
one). Unlike SFS, one basefile's history never depends on another's (no
proposition groups several acts into one commit), so the append decision is
still all-or-nothing across the run for simplicity (matching
sfs.asgit._append_reasons), but each basefile's own append is otherwise
independent."""

import hashlib
import json
import textwrap
from pathlib import Path

from ..lib import compress, gitledger, layout
from ..lib.errors import RebuildRequired
from ..lib.mdtext import document_markdown, node_markdown
from .download import RE_PLAIN_ACT

BRANCH = gitledger.BRANCH
BRANCH_REF = gitledger.BRANCH_REF
STAGING_REF = "refs/lagen/eurlex-history-as-git-staging"
AUTHOR = ("EUR-Lex", "eurlex@lagen.nu")
FORMAT = "1"


def celex_path(celex):
    """The git working-tree path for a plain sector-3 R/L CELEX --
    '32016R0679' -> '32016/R0679.md' -- or None when `celex` is not one (a
    corrigendum, a decision, a treaty, a case)."""
    if not RE_PLAIN_ACT.match(celex):
        return None
    return "%s%s/%s%s.md" % (celex[0], celex[1:5], celex[5], celex[6:])


def _first_recital(nodes):
    """The first {"type": "recital", ...} node in `nodes`, depth-first, or
    None. Recitals precede every article in an eurlex artifact's structure
    (`parse.base_preamble` puts them there), so this rarely descends far."""
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        if n.get("type") == "recital":
            return n
        found = _first_recital(n.get("children"))
        if found is not None:
            return found
    return None


def _act_name(art):
    """A distinctive, <=72-char commit subject for the act: its curated
    shortname (a well-known act like "eIDAS-förordningen") when the named
    dataset has one, else its official short title, else its full title --
    trimmed to a word boundary, the bare CELEX only for an act with none of
    the three (rare -- mostly very old acts)."""
    name = art.get("shortname") or art.get("label") or art.get("title") or art["celex"]
    return textwrap.shorten(name, width=72, placeholder="…")


def _recital_text(recital):
    """A recital's own text, without the printed "(N) " marker -- the
    marker belongs on the rendered document page, not the commit body, now
    that the act's name is the subject line rather than the recital."""
    rendered = node_markdown(recital)
    prefix = "(%s) " % recital.get("num")
    return rendered[len(prefix):] if recital.get("num") and rendered.startswith(prefix) else rendered


def message(art, date):
    """The commit message for one version artifact: subject is the act's
    own distinctive name (`_act_name`), body its first recital's text when
    it has one -- an EU act's recitals are its stated reasons, the nearest
    thing to SFS's proposition ingress -- closing on one line with what
    amended or corrected this wording (when it is a consolidation) and the
    exact lagen.nu page it renders as (the artifact's own `uri`, already
    keyed to the version: `.../konsolidering/<date>` for an archived
    wording, the bare act page for the current one).

    No `Lagen-*:` machine trailers: the ledger this export does need lives
    in `gitledger`'s sidecar file, not the message, so nothing here ever
    gets re-read -- a key:value block would only be decoration, and plain
    sentences read better. `date` (the commit's own, from `_version_date`)
    still earns a sentence of its own when it predates 1970: the git ident
    date clamps there (`gitledger.epoch`), so this is the one place such
    an act's true date survives unmangled."""
    subject = _act_name(art)
    recital = _first_recital(art.get("structure"))
    lines = [subject] + (["", _recital_text(recital)] if recital is not None else [])
    closing = []
    cons = art.get("consolidation")
    if cons:
        amending = [a["celex"] for a in cons.get("amending") or []
                   if a.get("celex")]
        corrigenda = cons.get("corrigenda") or []
        if amending:
            closing.append("Ändrad genom %s." % ", ".join(amending))
        elif corrigenda:
            # a corrigendum-only wording: it corrects the text, it does not
            # amend the law (lib.formex.cons_register's own distinction)
            closing.append("Rättad genom %s." % ", ".join(corrigenda))
    if date < "1970-01-01":
        closing.append("Ursprungsdatum %s (git visar 1970-01-01)." % date)
    closing.append(art["uri"])
    lines += ["", " ".join(closing)]
    return "\n".join(lines) + "\n"


def _version_date(art):
    """The date to commit this artifact under: the consolidation's own date
    when it is a consolidated wording, else the act's own date."""
    cons = art.get("consolidation")
    return (cons["date"] if cons else None) or art.get("date")


def _entries(basefile):
    """Every commit-worthy artifact for one act, oldest to newest: each
    archived consolidated version (`eurlex_versions_sidecar`, already
    ascending) via `eurlex_version_artifact`, then the act's current text
    (`layout.artifact`) last. None when the act has no parsed artifact at
    all (not yet parsed, or parse raised SkipDocument)."""
    main = compress.read_json(layout.artifact("eurlex", basefile),
                              default=None, empty=None)
    if not main:
        return None
    entries = []
    sidecar_path = layout.eurlex_versions_sidecar(basefile)
    if sidecar_path.exists():
        sidecar = json.loads(sidecar_path.read_text())
        for entry in sidecar["versions"]:
            art = compress.read_json(
                layout.eurlex_version_artifact(basefile, entry["version"]),
                default=None, empty=None)
            if art:
                entries.append(art)
    entries.append(main)
    return entries


def _kept_entries(basefile, log):
    """The (art, date, md) triples `basefile` actually gets a commit for,
    oldest first: `_entries`, deduped by rendered markdown (a consolidation
    that changed only metadata, not the text, gets no separate commit).
    Recomputed by both the ledger decision and the emission pass (see
    `_desired`) -- the corpus is large enough that holding every act's
    rendered markdown in memory for the whole run is worth avoiding, and
    re-rendering is cheap next to a parsed artifact already on disk."""
    entries = _entries(basefile)
    if not entries:
        return []
    prev_md = None
    kept = []
    for art in entries:
        date = _version_date(art)
        if not date:
            log("%s: no date, version left out of the history" % basefile)
            continue
        md = document_markdown(art)
        if md == prev_md:
            continue
        prev_md = md
        kept.append((art, date, md))
    return kept


def _record(basefile, date, md):
    return {"id": "%s@%s" % (basefile, date), "celex": basefile, "date": date,
           "body": hashlib.sha256(md.encode()).hexdigest()}


def _desired(basefiles, log):
    """`{basefile: [record, ...]}` for every requested basefile that has at
    least one commit-worthy entry -- the ledger decision's input, and (once
    a run is confirmed append-safe or a rebuild is chosen) the new ledger
    file's content."""
    desired = {}
    for basefile in sorted(basefiles):
        if celex_path(basefile) is None:
            continue
        kept = _kept_entries(basefile, log)
        if kept:
            desired[basefile] = [_record(basefile, date, md)
                                 for _art, date, md in kept]
    return desired


def existing_ledger(repodir):
    """`{basefile: [record, ...]}` from the ledger file (`gitledger`), or
    `{}` for a fresh repo, or one predating this format (format < 1 --
    always requires --rebuild-history, since v1 shipped with no ledger at
    all to distinguish a legacy eurlex repo from an unrelated one; see the
    module docstring).

    A malformed ledger is corruption, not an excuse to guess which act's
    history it meant."""
    ledger = gitledger.read(repodir)
    if ledger is None or ledger.get("format") != FORMAT:
        return {}
    if set(ledger) != {"format", "records"} or not isinstance(ledger["records"], dict):
        raise ValueError("invalid ledger file: %s" % gitledger.path(repodir))
    records = ledger["records"]
    for basefile, entries in records.items():
        if celex_path(basefile) is None or not isinstance(entries, list):
            raise ValueError("invalid ledger entry for %s" % basefile)
        for record in entries:
            if (not isinstance(record, dict)
                    or set(record) != {"id", "celex", "date", "body"}
                    or not all(isinstance(record[key], str)
                              for key in ("id", "celex", "date", "body"))):
                raise ValueError("invalid ledger record: %s" % record)
    return records


def _append_reasons(existing, desired, basefiles):
    """Why `desired` is not a strict append-only extension of `existing`,
    per basefile actually requested this run -- mirrors sfs.asgit's own
    function of the same name. Scoped to `basefiles`, not all of
    `existing`: a repo built with a wider scope than this run's holds
    ledger entries for acts this run never looked at, and those are simply
    untouched, not "removed" -- eurlex has no cross-file scope concept the
    way sfs does (rule:second-use-goes-to-lib did not apply: one basefile's
    history never depends on another's here), so the check is scoped
    per-call instead."""
    reasons = []
    for basefile in basefiles:
        old_records = existing.get(basefile)
        if old_records is None:
            continue
        new_records = desired.get(basefile)
        if new_records is None:
            reasons.append("%s is absent from the current corpus" % basefile)
        elif new_records[:len(old_records)] != old_records:
            reasons.append("%s changed" % basefile)
    return reasons


def _emit_commits(basefile, kept, ref, tip_box, expected):
    """The fast-import commit(s) for `kept` ((art, date, md) triples) of one
    act, in order. `expected` (the matching slice of `_desired`'s records,
    or None to skip the check) is reverified against each entry's actual
    hash here -- catches the artifact changing on disk between the decision
    pass (`_desired`) and this one, e.g. a concurrent parse/versions run on
    the same basefile; mirrors sfs.asgit.stream's snapshot-changed check.
    `tip_box` is a one-element list holding the tip to chain the *first*
    commit of the whole run onto -- shared and mutated to `[None]` across
    every basefile's call so only that one commit, whichever basefile it
    belongs to, carries `from`; already `[None]` for a rebuild, which
    starts the ref fresh."""
    if expected is not None and len(expected) != len(kept):
        raise RuntimeError("artifact changed during history export: %s" % basefile)
    path = celex_path(basefile)
    name, email = AUTHOR
    for i, (art, date, md) in enumerate(kept):
        if expected is not None:
            digest = hashlib.sha256(md.encode()).hexdigest()
            if digest != expected[i]["body"]:
                raise RuntimeError(
                    "artifact changed during history export: %s" % basefile)
        stamp = gitledger.epoch(date, clamp=True)
        yield ("commit %s\n"
              "author %s <%s> %s\n"
              "committer %s <%s> %s\n"
              % (ref, name, email, stamp, name, email, stamp)).encode()
        yield gitledger.data_payload(message(art, date))
        if tip_box[0]:
            yield b"from %s\n" % tip_box[0].encode()
            tip_box[0] = None
        yield b"M 644 inline %s\n" % path.encode()
        yield gitledger.data_payload(md)


def stream(basefiles, ref=BRANCH_REF, desired=None, log=print):
    """The fast-import byte stream for every commit-worthy entry of
    `basefiles`, oldest first per act -- a generator, so the corpus never
    sits in memory at once. Used for a full rebuild: the ref always starts
    fresh, never chained onto a prior tip. `_stream_append` emits only each
    basefile's new tail, chained onto the ref's existing history. `desired`
    (from `_desired`), when given, is reverified against each entry's
    actual hash at emit time (see `_emit_commits`)."""
    tip_box = [None]
    for basefile in sorted(basefiles):
        if celex_path(basefile) is None:
            continue
        kept = _kept_entries(basefile, log)
        expected = desired.get(basefile) if desired is not None else None
        yield from _emit_commits(basefile, kept, ref, tip_box, expected)


def _stream_append(basefiles, existing, desired, ref, tip, log):
    """Like `stream`, but for each basefile skips the entries already
    reflected in `existing` (a strict, unchanged-hash prefix -- verified by
    the caller's `_append_reasons` before this ever runs): only the
    genuinely new tail becomes a commit, and the very first of those chains
    onto `tip` -- the ref's tip before this run, so the new commits extend
    the existing history instead of replacing it."""
    tip_box = [tip]
    for basefile in sorted(basefiles):
        if celex_path(basefile) is None:
            continue
        kept = _kept_entries(basefile, log)
        already = len(existing.get(basefile, []))
        expected = desired.get(basefile, [])[already:]
        yield from _emit_commits(basefile, kept[already:], ref, tip_box, expected)


def export(repodir, basefiles, *, rebuild=False, log=print):
    """Update or build `repodir`'s `main` branch: a normal run appends only
    the new tail entries of an otherwise-unchanged corpus (an act whose
    already-committed wordings still hash the same); `rebuild=True` is the
    explicit, atomic answer to a changed rendering, a corrected artifact, or
    a repo predating the ledger. Returns the number of commits written."""
    repodir = Path(repodir)
    tip = gitledger.prepare_repo(repodir, BRANCH)
    desired = _desired(basefiles, log)
    existing = existing_ledger(repodir)
    if tip and not existing and not rebuild:
        raise RebuildRequired(
            "history-as-git ledger is missing or predates this format (a "
            "repo an older version of this tool built, or not an export "
            "repository at all); rerun with --rebuild-history")
    if rebuild:
        # a rebuild replaces `main` with exactly `desired` -- any act the
        # existing ledger knows about but this run's `basefiles` does not
        # would simply vanish from the new tree, and `reclaim=True` then
        # prunes its now-unreachable commits for good. That must be a
        # separate, explicit decision, not a side effect of rebuilding one
        # act's rendering: refuse rather than silently discard.
        missing = sorted(set(existing) - set(basefiles))
        if missing:
            raise ValueError(
                "history-as-git rebuild scope excludes %d already-committed "
                "act(s) (e.g. %s), which would be discarded; rebuild with "
                "every basefile currently in the ledger, or the whole corpus "
                "(omit the CELEX arguments)" % (len(missing), ", ".join(missing[:5])))
        commits = sum(len(records) for records in desired.values())
        if commits:
            # the staging ref, not `main`, is what `stream` populates fresh
            # (rebuild passes no `tip` to chain onto) -- `main` itself stays
            # untouched until gitledger.publish's compare-and-swap, which
            # needs its *current* value (`tip`) to still be there to swap
            gitledger.publish(repodir, stream(basefiles, ref=STAGING_REF,
                                              desired=desired, log=log),
                             branch_ref=BRANCH_REF, staging_branch_ref=STAGING_REF,
                             old_tip=tip, reclaim=True)
            gitledger.write(repodir, {"format": FORMAT, "records": desired})
        return commits
    reasons = _append_reasons(existing, desired, basefiles)
    if reasons:
        raise RebuildRequired("history-as-git requires rebuild: %s; rerun "
                              "with --rebuild-history" % "; ".join(reasons[:5]))
    commits = sum(len(new) - len(existing.get(bf, []))
                 for bf, new in desired.items())
    if commits:
        gitledger.publish(
            repodir, _stream_append(basefiles, existing, desired, STAGING_REF,
                                    tip, log),
            branch_ref=BRANCH_REF, staging_branch_ref=STAGING_REF, old_tip=tip)
        gitledger.write(repodir, {"format": FORMAT,
                                  "records": {**existing, **desired}})
    return commits
