# Föreskrift corpus audit — shared briefing

You audit ONE författningssamling (a "scope"). You compare what the issuing
agency publishes today with what the ferenda corpus holds. You file a GitHub
issue when our data is wrong. You never open a pull request and you never
change code.

Repo root: /home/staffan/repos/ferenda   Python: .venv/bin/python

## Hard rules

- Read-only on the repo. Do not edit any file under /home/staffan/repos/ferenda.
- Never run a version-control command that writes. Inspection only.
- Never run a pipeline: no `lagen download`, `parse`, `relate`, `generate`,
  `all`. They rewrite the corpus. You only read it.
- Politeness (rule:respect-politeness). Use ferenda's own HTTP client, which
  reads robots.txt Crawl-delay, paces, retries and caps the body:

      from ferenda.lib.net import request
      r = request(session, url)        # see ferenda/lib/net.py for the signature

  Read `ferenda/lib/net.py` first and call it the way the agency modules call
  it (`grep -n "request(" ferenda/foreskrift/agencies.py | head`).
  Plain `requests.get` is acceptable for a handful of pages if you sleep 2 s
  between them and send the BROWSER_UA from ferenda.lib.net.
- JavaScript pages: playwright chromium headless is installed (firefox and
  webkit are not). Keep 2 s between navigations.
- Budget: at most 60 HTTP requests to the agency site. Enumerate the index
  fully, then open individual pages only for a sample.
- If the site blocks you (403, WAF, captcha), stop fetching, say so in the
  report, and file no issue about the corpus. A block is not a corpus defect.

## Where our data lives

- Catalog: `site/data/catalog.sqlite` (open read-only:
  `sqlite3.connect("file:site/data/catalog.sqlite?mode=ro", uri=True)`).
  Table `documents`: uri, kind (= the fs slug), label ("KKVFS 2021:2"), title,
  date, publisher, source_url, path, expired, upphavande.
  Table `links`: from_uri, predicate, to_uri, to_root.
- Artifacts (the source of truth): `site/data/artifact/foreskrift/<fs>/<year>-<lop>.json.br`,
  brotli. Read with `ferenda.lib.compress.read_text(path_without_.br)`.
  Keys: identifier, metadata{title, publisher, beslutsdatum, ikrafttradandedatum,
  utkomFranTryck, bemyndigande[], upphaver[], andrar[], andradAv[]}, structure[].
- Download records: `site/data/downloaded/foreskrift/<fs>/<fs>-<year>-<lop>.json.br`
  with `files{regulation, consolidation[], amendment[], memo[], attachment[]}`
  next to the fetched PDFs.
- URI form: `https://lagen.nu/<fs>/<year>:<lopnummer>`.

## The repeal model (do not propose changing it)

A föreskrift is an as-published, immutable document. It carries no in-force or
expired status of its own. A later document repeals it, and that relation is
the only evidence of repeal:

- `links` rows with predicate `rpubl:upphaver`, from the repealing document to
  the repealed one. `catalog.upphaver_targets(con)` returns the repealed set.
- `documents.upphavande = 1` marks a document whose only content is a repeal.

So "our records do not mark X as repealed" is a defect only in these shapes:
- the repealing document exists in our corpus but its `upphaver` list is empty
  or points at the wrong target (a parse defect), or
- the repealing document is missing from our corpus entirely (a harvest defect).
Never file an issue asking for a status flag on the repealed document itself.

## What to check

1. Enumerate. Take the scope's entry page, follow its pagination or sub-pages,
   and build the agency's own list of designations ("KKVFS 2021:2", ...) with
   titles. Say in the report how many pages you read.
2. Missing documents: on the site, not in our records.
3. Extra documents: in our records, not on the site. Often legitimate (the site
   lists only in-force text). Check whether our copy is repealed by something we
   hold. Report the shape, do not assume a defect.
4. Repeal marking: for every document the site shows as upphävd / "har upphört
   att gälla" / struck through, check the repealing document in our corpus and
   its `upphaver` targets.
5. Titles: compare our `documents.title` against the site for at least 10
   documents. Flag junk: a file size "(142 kb)", a truncation, a PDF file name,
   a missing title, HTML entities, a designation repeated inside the title.
6. Consolidations: does the site publish konsoliderade versions? Count how many
   of our download records carry `files.consolidation`. A site that publishes
   them while we hold none is a defect.
7. Inherited series: your assignment lists the predecessor samlingar this
   agency took over. Check that documents from those series sit under the
   predecessor slug (not renumbered into the successor), and that the site's
   own listing of them matches what we hold.
8. Freshness: the newest designation on the site against the newest we hold.

## Output

Write a report to
`/tmp/claude-1000/-home-staffan-repos-ferenda/71497249-8a1a-4631-91b8-faeae967da12/scratchpad/fs-audit/<scope>.md`
with this shape:

    # <SCOPE> — <DESIGNATION>, <agency>
    Verdict: OK | DEFECT | BLOCKED
    Site list: <n> designations from <m> pages (<entry url>)
    We hold: <n> documents (<fs>), newest <designation>; site newest <designation>
    Missing: <count> — <up to 10 designations>
    Extra: <count> — <up to 10, with whether a repeal covers them>
    Repeal gaps: <count> — <examples>
    Title defects: <count> — <examples, ours vs theirs>
    Consolidations: site <yes/no>, we hold <n>
    Inherited: <per predecessor slug, one line>
    Issue: <url or "none">

    ## Evidence
    <the commands and the numbers behind each line above>

## Filing the issue

Only when you confirmed a corpus defect with evidence. One issue per scope,
covering every defect you found for that scope.

    gh issue list --state open --search "foreskrift <SCOPE>"   # check duplicates first
    gh issue create --repo staffanm/ferenda \
      --title "foreskrift: <short lowercase summary>" \
      --body-file <your body file>

Body: the entry URL, one section per defect, a short table of examples
(designation | site says | we hold), and the commands that reproduce it. State
which of the eight checks passed. Write plain Simplified Technical English:
short sentences, active voice, no metaphor. Do not attach a patch or a diff.
Do not open a pull request.

## Already filed — do not file these again

A corpus-wide scan already covered two classes. Mention an instance in your
report and cite the issue number; do not open a new issue for it.

- #50 — a predecessor-series document also minted under the successor slug
  (byte-identical PDF under both, e.g. `snfs/1987:12` and `nfs/1987:12`).
  Affected: lmfs/lmvfs, nfs/snfs, sifs/lifs, trvfs/vvfs, hslffs/lvfs.
- #52 — a document that stores another document's PDF (byte-identical file
  under two different designations). Affected: elsakfs, ffs, lsfs, mrtvfs, säifs.

Two more patterns keep recurring. Check both, and file them for your scope:

- An "upphävda föreskrifter" archive page that the harvest's index URL list
  does not visit, so every repealed document behind it is missing (AFS #45,
  EIFS #51).
- An index that paginates while `indexed_enumerate` reads page 1 only (AgVFS #42).
- #76 — a corpus-wide pdftotext check of every stored regulation PDF against the
  designation we minted. 108 records print a different number. If a document in
  your scope appears in that list, verify it and describe it in your report,
  citing #76; open a new issue only for a cause #76 does not name.
