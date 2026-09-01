# Running the pipelines

The per-source command reference: what each source's `download`, `parse` and
source-specific actions expect, and the order to run them in. For the
prerequisites and `config.yml` these commands need, read
[`README.md`](README.md). For the modules behind them, read
[`../developing/source-map.md`](../developing/source-map.md).

**Pending since 2026-08-29: the corpus must be reparsed and related.** The
canonical uri of the `celex`, `coe`, `icrc`, `untc`, `icc` and `icj`
namespaces lost its `ext/` segment (the uri's path is now the served path).
Every artifact, catalog row and search unit written before that carries the
old form, and the mismatch is silent, not loud: a stale artifact's EU or
treaty citation renders as **plain text with no link and no publisher
fallback**, because the uri no longer reads as one of those namespaces. The
served pages are unaffected — they never carried the prefix — so nothing
needs a redirect. Run a full `parse` → `relate` → `index` → `generate` over
the corpus (`lagen all rebuild`); an already-current source is skipped, so
the cost falls on the sources whose artifacts hold such citations.

**SFS** (operates on `site/data/{downloaded,artifact}/sfs/`, validated against
the golden corpus in the sibling reference checkout, `../ferenda.old/data/sfs/parsed/`):

```sh
uv run python -m ferenda.build sfs download                              # incremental; --force for a full backfill
uv run python -m ferenda.build sfs download --resume-after '[...]'       # resume a backfill interrupted mid-sweep,
                                                                                # from the ES search_after cursor it printed
uv run python -m ferenda.sfs parse site/data/downloaded/sfs/2018/585.json --basefile 2018:585
# golden = the reference projection's parsed XHTML (scaffolding in the sibling reference checkout), normalized to NF on the fly
uv run python -m ferenda.sfs validate ../ferenda.old/data/sfs/parsed site/data/downloaded/sfs --sections structure,references
uv run python -m ferenda.sfs refs FILE PARSED.xhtml  # citation diff for one doc
```

The SFST consolidation is text-only. During the normal SFS parse, omission
markers and the road-sign tables in 2007:90 are projected as typed `grafik`
nodes; the source model retains the original marker text. Mirror the official
published PDFs (the crop source), then vision-localize the gaps onto them.
Mirroring runs as part of `sfs download` and costs only bandwidth; the
vision pass is opt-in and elective (it costs tokens) and is never part of a
production build:

```sh
uv run python -m ferenda.build sfs mirror-pdf                     # every base act + registered amendment (also run by `sfs download`)
uv run python -m ferenda.build sfs mirror-pdf 2007:90             # named SFS act(s) only
uv run python -m ferenda.build sfs mirror-pdf --full              # re-fetch existing + re-ask about acts once denied
uv run python -m ferenda.build sfs ai-includegraphics 2007:90     # vision-localize that act's gaps
```

The mirror writes `site/data/downloaded/sfs/pdf/{year}/{number}.pdf`. Which
source holds an act follows from its SFS number, and both boundaries are exact
act numbers rather than dates: `2018:160` onward is the authentic online series
at svenskforfattningssamling.se, `1998:306`–`2018:159` is the printed series'
rkrattsdb mirror (so early-2018 acts, published before the 1 April switch, come
from there), and anything before `1998:306` exists only on paper — naming one
is an error. Beside the PDFs, `.mirror.json` records the acts an upstream
answered it has no PDF for: a missing file alone cannot say whether an act was
never fetched or has nothing to fetch, so without that record every such act
cost a request on every run. Each act is therefore asked about at most once —
the price being that a negative is permanent, so if the publisher posts a PDF it
previously lacked, only `--full` will find it. `ai-includegraphics` mirrors any
source PDF it still needs, so `mirror-pdf` need not have been run first.

Note that rkrattsdb.gov.se rate-limits: it starts returning `403` for a few
minutes after a burst, which `lib/net.py` rides out with backoff but which can
still abort a corpus-wide sweep. A rerun resumes cheaply (everything already on
disk is skipped). `ai-includegraphics` resolves each gap's provenance
deterministically — the amending SFS that last set that wording (register-first
for bilaga gaps, e.g. 2004:629's two independently-amended map appendices),
never guessed by the model — then asks the vision model (`VISION_MODEL` in
`config.py`, separate from the text `LLM_MODEL`) to locate page + bbox in that
PDF, writing a `.graphics` layer to the curated store (`lib/annstore.py`) with
per-entry `verified` flags that survive a rerun only while both provenance and
semantic identity still match. A road-sign statute (2007:90) skips the vision
model entirely: its 326 signs and their provenance are read off the published
PDFs' own text layer and ink, and the layer it writes is `status: "derived"` —
mechanical, so it renders without per-entry sign-off. The artifact's local `G1` id is not persisted as
identity: the layer is keyed by a `g-…` hash of structural path, kind/code,
normalized anchor and container-local occurrence, and stores the unhashed
`identity` object in each entry for review. Content copies of the same semantic
appendix share a key/crop; a *pending* temporal variant (a container the source
prints beside its in-force sibling with `/Träder i kraft I:.../`) instead gets
its own keys and its own provenance-correct source PDF. Generated candidates
are not publicly rendered until
their entry (or whole layer) is verified — by hand, or by a logged-in editor at
`GET /internal-api/v1/graphics/review` (see "Reviewing `.graphics` crops" below).
`GET /api/v1/sfs-graphic?uri=&node=` serves the
crop (`lib/facsimile.py`'s `cached` with a `bbox`) lazily from the
provenance-correct PDF; the renderer shows the crop where the layer has placed
one — captioned "Karta ur SFS X", linked to the amendment's `#L{nr}` register
entry on the same page — an honest placeholder otherwise, and prints each
temporal variant's entry-into-force state as a subdued slash-delimited
marker (`/Träder i kraft: den dag som regeringen bestämmer/`).

**SFS version history** (historical consolidations / time travel / diff): the
downloader archives every superseded consolidation under
`site/data/downloaded/sfs/archive/{y}/{n}/.versions/`. Retained HTML
consolidations use the same tree. The `versions` stage parses
them into `artifact/sfs/archive/…/.versions/{vy}/{vn}.json` plus a per-statute
`artifact/sfs/{y}/{n}.versions.json` sidecar; `generate` then renders one page per
historical lydelse at `/{sfsnr}/konsolidering/{version}` (watermarked
"Inaktuell författning"), the statute page grows a "Jämför lydelser" panel and
the bottom-of-page **Ändringar och övergångsbestämmelser** register view (per
amendment: publication links, the point-in-time konsolidering link, a diff
link against the previous lydelse, övergångsbestämmelser, förarbeten). The
diff view (`?diff=<version>`, `versions.js`) is computed on demand by
`GET /api/v1/document/diff` — always oldest→newest — (see also
`/api/v1/document/versions`). The whole history is also exportable as a git
repository (`history-as-git`, `sfs/asgit.py`), per
the module docstring of `ferenda/sfs/asgit.py`.

```sh
uv run python -m ferenda.build sfs versions            # incremental, all statutes
uv run python -m ferenda.build sfs versions 1998:204   # one statute
uv run python -m ferenda.build sfs parse               # required before a full Git export
uv run python -m ferenda.build sfs history-as-git /path/to/repo             # complete corpus; strict append-only updates
uv run python -m ferenda.build sfs history-as-git /path/to/repo --rebuild-history  # recreate corrected/backfilled history
uv run python -m ferenda.build sfs history-as-git /path/to/repo 1998:204   # separately scoped partial repo
```

**eurlex version history** (consolidated wordings / lydelse pages / diff): the
eurlex counterpart of the SFS machinery above, over the CONSLEG consolidations
CELLAR maintains for a base act. The consolidation walk rides `download`: the
acts sweep ends by discovering every consolidated version of every held plain
sector-3 R/L act, all versions and not only the latest, into
`site/data/downloaded/eurlex/{year}/{celex}/.versions/{date}/` (a per-CELEX
`download <CELEX> --force` fetches that one act's versions the same way). A
version already settled is skipped, so the walk is resumable: content stored,
or a dated `.no-content` marker recording that CELLAR held no swe/eng
manifestation for that wording. That negative is cached, not permanent — the
Publications Office translates a wording after minting it — so the version is
asked again once the marker ages past the indexing-lag window, which is what
keeps the thousands of never-translated wordings to one question per window
instead of one per run. A version is immutable once published, so a re-fetch
buys nothing; a `--limit` or
`--source soap` run skips the sweep. `parse` then serves the
*latest* Formex-bearing consolidation at the act's own uri, with the base
act's own preamble spliced in front; the `versions` stage parses every
superseded wording into `artifact/eurlex/archive/…/.versions/{date}.json` plus
a `{celex}.versions.json` sidecar, and `generate` renders one page per
historical lydelse at `/celex/{celex}/konsolidering/{date}`, banner and all —
the act's own page grows the same "Jämför lydelser" panel the SFS page has,
and `GET /api/v1/document/diff`/`/document/versions` serve EU acts alongside
statutes. The whole history is also exportable as a git repository
(`history-as-git`, `eurlex/asgit.py`), the eurlex counterpart of
`sfs history-as-git`, per the module docstring of `ferenda/eurlex/asgit.py`.

```sh
uv run python -m ferenda.build eurlex download acts               # year walk + the consolidation sweep
uv run python -m ferenda.build eurlex download 32014R0910 --force # one act + its versions
uv run python -m ferenda.build eurlex parse                       # picks up the latest downloaded consolidation
uv run python -m ferenda.build eurlex versions                    # incremental, all acts with consolidations
uv run python -m ferenda.build eurlex history-as-git /path/to/repo                   # every held sector-3 R/L act; strict append-only updates
uv run python -m ferenda.build eurlex history-as-git /path/to/repo --rebuild-history # recreate corrected/backfilled history
uv run python -m ferenda.build eurlex history-as-git /path/to/repo 32014R0910        # one act only
```

**DV** (operates on `site/data/downloaded/dom/` (API) and `site/data/downloaded/dv/` (legacy)):

```sh
# download + build the identity index
uv run python -m ferenda.dv.download site/data/downloaded/dom   # [--full] [--no-bilagor] [--limit N]
uv run python -m ferenda.build dv reindex                  # -> site/data/artifact/dom/identity-index.json
                                                                  # (also auto-run after any harvest that changed records)

# parse (driver-owned; `[ids…]` parses just those, empty = all stale; a case
# without an API record routes through the legacy parser automatically)
uv run python -m ferenda.build dv parse                                       # incremental, both paths
uv run python -m ferenda.dv.legacy --index site/data/artifact/dom/identity-index.json   # legacy path, batch report
uv run python -m ferenda.dv.legacy site/data/downloaded/dv/ADO/1993-100_1.doc # one Word file -> artifact

# rewrite dv/data/casenumbers.json from the parsed artifacts. A full-source
# `dv parse` already ends with this; run it by hand after a targeted parse, or
# to see what the snapshot holds
uv run python -m ferenda.build dv casenumbers
```

The DV parsers use the identity index. Each canonical case uses its best
source: the API record when present, or the Word original otherwise. The
parsers do not merge sources.
The incremental download only covers late publication within its 365-day
safety window below the watermark; a record edit or a referat published
later than that surfaces only under `--full`, so a periodic cron'd `--full`
sweep remains the backstop.

**avg — JO + JK + ARN + IMY + KKV decisions** (operates on `site/data/{downloaded,artifact}/avg/`):

```sh
uv run python -m ferenda.build avg download        # all five organs; or: … download jo
uv run python -m ferenda.build avg parse           # incremental, like every source
uv run python -m ferenda.build avg download jo --only jo/2340-2025   # one decision
uv run python -m ferenda.build avg download imy    # tillsyner + the two curated pages
uv run python -m ferenda.build avg download kkv    # tillsynsbeslut + ärendelista
uv run python -m ferenda.build avg arsberattelse    # rewrite avg/data/arsberattelse.json from the stored JO artifacts
```

`--only` for `imy` names a diarienummer (`--only imy/IMY-2024-2904`) and needs
the decision already harvested: a decision has no page of its own, so the
tillsyn page to refetch is looked up in its stored record. `--only` for `kkv`
also names a diarienummer, which itself contains a slash
(`--only kkv/558/2026`); it re-walks the ärendelista but fetches only the one
curated account it needs.

**rs — rättsliga ställningstaganden from seven myndigheter** (operates on
`site/data/{downloaded,artifact}/rs/`):

```sh
uv run python -m ferenda.build rs download          # the six HTTP agencies; or: … download fk
uv run python -m ferenda.build rs parse             # incremental, like every source
uv run python -m ferenda.build rs download fk --only fk/2025:01   # one statement
uv run python -m ferenda.build rs download migr     # Lifos (RS + RK), AIA-completed TLS
uv run python -m ferenda.build rs browser-download  # Skatteverket, weekly, headful Chrome
```

Identity is the agency's own number (`rs/fk/2025:01`, `rs/kfm/1-23-VER`,
`rs/migr/RS-028-2021`), so `--only` names that and needs its agency scope. A
first `rs download fk` fetches all 108 PDFs, because Försäkringskassans
Serienummer lives only in the document; later runs read the number off the
stored records and cost one listing request.

Skatteverket is the seventh agency and runs on its own command. It sits behind
the F5/Shape challenge SKVFS sits behind, so every navigation goes through
headful Chrome one at a time, and the run is paced well under the rate the
front tolerates. A first `rs browser-download` is 2,614 paced navigations —
some fifteen hours, sliceable with `--limit N`, and a resumed run skips
whatever is already stored — while a weekly run costs the register plus the
handful of documents that moved. Its documents are stored as `.html`, not
`.pdf`: Skatteverket publishes the ställningstagande *as* a web page.

**guidance — EU-organens vägledningar, 12 utgivare** (operates on
`site/data/{downloaded,artifact}/guidance/`):

```sh
uv run python -m ferenda.build guidance download          # every body
uv run python -m ferenda.build guidance download acer     # one body
uv run python -m ferenda.build guidance download ecb esrb # the two CELLAR bodies
uv run python -m ferenda.build guidance download edpb/riktlinjer --only edpb/riktlinjer/05-2020
uv run python -m ferenda.build guidance download edpb/wp --force   # re-resolve the WP29 ZIPs
uv run python -m ferenda.build guidance parse             # incremental, like every source
```

A download scope is one **upstream walk**, not one series: a bare utgivare
where one walk covers all of that body's series (ten of the twelve), and
`<utgivare>/<serie>` where the series come off different upstreams — the EDPB's
two open series come off its sitemap and its closed WP29 series off the
Commission newsroom.

Identity is the issuing body's own number, never a CELEX
(`edpb/riktlinjer/05-2020`, `eba/gl/2021-05`, `ecb/con/2013-82`, and
`esrb/2014-01` where the body numbers in one sequence and the address carries no
series segment), so `--only` names that and needs its scope. The Swedish version
is published wherever the body has issued one and the English one otherwise; the
record says which. The `edpb/wp` scope is a closed corpus of sixteen documents
whose text lives on the Commission newsroom, each costing a 10–28 MB language
ZIP to resolve — a routine run skips whatever is already on disk.

The ECB and the ESRB publish in EUT rather than on their own sites, so `ecb` and
`esrb` harvest out of CELLAR through `lib/cellar.py` with the same language and
format preferences the eurlex source uses, and their documents parse from
whichever manifestation CELLAR served — Formex through `lib/formex.py`, or the
PDF, or EUR-Lex HTML.

Citations to these documents are linked by the `VAGLEDNING` parse type, by the
EDPB's form (`Riktlinjer 05/2020`, `WP 248`) and by the five bodies whose number
carries their own acronym (`ESRB/2017/6`, `EBA/GL/2021/05`, `ESMA/2013/720`,
`CON/2013/82`, `BoR (11) 67`). `guidance/KNOWN-GAPS.md` records what the grammar
deliberately does *not* catch, and why EIOPA and ACER are left out of it.

**lawreview — tidskriftsartiklar, nio tidskrifter** (operates on
`site/data/{downloaded,artifact}/lawreview/`):

```sh
uv run python -m ferenda.build lawreview download           # all nine journals, fanned out one host each
uv run python -m ferenda.build lawreview download svjt      # one journal
uv run python -m ferenda.build lawreview download jp --only jp/2026-01-02
uv run python -m ferenda.build lawreview parse              # incremental, like every source
```

A failing journal is reported and the run carries on with the rest (re-run
the failed scope on its own). Every journal but nmt keeps a harvest
watermark, so a caught-up run reads only the newest year page (svjt) or the
first listing page(s) and the newest issue's page instead of re-walking the
archive; nmt's two listing pages are the whole archive, so it enumerates
them every run.

The articles are not republished on the site: they are mined for the
references they make, which is what puts an article on the context rails of
the statute, förarbete or rättsfall it names. The article's rail line links
to the journal's own page for it, and the articles have no pages, feeds or
search index entries of their own. SvJT's document is the article's
own web page (a page exists for every article, 1916 and all); JP's is the
issue's PDF (its issue page carries the title, author and abstract). JP's
host rate-limits with HTTP 466, which the fetch waits out on its own.
Lov & Data's document is the article's own web page, but only its 2022 and
later volumes exist as pages — the earlier volumes are full-issue PDFs, and
the walk takes only the issues whose pages list articles. Its articles
carry no page numbers, so the identifier stops at the issue
("Lov & Data 3/2022") and the basefile's sequence number keeps the issue's
articles apart.

**lawpub — the platform scope** (operates on
`site/data/{downloaded,artifact}/lawreview/lawpub/`):

```sh
uv run python -m ferenda.build lawreview download lawpub  # the whole listing, newest first
uv run python -m ferenda.build lawreview download lawpub --only lawpub/880
uv run python -m ferenda.build lawreview parse            # incremental, the whole source
```

The platform is a single listing across seven publishers, so there is no
per-publisher scope. The walk stops on a harvest watermark, so a caught-up
run reads only the newest listing pages. Only open-access items are stored;
a locked ("Stängd") item has no PDF the platform will serve. The scope's
articles are lawreview documents: mined, unpublished, their rail lines on
the shared "Artiklar" row, each linking the platform's own page for the
article. Two of the platform's seven publishers — Förvaltningsrättslig
tidskrift (FT) and Stockholm IP Law Review (SIPLR) — are also harvested on
their own hosts by the `ft` and `siplr` scopes, so one article can arrive
twice, catalogued under two basefiles (`lawreview/ft/...` and
`lawreview/lawpub/...`); the shared row then shows both lines, and
de-duplication is an open decision.

**HUDOC + Council of Europe treaties + ICRC IHL treaties + UN Treaty Collection + ICC case law**:

```sh
uv run python -m ferenda.build coe download                 # all Treaty Office instruments
uv run python -m ferenda.build coe parse                    # official PDF text -> article artifacts
uv run python -m ferenda.build hudoc download               # judgments + decisions, then the Court's own
                                                                 # summaries and the Swedish translations
uv run python -m ferenda.build hudoc download decisions     # one collection (each has its own watermark)
uv run python -m ferenda.build hudoc download --limit 1000  # bounded: the two smaller harvests are skipped
uv run python -m ferenda.build hudoc parse
uv run python -m ferenda.build hudoc casenames               # rewrite hudoc/data/casenames.json from the stored records
uv run python -m ferenda.build icrc download                # all ICRC IHL treaties
uv run python -m ferenda.build icrc parse                   # JSON:API envelope -> article artifacts
uv run python -m ferenda.build untc download                # the 14 curated treaties: status page + authentic text
uv run python -m ferenda.build untc parse                   # both halves -> metadata, participation and articles
uv run python -m ferenda.build icc download                 # the curated ICC substantive decisions
uv run python -m ferenda.build icc parse                    # Legal Tools metadata + PDF -> article artifacts
uv run python -m ferenda.build icj download                 # judgments, advisory opinions, provisional-measures orders
uv run python -m ferenda.build icj parse                    # I.C.J. Reports PDF -> numbered-paragraph artifacts
uv run python -m ferenda.build all relate                   # joins HUDOC cases to CoE articles
```

`coe download` never touches the Cloudflare-fronted portal pages: it POSTs one
search to the Treaty Office's anonymous JSON web service
(`conventions-ws.coe.int`, token embedded in the public `full-list2` page,
mounted through `lib.net.mount_legacy_tls` for its small-DH-key TLS), which
returns all 233 treaties with metadata in that one response, then downloads
each official English text as a plain PDF from `rm.coe.int`. HUDOC itself is
directly harvestable off `/app/query/results` and needs no browser automation
either; its body fetches run through a small worker pool (`WORKERS=4` in
`hudoc/download.py`) since they are the whole cost of a harvest. `icrc
download` reads the ICRC's own anonymous Drupal 10 JSON:API
(`ihl-databases.icrc.org`) directly — one paginated list call enumerates the
111 treaties, one `include=`-expanded fetch per treaty returns the whole
envelope including the authentic article text, so there is no separate PDF
step and `icrc parse` never touches the network. `untc download` fetches each treaty **twice**, because no one publisher carries
both halves: the status from `treaties.un.org`'s `ViewDetailsIII.aspx` (dates,
UNTS registration, per-state participation — and no treaty text at all), and
the authentic text from the treaty's own depositary. Deliberately not from the
UNTS itself, which reproduces each instrument as registered and so is a scanned
corpus: volume 999 carries the ICCPR over 92 pages with an image on all 92, and
volume 1161 the Berne Convention over 44 of 44. OHCHR sits behind the same
Cloudflare challenge as the ICJ and un.org refuses the harvester's user agent on
the UNCLOS PDF, so every text comes through `lib.browser.DetachedChrome` — one
session for the run, about 9 s per treaty. `untc parse` reads both offline.
`icc download` also avoids the Cloudflare-fronted `/court-record` pages: it
facet-scrapes icc-cpi.int `/decisions` for the curated Rome-Statute decision
types to get each record's document number, then resolves that number
against the ICC Legal Tools API (`legal-tools.org/api/ltdDocs`) for metadata
and the decision PDF, so `icc parse` reads the stored Legal Tools record and
PDF text and never touches the network either. `icj download` is the one
folkrätt harvest that needs a browser: the `/decisions` index answers ordinary
HTTP, but every decision PDF under `/sites/default/files/case-related/` returns
a Cloudflare challenge that no header or cookie from the index clears, so the
bodies come through `lib.browser.DetachedChrome` — one headful session for the
whole run, about 9 s per document, ~40 minutes for the 255 in scope. Rerun
`tools/corpus/icj_vocabulary.py` after a harvest that adds a year of decisions: it
rebuilds `icj/data/vocabulary.txt`, the word list that guides the OCR repair of
the pre-2002 scans, and the file is a recipe input so a rebuild re-stales every
scanned decision.

**remisser — regeringen.se referral responses** (keyed on the referred
document, not the regeringen.se case-page slug; operates on
`site/data/{downloaded,artifact}/remisser/<typ>/` — an ärende record and its
answer PDFs share one download tree, `site/data/downloaded/remisser/<typ>/<id-slug>.json`
beside `site/data/downloaded/remisser/<typ>/<id-slug>/<org>.pdf`; never
`relate`d/`generate`d — see the module map above):

```sh
uv run python -m ferenda.build remisser download                    # harvest new ärenden + re-poll open ones
uv run python -m ferenda.build remisser download --only <arende-url>  # one ärende, bypassing the listing walk
uv run python -m ferenda.build remisser parse                       # incremental, like every source
uv run python -m ferenda.build remisser ai-analyze <typ>/<document id>/<org-slug>  # the sole LLM pass, one answer
uv run python -m ferenda.build remisser ai-analyze <typ>/<document id>              # whole ärende: every answer still lacking a layer
uv run python -m ferenda.build remisser ai-analyze --update                        # every analysed ärende still open: pick up answers that arrived since
```

**site — lagen.nu's editorial chrome** (frontpage / om / sitenews; parsed +
generated but never `relate`d/indexed/dumped — see the module map above):

```sh
uv run python -m ferenda.build site parse       # markdown -> artifacts, incremental
uv run python -m ferenda.build site generate     # rewrite just the editorial pages (write_site)
```

### Wiki content repo (begrepp + kommentar)

The hand-authored commentary (`kommentar`) and concept glossary (`begrepp`)
are **git-backed markdown** in a separate content repo (`lagen-wiki`),
checked out alongside this one and pointed at by `WIKI_ROOT`:

```sh
git clone <lagen-wiki remote> ../lagen-wiki    # or: git submodule update --init
uv run python -m ferenda.build begrepp parse
uv run python -m ferenda.build kommentar parse
```

`WIKI_ROOT` defaults to `../lagen-wiki` (a sibling of the repo); override it
with the `wiki_root` key in `config.yml` or the `WIKI_ROOT` env var. The
content layout is `concept/<Name>.md` (frontmatter `title:`) and
`commentary/<source>/<relpath>.md` (frontmatter `annotates:`) — the commentary
is filed under the source it annotates and that source's basefile→path rule, so
`SFS/1915:218` lives at `commentary/sfs/1915/218.md`. The parsed artifact mirrors
this — `site/data/artifact/kommentar/<host_source>/<host_relpath>.json` (e.g.
`site/data/artifact/kommentar/eurlex/2023/32023R2854.json`), reusing the host source's own
path transform (`layout.kommentar_host`) so commentaries on different sources can
never collide on one flat name. Concept links are
`[label](begrepp:Concept)`, external links are ordinary markdown
`[label](https://…)`, legal citations stay plain text (the citation engine links
them), and `aliases:` carries old names from MediaWiki redirects. The parser is
`lib/markdown.py`.

Each `## …` heading anchors the section to the host node it annotates, per host:

| heading | anchor | host |
|---|---|---|
| `## N §` | `#P{N}` | continuously-numbered SFS |
| `## N kap M §` | `#K{N}P{M}` | per-chapter SFS |
| `## Artikel N` | `#{N}` | EU act article |
| `## Artikel N.M` / `## Artikel N.M a` | `#{N}.{M}` / `#{N}.{M}.{a}` | EU sub-article (definition/list point) |
| `## Skäl N` or `## (N)` | `#recital-{N}` | EU recital |

`annotates:` is an SFS number (`2009:400`) or a CELEX (`32024R2847`); the host act
is resolved accordingly (`wiki.host_uri`). A section may carry prose **and** a
curated external-links list: a `## Externa länkar` bullet block attaches to the
section heading it sits under (per-article guidance, shown in that node's rail),
or to the act as a whole when it precedes any section heading (document-level,
shown in the "Om dokumentet" rail). Bullets are `- [label](https://…) — note`.

`lagen kommentar validate [basefiles…]` reports section anchors that match no node
in the annotated act (a mistyped `## Artikel 99` / amended-away `## 24 kap 2 §`);
the same check warns during `relate`.

`lagen kommentar ai-annotate <basefile>` (opt-in, LLM) is the AI guidance linker
(PRD Step 4). An annotation declares its external guidance documents by hand in a
`guidance:` frontmatter block — a list of `{title, url, pdf}` mappings, the `pdf:`
being the direct download link (a guidance doc is short-lived; the URL is not
derivable from the act):

```markdown
---
annotates: 32023R2854
guidance:
  - title: Frågor och svar om dataakten
    url: https://digital-strategy.ec.europa.eu/en/library/…-data-act
    pdf: https://ec.europa.eu/newsroom/dae/redirection/document/108144
---
## Externa länkar
- [Frågor och svar om dataakten (FAQ)](https://…) — Europeiska kommissionen
```

The `guidance:` block is authored by hand because the one thing no machine can
derive is the binding "*this document is guidance on **this** act*": a Commission
DG microsite carries no machine-readable link from a guidance PDF to the
legislation it explains (verified against Cellar / EUR-Lex / data.europa.eu — the
relation lives only in prose). `lagen kommentar propose-guidance <dg-page-url |
CELEX> [<CELEX>]` does the drudge around that judgement: given a guidance *page*
URL (e.g. `…/en/policies/data-act`) it scrapes that page for the act's EUR-Lex
reference (a cross-check against the optional CELEX) and the guidance/library
items it links, resolves each to its current
`newsroom/dae/redirection/document/NNNNN` PDF (that id is version-specific — it
changes on every FAQ revision, which is why it can't be authored once), and prints
a **draft `guidance:` block** to review and paste. A human still decides which
candidates are genuine guidance on the act (not the factsheets / impact
assessments / general policy the page also lists).

Given a **CELEX** instead of a URL, it looks the page(s) up in an index built by
`lagen kommentar discover-guidance`, which crawls the configured Commission
guidance sites' sitemaps (`guidance_discover.GUIDANCE_SITES` — only DG CONNECT's
`digital-strategy.ec.europa.eu/en/policies/<slug>` hubs follow an enumerable
per-act shape today; sibling DG sites stay manual) and records, per act CELEX, the
hub pages that link it (`site/data/artifact/kommentar/guidance-index.json`). The DG WAF
429s a random slice of every run, so the index **merges across runs and
converges** — re-run to fill the gaps, or `--force` for a clean authoritative
rebuild when the rate budget is fresh. So the usual flow is `discover-guidance`
once, then `propose-guidance <CELEX>` per act.

Guidance *published in the OJ* is a different animal — it gets its own sector-5
`XC`/`DC` CELEX and is machine-linked to the parent act in Cellar
(`work_cites_work` / `resource_legal_based_on_resource_legal`), so it belongs in
the corpus as an ordinary eurlex document, not as an external `.ann` link
(sector-5 harvest is not wired yet).

The action downloads + caches each PDF (under `site/data/downloaded/kommentar/guidance/`), flattens it
to page-marked text, and asks the configured Berget model to map guidance sections
(FAQ questions) to the act's **fine-grained targets** — not just whole articles but
the sub-articles and recitals the act divides into: a single definition `2.21`, a
numbered paragraph `6.2`, a recital `recital-15` (the dotted sub-article / `recital-N`
anchor grammar `lib.eu_structure` mints, shared with the renderer and the wiki
commentary headings, so a link lands on the exact node). A FAQ answer about two definitions links to exactly those two, not to
article 2 as a whole. The result is written as a **`.ann` layer** in the curated
store (`lib/annstore.py`, `WIKI_ROOT/ann/kommentar/…`, mirroring the kommentar
artifact's own relpath) — `{"guidanceLinks": {anchor: [{label, href, desc, section}]}}` —
the AI-created (then human-corrected) layer, kept separate from the hand-edited
markdown, mirroring eurlex's `.ann` editorial layer. `label` names the source and
its own section reference ("Frågor och svar om dataakten, question 8"), `desc` is
that section's title (the FAQ question), so the rail renders `link: question`. The
guidance document's own `section` (a FAQ question number) is the durable,
human-dereferenceable locator; the `#page=N` deep link is a convenience, located by
matching the section title back into the PDF (the model miscounts pages). Like every
`ai-*` action the LLM is called only here, never from a corpus-wide
parse/relate/generate. The `.ann` is woven into the annotated act's rail by
`page._kommentar_indexes` (it merges each kommentar `.ann`'s `guidanceLinks`
alongside the curated per-article guidance); a sub-article gets its citation anchor
+ rail only when something targets it, so a forced/full `generate` surfaces the AI
links on the right nodes.

A kommentar is a **separate source**: editing a `commentary/…md` file shows up on
the annotated act's page only after re-running the wiki pipeline and the catalog —
`lagen kommentar parse && lagen kommentar relate && lagen <host> generate
<basefile>` (e.g. `lagen eurlex generate 32024R2847`; the host's own
`parse`/`generate` stages never read the wiki).

The repo was seeded from the live MediaWiki SQLite DB, replaying the full
per-revision history as one git commit per revision:

```sh
uv run python tools/migrations/mediawiki_to_markdown.py path/to/lagen.sqlite ../lagen-wiki
uv run python tools/migrations/wiki_artifact_diff.py path/to/lagen.sqlite   # losslessness check
```

`wiki_artifact_diff.py` asserts the migration's safety property: for every
page, `markdown → artifact` is byte-identical to the old `wikitext →
artifact`, modulo two adjudicated, content-free normalisations (see the
script) plus one deliberate exception: a wikitext list line, which the old
parser read as literal prose with its marker left in the text (`# Numrerad
punkt …`), now reads as a `lista`/`punkt` artifact node instead — a content
*fix*, so `wiki_artifact_diff.py` reports it as a mismatch rather than
normalising it away.

`lib/wikitext.py` is retired from the pipeline and kept only as the
converter's/diff's reference.

### Site content (frontpage + om + sitenews)

lagen.nu's editorial chrome — the curated frontpage law list, the `/om/*`
about pages, and the sitenews feed — is likewise **git-backed markdown**,
alongside `concept/` and `commentary/` in the same `lagen-wiki` repo
(`WIKI_ROOT`):

```
site/frontpage.md      # ## <Category> headings + - [Label](sfs:…) bullets
site/om/<slug>.md       # one file per /om/<slug> about page
site/sitenews.md        # ## YYYY-MM-DD HH:MM:SS Title sections, newest content first
```

The markdown files are the source of truth. Edit them directly and commit the
content-repository change.

```sh
uv run python -m ferenda.build site parse    # markdown -> artifacts, incremental
uv run python -m ferenda.build site generate # rewrite the editorial pages
```

### Corpus statistics (`/statistik`)

```sh
uv run python -m ferenda.build stats compute   # measure the corpus (minutes)
uv run python -m ferenda.build stats generate  # render /statistik
```

`compute` reads the catalog and the sfs/eurlex/forarbete/dv artifact trees, so
it must run **after `relate`**. It is not incremental — every measurement is a
fact about the whole corpus, so there is no subset to refresh — and its stage
declares no per-document `inputs` and is marked `always=True`, so there is no
freshness gate: every invocation re-measures, `--force` or not, and archives a
dated copy under `artifact/stats/archive/`. `generate` raises if no artifact
has been computed; a statistics page without measurements would publish an
empty claim. `lagen all rebuild` runs `compute` automatically on a whole-corpus
run, between `dump` and `generate` (not on a single-source rebuild).

**Measure 56 needs the EU amendment relations first.** "Längst mellan två
författningar" walks base acts only, and it knows which EU acts merely amend or
implement another one from the `rpubl:andrar` / `rinfoex:genomforRattsakt`
links parse mints off each act's `notice.ttl`. A corpus harvested before those
relations existed carries none, and `compute` raises rather than publishing the
71-step al-Qaida sanctions ladder under a lede saying amending acts do not
count. Give the corpus its relations once:

```sh
lagen eurlex refresh-metadata --all      # rewrites every notice.ttl, no content refetched
lagen eurlex parse --force               # the relations reach the artifact
lagen all relate                         # and the catalog
```

`--all` is the point: without it `refresh-metadata` walks only the documents
whose notice records no repeal, which is right for a repeal audit (a repeal
never lifts, so the audit shrinks) and wrong for a new metadata field — a
repealed act amends things too. The refresh is metadata-only, one SPARQL round
trip per chunk, about 15 minutes for 64 043 documents. The endpoint throttles
under a long run, so re-run it if it stops short.

