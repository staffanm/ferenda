# PRD: `lagen sfs history-as-git` — the SFS corpus as a git repository

*Status: future work, parked 2026-07-02. Depends on the SFS version-history
machinery (`sfs/versions.py`, the download archive) which is done.*

## Idea

Whenever a legal-information project is announced, techies claim everything
would be better if the laws were in a git repository. Make it real: a
subcommand that **creates or updates** a git repo containing the whole SFS
collection as plaintext, with adds, changes and deletions expressed as
meaningful commits.

## Product shape

- `lagen sfs history-as-git <repodir> [basefile…]` — builds the repo from
  scratch, or appends the not-yet-committed events to an existing one
  (idempotent; re-running after a harvest adds only the new history).
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
- **Incremental update**: record the last-committed event (e.g. a state file
  under the target repo's `.git/`, or a `Lagen-Event:` trailer greppable from
  the tip); on re-run, skip events already present.
- **Initial-state caveat**: the earliest archived snapshot of an old law is
  usually already consolidated ("t.o.m. SFS 2003:466"), not the original
  as-enacted text — the add-commit message should say so.

## Open questions

- Handle base-act renames/omtryck (`rinfoex:omtryck`) as git renames?
- Letter-series acts (`N1988:1`) and space-carrying ids ("1827:60 s.1007")
  need filename slugs consistent with `layout.relpath`.
- Should övergångsbestämmelser be part of the file body (they are not in
  `forfattningstext`)?
