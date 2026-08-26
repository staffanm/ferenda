# PRD: `lagen sfs history-as-git` — the SFS corpus as a git repository

*Status: implemented 2026-07-09 (`sfs/asgit.py`, `lagen sfs history-as-git`).
Reads the download archive directly (no `versions`-stage dependency) plus the
parsed SFS artifacts for amendment metadata. Prop signers/ingress come from
the förarbete artifacts (`signatur` blocks + the promoted ingress avsnitt,
tagged at parse time); rskr signers from the rskr corpus
(`forarbete/rskr.py`, `lagen forarbete download rskr`). Open questions below
resolved: filename slugs follow `layout.relpath` (`N1988/1.txt`,
`1827/60_s.1007.txt`), övergångsbestämmelser excluded (body = forfattningstext
only), and an omtryck turns out not to be a rename at all (see below). One
deviation from the sketch: per-amendment utfärdandedatum turned out to be
almost never available in the register, so the author date falls back to
ikraftträdandedatum (noted in the commit body), then July 1 of the SFS year.*

*A full export of the corpus writes 30 214 commits (2026-08-25).*

## Idea

Whenever a legal-information project is announced, techies claim everything
would be better if the laws were in a git repository. Make it real: a
subcommand that **creates or updates** a git repo containing the whole SFS
collection as plaintext, with adds, changes and deletions expressed as
meaningful commits.

## Product shape

- `lagen sfs history-as-git <repodir> [basefile…]` — builds a clean dedicated
  repo from scratch, or appends a strict extension to an existing export. Each
  file transition carries a body and metadata fingerprint, so corrections,
  backfills, changed proposition attribution and late members of an existing
  proposition fail clearly instead of silently changing the tip. Re-run with
  `--rebuild-history` to atomically recreate `main` from the complete corpus.
  A full export requires every current SFS source to have been parsed first;
  it refuses missing artifacts or unreadable snapshots before moving a ref.
- One file per statute, e.g. `1998/204.txt`, containing the **plaintext body
  extracted from the downloads** (the same text the parser consumes:
  `fulltext.forfattningstext` from the beta JSON, `extract_body` from the two
  legacy HTML generations).
- **Commit = amendment event.** When one proposition amends several statutes
  (e.g. prop 2020/21:194 → SFS 2021:952, 2021:953, …), all those file changes
  land in a **single commit**, grouped by the proposition id found in each
  cutoff amendment's förarbeten.
- **Author** — the signers of the proposition (for SFS 2021:952 / prop
  2020/21:194: Stefan Löfven + Mikael Damberg), extracted from the prop's
  closing signature block in the förarbete artifact.
- **Committer** — the signers of the corresponding riksdagsskrivelse (e.g.
  Andreas Norlén + Kristina Svartz per the rskr document on riksdagen.se).
- **Log message** — the ingress of the proposition ("För att stärka skyddet
  för Sveriges säkerhet föreslår…"), i.e. the first paragraph of
  "Propositionens huvudsakliga innehåll", with the affected SFS numbers listed
  in the body.
- **Adds**: a base act entering the corpus (its first known consolidation).
  **Deletions**: a repeal (`rinfoex:upphavdAv` + `rpubl:upphavandedatum`) —
  the file is removed in the repealing act's own commit when it belongs to
  the same event.
- **Dates**: author date = the amendment's utfärdandedatum (decision),
  committer date = ikraftträdandedatum (entry into force).

## Design notes (from the first scoping pass)

- **Use `git fast-import`.** Tens of thousands of commits (31k+ archived
  consolidations + 13.8k current + repeals); one `git commit` process per
  event is far too slow, a fast-import stream is minutes. Multi-file commits,
  arbitrary author/committer/timestamps and deletes are all native.
- **Granularity is bounded by the archive.** A commit can only reflect the
  delta between two *available* snapshots; consecutive archived versions
  sometimes fold in several amendments (the archive has gaps). Attribute the
  transition to the newer snapshot's cutoff amendment and name any other
  amendments folded in inside the message body.
- **Event timeline**: per statute, the sidecar's ordered versions + the
  current consolidation give the snapshot transitions; each transition keys on
  (prop id if known, else the cutoff SFS nr). Sort all events globally by
  date, emit in order.
- **Metadata sources & fallbacks**:
  - prop ingress + signature names: from the förarbete artifact (verify the
    parse actually captures the signature block; fall back to
    `Regeringen <regeringen@lagen.nu>`).
  - rskr signers: rskr documents are cited in the register but likely not
    harvested as a corpus — needs either a small rskr fetcher
    (riksdagen.se open data) or the fallback `Riksdagen <riksdagen@lagen.nu>`.
  - Synthesize e-mail addresses as name slugs on a clearly-non-real domain
    (`stefan.lofven@lagen.nu`), never real-looking government addresses.
- **Incremental update**: every commit records a `Lagen-Transition:` JSON
  trailer for each file transition, with stable identity, plaintext digest and
  metadata digest. A normal rerun may only add new, later transitions in wholly
  new events. A same-cutoff correction, backfill, changed proposition metadata
  or partial event is a rebuild request, never a silent omission or duplicate.
  The strictness is per statute and per event: a statute *newly entering* the
  corpus appends its entire (possibly decades-old) history as new events at
  the tip, so cross-statute chronology only holds within one build — rebuild
  when global commit order matters.
- **Repository safety**: the target must be a clean non-bare worktree with
  `main` checked out. The exporter parents only from `main`, never another ref;
  it never force-checks out over local changes.
- **Initial-state caveat**: the earliest archived snapshot of an old law is
  usually already consolidated ("t.o.m. SFS 2003:466"), not the original
  as-enacted text — the add-commit message should say so.

## What the corpus forces the model to admit

Four things the first scoping pass did not foresee, each measured against the
whole corpus and each visible in the export's own output.

- **An omtryck is not a rename.** `rinfoex:omtryck` names the amending act
  whose text was printed as a reprint of the whole statute. The statute keeps
  its SFS number, and the file is named by that number, so no path moves. The
  omtryck is named on the transition it falls on instead ("ändrad t.o.m. SFS
  1992:1769, omtryckt") — 47 of the corpus's 445 omtryck fall on a
  consolidation the archive holds; the rest predate it.
- **Neither is a replacement act, though it is the case a reader wants.** Git
  records no renames: a commit stores a tree, and `git log --follow` recovers a
  move by comparing content when it reads. A new act is newly written text, so
  the similarity never reaches even `-M40%` (the new vapenlag, SFS 2026:408,
  against the SFS 1996:67 it replaces). The succession is stated in the message
  instead ("ersätter SFS 1996:67"), for the 1 685 of 5 887 repeals whose
  successor enters the corpus in the same event.
- **One commit per proposition cannot always hold one position.** A proposition
  may amend statute X before another proposition does and statute Y after it,
  and a single proposition may produce two amending acts to the *same* statute
  (prop. 2007/08:13 and prop. 2007/08:21 interleave twice in 1997:483). 423
  propositions fall back to the per-SFS-number key — the key an amendment with
  no known proposition already takes. The commit still names the proposition
  and is still authored by its signers; it is simply not merged with that
  proposition's other statutes.
- **An unusable archived consolidation is a gap, not incompleteness.** 107 of
  them: junk the old downloader saved instead of the document (a rkrattsbaser
  search-results page, a FELMEDDELANDE page), 20 snapshots whose own Rubrik
  names *another act* (one shifted chain an old import left behind, 1982:787's
  newest archive holding 2008:313's text and so on down to 1982:801), and one
  cutoff whose year is not a year. They are reported and dropped; the
  amendments they would have separated are named as folded in the next commit,
  which is what the archive's known gaps already produce. Only an unreadable
  *current* download still refuses the export.

## Open questions

- Letter-series acts (`N1988:1`) and space-carrying ids ("1827:60 s.1007")
  need filename slugs consistent with `layout.relpath`. *(Resolved: they do.)*
- Should övergångsbestämmelser be part of the file body (they are not in
  `forfattningstext`)? *(Resolved: no.)*
