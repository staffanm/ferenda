"""The source/stage protocol every source registers itself through.

Two dataclasses (`Stage`, `Source`), the registry dict they land in
(`SOURCES`), the run-wide options the driver sets once (`RunOptions` / `RUN`),
and the handful of shape helpers several sources share. `build.py` fills
`SOURCES` at import time; the freshness engine and the CLI read it back.

There is no base class and no subclassing: a source is a program that
registers data (rule:sources-are-programs).
"""

import functools
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import compress, datasets, layout, patch

PKG = Path(__file__).parent.parent   # the ferenda package root

POLITENESS = 0.3   # seconds between per-document network fetches

# The hand-edited tables the citation scan reads: the named-law/abbreviation
# table every LagrumParser is built from and emdref's Swedish respondent-state
# names. An edit to one changes parse output exactly like a grammar edit, so it
# rides every recipe that carries lagrum.py/emdref.py -- the same policy
# treaty_names.json gets on the treaty-linking recipes. It lives here, with the
# protocol, because eleven sources' recipes name it and no one of them owns it.
#
# The *derived* snapshots the same scan reads are deliberately NOT recipe
# inputs: emdref's ECHR case registry (datasets.EMD_CASES, `hudoc casenames`),
# the JO ämbetsberättelse page table (datasets.JO_ARSBERATTELSE, `avg
# arsberattelse`) and the case-number index (datasets.CASENUMBERS, rewritten by
# every full-source `dv parse`). Each grows with its corpus, and hashing it
# would reparse eleven (or six) sources in full every time a harvest adds a
# decision -- a day on prod. A newly held decision is new: a document parsed
# after the refresh resolves it, and an already-parsed document that cited it
# before we held it reaches the link only at the next code-staleness or
# --force pass, the way dv's identity index already works (dv/source.DV_CODE).
CITATION_DATA = (datasets.NAMEDLAWS, datasets.EMD_RESPONDENTS)
# The case-number matcher, on the recipes of the sources that can actually
# request MALNUMMER (`lagrum.ALL_PARSE_TYPES`: dv, forarbete, avg, rs,
# lawreview, wiki). Kept out of CITATION_DATA because the
# sfs/eurlex/foreskrift/guidance parsers never ask for that parse type.
CASENUMBER_CODE = (PKG / "lib" / "malnummer.py",)


# --------------------------------------------------------------------------
# stage / source protocol
# --------------------------------------------------------------------------

@dataclass
class Stage:
    name: str
    run: Callable[[str], None]            # recipe: read inputs, write output
    output: Callable[[str], Path]         # basefile -> produced file
    inputs: Callable[[str], list[Path]] = lambda bf: []   # dependency files
    depends: str | None = None            # upstream stage name
    code: tuple[Path, ...] = ()           # impl files; their hash = version
    # never fresh: the stage's real inputs are the whole corpus, too large to
    # hash, so the driver cannot answer "has anything changed" and must not
    # pretend it can. Without this a no-inputs stage is judged on its recipe
    # hash alone -- constant between edits -- so it runs once and is skipped
    # for ever after, silently freezing its output (see `stats compute`).
    always: bool = False
    # the corpus verb a rebuild runs this stage after. The default keeps a
    # stage in the rebuild's leading parse/versions loop; `phase="dump"` runs
    # it after the dump verb (`stats compute` measures the catalog relate has
    # just rebuilt, so it cannot run in that loop -- see corpus.cmd_all).
    phase: str = "parse"
    # override the source's own list_basefiles for this stage only -- a stage
    # whose real unit of work is finer than "one basefile" (sfs/eurlex
    # versions: one archived consolidation, not one statute/act) dispatches
    # over its own finer-grained key instead, so each one is independently
    # freshness-checked and spread across the pool, rather than one worker
    # serially parsing every consolidation of one document alone (2026-09-04:
    # sfs versions' worst case, inkomstskattelagen's ~100 versions, measured
    # 1,454s single-threaded on one worker while 31 others sat idle). None
    # (the default) means every other stage's behaviour is unchanged. Keys
    # are "<coarse-name>@<sub-key>" by convention -- build.py's CLI dispatch
    # expands a bare coarse name given on the command line ("lagen sfs
    # versions 1999:1229") to every one of this stage's own keys under it.
    list_basefiles: Callable[[], list[str]] | None = None


def stage_basefiles(source: "Source", stage_name: str) -> list[str]:
    """The keys a corpus verb dispatches over for `source`'s `stage_name`:
    the stage's own `list_basefiles` if it set one, else the source's."""
    stage = source.stages[stage_name]
    return stage.list_basefiles() if stage.list_basefiles else source.list_basefiles()


def fanout_key(basefile: str, sub: str) -> str:
    """The dispatch key of one sub-item of a fan-out stage (`Stage.list_basefiles`):
    "<basefile>@<sub>" -- an archived consolidation of a statute, a dated
    consolidation of an EU act."""
    return "%s@%s" % (basefile, sub)


def split_fanout_key(key: str) -> tuple[str, str]:
    """`(basefile, sub)` of a fan-out key -- the inverse of `fanout_key`."""
    basefile, sub = key.split("@", 1)
    return basefile, sub


def stage_keys(source: "Source", stage_name: str, basefiles: list[str]) -> list[str]:
    """The keys `stage_name` dispatches for the named documents -- what a
    targeted run ("lagen sfs versions 1999:1229", or the versions prerequisite
    of a targeted generate) expands each given name to: every key under it for
    a fan-out stage, the names themselves for any other stage. A name the
    source does not list is an error, not an empty run -- a typo used to fail
    its one document, and must not become a clean zero-document run
    (rule:fail-fast)."""
    stage = source.stages[stage_name]
    if not stage.list_basefiles:
        return list(basefiles)
    unknown = sorted(set(basefiles) - set(source.list_basefiles()))
    if unknown:
        raise ValueError("%s: no such document(s): %s"
                         % (source.name, ", ".join(unknown)))
    given = set(basefiles)
    return [k for k in stage.list_basefiles() if split_fanout_key(k)[0] in given]


@dataclass
class Source:
    name: str
    list_basefiles: Callable[[], list[str]]
    stages: dict[str, Stage]
    harvest: Callable[[list], None] | None = None  # bulk download (discovery)
    origin: str | None = None             # human base URL, shown when harvesting
    self_banner: bool = False             # source prints its own per-subtype
                                          # "<src> <sub>: Starting at <url>" lines
                                          # (forarbete); dispatcher stays silent
    actions: dict[str, Callable[..., object]] = field(default_factory=dict)  # name -> verb(args)
    scopes: frozenset[str] = field(default_factory=frozenset)  # harvest sub-corpora
    notes: str = ""                       # extra `lagen <src> -h` help (flags etc.)
    # corpus-level hooks keyed by verb name, run once per source after that
    # verb's sweep over it (dv reconciles its artifact tree after parse)
    after: dict[str, tuple[Callable[[], None], ...]] = field(default_factory=dict)
    render: Callable | None = None        # the source's own page renderer
                                          # `render(art, site) -> str`
    # the published-artifact lister: what relate/index/dump/generate read.
    # None = the source catalogues no documents (remisser, site, stats)
    artifacts: Callable[[], list[Path]] | None = None
    searchable: bool = True               # False: relate the source but hold no
                                          # search units for it (kommentar, lawreview)
    # rows for pages generate renders that have no catalog row of their own
    # (sfs/eurlex lydelser, föreskrift /grund); the argument is the run's
    # `only` set of artifact paths, or None for the whole source
    extra_pages: Callable[[set[str] | None], list] | None = None
    # a whole-source page writer for a source whose pages are not catalog rows
    # (site's editorial chrome, stats' /statistik): `write_pages(dest, *,
    # whole_corpus)`
    write_pages: Callable[..., None] | None = None
    owns_frontpage: Callable[[], bool] | None = None  # the source writes its own
                                          # frontpage, so generate writes no generic one
    # the source's part of relate's cross-document block, given the open
    # catalog. Returns `(counts, warnings)`: `{report label: count}` prints as
    # "relate: <n> <label>", each warning is a finished line relate prints
    relate_cross: Callable[[sqlite3.Connection],
                           tuple[dict[str, int], list[str]]] | None = None
    # the source's own code behind `relate_cross`: an edit re-runs only the
    # cross-document block (it joins corpus._corr_watermark beside CORR_CODE),
    # not every document's extraction
    cross_code: tuple[Path, ...] = ()
    # every side file outside the catalog that the source's pages or
    # cross-passes read: authored .ann/.corr layers, versions-stage sidecars,
    # the artifacts of a source that catalogues none. Both the cross-block gate
    # and generate's coarse gate fold them in, so authoring, regenerating or
    # hand-editing one reopens both (corpus._layers)
    layers: Callable[[], list[Path]] | None = None
    # the pristine intermediate-text provider and the human label of the
    # format a patch edits (`patchsource.intermediate` returns the pair): the
    # plain statute text for sfs, the Formex XML for eurlex, pdftohtml XML for
    # the PDF-bodied ones. Set by the sources whose parser calls `patch.apply`
    # at a choke point; None means the source is not text-patchable.
    intermediate: tuple[Callable[[str], str], str] | None = None
    # the module that registered this source (its `<package>/source.py`),
    # filled in by build.py's registration loop. `searchable` is declared
    # there, and that flag is part of the index step's recipe -- so an
    # unsearched source's index step fingerprints its own registration and
    # flipping the flag restales exactly that source (lib/corpus.cmd_index).
    registration: tuple[Path, ...] = ()


SOURCES: dict[str, Source] = {}


def origin(url):
    """The scheme://host/ base of an endpoint, for the harvest banner."""
    parts = urlsplit(url)
    return "%s://%s/" % (parts.scheme, parts.netloc)


@functools.cache
def session(download_mod):
    """One HTTP session per download module, for a source's per-document
    stages and actions: built from the module's own `make_session` and
    `USER_AGENT`, cached so one run shares one connection pool per host."""
    return download_mod.make_session(download_mod.USER_AGENT)


def patch_input(source, basefile):
    """The document's patch file as a 0/1-element list, to fold into a source's
    freshness inputs -- so editing a patch re-stales that document's parse (the
    patch is a genuine parse input). Text-patchable sources add this to `inputs`."""
    patchfile = patch.find_patch(source, basefile)[0]
    return [patchfile] if patchfile else []


def write_artifact(source, basefile, art, source_url=None):
    """Serialize a parsed artifact, stamping the one uniform `source_url` key
    that the renderer turns into the page's "Källa" link. The url is resolved
    here, once, for every source -- the single point where a downloader and a
    parser cooperate to supply it, in precedence order:

      1. one the parser set explicitly on the artifact (art["source_url"]);
      2. `source_url` recorded by the downloader (the real fetched/landing
         location -- passed in by the parse run that read the record);
      3. one layout derives by rule from the document's identity (e.g. an EU
         act's ELI from its CELEX).

    A document with none simply carries no source_url and its page omits the
    link."""
    url = (art.get("source_url") or source_url
           or layout.source_url(source, basefile))
    if url:
        art["source_url"] = url
    write_artifact_to(layout.artifact(source, basefile), art)


def write_artifact_to(path, art):
    """Write an artifact dict to an explicit path, in the artifact tree's one
    serialization (stable key order and indentation, so two builds diff
    readably; the tree's compression -- `compress.write_json`). Split out of
    `write_artifact` so a copy written somewhere other than the document's own
    path -- the dated stats snapshot -- is the same bytes by construction."""
    compress.write_json(path, art)


# --------------------------------------------------------------------------
# source-shape helpers. A source is a program, not a subclass -- but several of
# them are the *same* program over different data (the download module, the
# parser, the download root, the recipe files, the help text), and a few checks
# recur across otherwise unrelated ones. Those are configured here rather than
# copied per source; anything a source actually does differently stays its own
# code (rule:sources-are-programs -- variation as data, never as a base class).
# --------------------------------------------------------------------------

def require_single_scope(name, scopes, noun, example):
    """Refuse `--only` unless exactly one scope says where to look. `--only`
    names one document; with no scope (or several) the harvester would walk
    every listing to find it, which is never what the flag is asking for."""
    if RUN.only and len(scopes) != 1:
        sys.exit("%s --only needs exactly one %s scope, e.g. `%s`"
                 % (name, noun, example))


def record_inputs(source, *per_document, extra=()):
    """The `inputs` callable of a source whose parse reads a fixed handful of
    files per document -- the harvested record, the body it names, a sidecar --
    plus any constant `extra` (a curated data file the parser is configured by)
    and the document's own patch. Six sources' whole freshness input set, which
    is why the five-line function is written once here rather than per source.

    Each `per_document` entry maps a basefile to a path; a downloader's
    `record_path(root, basefile)` becomes one through `functools.partial`."""
    return lambda bf: ([f(bf) for f in per_document] + list(extra)
                       + patch_input(source, bf))


def sum_scope_totals(totals):
    """`(seen, changed)` across a `{scope: (seen, new)}` map -- what the harvests
    that sweep several sub-corpora at once (avg's five organs, rs's and
    föreskrift's agencies, edpb's three series) get back from their sync. They
    print a line per scope for a person and report the sum to the ledger."""
    return (sum(seen for seen, _new in totals.values()),
            sum(new for _seen, new in totals.values()))


def scoped_harvest(name, download_mod, root, scopes, *, noun, example, label,
                   report=True, **sync):
    """The bulk harvest of a source whose corpus is a set of independent
    sub-corpora on separate hosts (föreskrift's agencies, avg's organs, rs's
    myndigheter, guidance's issuers, lawreview's journals): refuse an `--only`
    that names no single scope, honour `--dry-run`, run the module's own
    `sync`, report a line per scope and return the run's totals.

    `noun` and `example` word the `--only` refusal, `label` names what an
    unscoped dry run would fetch, and `sync` carries the keywords this
    downloader takes beyond the shared four (`limit`, `jobs`) -- lawreview
    takes no `jobs` (it fans out one worker per journal), föreskrift no
    `limit`. `report=False` is for a sync that prints each scope's summary
    itself (föreskrift's)."""
    require_single_scope(name, scopes, noun, example)
    if RUN.dry_run:
        print("%s download: would download %s into %s"
              % (name, RUN.only or ", ".join(scopes) or label, root))
        return
    totals = download_mod.sync(root, scopes=scopes or None, full=RUN.force,
                               only=RUN.only, **sync)
    if report:
        for scope, (seen, new) in totals.items():
            print("%s %s: %d seen, %d new" % (name, scope, seen, new))
    return sum_scope_totals(totals)


def parse_stage(name, parse_fn, root, *, inputs, code):
    """The parse stage of a source whose parser turns (basefile, download root)
    into a finished artifact in one call -- eight sources' entire parse recipe,
    which is why neither the recipe nor the artifact path is worth a named
    two-line function apiece."""
    return Stage("parse", lambda bf: write_artifact(name, bf, parse_fn(bf, root)),
                 functools.partial(layout.artifact, name),
                 inputs=inputs, code=code)


def simple_source(name, download_mod, parse_fn, root, code, *, inputs, origin,
                   notes, dry_label, render, artifacts):
    """A source whose whole chain is the common shape: one bulk
    ``sync(root, full=, only=, limit=, delay=)`` over a publisher's own list of
    instruments, and a parse that reads the stored record(s) into an artifact in
    one call. No sub-scopes, no per-document download stage, no extra actions.

    `dry_label` names what a `--dry-run` would fetch. A source whose sync takes
    anything beyond the shared five keeps its own registration (hudoc does, for
    its `--lang` and its two collection scopes) -- this is a shape shared by
    several sources, not a base class to bend."""

    def harvest(_scopes):
        if RUN.dry_run:
            print("%s download: would download %s into %s"
                  % (name, RUN.only or dry_label, root))
            return
        seen, changed = download_mod.sync(
            root, full=RUN.force, only=RUN.only, limit=RUN.limit,
            delay=POLITENESS)
        print("%s download: %d seen, %d changed" % (name, seen, changed))
        return seen, changed

    return Source(name, lambda: download_mod.list_basefiles(root),
                  {"parse": parse_stage(name, parse_fn, root,
                                         inputs=inputs, code=code)},
                  harvest=harvest, origin=origin, notes=notes,
                  render=render, artifacts=artifacts)


# run-wide options, set once in main() (kept off the recursion signature)
@dataclass
class RunOptions:
    dry_run: bool = False
    force: bool = False
    verbose: bool = False   # -v: stream per-step progress to stderr (the long
                            # ai-* vision passes otherwise run silent for minutes)
    no_deps: bool = False
    ignore_code_changes: bool = False  # skip the recipe-version check (dev:
                                       # don't rebuild all when parse code changes)
    aggregates_only: bool = False  # generate: only the corpus-wide pages
    assets_only: bool = False    # generate: only copy the static css/js chrome
    since: date | None = None    # eurlex: discovery floor (overrides watermark)
    lang: str | None = None      # eurlex/hudoc: comma-separated languages
    source: str = "sparql"       # eurlex: discovery backend (sparql|soap)
    only: str | None = None      # source-specific targeted download
    riksmote: str | None = None  # forarbete bet: narrow the harvest to one riksmöte
    limit: int | None = None     # harvest/import cap (a test/backfill slice)
    obfuscated: bool = False     # mkpatch: obfuscate the patch (PII redactions)
    resume_after: str | None = None  # sfs download: resume an interrupted backfill
    rebuild_history: bool = False  # sfs history-as-git: rewrite main from corpus
    every: bool = False            # eurlex refresh-metadata: the whole corpus,
                                   # repealed acts included, not just the audit
    update: bool = False         # remisser ai-analyze: refresh every open ärende
    matching: str | None = None  # remisser ai-analyze: select ärenden by basefile prefix
    jobs: int = 1                # worker count for harvests that fan out (foreskrift)


RUN = RunOptions()


def set_run(options):
    """Rebind the run-wide options. Every reader reaches them as `stage.RUN`, so
    a rebinding here is seen everywhere -- which is what a pool worker needs:
    it re-imports the module fresh and `_worker_init` installs the parent's
    options through this."""
    global RUN
    RUN = options
