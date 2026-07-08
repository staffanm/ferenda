"""Make-like incremental build driver for the new pipeline.

    lagen <source> <action> [basefile...] [flags]

A *source* (sfs, dv, …) is a small program that registers a few *stages*
(download → parse → relate → generate). The driver owns the verbs, the
freshness logic and `all`/parallelism; it knows nothing source-specific.
Each stage is a pure function `inputs(basefile) → output(basefile)` with a
recipe; the driver runs a recipe only when its output is stale.

Freshness is content-based, not mtime-based: a stage is fresh when its
output exists and the manifest records the same input hash *and* the same
recipe version (a hash of the stage's own implementation files, so editing
the parser re-stales every doc without a blanket --force). Asking for a
downstream action brings stale upstream stages up to date first (make
semantics); `--no-deps` scopes to just the named stage.

    lagen sfs parse 2018:585        # parse one statute (download must exist)
    lagen sfs parse                 # every stale SFS basefile
    lagen dv parse -j8              # all court decisions, 8 workers
    lagen sfs status                # per-stage fresh/stale/missing counts
    lagen all parse -n              # dry-run: print the plan, do nothing
    lagen all rebuild               # parse→relate→index→dump→generate (offline)
    lagen all all                   # download too, then rebuild — full sync

The parallelisable steps (parse, index) default to all CPU cores; `-j1`
serialises. relate is single-writer (SQLite) and always serial.

(Also runnable as `python -m accommodanda.build …`.)
"""

import argparse
import functools
import hashlib
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import requests

from . import config, patchsource
from .api import app as api_app
from .api import edit as api_edit
from .api import patch as api_patch
from .avg import download as avg_download
from .avg import legacy as avg_legacy
from .avg import parse as avg_parse
from .dv import download as dv_download
from .dv import identity as dv_identity
from .dv import namedcases as dv_namedcases_mod
from .dv.parse import api_member, parse_api_record, to_artifact
from .eurlex import annotate as eurlex_annotate
from .eurlex import bulk as eurlex_bulk
from .eurlex import download as eurlex_download
from .eurlex import parse as eurlex_parse
from .forarbete import download as fa_download
from .forarbete import genomforande as fa_genomforande
from .forarbete import kommentar as fa_kommentar
from .forarbete import legacy as fa_legacy
from .forarbete import parse as fa_parse
from .forarbete import riksdagen as fa_riksdagen
from .foreskrift import download as foreskrift_download
from .foreskrift import harvest as foreskrift_harvest_mod
from .foreskrift import legacy as foreskrift_legacy
from .foreskrift import parse as foreskrift_parse
from .foreskrift.agencies import REGISTRY as FORESKRIFT_AGENCIES
from .lib import (
    casenaming,
    catalog,
    dump,
    layout,
    markdown,
    patch,
    render,
    runlog,
    search,
    util,
)
from .lib.datasets import NAMEDCASES as NAMEDCASES_JSON
from .lib.datasets import NAMEDLAWS as NAMEDLAWS_JSON
from .lib.errors import SkipDocument
from .lib.lagrum import LagrumParser, load_namedlaws
from .remisser import ai_analyze as remisser_analyze
from .remisser import download as remisser_download
from .remisser import model as remisser_model
from .remisser import parse as remisser_parse
from .sfs import correspond as sfs_correspond
from .sfs import download as sfs_download
from .sfs import load_inputs
from .sfs import versions as sfs_versions_mod
from .sfs.nf import to_normalform
from .site import parse as site_parse
from .site import render as site_render
from .wiki import annotate as wiki_annotate
from .wiki import guidance_discover
from .wiki import parse as wiki_parse

POLITENESS = 0.3   # seconds between per-document network fetches
DATA = config.DATA                            # corpus location (config.yml: data_root)
MANIFEST = DATA / ".build" / "manifest.json"
WATERMARKS = DATA / ".build" / "watermarks.json"   # small per-(step,source) gates
RUNS = DATA / ".build" / "runs.ndjson"             # append-only run ledger
ERRORS = DATA / ".build" / "errors.json"           # per-doc latest-outcome store
STATUS = DATA / ".build" / "status.json"           # rolling health snapshot
CATALOG = DATA / "catalog.sqlite"
GENERATED = layout.GENERATED
DUMPS = DATA / "dumps"                         # NDJSON bulk exports


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
    code: tuple = ()                      # impl files; their hash = version


@dataclass
class Source:
    name: str
    list_basefiles: Callable[[], list]
    stages: dict                          # name -> Stage
    harvest: Callable[[list], None] | None = None  # bulk download (discovery)
    origin: str | None = None             # human base URL, shown when harvesting
    actions: dict = field(default_factory=dict)  # name -> source-specific verb()
    scopes: frozenset = field(default_factory=frozenset)  # harvest sub-corpora
    notes: str = ""                       # extra `lagen <src> -h` help (flags etc.)


SOURCES: dict = {}


def _origin(url):
    """The scheme://host/ base of an endpoint, for the harvest banner."""
    parts = urlsplit(url)
    return "%s://%s/" % (parts.scheme, parts.netloc)


def _patch_input(source, basefile):
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
           or layout.source_url(source, basefile, art.get("metadata")))
    if url:
        art["source_url"] = url
    layout.artifact(source, basefile).write_text(
        json.dumps(art, ensure_ascii=False, indent=2, sort_keys=True))


# --------------------------------------------------------------------------
# freshness
# --------------------------------------------------------------------------

def hash_files(paths):
    """Content hash over the existing files in `paths` (order-independent
    in declaration but name-tagged so a rename counts)."""
    h = hashlib.sha256()
    for p in sorted(map(Path, paths), key=str):
        if p.exists():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


@functools.cache
def recipe_version(code):
    return hash_files(code) if code else "0"


def manifest_key(source, stage, basefile):
    return "%s/%s/%s" % (source, stage, basefile)


def is_fresh(manifest, source, stage, basefile, inputs_hash=None):
    out = stage.output(basefile)
    if not out.exists():
        return False
    if not stage.inputs(basefile) and not stage.code:
        # nothing to version the output against (e.g. download: the "input" is
        # a remote service, not a file) -- an existing output is by definition
        # up to date, whether the driver or the bulk harvester produced it
        return True
    entry = manifest.get(manifest_key(source.name, stage.name, basefile))
    if inputs_hash is None:
        inputs_hash = hash_files(stage.inputs(basefile))
    return bool(entry) \
        and entry["inputs"] == inputs_hash \
        and (RUN.ignore_code_changes
             or entry["version"] == recipe_version(stage.code))


def code_changed(store, kind, source, code):
    """Whether `source`'s extraction/index code changed since its last <kind> run
    (relate/index/parse/generate -- the steps gated by the coarse watermark store,
    not the per-doc manifest). True forces a full rebuild of that source, the same
    recipe-version rule parse/generate use per-doc, so editing catalog.py /
    search.py / text.py / render.py re-stales the step without a blanket --force;
    `--ignore-code-changes` pins it fresh. Keyed per source so a partial run can't
    mark another source current."""
    if RUN.ignore_code_changes:
        return False
    entry = store.get(manifest_key(kind, "__code__", source))
    return not entry or entry["version"] != recipe_version(code)


def record_code_version(store, kind, source, code):
    store[manifest_key(kind, "__code__", source)] = {
        "version": recipe_version(code)}


def file_watermark(paths):
    """A cheap, content-insensitive fingerprint of a file set: each path with its
    size + mtime, no contents read. Detects any add / remove / rewrite (parse
    rewrites an artifact, bumping its mtime), so relate/dump can skip a source
    whose artifacts are all untouched since last run -- instead of re-reading and
    re-hashing every file. --force or a code-version change overrides it."""
    h = hashlib.sha256()
    for p in paths:                          # ARTIFACTS yields them already sorted
        st = p.stat()
        h.update(("%s\x1f%d\x1f%d\x1e" % (p, st.st_size, st.st_mtime_ns)).encode())
    return h.hexdigest()


def stage_watermark(source, stage_name):
    """A cheap fingerprint of a per-document stage's inputs (parse, versions):
    each basefile plus its input files' size+mtime (no content read). Unchanged
    ⟹ no document needs re-running and none appeared, so the whole per-document
    freshness scan (which content-hashes every input) can be skipped. Basefiles
    are folded in so a newly-downloaded doc whose input doesn't exist yet still
    moves the mark."""
    stage = source.stages[stage_name]
    h = hashlib.sha256()
    for bf in source.list_basefiles():
        h.update(bf.encode())
        for p in sorted(stage.inputs(bf), key=str):
            # the per-source stage protocol is untyped; inputs() yields Paths
            if p.exists():  # ty: ignore[unresolved-attribute]
                st = p.stat()  # ty: ignore[unresolved-attribute]
                h.update(("\x1f%d\x1f%d" % (st.st_size, st.st_mtime_ns)).encode())
        h.update(b"\x1e")
    return h.hexdigest()


def watermark_fresh(store, kind, source, wm):
    """Whether `source`'s last <kind> run saw the same input watermark -- i.e. no
    input changed since, so the whole step can be skipped (combined with a code
    check and --force by the caller)."""
    entry = store.get(manifest_key(kind, "__wm__", source))
    return bool(entry) and entry["wm"] == wm


def record_watermark(store, kind, source, wm):
    store[manifest_key(kind, "__wm__", source)] = {"wm": wm}


def up_to_date(store, kind, source, wm, code):
    """A relate/index/dump/parse/generate step can be skipped for `source` when
    neither its inputs (watermark) nor its code changed and --force isn't set."""
    return (not RUN.force and not code_changed(store, kind, source, code)
            and watermark_fresh(store, kind, source, wm))


def record_step(store, kind, source, wm, code):
    record_watermark(store, kind, source, wm)
    record_code_version(store, kind, source, code)


# --------------------------------------------------------------------------
# the driver
# --------------------------------------------------------------------------

@dataclass
class Result:
    planned: list = field(default_factory=list)   # (stage, basefile)
    done: list = field(default_factory=list)
    errors: list = field(default_factory=list)     # (stage, basefile, msg, tb)
    updates: dict = field(default_factory=dict)    # manifest key -> entry
    skips: list = field(default_factory=list)      # (stage, basefile) SkipDocument
    fresh: list = field(default_factory=list)      # (stage, basefile) skipped as fresh
    timings: list = field(default_factory=list)    # (stage, basefile, secs)


def ensure(source, stage_name, basefile, manifest, res, force, no_deps):
    """Bring (stage, basefile) up to date, recursing into its dependency
    first (unless --no-deps). `force` applies to the named stage only; the
    dependency is still freshness-checked. Returns True on success."""
    stage = source.stages[stage_name]
    if stage.depends and not no_deps:
        if not ensure(source, stage.depends, basefile, manifest, res,
                      False, no_deps):
            return False
    # hash once: the freshness check and the post-run manifest entry use the
    # same digest (the recipe reads the inputs, never writes them)
    inputs_hash = hash_files(stage.inputs(basefile))
    if not force and is_fresh(manifest, source, stage, basefile, inputs_hash):
        # fresh ⟹ a valid, up-to-date artifact exists (is_fresh checks output
        # existence), so the doc is not failing -- heal any stale error left by an
        # earlier transient failure (e.g. an input momentarily missing) without a
        # --force re-parse. report() folds res.fresh into the error-clear set.
        res.fresh.append((stage_name, basefile))
        return True
    res.planned.append((stage_name, basefile))
    if RUN.dry_run:
        return True
    t0 = time.perf_counter()
    try:
        stage.output(basefile).parent.mkdir(parents=True, exist_ok=True)
        stage.run(basefile)
    except SkipDocument:
        # a deliberately empty document (removed/expired): write an empty
        # artifact so it is considered built and not retried every run
        stage.output(basefile).write_bytes(b"")
        res.skips.append((stage_name, basefile))
    except Exception as e:  # noqa: BLE001 — per-doc resilience point: recorded in res.errors, run continues (rule:no-catch-log-continue)
        res.errors.append((stage_name, basefile, "%s: %s"
                           % (type(e).__name__, e), traceback.format_exc()))
        return False
    res.timings.append((stage_name, basefile, time.perf_counter() - t0))
    res.updates[manifest_key(source.name, stage_name, basefile)] = {
        "inputs": inputs_hash,
        "version": recipe_version(stage.code)}
    res.done.append((stage_name, basefile))
    return True


# run-wide options, set once in main() (kept off the recursion signature)
@dataclass
class RunOptions:
    dry_run: bool = False
    force: bool = False
    no_deps: bool = False
    ignore_code_changes: bool = False  # skip the recipe-version check (dev:
                                       # don't rebuild all when parse code changes)
    aggregates_only: bool = False  # generate: only the corpus-wide pages
    since: date | None = None    # eurlex: discovery floor (overrides watermark)
    lang: str | None = None      # eurlex: comma-separated languages
    source: str = "sparql"       # eurlex: discovery backend (sparql|soap)
    only: str | None = None      # forarbete: fetch a single document
    riksmote: str | None = None  # forarbete bet: narrow the harvest to one riksmöte
    limit: int | None = None     # import-legacy (avg/forarbete): cap the run (a slice)
    rot13: bool = False          # mkpatch: obfuscate the patch (PII redactions)


RUN = RunOptions()

# The current invocation's run id, minted once in main() for a pipeline action
# (workers never need it, so it stays off RunOptions). INVARIANT: no run id ⇒
# every ledger/errors emission is a no-op -- the single rule that covers
# --dry-run, serve and runs, so no guard has to be scattered around the choke
# points. The ledger/errors emissions go through the three helpers below;
# `status` is the deliberate exception -- it carries no run id yet writes
# status.json's authoritative snapshot cell directly (see cmd_status), so it
# does not route through them. RUN_ERRORS accumulates THIS run's segment error
# total (not the per-source currently-failing count report() prints) for the
# run-end verdict.
RUN_ID = None
RUN_ERRORS = 0


def _emit_segment(step, source, secs, *, total=None, ran=None, errors=0,
                  skipped_fresh=0, skipdoc=0, status, slowest=()):
    global RUN_ERRORS
    if RUN_ID is None:
        return
    RUN_ERRORS += errors
    runlog.emit_segment(RUNS, RUN_ID, step, source, secs, total=total, ran=ran,
                        errors=errors, skipped_fresh=skipped_fresh,
                        skipdoc=skipdoc, status=status, slowest=slowest)


def _apply_outcomes(source, errors, done):
    if RUN_ID is None:
        return
    runlog.apply_outcomes(ERRORS, source, errors, done, RUN_ID)


def _reconcile_orphans(source, valid):
    if RUN_ID is None:
        return
    runlog.reconcile_orphans(ERRORS, source, set(valid))


def _update_status_cell(source, stage, cell):
    if RUN_ID is None:
        return
    runlog.update_status_cell(STATUS, source, stage, cell)


def build_one(source, action, basefile, manifest):
    res = Result()
    ensure(source, action, basefile, manifest, res, RUN.force, RUN.no_deps)
    return res


def _worker(job):
    source_name, action, basefile = job
    return build_one(SOURCES[source_name], action, basefile, _WORKER_MANIFEST)


_WORKER_MANIFEST: dict = {}


def _worker_init(manifest, run_options):
    # child processes re-import this module fresh -- carry the run options
    # and the pre-run manifest snapshot across the process boundary
    global _WORKER_MANIFEST, RUN
    _WORKER_MANIFEST = manifest
    RUN = run_options


def _progress(source, action, done, total, merged, basefile):
    """Live one-line counter on stderr (the shared util.status pattern), carrying
    the source/action, the running counts, and the most recently completed
    basefile."""
    verb = "planned" if RUN.dry_run else "ran"
    count = len(merged.planned) if RUN.dry_run else len(merged.done)
    util.status(done, total, "%s %s  %s %d  err %d  %s"
                % (source, action, verb, count, len(merged.errors), basefile))


SAVE_EVERY = 1000      # checkpoint the manifest mid-run, every this many docs


def run_action(source, action, basefiles, jobs):
    manifest = load_manifest()
    merged = Result()
    total = len(basefiles)
    done = 0

    def persist():
        if merged.updates and not RUN.dry_run:
            manifest.update(merged.updates)
            save_manifest(manifest)

    def absorb(res, basefile):
        nonlocal done
        _absorb(merged, res)
        done += 1
        _progress(source.name, action, done, total, merged, basefile)
        if done % SAVE_EVERY == 0:
            persist()       # checkpoint so a kill mid-run doesn't lose progress

    try:
        if jobs > 1 and not RUN.dry_run:
            with ProcessPoolExecutor(max_workers=jobs, initializer=_worker_init,
                                     initargs=(manifest, RUN)) as pool:
                # as_completed -> results in completion order (a slow doc no
                # longer stalls the display), each paired with its basefile
                futures = {pool.submit(_worker, (source.name, action, bf)): bf
                           for bf in basefiles}
                for fut in as_completed(futures):
                    absorb(fut.result(), futures[fut])
        else:
            for bf in basefiles:
                absorb(build_one(source, action, bf, manifest), bf)
    finally:
        # always flush what was done -- on normal completion AND on Ctrl-C, so an
        # interrupted slow source (forarbete) keeps the docs it already parsed
        if total:
            sys.stderr.write("\n")
        persist()
    return merged


def _absorb(into, res):
    into.planned += res.planned
    into.done += res.done
    into.errors += res.errors
    into.updates.update(res.updates)
    into.skips += res.skips
    into.fresh += res.fresh
    into.timings += res.timings


# --------------------------------------------------------------------------
# manifest persistence. The manifest is one ~hundreds-of-MB JSON (an entry per
# (source, stage, basefile)); parsing it costs ~2s, so a single `all` run that
# loaded it once per step paid that many times over. Cache the parsed dict for
# the life of the process -- every load_manifest() in one invocation shares (and
# mutates) the same dict, and save just rewrites it. Workers get an explicit
# snapshot via the pool initializer, so the cache never crosses a process.
# --------------------------------------------------------------------------

_MANIFEST_CACHE = None


def load_manifest():
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is None:
        _MANIFEST_CACHE = (json.loads(MANIFEST.read_text())
                           if MANIFEST.exists() else {})
    return _MANIFEST_CACHE


def save_manifest(manifest):
    global _MANIFEST_CACHE
    _MANIFEST_CACHE = manifest
    util.write_atomic(MANIFEST, json.dumps(manifest, ensure_ascii=False,
                                           sort_keys=True))


# The coarse per-(step, source) watermarks live in their own tiny file, NOT the
# big per-doc manifest -- so a no-op run reads only this to decide every step can
# be skipped, never parsing the ~57 MB manifest (which is loaded only when a
# source actually changed and needs the per-document freshness scan).
_WATERMARKS_CACHE = None


def load_watermarks():
    global _WATERMARKS_CACHE
    if _WATERMARKS_CACHE is None:
        _WATERMARKS_CACHE = (json.loads(WATERMARKS.read_text())
                             if WATERMARKS.exists() else {})
    return _WATERMARKS_CACHE


def save_watermarks(store):
    global _WATERMARKS_CACHE
    _WATERMARKS_CACHE = store
    util.write_atomic(WATERMARKS, json.dumps(store, ensure_ascii=False,
                                             sort_keys=True, indent=0))


# --------------------------------------------------------------------------
# SFS source
# --------------------------------------------------------------------------

PKG = Path(__file__).parent
SFS_CODE = tuple(PKG / "sfs" / ("%s.py" % m) for m in (
    "__init__", "extract", "reader", "tokenizer", "assembler", "model", "nf",
    "register", "begrepp")) + (PKG / "lib" / "lagrum.py",)


@functools.cache
def _namedlaws():
    return load_namedlaws(NAMEDLAWS_JSON)


@functools.cache
def _sfs_session():
    return sfs_download.make_session(sfs_download.USER_AGENT)


def sfs_downloaded(basefile):
    return layout.sfs_sfst(basefile)


def sfs_source(basefile):
    """The new beta-API _source JSON (downloaded/{y}/{n}.json), the primary
    form; the legacy SFST/SFSR HTML sit in downloaded/sfst|sfsr/ siblings."""
    return layout.sfs_source(basefile)


def sfs_register(basefile):
    return layout.sfs_sfsr(basefile)


def sfs_inputs(basefile):
    """Freshness inputs: the JSON _source when present (the new beta API), else
    the legacy SFST + SFSR HTML pair -- plus the document's patch file if one
    exists (`_patch_input`), so editing a patch re-stales the parse."""
    if sfs_source(basefile).exists():
        inputs = [sfs_source(basefile)]
    else:
        inputs = [sfs_downloaded(basefile), sfs_register(basefile)]
    return inputs + _patch_input("sfs", basefile)


def sfs_artifact(basefile):
    return layout.artifact("sfs", basefile)


def sfs_download_run(basefile):
    """Fetch one named act's consolidated _source from the beta database,
    archiving any superseded consolidation (the old download_single). New-act
    *discovery* is sfs_harvest (bare `lagen sfs download`), not this."""
    source = sfs_download.fetch_one(_sfs_session(), basefile)
    if source is None:
        raise RuntimeError("no published act %s in the beta database" % basefile)
    sfs_download.save_document(layout.SFS_DOWNLOADED, source)
    time.sleep(POLITENESS)


def sfs_harvest(scopes):
    """Bulk discovery harvest -- a search_after sweep of the whole corpus, the
    only way to find acts not yet on disk (the old download_new). Incremental
    by default (stops at the first page with nothing new); `--force` walks the
    entire corpus oldest-first. Throttled and self-logging (per page)."""
    if RUN.dry_run:
        print("sfs download: would harvest the corpus into %s"
              % layout.SFS_DOWNLOADED)
        return
    seen, new, updated, skipped = sfs_download.sync(layout.SFS_DOWNLOADED,
                                                    full=RUN.force)
    print("sfs download: %d seen, %d new, %d updated, %d skipped"
          % (seen, new, updated, skipped))


def sfs_parse_run(basefile):
    doc, register, sfst_header = load_inputs(
        sfs_source(basefile), sfs_downloaded(basefile),
        sfs_register(basefile), basefile)
    nf = to_normalform(doc, basefile,
                       refparser=LagrumParser(_namedlaws(), basefile),
                       register=register, sfst_header=sfst_header)
    write_artifact("sfs", basefile, nf)


SFS_VERSIONS_CODE = SFS_CODE + (PKG / "sfs" / "versions.py",)


def sfs_versions_inputs(basefile):
    """Freshness inputs of the versions stage: every archived consolidation.
    Archive files are immutable once written, so this set changes only when
    the downloader supersedes a consolidation (or history is imported)."""
    return [path for _, path in layout.sfs_version_downloads(basefile)]


def sfs_versions_sidecar(basefile):
    return layout.sfs_versions_sidecar(basefile)


def sfs_versions_run(basefile):
    """Parse every archived consolidation of one statute into per-version
    artifacts + the sidecar index (see sfs.versions). The sidecar is written
    even when the statute has no archive, marking the stage built."""
    sfs_versions_mod.build(basefile,
                           refparser=LagrumParser(_namedlaws(), basefile))


def sfs_list():
    """Every *regular* SFS basefile with a source: the new beta JSON
    (downloaded/{y}/{n}.json) or the legacy SFST HTML (downloaded/sfst/).

    Acts whose year segment is non-numeric -- amendments to government-agency
    regulations carrying a letter prefix, e.g. 'N2026:3' -- are harvested and
    stored but excluded here: they don't belong in the SFS-centric publication
    and will be picked up by the myndfskr (myndighetsföreskrifter) port."""
    return sorted({"%s:%s" % (p.parent.name, p.stem.replace("_", " "))
                   for p in layout.SFS_DOWNLOADED.glob("*/*.json")
                   if p.parent.name.isdigit() and not p.name.startswith(".")}
                  | {"%s:%s" % (p.parent.name, p.stem.replace("_", " "))
                     for p in (layout.SFS_DOWNLOADED / "sfst").glob("*/*.html")
                     if not p.name.startswith(".")})


def sfs_ai_correspond(basefiles):
    """`lagen sfs ai-correspond <new-sfs> <prop-basefile> [<old-sfs>]` -- LLM-derive
    the old->new paragraf correspondence map for a restructured statute from the
    proposition's författningskommentar, validate every edge against both laws'
    paragrafs, and write it as a `.corr` sidecar next to the new statute. The old
    law is read from the new law's repeal clause unless given. One-shot per id,
    like eurlex ai-annotate; the LLM is never called from parse/relate/generate."""
    if not 2 <= len(basefiles) <= 3:
        sys.exit("usage: lagen sfs ai-correspond <new-sfs> <prop-basefile> "
                 "[<old-sfs>]  (e.g. 2018:585 prop/2017-18-89)")
    new_sfs, prop = basefiles[0], basefiles[1]
    new_art = json.loads(sfs_artifact(new_sfs).read_text())
    prop_art = json.loads(fa_artifact(prop).read_text())
    old_uri = ("https://lagen.nu/" + basefiles[2] if len(basefiles) == 3
               else sfs_correspond.detect_old_law(new_art))
    assert old_uri, ("%s: could not detect the repealed law from its transition "
                     "clause; pass it as the third argument" % new_sfs)
    old_art = json.loads(sfs_artifact(old_uri.rsplit("/", 1)[-1]).read_text())
    out = sfs_artifact(new_sfs).with_suffix(".corr")
    if RUN.dry_run:
        print("sfs ai-correspond: would map %s <- %s via %s -> %s"
              % (new_sfs, old_uri.rsplit("/", 1)[-1], prop, out))
        return
    # reading the proposition's författningskommentar is förarbete's job; build
    # composes the two verticals (sfs.correspond no longer imports forarbete)
    fk = fa_kommentar.fk_section(
        prop_art, new_art["metadata"]["properties"]["dcterms:title"])
    sidecar, stats = sfs_correspond.correspond(new_art, prop_art, old_art, fk)
    out.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2))
    print("sfs ai-correspond %s: %d edges from %d (%d rejected), wrote %s"
          % (new_sfs, stats["emitted"], stats["raw"], stats["rejected"], out))


SOURCES["sfs"] = Source("sfs", sfs_list, {
    # download has no input files (the input is the remote DB) and its output
    # is valid regardless of the fetcher's version, so inputs/code stay empty:
    # an act on disk is "fresh" until --force re-fetches it.
    "download": Stage("download", sfs_download_run, sfs_source),
    "parse": Stage("parse", sfs_parse_run, sfs_artifact,
                   inputs=sfs_inputs, code=SFS_CODE),
    # historical consolidations: parse the download archive (superseded
    # versions, incl. two decades of legacy HTML snapshots) into per-version
    # artifacts + a sidecar index, feeding the lydelse pages and the diff view
    "versions": Stage("versions", sfs_versions_run, sfs_versions_sidecar,
                      inputs=sfs_versions_inputs, code=SFS_VERSIONS_CODE),
}, harvest=sfs_harvest, origin=_origin(sfs_download.ENDPOINT),
   actions={"ai-correspond": sfs_ai_correspond},
   notes="ai-correspond <new-sfs> <prop> [<old-sfs>]: LLM-derive the old->new "
         "paragraf correspondence map into a .corr sidecar")


# --------------------------------------------------------------------------
# DV source
# --------------------------------------------------------------------------

DOM_DOWNLOADED = layout.DOM_DOWNLOADED            # dv api records (primary)
DV_LEGACY_DOWNLOADED = layout.DV_LEGACY_DOWNLOADED  # legacy raw feed
DV_INDEX = layout.DOM_INDEX
DV_CODE = (PKG / "dv" / "parse.py", PKG / "dv" / "model.py",
           PKG / "dv" / "structure.py", PKG / "lib" / "casenaming.py",
           PKG / "lib" / "lagrum.py")


@functools.cache
def _dv_cases():
    cases = json.loads(DV_INDEX.read_text())
    return {c["canonical_id"]: c for c in cases if api_member(c)}


@functools.cache
def _dv_session():
    return dv_download.make_session(dv_download.USER_AGENT)


def dv_artifact(basefile):
    return layout.artifact("dv", basefile)


def dv_record(basefile):
    # the identity index stores paths data_root-relative (portable); resolve here
    return util.load_relpath(layout.DATA, api_member(_dv_cases()[basefile])["path"])


def dv_download_run(basefile):
    """Re-fetch one named case's API record (by the uuid the identity index
    already holds) and its attachments. New-case *discovery* is dv_harvest
    (bare `lagen dv download`) + identity reindex -- a case has no uuid to
    fetch until the harvest has seen it, so it can't enter through here."""
    member = api_member(_dv_cases()[basefile])
    record = dv_download.fetch_record(_dv_session(), member["uuid"])
    out = dv_record(basefile)
    util.write_atomic(out, json.dumps(
        record, ensure_ascii=False, indent=2).encode())
    dv_download.download_bilagor(_dv_session(), out.parent.parent, record,
                                 POLITENESS)
    time.sleep(POLITENESS)


def dv_harvest(scopes):
    """Bulk discovery harvest of the courts' publication API -- the only way to
    find cases not yet on disk (paginates the whole corpus). Incremental by
    default; `--force` walks it all oldest-first. Throttled, self-logging.

    Rebuilds the identity index afterwards so new cases are immediately
    visible to parse. The rebuild is a single whole-corpus pass (the index is
    a global union-find, not incrementally updatable) and needs no parsing
    (keys come from raw record fields + legacy filenames), so it runs once at
    the end rather than per page."""
    if RUN.dry_run:
        print("dv download: would harvest into %s, then rebuild %s"
              % (DOM_DOWNLOADED, DV_INDEX))
        print("dv download: would refresh named-rättsfall snapshot %s"
              % NAMEDCASES_JSON)
        return
    seen, changed = dv_download.sync(DOM_DOWNLOADED, full=RUN.force)
    print("dv download: %d records seen, %d new/changed" % (seen, changed))
    if changed or not DV_INDEX.exists():
        dv_reindex()
    else:
        print("dv download: no new records, identity index left as is")
    # also refresh the named-rättsfall snapshot: HD updates that list on its own
    # cadence (independent of which cases we just downloaded), so a harvest is the
    # natural moment to re-pull it. Best-effort -- a fetch failure here must not
    # undo a successful case harvest (the committed snapshot stays the fallback).
    try:
        dv_namedcases()
    except requests.exceptions.RequestException as e:
        print("dv download: named-rättsfall refresh skipped (%s)" % e)


def dv_reindex(args=()):
    """Rebuild the identity index from the records already on disk -- one
    whole-corpus union-find pass, no network and no parsing. Runs automatically
    after a harvest that changed anything, and on demand as `lagen dv reindex`
    (e.g. after revising the entity-resolution rules)."""
    if RUN.dry_run:
        print("dv reindex: would rebuild %s from %s + %s"
              % (DV_INDEX, DOM_DOWNLOADED, DV_LEGACY_DOWNLOADED))
        return
    print("dv reindex: rebuilding identity index ...")
    dv_identity.reindex(dvdir=str(DV_LEGACY_DOWNLOADED),
                        domstoldir=str(DOM_DOWNLOADED),
                        out=str(DV_INDEX))
    _dv_cases.cache_clear()


def dv_namedcases(args=()):
    """Refresh the named-rättsfall snapshot (`lagen dv namedcases`): download
    HD's official list of named precedents and rewrite dv/data/namedcases.json,
    which the ⌘K resolver reads to turn a nickname ("Instagrambilden") into the
    published case URI. Independent of the per-document download/parse chain --
    it's a single small curated dataset, not corpus artifacts."""
    if RUN.dry_run:
        print("dv namedcases: would harvest %s -> %s"
              % (dv_namedcases_mod.URL, NAMEDCASES_JSON))
        return
    cases = dv_namedcases_mod.harvest()
    resolvable = sum(1 for c in cases if c["uri"])
    print("dv namedcases: %d named cases (%d resolvable) -> %s"
          % (len(cases), resolvable, NAMEDCASES_JSON))


def dv_parse_run(basefile):
    record = json.loads(dv_record(basefile).read_text())
    av = parse_api_record(record, basefile)
    # the case's public publication-search page is keyed by the record's
    # gruppKorrelationsnummer (the publication group), not derivable from basefile
    grupp = record.get("gruppKorrelationsnummer")
    art = to_artifact(av, canonical_id=basefile)
    # stamp the canonical, name-prefixed display title onto the artifact here, so
    # the pure catalog reads it off the artifact without recomputing (the naming
    # grammar itself lives in lib.casenaming, read identically by page + catalog)
    art["label"] = casenaming.case_label(art)
    write_artifact("dv", basefile, art,
                   source_url=layout.dv_source_url(grupp) if grupp else None)


SOURCES["dv"] = Source("dv", lambda: sorted(_dv_cases()), {
    "download": Stage("download", dv_download_run, dv_record),
    "parse": Stage("parse", dv_parse_run, dv_artifact,
                   inputs=lambda bf: [dv_record(bf)] + _patch_input("dv", bf),
                   code=DV_CODE),
}, harvest=dv_harvest, origin=_origin(dv_download.API),
   actions={"reindex": dv_reindex, "namedcases": dv_namedcases})


# --------------------------------------------------------------------------
# förarbete source (preparatory works from regeringen.se)
# --------------------------------------------------------------------------

# legacy_formats.py is in FA_CODE because the frozen-import html route reads it at
# parse time (a text/tml body -> paragraphs), so editing it re-stales those docs.
# legacy.py (the import verb) is NOT: it only produces records, which are parse's
# per-doc inputs and already versioned via fa_record's inputs hash.
FA_CODE = (PKG / "forarbete" / "parse.py", PKG / "forarbete" / "model.py",
           PKG / "forarbete" / "structure.py", PKG / "forarbete" / "kommentar.py",
           PKG / "forarbete" / "legacy_formats.py", PKG / "lib" / "lagrum.py")


def fa_record(basefile):
    return layout.fa_record(basefile)


def fa_parse_inputs(basefile):
    """Freshness inputs of the förarbete parse stage: the downloaded record and
    the re-OCR sidecar slot (§7g). The sidecar is listed even while absent, so
    dropping a modern-OCR'd PDF there (which `_legacy_body` then parses instead of
    the frozen scan) re-stales exactly that document's parse."""
    return ([fa_record(basefile), layout.fa_ocr_pdf(*basefile.split("/", 1))]
            + _patch_input("forarbete", basefile))


def fa_artifact(basefile):
    return layout.artifact("forarbete", basefile)


def fa_list():
    """Every harvested record as 'type/slug' (the artifact subdir excluded by
    the single-level glob)."""
    return sorted("%s/%s" % (p.parent.name, p.stem)
                  for p in layout.FA_DOWNLOADED.glob("*/*.json")
                  if not p.name.startswith("."))


def fa_harvest(scopes):
    """Bulk harvest of preparatory works. Most doctypes come from regeringen.se
    (the old download_new); `bet` (utskottsbetänkanden) comes from
    data.riksdagen.se via a separate downloader. `scopes` narrows to the named
    doctypes (prop/sou/ds/bet/...); empty = all. `--only BASEFILE` (with exactly
    one regeringen scope) fetches just that one document, walking the listing
    until it is found (regeringen types only -- bet has no --only).
    `--riksmote YYYY/YY` (with exactly the bet scope) narrows the bet harvest
    to one riksmöte -- a dev/manual slice that never advances the watermark."""
    if RUN.only and len(scopes) != 1:
        sys.exit("forarbete --only needs exactly one doctype, e.g. "
                 "`lagen forarbete download prop --only 2025/26:28`")
    do_bet = "bet" in scopes if scopes else True
    reg_scopes = [s for s in scopes if s != "bet"]
    do_reg = bool(reg_scopes) or not scopes   # empty scopes = all regeringen types
    if RUN.only and do_bet:
        sys.exit("forarbete --only is not supported for bet "
                 "(data.riksdagen.se); use a full or incremental download")
    if RUN.riksmote and (do_reg or not do_bet):
        sys.exit("forarbete --riksmote needs exactly the bet scope, e.g. "
                 "`lagen forarbete download bet --riksmote 2025/26`")
    if RUN.dry_run:
        print("forarbete download: would harvest %s into %s"
              % (RUN.only or ", ".join(scopes) or "all types",
                 layout.FA_DOWNLOADED))
        return
    if do_reg:
        totals = fa_download.sync(str(layout.FA_DOWNLOADED), types=reg_scopes or None,
                                  full=RUN.force, only=RUN.only)
        for typ, (seen, new) in totals.items():
            print("forarbete %s: %d seen, %d new" % (typ, seen, new))
    if do_bet:
        seen, new = fa_riksdagen.sync(str(layout.FA_DOWNLOADED), full=RUN.force,
                                      riksmote=RUN.riksmote)
        print("forarbete bet: %d seen, %d new" % (seen, new))


def fa_parse_run(basefile):
    record = json.loads(fa_record(basefile).read_text())
    art = fa_parse.to_artifact(fa_parse.parse_record(record, layout.FA_DOWNLOADED))
    # a proposition's författningskommentar states which EU directive article a
    # provision transposes -- attach those genomför relations as a typed section
    # so relate emits the implements edges and the page renders them (§7d).
    implements = fa_kommentar.extract(art)
    if implements:
        art["implements"] = implements
    # the regeringen.se landing page the downloader recorded -- not derivable by
    # rule, so it travels with the record into the artifact's source_url
    write_artifact("forarbete", basefile, art, source_url=record.get("url"))


FA_LEGACY_CORPORA = "|".join(fa_legacy.IMPORTERS)


def fa_import_legacy(args):
    """`lagen forarbete import-legacy <corpus> [<path>]` -- one-time import of a
    frozen förarbete corpus (§7g) into the forarbete record layout, so `parse`
    then treats each doc like a harvested one (no network). `<corpus>` is one of
    propriksdagen, souregeringen, dsregeringen, dirregeringen, soukb, dirasp,
    propkb, proptrips, dirtrips. The records reference the frozen body bytes in
    place (relative to LEGACY_ROOT), never copying them (the 371 GB soukb tree is
    pointed at, not duplicated). Path defaults to `LEGACY_ROOT/<corpus>`. `--limit
    N` caps the run (a test slice); `--force` re-imports the corpus's own records
    (never overwriting a live or better-format record)."""
    if not args or args[0] not in fa_legacy.IMPORTERS or len(args) > 2:
        sys.exit("usage: lagen forarbete import-legacy {%s} [<path>]"
                 % FA_LEGACY_CORPORA)
    corpus = args[0]
    path = args[1] if len(args) == 2 else str(config.LEGACY_ROOT / corpus)
    if RUN.dry_run:
        print("forarbete import-legacy: would import %s %s into %s"
              % (corpus, path, layout.FA_DOWNLOADED))
        return
    fa_legacy.import_corpus(corpus, path, layout.FA_DOWNLOADED,
                            limit=RUN.limit, force=RUN.force)


SOURCES["forarbete"] = Source("forarbete", fa_list, {
    "parse": Stage("parse", fa_parse_run, fa_artifact,
                   inputs=fa_parse_inputs, code=FA_CODE),
}, harvest=fa_harvest, origin=_origin(fa_download.BASE),
   scopes=frozenset(fa_download.TYPES) | {"bet"},
   actions={"import-legacy": fa_import_legacy},
   notes="download flag: --only BASEFILE (fetch one document; needs one "
         "regeringen scope)\n"
         "download flag: --riksmote YYYY/YY (narrow the bet harvest to one "
         "riksmöte; needs the bet scope, never advances the watermark)\n"
         "import-legacy {%s} [<path>]: one-time import of a frozen förarbete "
         "corpus (--limit N caps it; --force re-imports)" % FA_LEGACY_CORPORA)


# --------------------------------------------------------------------------
# EUR-Lex source (EU treaties, legislation, case law from CELLAR; CELEX ids)
# --------------------------------------------------------------------------

EURLEX_CODE = (PKG / "eurlex" / "parse.py", PKG / "eurlex" / "parse_html.py",
               PKG / "eurlex" / "parse_pdf.py", PKG / "eurlex" / "lang.py",
               PKG / "eurlex" / "model.py", PKG / "eurlex" / "structure.py",
               PKG / "lib" / "lagrum.py")


@functools.cache
def _eurlex_session():
    return eurlex_download.make_session(eurlex_download.USER_AGENT)


def eurlex_notice(basefile):
    """The tree-notice graph -- the freshness marker for a downloaded CELEX."""
    return layout.eurlex_dir(basefile) / "notice.ttl"


def eurlex_content(basefile):
    """The content file parse reads (Formex/HTML/PDF), if any was obtained in a
    wanted language."""
    path, _lang, _route = eurlex_parse.content_file(layout.eurlex_dir(basefile))
    return [path] if path else []


def eurlex_artifact(basefile):
    return layout.artifact("eurlex", basefile)


def eurlex_parse_run(basefile):
    path, lang, route = eurlex_parse.content_file(layout.eurlex_dir(basefile))
    if path is None:
        raise SkipDocument("%s: no swe/eng content" % basefile)
    art = eurlex_parse.to_artifact(
        eurlex_parse.parse_content(path, route, basefile, lang))
    write_artifact("eurlex", basefile, art)


def eurlex_download_run(basefile):
    """Fetch one CELEX (tree notice + best content per language) from CELLAR.
    Discovery of new CELEX is eurlex_harvest (bare `lagen eurlex download`)."""
    stored = eurlex_download.download_document(
        _eurlex_session(), layout.EURLEX_DOWNLOADED, basefile,
        eurlex_download.LANGUAGES, POLITENESS)
    if not stored:
        print("%s: no manifestation in %s" % (
            basefile, "/".join(eurlex_download.LANGUAGES)), flush=True)


def eurlex_harvest(scopes):
    """Bulk discovery sweep via the CELLAR SPARQL endpoint. `scopes` narrows it
    to the named sectors (treaties/acts/caselaw); empty = all. Incremental by
    default (watermark-bounded, skips CELEX already on disk); --force re-fetches
    everything. --since/--lang/--source tune discovery (see RunOptions)."""
    if RUN.dry_run:
        print("eurlex download: would harvest %s into %s"
              % (", ".join(scopes) or "treaties/acts/caselaw",
                 layout.EURLEX_DOWNLOADED))
        return
    languages = tuple(RUN.lang.split(",")) if RUN.lang else eurlex_download.LANGUAGES
    for sector in (scopes or list(eurlex_download.SECTORS)):
        seen, stored, skipped = eurlex_download.sync(
            layout.EURLEX_DOWNLOADED, sector, full=RUN.force, since=RUN.since,
            languages=languages, source=RUN.source)
        print("eurlex %s: %d seen, %d stored, %d skipped"
              % (sector, seen, stored, skipped))


def eurlex_unpack(args):
    """`lagen eurlex unpack-bulk <dir-or-zip>` -- import a CELLAR bulk
    legislation dump into the per-CELEX layout, so `parse` then treats the works
    exactly like downloaded documents (no network)."""
    if len(args) != 1:
        sys.exit("usage: lagen eurlex unpack-bulk <bulk-dir-or-zip>")
    if RUN.dry_run:
        print("eurlex unpack-bulk: would import %s into %s"
              % (args[0], layout.EURLEX_DOWNLOADED))
        return
    eurlex_bulk.unpack_bulk(args[0], layout.EURLEX_DOWNLOADED)


def eurlex_prune(args=()):
    """`lagen eurlex prune-empty` -- remove harvest dirs left as a bare notice.ttl
    with no Swedish/English document (a pre-accession act never translated). The
    harvest tree is rebuildable, so this only drops dead weight the parser skips."""
    n = eurlex_download.prune_empty(layout.EURLEX_DOWNLOADED, remove=not RUN.dry_run)
    print("eurlex prune-empty: %s %d notice-only dir(s) in %s"
          % ("would remove" if RUN.dry_run else "removed", n,
             layout.EURLEX_DOWNLOADED))


def eurlex_ai_annotate(basefiles):
    """`lagen eurlex ai-annotate <CELEX> ...` -- author the editorial `.ann` layer
    (thematic recital groups + article<->recital links) for the named sector-3
    acts by calling the LLM endpoint. Deliberately one-shot per id: the LLM is
    never called from parse/relate/generate, only from this explicit action."""
    if not basefiles:
        sys.exit("usage: lagen eurlex ai-annotate <CELEX> [<CELEX> ...]")
    for celex in basefiles:
        if RUN.dry_run:
            print("eurlex ai-annotate: would annotate %s -> %s"
                  % (celex, eurlex_artifact(celex).with_suffix(".ann")))
            continue
        out = eurlex_annotate.annotate(celex)
        print("eurlex ai-annotate %s: wrote %s" % (celex, out))


SOURCES["eurlex"] = Source("eurlex", lambda: eurlex_download.list_basefiles(
    layout.EURLEX_DOWNLOADED), {
    "download": Stage("download", eurlex_download_run, eurlex_notice),
    "parse": Stage("parse", eurlex_parse_run, eurlex_artifact,
                   inputs=lambda bf: eurlex_content(bf) + _patch_input("eurlex", bf),
                   depends="download", code=EURLEX_CODE),
}, harvest=eurlex_harvest, origin=_origin(eurlex_download.SOAP_ENDPOINT),
   scopes=frozenset(eurlex_download.SECTORS),
   actions={"unpack-bulk": eurlex_unpack, "ai-annotate": eurlex_ai_annotate,
            "prune-empty": eurlex_prune},
   notes="download flags: --since YYYY-MM-DD, --lang swe,eng, --source sparql|soap\n"
         "unpack-bulk <dir|zip>: import a CELLAR bulk legislation dump\n"
         "prune-empty: remove harvest dirs with only a notice.ttl (no swe/eng doc)\n"
         "ai-annotate <CELEX>: LLM-author the editorial .ann layer (sector-3 acts)")


# --------------------------------------------------------------------------
# föreskrift source (agency regulations: FFFS, … -- per-fs subtrees, PDF body)
# --------------------------------------------------------------------------

def foreskrift_list():
    """Every harvested base regulation as 'fs/year:num' (the artifact subdir
    excluded by the single-level glob)."""
    return sorted(json.loads(p.read_text())["basefile"]
                  for p in layout.FORESKRIFT_DOWNLOADED.glob("*/*.json")
                  if not p.name.startswith("."))


def foreskrift_harvest(scopes):
    """Bulk harvest of the agency författningssamlingar (scopes = fs codes, e.g.
    'fffs'; empty = all registered). `--full` re-walks and refreshes existing
    base regulations; `--only fs/year:num` (one scope) fetches a single one."""
    if RUN.only and len(scopes) != 1:
        sys.exit("foreskrift --only needs exactly one fs scope, e.g. "
                 "`lagen foreskrift download fffs --only fffs/2013:10`")
    if RUN.dry_run:
        print("foreskrift download: would harvest %s into %s"
              % (RUN.only or ", ".join(scopes) or "all agencies",
                 layout.FORESKRIFT_DOWNLOADED))
        return
    totals = foreskrift_download.sync(str(layout.FORESKRIFT_DOWNLOADED),
                                      scopes=scopes or None, full=RUN.force,
                                      only=RUN.only)
    for fs, (seen, new) in totals.items():
        print("foreskrift %s: %d seen, %d new" % (fs, seen, new))


# the parser is one shared engine over every fs (its own model/structure plus the
# shared PDF extraction + citation engine), so a change to any of these re-stales
# every föreskrift the recipe-version way -- just like SFS/eurlex parse.
FORESKRIFT_CODE = (PKG / "foreskrift" / "parse.py",
                   PKG / "foreskrift" / "model.py",
                   PKG / "foreskrift" / "structure.py",
                   PKG / "lib" / "pdftext.py", PKG / "lib" / "lagrum.py")


def foreskrift_record(basefile):
    """The harvested record JSON (``<fs>/<slug>.json``) for one base regulation."""
    fs = basefile.split("/", 1)[0]
    return foreskrift_harvest_mod.record_path(
        layout.FORESKRIFT_DOWNLOADED, fs, basefile)


def foreskrift_artifact(basefile):
    return layout.artifact("foreskrift", basefile)


def foreskrift_inputs(basefile):
    """The record JSON plus every body PDF it references (the regulation and any
    konsoliderad versions); re-downloading any of them re-stales the parse."""
    rec = foreskrift_record(basefile)
    paths = [rec]
    if rec.exists():
        record = json.loads(rec.read_text())
        fsdir = layout.FORESKRIFT_DOWNLOADED / record["fs"]
        files = record.get("files", {})
        reg = files.get("regulation")
        # a live-harvest file lives under fsdir/<name>; a frozen-import file (§7g)
        # is resolved in place under LEGACY_ROOT -- body_path handles both.
        if reg:
            paths.append(foreskrift_parse.body_path(
                str(layout.FORESKRIFT_DOWNLOADED), record["fs"], reg))
        paths += [fsdir / c["name"] for c in files.get("consolidation", [])
                  if c.get("name")]
    return paths + _patch_input("foreskrift", basefile)


def foreskrift_parse_run(basefile):
    """One harvested record -> its JSON artifact: the body structure, the masthead
    metadata, and the bemyndigande/genomför citation edges the model carries."""
    record = json.loads(foreskrift_record(basefile).read_text())
    reg = foreskrift_parse.parse_record(record, str(layout.FORESKRIFT_DOWNLOADED))
    write_artifact("foreskrift", basefile, reg.to_artifact())


def foreskrift_import_legacy(args):
    """`lagen foreskrift import-legacy {skvfs|sosfs} [<path>]` -- one-time import of
    a harvest-blocked författningssamling corpus (§7g pri 6) from the frozen legacy
    tree into the föreskrift record layout, so `parse` then treats each regulation
    like a harvested one (no network). Records reference the frozen regulation PDF
    bytes in place (relative to LEGACY_ROOT), never copying them; each frozen tree
    holds two fs series (skvfs+rsfs, sosfs+hslffs). Path defaults to
    `LEGACY_ROOT/<corpus>`. `--limit N` caps the run (a test slice); `--force`
    re-imports the corpus's own records (never overwriting a live-harvest one)."""
    if not args or args[0] not in foreskrift_legacy.LEGACY_CORPORA or len(args) > 2:
        sys.exit("usage: lagen foreskrift import-legacy {skvfs|sosfs} [<path>]")
    corpus = args[0]
    path = args[1] if len(args) == 2 else str(config.LEGACY_ROOT / corpus)
    if RUN.dry_run:
        print("foreskrift import-legacy: would import %s %s into %s"
              % (corpus, path, layout.FORESKRIFT_DOWNLOADED))
        return
    foreskrift_legacy.import_corpus(corpus, path, str(layout.FORESKRIFT_DOWNLOADED),
                                    limit=RUN.limit, force=RUN.force)


# No per-document download stage: the body PDFs arrive only through the bulk
# `foreskrift_harvest` sweep (or, for the two harvest-blocked corpora, the
# `import-legacy` action), so parse depends on no upstream stage -- it runs over
# whatever the harvest has put on disk (relate/index/dump/generate then act on the
# artifacts by source name, like every other source).
SOURCES["foreskrift"] = Source("foreskrift", foreskrift_list, {
    "parse": Stage("parse", foreskrift_parse_run, foreskrift_artifact,
                   inputs=foreskrift_inputs, code=FORESKRIFT_CODE),
},
    harvest=foreskrift_harvest,
    # display label only, nothing is ever fetched from a central index: the
    # harvest engine drives each agency's own site from foreskrift/agencies.py
    origin="the %d agency sites in foreskrift/agencies.py"
           % len(FORESKRIFT_AGENCIES),
    scopes=frozenset(FORESKRIFT_AGENCIES),
    actions={"import-legacy": foreskrift_import_legacy},
    notes="download flag: --only fs/year:num (fetch one; needs one fs scope)\n"
          "scopes are författningssamling codes (fffs, …); empty = all agencies\n"
          "import-legacy {skvfs|sosfs} [<path>]: one-time import of the frozen "
          "harvest-blocked corpus (--limit N caps it; --force re-imports)")


# --------------------------------------------------------------------------
# avg source (JO + JK vägledande myndighetsavgöranden)
# --------------------------------------------------------------------------

AVG_CODE = (PKG / "avg" / "parse.py", PKG / "avg" / "model.py",
            PKG / "avg" / "download.py",
            PKG / "lib" / "pdftext.py", PKG / "lib" / "lagrum.py")


def avg_list():
    return sorted(bf for org in ("jo", "jk", "arn")
                  for bf in util.list_basefiles(layout.AVG_DOWNLOADED, org))


def avg_record(basefile):
    return util.record_path(layout.AVG_DOWNLOADED, basefile.split("/", 1)[0],
                            basefile)


def avg_inputs(basefile):
    """The record JSON plus the decision body file (JO/ARN: the PDF; JK: the
    landing page) -- re-downloading/re-importing either re-stales the parse."""
    paths = [avg_record(basefile)]
    if basefile.startswith("jo/"):
        pdf = avg_download.jo_pdf_path(layout.AVG_DOWNLOADED, basefile)
        if pdf.exists():
            paths.append(pdf)
    elif basefile.startswith("arn/"):
        paths.append(avg_legacy.arn_pdf_path(layout.AVG_DOWNLOADED, basefile))
    else:
        paths.append(avg_download.jk_html_path(layout.AVG_DOWNLOADED, basefile))
    return paths + _patch_input("avg", basefile)


def avg_artifact(basefile):
    return layout.artifact("avg", basefile)


def avg_parse_run(basefile):
    write_artifact("avg", basefile,
                   avg_parse.parse_record(basefile, layout.AVG_DOWNLOADED))


def avg_harvest(scopes):
    """Bulk harvest of the JO/JK/ARN decisions (scopes = organ codes; empty =
    all three). `--force` re-walks the whole corpus (JO) / refetches landings
    (JK) / refetches every PDF (ARN); `--only jo/2340-2025` fetches a single
    decision (needs its organ scope)."""
    if RUN.only and len(scopes) != 1:
        sys.exit("avg --only needs exactly one organ scope, e.g. "
                 "`lagen avg download jo --only jo/2340-2025`")
    if RUN.dry_run:
        print("avg download: would harvest %s into %s"
              % (RUN.only or ", ".join(scopes) or "jo + jk + arn",
                 layout.AVG_DOWNLOADED))
        return
    totals = avg_download.sync(layout.AVG_DOWNLOADED, scopes=scopes or None,
                               full=RUN.force, only=RUN.only)
    for org, (seen, new) in totals.items():
        print("avg %s: %d seen, %d new" % (org, seen, new))


def avg_import_legacy(args):
    """`lagen avg import-legacy arn <frozen-arn-tree>` -- one-time import of the
    frozen ARN corpus (a decision file + fragment.html metadata per case) into the
    avg record layout, so `parse` then treats each referat like a harvested
    decision (no network). doc/wpd/rtf bodies are converted to PDF via
    LibreOffice; native PDFs are copied. `--force` re-imports records already on
    disk; `--limit N` caps the run (a test slice)."""
    if len(args) != 2 or args[0] != "arn":
        sys.exit("usage: lagen avg import-legacy arn <frozen-arn-tree>")
    if RUN.dry_run:
        print("avg import-legacy: would import ARN corpus %s into %s"
              % (args[1], layout.AVG_DOWNLOADED))
        return
    avg_legacy.import_arn(args[1], layout.AVG_DOWNLOADED, limit=RUN.limit,
                          force=RUN.force)


# No per-document download stage (the foreskrift rule): decisions arrive only
# through the bulk `avg_harvest` sweep (or, for the frozen ARN corpus, the
# `import-legacy` action), so parse runs over whatever is on disk;
# relate/index/dump/generate act on the artifacts by source name.
SOURCES["avg"] = Source("avg", avg_list, {
    "parse": Stage("parse", avg_parse_run, avg_artifact,
                   inputs=avg_inputs, code=AVG_CODE),
},
    harvest=avg_harvest,
    origin="https://www.jo.se/",
    scopes=frozenset({"jo", "jk", "arn"}),
    actions={"import-legacy": avg_import_legacy},
    notes="download flag: --only org/dnr (fetch one; needs its organ scope)\n"
          "scopes are the organs: jo (Riksdagens ombudsmän), jk "
          "(Justitiekanslern), arn (Allmänna reklamationsnämnden); empty = all\n"
          "arn download harvests the live vägledande-beslut listing (2017-); it "
          "overwrites any frozen import of the same dnr (live wins)\n"
          "import-legacy arn <path>: one-time import of the frozen ARN corpus "
          "(1991-2022; --limit N caps it; --force re-imports)")


# --------------------------------------------------------------------------
# remisser source (regeringen.se remiss/referral responses -- never rendered
# as its own pages; parsed answers feed the sole LLM pass, ai-analyze, whose
# .ann sidecars a later render pass surfaces on the referred förarbete's rail)
# --------------------------------------------------------------------------

REMISSER_CODE = (PKG / "remisser" / "parse.py", PKG / "remisser" / "model.py",
                 PKG / "lib" / "pdftext.py")


def remisser_list():
    """Every downloaded remiss-answer basefile ("<case-slug>/<org-slug>"), one
    per `Remissinstans` marked downloaded -- the parse stage's targets. Not
    every `Remiss.svar` entry: an instance not yet fetched has no PDF to parse."""
    out = []
    for path in sorted(layout.REMISSER_DOWNLOADED.glob("*.json")):
        if path.name.startswith("."):
            continue
        remiss = remisser_model.Remiss.from_dict(json.loads(path.read_text()))
        out.extend("%s/%s" % (remiss.basefile, remisser_model.org_slug(inst.source_url))
                   for inst in remiss.svar if inst.downloaded)
    return out


def remisser_record(basefile):
    return layout.remisser_case(basefile.split("/", 1)[0])


def remisser_pdf(basefile):
    case_basefile, org_slug = basefile.split("/", 1)
    return layout.remisser_answer(case_basefile, org_slug)


def remisser_artifact(basefile):
    return layout.artifact("remisser", basefile)


def remisser_inputs(basefile):
    return [remisser_record(basefile), remisser_pdf(basefile)] + _patch_input(
        "remisser", basefile)


def remisser_parse_run(basefile):
    write_artifact("remisser", basefile,
                   remisser_parse.parse_record(
                       basefile, layout.REMISSER_DOWNLOADED).to_dict())


def remisser_harvest(scopes):
    """Bulk harvest: discover new remiss cases, re-poll every still-open one for
    newly-arrived answers, and fetch any answer PDF not yet cached. No sub-scopes
    (unlike avg's organs / forarbete's doctypes) -- one homogeneous listing.
    `--only <url>` fetches exactly one case by its regeringen.se URL, bypassing
    the listing walk entirely (the archive runs to thousands of pages, so this
    is the escape hatch for "just this one case")."""
    if RUN.dry_run:
        print("remisser download: would harvest into %s"
              % layout.REMISSER_DOWNLOADED)
        return
    if RUN.only:
        result = remisser_download.sync_one(RUN.only)
        print("remisser %s: %d svar, %d fetched"
              % (result["basefile"], result["svar"], result["fetched"]))
        return
    summary = remisser_download.sync(full=RUN.force)
    print("remisser: %d new, %d repolled, %d closed, %d fetched"
          % (summary["new"], summary["repolled"], summary["closed"], summary["fetched"]))


def remisser_ai_analyze(basefiles):
    """`lagen remisser ai-analyze <basefile> ...` -- the sole LLM pass: map one
    remissvar onto the sections of the SOU/Ds it discusses (sentiment + verbatim
    quote per section, plus an overall stance), written as a `.ann` sidecar. One
    basefile is `"<case-slug>/<org-slug>"`; the LLM is never called from
    parse/relate/generate."""
    if not basefiles:
        sys.exit("usage: lagen remisser ai-analyze <basefile> [<basefile> ...]")
    for basefile in basefiles:
        if RUN.dry_run:
            print("remisser ai-analyze: would analyze %s -> %s"
                  % (basefile, remisser_artifact(basefile).with_suffix(".ann")))
            continue
        out = remisser_analyze.analyze(basefile)
        print("remisser ai-analyze %s: wrote %s" % (basefile, out))


# No per-document download stage (the avg/foreskrift rule): answers arrive only
# through the bulk `remisser_harvest` sweep, so parse runs over whatever is on
# disk; relate/index/dump/generate never touch this source (it publishes nothing).
SOURCES["remisser"] = Source("remisser", remisser_list, {
    "parse": Stage("parse", remisser_parse_run, remisser_artifact,
                   inputs=remisser_inputs, code=REMISSER_CODE),
},
    harvest=remisser_harvest,
    origin="https://www.regeringen.se/remisser/",
    actions={"ai-analyze": remisser_ai_analyze},
    notes="download flag: --only <regeringen.se case url> (fetch one case + its "
          "answer PDFs, bypassing the listing walk entirely)\n"
          "download harvests the whole /remisser/ listing (new cases, watermarked "
          "so a normal run doesn't re-walk the whole archive) then re-polls every "
          "still-open case for newly-arrived answers; --full ignores the "
          "watermark and re-walks everything\n"
          "ai-analyze <basefile>: LLM-map one answer onto the referred SOU/Ds's "
          "sections (sentiment + quote per section), written as a .ann sidecar\n"
          "this source is never related/generated -- it feeds the referred "
          "förarbete's rail, not its own pages")


# --------------------------------------------------------------------------
# wiki sources: kommentar (SFS commentary) + begrepp (concept glossary), both
# parsed from the MediaWiki dump
# --------------------------------------------------------------------------

WIKI_ROOT = layout.WIKI_ROOT
WIKI_CODE = (PKG / "wiki" / "parse.py", PKG / "lib" / "markdown.py",
             PKG / "lib" / "lagrum.py", PKG / "lib" / "eu_structure.py")


def kommentar_record(basefile):
    return Path(wiki_parse.kommentar_index(str(WIKI_ROOT))[basefile])


def kommentar_artifact(basefile):
    return layout.artifact("kommentar", basefile)


def kommentar_parse_run(basefile):
    art = wiki_parse.kommentar_artifact(str(kommentar_record(basefile)))
    write_artifact("kommentar", basefile, art)


def begrepp_record(basefile):
    return Path(wiki_parse.begrepp_index(str(WIKI_ROOT))[basefile])


def begrepp_artifact(basefile):
    return layout.artifact("begrepp", basefile)


def begrepp_parse_run(basefile):
    art = wiki_parse.begrepp_artifact(str(begrepp_record(basefile)))
    write_artifact("begrepp", basefile, art)


def kommentar_anchor_warnings(con, basefiles=()):
    """Section anchors in kommentar artifacts that resolve to no node in the act
    they annotate -- a mistyped `## Artikel N` / `## N kap M §` whose commentary
    and guidance would silently never surface in any rail (PRD Step 3). Returns
    `[(basefile, host_uri, [dangling anchors])]`; a host act absent from the
    corpus is skipped (its anchors can't be checked against a missing artifact).
    `basefiles` restricts the scan to those ids."""
    want = set(basefiles)
    out = []
    root = catalog.data_root(con)              # stored paths are data_root-relative
    for (path,) in con.execute(
            "SELECT path FROM documents WHERE source = 'kommentar' AND path <> ''"):
        komm = json.loads((root / path).read_bytes())
        if want and komm.get("basefile") not in want:
            continue
        row = con.execute("SELECT path FROM documents WHERE uri = ? AND path <> ''",
                          (komm.get("annotates"),)).fetchone()
        if not row:
            continue
        bad = wiki_parse.dangling_anchors(komm, json.loads((root / row[0]).read_bytes()))
        if bad:
            out.append((komm.get("basefile"), komm.get("annotates"), bad))
    return out


def kommentar_validate(basefiles=()):
    """`lagen kommentar validate [basefiles…]` -- report commentary section anchors
    that don't resolve to a node in the annotated act (PRD Step 3 validation), so a
    mistyped heading is caught instead of silently dropping its rail content. Reads
    the catalog; run `lagen kommentar relate` first if it is stale."""
    assert CATALOG.exists(), (
        "no catalog at %s -- run `lagen kommentar relate` first" % CATALOG)
    con = catalog.connect(CATALOG)
    warnings = kommentar_anchor_warnings(con, basefiles)
    con.close()
    for bf, host, anchors in warnings:
        print("kommentar %s -> %s: no matching node for %s"
              % (bf, host, ", ".join(anchors)))
    print("kommentar validate: %d file(s) with dangling anchors" % len(warnings))


def kommentar_ai_annotate(basefiles):
    """`lagen kommentar ai-annotate <basefile> ...` -- the Step-4 AI guidance
    linker: read the external guidance PDFs a commentary file declares in its
    `guidance:` frontmatter and LLM-derive, per article, which guidance section
    explains it. Writes a `.ann` sidecar next to the kommentar artifact (the
    AI-created layer, kept separate from the hand-edited markdown). One-shot per
    id: the LLM is never called from parse/relate/generate."""
    if not basefiles:
        sys.exit("usage: lagen kommentar ai-annotate <basefile> [<basefile> ...]")
    for basefile in basefiles:
        if RUN.dry_run:
            print("kommentar ai-annotate: would annotate %s -> %s"
                  % (basefile, kommentar_artifact(basefile).with_suffix(".ann")))
            continue
        out = wiki_annotate.annotate(basefile, WIKI_ROOT)
        print("kommentar ai-annotate %s: wrote %s" % (basefile, out))


def kommentar_discover_guidance(args):
    """`lagen kommentar discover-guidance [<limit>]` -- crawl the configured
    Commission guidance sites (their sitemaps) and (re)build the `CELEX ->
    guidance-page` index, so `propose-guidance <CELEX>` can auto-find an act's
    page(s) instead of a hand-known URL. The site rate-limits (429s a random slice
    of every run), so the index *merges across runs* and converges -- re-run to
    fill the gaps; `--force` starts a clean, authoritative index. `<limit>` caps
    pages (a quick check). No LLM."""
    limit = int(args[0]) if args else None
    if RUN.dry_run:
        print("kommentar discover-guidance: would crawl %d site(s) -> %s"
              % (len(guidance_discover.GUIDANCE_SITES),
                 guidance_discover.INDEX_PATH))
        return

    def progress(done, total, url):
        util.status(done, total, "discover-guidance  %s" % url)

    index, stats = guidance_discover.build_index(
        progress=progress, limit=limit, force=RUN.force)
    sys.stderr.write("\n")
    path = guidance_discover.write_index(index)
    missed = stats["total"] - stats["fetched"]
    print("kommentar discover-guidance: fetched %d/%d page(s) this run "
          "(%d rate-limited), index now %d act(s) -> %s"
          % (stats["fetched"], stats["total"], missed, len(index), path))
    if missed:
        print("  re-run `lagen kommentar discover-guidance` to fill the %d "
              "rate-limited page(s); the index merges across runs" % missed)


def kommentar_propose_guidance(args):
    """`lagen kommentar propose-guidance <dg-page-url | CELEX> [<CELEX>]` -- Track-B
    guidance proposer (no LLM): scrape a Commission guidance page for the guidance
    PDFs it links and print a draft `guidance:` frontmatter block to review and
    paste into the act's kommentar markdown, whence `ai-annotate` links it. Given a
    URL it scrapes that page (the optional CELEX cross-checks the page's EUR-Lex
    link); given a CELEX it looks the page(s) up in the `discover-guidance` index. A
    person still decides which candidates are genuine guidance on the act."""
    if not args:
        sys.exit("usage: lagen kommentar propose-guidance "
                 "<dg-page-url | CELEX> [<CELEX>]")
    arg = args[0]
    if arg.lower().startswith("http"):
        targets, expect = [arg], (args[1] if len(args) > 1 else None)
    else:
        targets, expect = guidance_discover.pages_for(arg), arg
        if not targets:
            sys.exit("no guidance page indexed for %s -- pass the page URL, or run "
                     "`lagen kommentar discover-guidance` first" % arg)
        print("# %s -> %d guidance page(s) from the index"
              % (arg, len(targets)), file=sys.stderr)
    for policy_url in targets:
        celexes, resolved, skipped = guidance_discover.propose(policy_url)
        print("# %s\n# act on this page (EUR-Lex): %s"
              % (policy_url, ", ".join(sorted(celexes)) or "none found"),
              file=sys.stderr)
        if expect and expect not in celexes:
            print("# WARNING: expected CELEX %s not among the page's EUR-Lex links"
                  % expect, file=sys.stderr)
        for title, url, _ in skipped:
            print("# no PDF resolved (check by hand): %s -- %s" % (title, url),
                  file=sys.stderr)
        print("# --- review before pasting: keep only genuine guidance ON the act, "
              "drop factsheets / impact assessments / general policy ---")
        print(guidance_discover.frontmatter_block(resolved))


SOURCES["kommentar"] = Source(
    "kommentar",
    lambda: sorted(wiki_parse.kommentar_index(str(WIKI_ROOT))),
    {"parse": Stage("parse", kommentar_parse_run, kommentar_artifact,
                    inputs=lambda bf: [kommentar_record(bf)], code=WIKI_CODE)},
    actions={"validate": kommentar_validate, "ai-annotate": kommentar_ai_annotate,
             "discover-guidance": kommentar_discover_guidance,
             "propose-guidance": kommentar_propose_guidance},
    notes="validate: report commentary section anchors with no matching node in "
          "the annotated act (also warned during relate)\n"
          "ai-annotate <basefile>: LLM-link the declared guidance PDFs to the "
          "act's articles, written as a .ann sidecar\n"
          "discover-guidance: crawl the Commission guidance sites to (re)build the "
          "CELEX -> guidance-page index\n"
          "propose-guidance <dg-page-url | CELEX> [<CELEX>]: scrape a Commission "
          "guidance page (or look it up by CELEX) for a draft `guidance:` block "
          "(no LLM)")

SOURCES["begrepp"] = Source(
    "begrepp",
    lambda: sorted(wiki_parse.begrepp_index(str(WIKI_ROOT))),
    {"parse": Stage("parse", begrepp_parse_run, begrepp_artifact,
                    inputs=lambda bf: [begrepp_record(bf)], code=WIKI_CODE)})


# the site vertical: lagen.nu's editorial chrome (curated frontpage, /om about
# pages, sitenews), authored as markdown in the same lagen-wiki content repo
# (site/). It is parsed to artifacts and rendered during generate, but -- like
# remisser -- it carries no citation graph, so it is absent from ARTIFACTS below
# and is never related/indexed/dumped.
SITE_CODE = (PKG / "site" / "parse.py", PKG / "site" / "model.py",
             PKG / "lib" / "markdown.py")


def site_record(basefile):
    return site_parse.record(str(WIKI_ROOT), basefile)


def site_artifact(basefile):
    return layout.artifact("site", basefile)


def site_parse_run(basefile):
    write_artifact("site", basefile, site_parse.artifact(str(WIKI_ROOT), basefile))


SOURCES["site"] = Source(
    "site",
    lambda: site_parse.list_basefiles(str(WIKI_ROOT)),
    {"parse": Stage("parse", site_parse_run, site_artifact,
                    inputs=lambda bf: [site_record(bf)], code=SITE_CODE)})


def rebuild_after_commit(changes):
    """Regenerate the static pages an inline-editor commit touched, in dependency
    order: re-parse the changed markdown -> relate the affected wiki source(s) so
    the catalog picks up new/edited commentary edges -> regenerate just the
    touched host/concept pages (and, for editorial edits, the site pages). Reuses
    the exact stage functions the `lagen` CLI runs; a web request mints no run id,
    so the ledger emissions inside them no-op (see `RUN_ID`). `changes` is the
    list `editcart.commit` returns -- `{"kind": kommentar|begrepp|site,
    "basefile": …}`. Returns the public URLs of the rebuilt pages.

    Called from `api/edit.py` (the write side of the service). build already
    imports `api.app`; this is the one call back the other way, used only at
    request time, never at import time -- so the mutual reference stays sound."""
    kommentar = [c["basefile"] for c in changes if c["kind"] == "kommentar"]
    begrepp = [c["basefile"] for c in changes if c["kind"] == "begrepp"]
    site = [c["basefile"] for c in changes if c["kind"] == "site"]
    for bf in kommentar:
        kommentar_parse_run(bf)
    for bf in begrepp:
        begrepp_parse_run(bf)
    for bf in site:
        site_parse_run(bf)
    relate = [n for n, present in (("kommentar", kommentar), ("begrepp", begrepp))
              if present]
    if relate:
        cmd_relate(relate)
    urls = []
    for bf in kommentar:                 # a commentary rides its host act's page
        host = layout.kommentar_host(bf)
        cmd_generate(only={str(layout.artifact(host, bf))}, source=host)
        urls.append(layout.page_url(wiki_parse.host_uri(bf)))
    for bf in begrepp:
        cmd_generate(only={str(layout.artifact("begrepp", bf))}, source="begrepp")
        urls.append(layout.page_url(markdown.begrepp_uri(bf)))
    if site:
        cmd_generate(source="site")      # write_site rewrites all editorial pages
        urls += ["/" if bf == "frontpage" else "/" + bf for bf in site]
    return urls


# wire the editor's commit endpoint to the rebuild above (build imports the api
# package, so this is the sound direction to close the loop -- see api/edit.py)
api_edit.set_rebuild(rebuild_after_commit)


def reparse_one(source, basefile):
    """Force-reparse one document, writing its artifact JSON in place -- the
    patch editor's post-save hook, so a just-saved patch is immediately effective
    in the corpus (the artifact is what the API and the next `generate` read).
    Reuses the source's own parse recipe, like `rebuild_after_commit` does for
    the markdown editor."""
    stage = SOURCES[source].stages.get("parse")
    if stage is None:
        raise ValueError("source %r has no parse stage" % source)
    stage.run(basefile)


api_patch.set_reparse(reparse_one)


# --------------------------------------------------------------------------
# derived layer: relate (catalog) + generate (static site). Corpus-wide verbs,
# not per-document Stages, for two reasons: relate writes shared catalog rows
# (not one output file per basefile), and a doc's generated HTML has a
# *data-dependent* prerequisite set -- its own artifact plus the artifacts of
# exactly the documents that cite it (its inbound set, read from the catalog;
# the old pipeline's deps files). That set isn't expressible in the static
# Stage.inputs(basefile) protocol. For now both rebuild whole; a per-doc
# incremental generate would key off that inbound set.
# --------------------------------------------------------------------------

ARTIFACTS = {
    # the versions-stage sidecars live next to the main artifacts but describe
    # historical consolidations -- not corpus documents, so not related/dumped
    "sfs": lambda: sorted(p for p in layout.SFS_ARTIFACT.glob("*/*.json")
                          if not p.name.endswith(".versions.json")),
    # dv + kommentar go through layout.artifacts(), the single home that already
    # excludes the non-document index sidecars (DOM_INDEX / guidance-index.json)
    # -- no hand-globbed carve-out here (else the exclusion drifts across surfaces)
    "dv": lambda: layout.artifacts("dv"),
    "forarbete": lambda: sorted(layout.artifact_dir("forarbete").glob("*/*.json")),
    "kommentar": lambda: layout.artifacts("kommentar"),
    "begrepp": lambda: sorted(layout.artifact_dir("begrepp").glob("*.json")),
    "eurlex": lambda: sorted(layout.artifact_dir("eurlex").glob("*/*.json")),
    "foreskrift": lambda: sorted(
        layout.artifact_dir("foreskrift").glob("*/*.json")),
    "avg": lambda: sorted(layout.artifact_dir("avg").glob("*/*.json")),
}


# relate's per-source extraction (the documents/links it derives per artifact)
# lives wholly in catalog.py; index's unit shape + body extraction in
# search.py + text.py. A change to these re-stales the corresponding step the same
# way a parser edit re-stales parse (recipe-version rule).
RELATE_CODE = (PKG / "lib" / "catalog.py",)
INDEX_CODE = (PKG / "lib" / "search.py", PKG / "lib" / "text.py")
DUMP_CODE = (PKG / "lib" / "dump.py",)


def cmd_relate(names):
    """(Re)build each named source's rows in the shared catalog from its
    artifacts on disk -- documents + the citation edges they carry inline.
    Incremental on artifact content (unchanged artifacts are skipped); editing
    the extraction code (catalog.py) or passing --force re-extracts every
    artifact of the affected source."""
    store = load_watermarks()
    # a missing catalog invalidates every watermark -- the rows it claims are
    # current don't exist, so nothing may be skipped (matches stale_sources())
    catalog_missing = not CATALOG.exists()
    dirty = False
    for name in names:
        if name not in ARTIFACTS:
            continue
        paths = ARTIFACTS[name]()
        wm = file_watermark(paths)
        if not catalog_missing and up_to_date(store, "relate", name, wm,
                                              RELATE_CODE):
            print("relate %s: up to date (%d artifacts unchanged) -- skipped"
                  % (name, len(paths)))
            _emit_segment("relate", name, 0.0, total=len(paths), ran=0,
                          skipped_fresh=len(paths), status="skipped")
            continue

        def progress(seen, total, changed, current, name=name):
            util.status(seen, total, "relate %s  %d changed  %s"
                        % (name, changed, current))

        recode = code_changed(store, "relate", name, RELATE_CODE)
        if recode and not RUN.force:
            print("relate %s: extraction code changed -- re-extracting all" % name)
        t0 = time.perf_counter()
        docs, edges, changed = catalog.rebuild(
            CATALOG, name, paths, progress=progress, force=RUN.force or recode)
        _emit_segment("relate", name, time.perf_counter() - t0, total=docs,
                      ran=changed, status="ok")
        record_step(store, "relate", name, wm, RELATE_CODE)
        dirty = True
        sys.stderr.write("\n")
        print("relate %s: %d documents, %d links (%d re-extracted this run)"
              % (name, docs, edges, changed))

    # cross-document post-passes (need the whole catalog, so they run last): pin
    # each förarbete genomför-direktiv statement to the SFS paragraf it transposes,
    # load the SFS correspondence (.corr) layers, and mint a stub begrepp node for
    # every defined term / nyckelord the corpus references. Their inputs are the
    # catalog (changed only if a source was re-related above) and the .corr files,
    # so a no-op run skips them too -- gated on a .corr watermark.
    corr_wm = file_watermark(sorted(layout.SFS_ARTIFACT.glob("*/*.corr")))
    if dirty or RUN.force or not watermark_fresh(store, "relate", "__corr__",
                                                 corr_wm):
        t0 = time.perf_counter()
        con = catalog.connect(CATALOG)
        pinned = fa_genomforande.resolve(con)
        corr = [row for p in layout.SFS_ARTIFACT.glob("*/*.corr")
                for row in sfs_correspond.corr_rows(json.loads(p.read_text()))]
        catalog.set_correspondence(con, corr)
        folded = catalog.canonicalize_concepts(con)
        concepts = catalog.synthesize_concepts(con)
        anchor_warnings = kommentar_anchor_warnings(con)
        con.close()
        _emit_segment("relate", "__corr__", time.perf_counter() - t0, status="ok")
        record_watermark(store, "relate", "__corr__", corr_wm)
        dirty = True
        print("relate: %d genomför-direktiv relations pinned to SFS paragrafs"
              % pinned)
        print("relate: %d old->new paragraf correspondences loaded from .corr "
              "layers" % len(corr))
        print("relate: %d inflected concept variants folded onto canonical begrepp"
              % folded)
        print("relate: %d concept stubs minted from defined terms + nyckelord"
              % concepts)
        for bf, host, anchors in anchor_warnings:
            print("relate: WARNING kommentar %s annotates %s but has no matching "
                  "node for %s -- check the heading numbering"
                  % (bf, host, ", ".join(anchors)))
    else:
        print("relate: nothing changed -- cross-document passes skipped")
        _emit_segment("relate", "__corr__", 0.0, status="skipped")
    if dirty:
        save_watermarks(store)
    print("catalog: %s" % CATALOG)


def cmd_index(names, jobs=1):
    """Sync the OpenSearch full-text index for each named source from the catalog
    + artifacts -- a whole-document unit plus one fragment per § node, the
    paragraph-precise search behind the killer feature. Incremental: only new or
    content-changed documents are (re)indexed and vanished ones dropped, so a
    re-run with nothing changed is cheap. Editing the index code (search.py /
    text.py) or passing --force reindexes every document of the affected source.
    `relate` is its prerequisite (run that first). `jobs>1` fans the bulk
    round-trips across threads. Needs a running OpenSearch (OPENSEARCH_URL,
    default http://localhost:9200)."""
    store = load_watermarks()
    dirty = False
    index = search.SearchIndex()
    con = catalog.connect(CATALOG)
    # a dropped index invalidates every watermark -- skipping would leave the
    # source's docs unindexed, so nothing may be skipped until it's rebuilt
    index_present = index.exists()
    for name in names:
        if name not in ARTIFACTS:
            continue
        wm = catalog.source_content_signature(con, name)
        if index_present and up_to_date(store, "index", name, wm, INDEX_CODE):
            print("index %s: up to date (catalog unchanged) -- skipped" % name)
            _emit_segment("index", name, 0.0, ran=0, status="skipped")
            continue

        def progress(seen, total, current="", name=name):
            util.status(seen, total, "index %s  %s" % (name, current))
        recode = code_changed(store, "index", name, INDEX_CODE)
        if recode and not RUN.force:
            print("index %s: index code changed -- reindexing all" % name)
        t0 = time.perf_counter()
        docs, indexed, errors, missing, skipped, deleted = index.index_source(
            con, name, progress=progress, jobs=jobs, force=RUN.force or recode)
        _emit_segment("index", name, time.perf_counter() - t0, total=docs,
                      ran=indexed, errors=len(errors), skipped_fresh=skipped,
                      status="errors" if errors else "ok")
        record_step(store, "index", name, wm, INDEX_CODE)
        dirty = True
        sys.stderr.write("\n")
        print("index %s: %d documents -> %d units indexed, %d up to date, "
              "%d deleted, %d errors"
              % (name, docs, indexed, skipped, deleted, len(errors)))
        if missing:
            print("index %s: %d catalogued artifacts gone from disk, skipped "
                  "(run `lagen %s relate` to prune): %s"
                  % (name, len(missing), name, ", ".join(missing[:5])
                     + (" ..." if len(missing) > 5 else "")))
    con.close()
    if dirty:
        save_watermarks(store)
    print("search index '%s' on %s" % (search.INDEX, config.OPENSEARCH_URL))


def cmd_dump(names):
    """Write a gzipped NDJSON bulk dump per named source -- every artifact, one
    compact JSON per line, byte-equivalent to the on-disk artifact (the citation
    graph is already inline, so each line is self-contained). The machine-
    readable corpus export that replaces the retired RDF/Fuseki dumps."""
    DUMPS.mkdir(parents=True, exist_ok=True)
    store = load_watermarks()
    dirty = False
    for name in names:
        if name not in ARTIFACTS:
            continue
        out = DUMPS / ("%s.ndjson.gz" % name)
        paths = ARTIFACTS[name]()
        wm = file_watermark(paths)
        if out.exists() and up_to_date(store, "dump", name, wm, DUMP_CODE):
            print("dump %s: up to date (%d artifacts unchanged) -- skipped"
                  % (name, len(paths)))
            _emit_segment("dump", name, 0.0, total=len(paths), ran=0,
                          skipped_fresh=len(paths), status="skipped")
            continue

        def progress(seen, total, name=name):
            util.status(seen, total, "dump %s" % name)
        t0 = time.perf_counter()
        lines = dump.dump_source(paths, out, progress=progress)
        _emit_segment("dump", name, time.perf_counter() - t0, total=lines,
                      ran=lines, status="ok")
        record_step(store, "dump", name, wm, DUMP_CODE)
        dirty = True
        sys.stderr.write("\n")
        print("dump %s: %d documents -> %s" % (name, lines, out))
    if dirty:
        save_watermarks(store)


def cmd_download_all(names, jobs):
    """Upstream discovery + fetch for each named source: the bulk harvest where a
    source has one (sweeping in newly-published documents), else its per-document
    download stage over the ids it already knows. The slow, network-bound head of
    the pipeline -- kept separate from `rebuild` so the offline rebuild stays fast.
    A source derived from another's dump (kommentar/begrepp) has nothing to fetch
    and is skipped."""
    had_errors = False
    for name in names:
        source = SOURCES[name]
        if source.harvest is not None:
            if source.origin:
                print("Downloading %s from %s" % (name, source.origin), flush=True)
            t0 = time.perf_counter()
            try:
                source.harvest([])                       # [] = full discovery
                _emit_segment("download", name, time.perf_counter() - t0, status="ok")
            except Exception:  # noqa: BLE001 — per-source resilience point: one source's harvest failure must not abort the remaining sources; printed + nonzero exit at end (rule:no-catch-log-continue)
                traceback.print_exc()
                _emit_segment("download", name, time.perf_counter() - t0,
                              status="errors", errors=1)
                had_errors = True
        elif "download" in source.stages:
            basefiles = source.list_basefiles()
            result = run_action(source, "download", basefiles, jobs)
            report(source, "download", result, len(basefiles), full_source=True)
            had_errors |= bool(result.errors)
    return had_errors


def _run_stage_gated(source, step, jobs, store):
    """Run a watermark-gated per-document stage (parse/versions) over a whole
    source. Coarse gate: if the stage's inputs + recipe are unchanged, skip the
    per-doc freshness scan (which content-hashes every input) wholesale -- "up to
    date -- skipped"; else run it and, on a clean sweep, record the watermark in
    `store` so the next run can skip. Shared by `cmd_all` and the single-source
    dispatch so a direct `lagen <src> parse` gets the same shortcut. Returns
    (had_errors, recorded) -- `recorded` tells the caller to save `store`."""
    pcode = source.stages[step].code
    wm = stage_watermark(source, step)
    if up_to_date(store, step, source.name, wm, pcode):
        print("%s %s: up to date -- skipped" % (step, source.name))
        # bypasses report(); emit the skipped segment so the run detail still
        # shows the whole pipeline (§2)
        _emit_segment(step, source.name, 0.0, ran=0, status="skipped")
        return False, False
    basefiles = source.list_basefiles()
    result = run_action(source, step, basefiles, jobs)
    report(source, step, result, len(basefiles), full_source=True)
    # only watermark a clean sweep: a failed doc leaves the source un-marked so
    # the next run retries it (and re-surfaces the error) rather than skipping
    if result.errors:
        return True, False
    record_step(store, step, source.name, wm, pcode)
    return False, True


def cmd_all(names, jobs, whole_corpus, download=False):
    """Run the build pipeline for the named sources. The offline core (action
    `rebuild`) is parse -> relate -> index -> dump -> generate; action `all`
    prepends the network-bound download. Each step is independently incremental,
    so a re-run with nothing changed is cheap.

    parse runs over each source's already-downloaded basefiles (bringing only
    missing/stale parses up to date; with `download=False` it discovers nothing
    new, so it makes no network calls). relate/index/dump act on the named
    sources; generate rebuilds the whole corpus when the run targets `all`
    sources, else just the named sources' pages."""
    had_errors = False
    if download:
        had_errors = cmd_download_all(names, jobs)
    store = load_watermarks()
    parse_dirty = False
    for step in ("parse", "versions"):
        for name in names:
            source = SOURCES[name]
            if step not in source.stages:
                continue
            errs, recorded = _run_stage_gated(source, step, jobs, store)
            had_errors |= errs
            parse_dirty |= recorded
    if parse_dirty:
        save_watermarks(store)
    cmd_relate(names)
    cmd_index(names, jobs)
    cmd_dump(names)
    if whole_corpus:
        cmd_generate(jobs=jobs)
    else:
        for name in names:
            cmd_generate(source=name, jobs=jobs)
    return had_errors


def stale_sources():
    """Sources whose artifacts have changed since the catalog was last built
    (make's rule: a prerequisite newer than the target). A missing catalog
    makes every source stale; --force re-relates all."""
    if RUN.force or not CATALOG.exists():
        return list(ARTIFACTS)
    cutoff = CATALOG.stat().st_mtime
    return [name for name, lister in ARTIFACTS.items()
            if any(p.stat().st_mtime > cutoff for p in lister())]


# a page's rendered HTML is a function of the render/query code plus the
# artifacts in its prerequisite set (computed per page from the catalog)
GENERATE_CODE = (PKG / "lib" / "render.py", PKG / "lib" / "catalog.py",
                 PKG / "lib" / "markdown.py", PKG / "lib" / "layout.py",
                 PKG / "lib" / "history.py", PKG / "lib" / "casenaming.py",
                 PKG / "lib" / "eu_structure.py", PKG / "site" / "render.py")


def generate_watermark():
    """The coarse gate for a full-corpus generate: the whole-catalog content
    signature plus the .corr/.ann/.versions.json sibling layers that relate
    doesn't fold into content_hash. Unchanged (with the render code) ⟹ every
    page is fresh, so the ~100k-page freshness scan can be skipped wholesale."""
    con = catalog.connect(CATALOG)
    sig = catalog.catalog_signature(con)
    con.close()
    sides = file_watermark(sorted(
        list(layout.SFS_ARTIFACT.glob("*/*.corr"))
        # the versions-stage sidecars: a new historical consolidation must
        # re-render its statute's page (version panel) + the version pages
        + list(layout.SFS_ARTIFACT.glob("*/*.versions.json"))
        + list(layout.artifact_dir("eurlex").glob("*/*.ann"))
        # the kommentar ai-annotate guidance layer rides a *different* document's
        # rail (the host act's), so -- like the cross-document .corr case -- a full
        # or forced generate is what propagates an edit to the host page
        + list(layout.artifact_dir("kommentar").rglob("*.ann"))
        # the site artifacts (frontpage/om/sitenews) aren't catalog rows, so the
        # catalog signature above never sees them -- fold them in directly so a
        # re-parsed editorial edit reopens the generate gate (else a full generate
        # would skip and ship the stale site)
        + list(layout.artifact_dir("site").rglob("*.json"))))
    return hashlib.sha256((sig + "\x1f" + sides).encode()).hexdigest()


def sfs_version_pages(sidecars):
    """The historical-consolidation ("lydelse") pages to render, one (uri,
    source, path, title) row per parsed version, read from the given
    versions-stage sidecars. They are not catalog rows -- versions carry no
    citations or search entries -- so generate appends them to the plan as
    extra pages."""
    rows = []
    for sc in sidecars:
        if not sc.exists():
            continue
        basefile = layout.sfs_sidecar_basefile(sc)
        for entry in json.loads(sc.read_text())["versions"]:
            version = entry["version"]
            rows.append((entry["uri"], "sfs",
                         str(layout.sfs_version_artifact(basefile, version)),
                         "SFS %s i lydelse enligt SFS %s" % (basefile, version)))
    return rows


def cmd_generate(only=None, source=None, jobs=1):
    """Render every catalogued document to static HTML, with live outbound
    links and inbound annotations queried from the catalog, plus a frontpage.
    Auto-runs `relate` first for any source whose artifacts are newer than the
    catalog -- relate is generate's upstream dependency.

    Incremental like parse: a page is re-rendered only when its prerequisite
    artifacts (itself + the documents citing it + the documents it cites) or the
    render code changed. `--force` rebuilds all; `--ignore-code-changes` ignores
    the render-code version (rebuild only on data changes).

    `source` restricts the run to one source's pages (`lagen <source> generate`);
    `only`, a set of artifact path strings, restricts it to those documents
    (`lagen <source> generate <id>`). Either scoping leaves the corpus-wide
    aggregate pages as they are and uses the catalog as-is (no auto-relate).

    `--aggregates-only` rewrites just the corpus-wide pages (frontpage + browse
    indexes) from the current catalog, skipping the per-document render -- a
    seconds-long refresh after a frontpage/browse change, not a full rebuild."""
    # segment source: the whole-site run reports under __site__, a scoped
    # per-source render (`lagen <src> generate`) under that source's name
    seg_source = source or "__site__"
    t0 = time.perf_counter()
    if source == "site":
        # `lagen site generate`: rewrite just the editorial pages from the current
        # site artifacts (the generic per-document/aggregate paths below have no
        # site rows to render). Kept here in the driver -- lib/render never learns
        # the `site` name.
        site_render.write_site(GENERATED)
        print("generate: rebuilt site pages (frontpage, /om, sitenews) -> %s" % GENERATED)
        _emit_segment("generate", "site", time.perf_counter() - t0, status="ok")
        return
    if RUN.aggregates_only:
        con = catalog.connect(CATALOG)
        render.render_aggregates(con, GENERATED, CATALOG,
                                 write_index=not site_render.has_frontpage())
        con.close()
        site_render.write_site(GENERATED)
        print("generate: rebuilt frontpage + browse indexes + site pages -> %s" % GENERATED)
        _emit_segment("generate", "__site__", time.perf_counter() - t0, status="ok")
        return

    # a full generate auto-relates any stale source first (relate is its upstream
    # dependency); a scoped render skips that corpus-wide scan and uses the catalog
    # as-is -- run `lagen <source> relate` to refresh it
    scoped = only is not None or source is not None
    stale = [] if scoped else stale_sources()
    if stale:
        print("catalog stale for %s -- relating first" % ", ".join(stale))
        cmd_relate(stale)

    # full-corpus generate: a coarse gate over the whole catalog + .corr/.ann
    # layers + render code. All unchanged since the last full generate ⟹ every
    # page is fresh, so skip the per-page scan entirely (the manifest, big, isn't
    # even loaded). A scoped render keeps the per-page path.
    site_wm = None
    if not scoped:
        store = load_watermarks()
        site_wm = generate_watermark()
        if up_to_date(store, "generate", "__site__", site_wm, GENERATE_CODE):
            print("generate: up to date -- skipped (%s)" % GENERATED)
            _emit_segment("generate", "__site__", 0.0, ran=0, status="skipped")
            return

    manifest = load_manifest()
    code_version = recipe_version(GENERATE_CODE)
    updates = {}
    own_hash = {}                # artifact path -> content hash, memoized per run

    def page_signature(art_path, dep_digest, content_hash):
        # only the page's OWN artifact enters the signature (it changes when the doc
        # is re-parsed); its neighbours enter via dep_digest as a set of
        # relationships, not their contents -- an immutable case re-appearing
        # unchanged must not invalidate every law it cites. The artifact's bytes are
        # NOT re-read here: relate already stored their sha256 as the catalog's
        # `content_hash`, so generate reuses it instead of re-hashing all ~6.3 GB in
        # the single-threaded planning loop (§2.1). Only the page's sibling LLM
        # layers are read from disk (they aren't catalogued), so authoring or editing
        # one re-renders just that page: `.ann` (eurlex ai-annotate) and `.corr` (sfs
        # ai-correspond, the new statute's corresponding-cases margin). The *old*
        # statute's margin reads a `.corr` next to a different document, so a `.corr`
        # edit reaches it only via relate + a full/forced generate.
        p = str(art_path)
        if p not in own_hash:
            # a synthesized concept stub has no artifact on disk (empty path) and so
            # no sibling layers; an uncatalogued page (sfs historical consolidation)
            # has an artifact but no catalog content_hash, so its bytes are hashed
            # directly. .versions.json is the sfs versions-stage sidecar: an archived
            # consolidation appearing re-renders the statute's page (its version
            # panel lists the new lydelse).
            fp = Path(p) if p else None
            sides = ((fp.with_suffix(".ann"), fp.with_suffix(".corr"),
                      fp.with_suffix(".versions.json")) if fp else ())
            base = content_hash if content_hash is not None else (
                catalog.content_hash(fp.read_bytes()) if fp and fp.exists() else "")
            own_hash[p] = hashlib.sha256(base.encode() + b"".join(
                s.read_bytes() if s.exists() else b"" for s in sides)).hexdigest()
        return hashlib.sha256((own_hash[p] + dep_digest).encode()).hexdigest()

    def fresh(uri, out_path, art_path, dep_digest, content_hash):
        if RUN.force or not out_path.exists():
            return False
        entry = manifest.get(manifest_key("generate", "page", uri))
        return bool(entry) \
            and entry["inputs"] == page_signature(art_path, dep_digest, content_hash) \
            and (RUN.ignore_code_changes or entry["version"] == code_version)

    def record(uri, art_path, dep_digest, content_hash):
        updates[manifest_key("generate", "page", uri)] = {
            "inputs": page_signature(art_path, dep_digest, content_hash),
            "version": code_version}

    def progress(done, total, current="", rendered=0):
        util.status(done, total, "generate  %d rendered  %s" % (rendered, current))

    # the sfs historical-consolidation pages ride along whenever the run covers
    # sfs: the whole corpus, the sfs source, or specific sfs documents (whose
    # sidecars sit next to the artifacts named in `only`)
    if only is not None:
        extra = sfs_version_pages([Path(p).with_suffix(".versions.json")
                                   for p in only
                                   if Path(p).is_relative_to(layout.SFS_ARTIFACT)])
    elif source in (None, "sfs"):
        extra = sfs_version_pages(
            sorted(layout.SFS_ARTIFACT.glob("*/*.versions.json")))
    else:
        extra = []

    total, rendered = render.generate_site(CATALOG, GENERATED, progress=progress,
                                           fresh=fresh, record=record, only=only,
                                           source=source, jobs=jobs, extra=extra,
                                           write_index=not site_render.has_frontpage())
    if not scoped:                       # editorial pages ride a full-corpus run
        site_render.write_site(GENERATED)
    sys.stderr.write("\n")
    if updates:
        manifest.update(updates)
        save_manifest(manifest)
    if not scoped:                       # record the site watermark for next time
        record_step(store, "generate", "__site__", site_wm, GENERATE_CODE)
        save_watermarks(store)
    _emit_segment("generate", seg_source, time.perf_counter() - t0, total=total,
                  ran=rendered, status="ok")
    if only is not None and not total:
        print("generate: no catalogued document matched %d requested id(s) -- "
              "parse/relate them first" % len(only))
        return
    print("generate: %d pages (%d rendered, %d fresh)%s -> %s"
          % (total, rendered, total - rendered,
             " [scoped; aggregates untouched]" if scoped else "", GENERATED))
    if not scoped:
        print("serve with: lagen all serve   (then open http://localhost:8000/)")


def cmd_serve(host="127.0.0.1", port=8000):
    # one process serves the whole thing: the static site and the REST API it
    # consumes (the API answers under /api/v1/, the site is everything else).
    # Same origin, so the ⌘K palette needs no second port.
    if not GENERATED.exists():
        raise SystemExit("nothing generated yet -- run `lagen all generate` first")
    # show the LAN-reachable host when bound to a wildcard, else localhost
    shown = "localhost" if host in ("127.0.0.1", "localhost") else host
    print("serving site + API at http://%s:%d/  "
          "(API under /api/v1/, docs at /docs, Ctrl-C to stop)" % (shown, port))
    api_app.serve(str(GENERATED), host=host, port=port)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def status_scan(source, manifest, errors):
    """Structured per-stage status for one source: {total, fresh, stale, missing,
    empty, failed} per stage, `failed` a list of the basefiles with a live
    errors.json entry (so "failed" ≠ "never tried"). `empty` counts zero-byte
    outputs -- the SkipDocument marker (a deliberately empty document), the stat
    already paid by the exists-check. `errors` is the errors.json store keyed
    "<source>/<stage>/<basefile>". Lives here (not lib) because it needs
    is_fresh/Stage, which can't move to lib (build.py imports the API app)."""
    basefiles = source.list_basefiles()
    out = {}
    for name, stage in source.stages.items():
        fresh = stale = missing = empty = 0
        failed = []
        for bf in basefiles:
            if errors.get("%s/%s/%s" % (source.name, name, bf)):
                failed.append(bf)
            output = stage.output(bf)
            if not output.exists():
                missing += 1
            elif output.stat().st_size == 0:
                empty += 1
            elif is_fresh(manifest, source, stage, bf):
                fresh += 1
            else:
                stale += 1
        out[name] = {"total": len(basefiles), "fresh": fresh, "stale": stale,
                     "missing": missing, "empty": empty, "failed": failed}
    return out


def cmd_status(source):
    """Full, authoritative recompute of `source`'s per-stage health, printed and
    written to status.json as the exact snapshot cells (the CLI-only exact writer;
    the cheap per-segment writer in report() covers full-source pipeline runs)."""
    manifest = load_manifest()
    scan = status_scan(source, manifest, runlog.read_errors(ERRORS))
    total = next(iter(scan.values()))["total"] if scan else 0
    print("%s: %d basefiles" % (source.name, total))
    for name, st in scan.items():
        print("  %-10s %6d fresh  %6d stale  %6d missing  %6d failed  %6d empty"
              % (name, st["fresh"], st["stale"], st["missing"],
                 len(st["failed"]), st["empty"]))
        runlog.update_status_cell(STATUS, source.name, name, {
            "total": st["total"], "fresh": st["fresh"], "stale": st["stale"],
            "missing": st["missing"], "failed": len(st["failed"]),
            "empty": st["empty"], "run": RUN_ID})


def report(source, action, result, requested, full_source):
    """Print one action's outcome and fold it into the run instrumentation:
    emit the (action, source) segment, apply the per-doc outcomes to errors.json
    and -- only when the run covered the whole source (`full_source`, no explicit
    basefile args) -- write the cheap status.json cell. All emissions are no-ops
    without a run id (--dry-run, non-pipeline verbs)."""
    verb = "would run" if RUN.dry_run else "ran"
    skipped = requested - len({bf for _, bf in result.planned}) \
        - len({bf for _, bf, _, _ in result.errors})
    print("%s %s (%d basefiles): %s %d, skipped (fresh) %d, errors %d" % (
        source.name, action, requested, verb, len(result.planned),
        skipped, len(result.errors)))
    for stage, bf, msg, _tb in result.errors[:20]:
        print("  ERROR %s %s: %s" % (stage, bf, msg))
    secs = sum(s for _, _, s in result.timings)
    slowest = sorted(((bf, s) for _, bf, s in result.timings),
                     key=lambda x: x[1], reverse=True)
    _emit_segment(action, source.name, secs, total=requested,
                  ran=len(result.done), errors=len(result.errors),
                  skipped_fresh=skipped, skipdoc=len(result.skips),
                  status="errors" if result.errors else "ok", slowest=slowest)
    # clear stale errors for docs (re)built this run AND for docs skipped as
    # fresh -- both mean the doc now has a valid artifact and is not failing
    _apply_outcomes(source.name, result.errors, result.done + result.fresh)
    # a full-source run proves the current basefile set is complete, so error
    # entries for basefiles it no longer lists are orphans (a doc left the corpus,
    # or an enumerator bug once emitted it) -- drop them, since they are never
    # re-run and fresh-skip healing can't reach them
    if full_source:
        _reconcile_orphans(source.name, source.list_basefiles())
    if RUN_ID is not None:
        # scope the failing count to THIS source -- a `lagen dv parse` must not
        # report another source's errors (the store holds every source's)
        prefix = source.name + "/"
        failing = sum(1 for k in runlog.read_errors(ERRORS)
                      if k.startswith(prefix))
        print("%d docs failing in %s" % (failing, source.name))
    # cheap cell: a full-source run proves the source -- everything planned+done
    # is now fresh, nothing missing (§1c). A targeted run must NOT touch the cell.
    if full_source:
        _update_status_cell(source.name, action, {
            "total": requested, "fresh": requested - len(result.errors),
            "stale": 0, "missing": 0, "failed": len(result.errors),
            "empty": len(result.skips), "run": RUN_ID})


def _help(name):
    """Contextual `lagen <source> -h`: the source's actions, harvest scopes and
    any source-specific flags."""
    src = SOURCES[name]
    verbs = ["download"] if src.harvest else []
    verbs += [s for s in src.stages if s not in verbs] + list(src.actions)
    print("usage: lagen %s <action> [ids|scopes] [options]" % name)
    if src.origin:
        print("\nsource:  %s" % src.origin)
    print("actions: %s" % ", ".join(verbs))
    if src.scopes:
        print("\ndownload scopes (narrow the harvest to sub-corpora):")
        print("  %s" % ", ".join(sorted(src.scopes)))
        print("  e.g. `lagen %s download %s`   (no scope = the whole corpus)"
              % (name, sorted(src.scopes)[0]))
    if src.harvest is not None and "download" in src.stages:
        print("\n`lagen %s download <id>` refetches a single document by id." % name)
    if src.notes:
        print("\n%s" % src.notes)
    print("\nglobal options: `lagen -h`")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # contextual help: `lagen <source> [action] -h` -> that source's help
    if "-h" in argv or "--help" in argv:
        leading = next((a for a in argv if not a.startswith("-")), None)
        if leading in SOURCES:
            _help(leading)
            return
    p = argparse.ArgumentParser(prog="lagen", description=(__doc__ or "").split("\n")[0])
    p.add_argument("source", help="source name (%s) or 'all'"
                   % ", ".join(SOURCES))
    p.add_argument("action",
                   help="download | parse | relate | generate | index | dump "
                        "| rebuild | all | serve | status | patch-show | mkpatch "
                        "| a source action (e.g. dv reindex). `rebuild` runs the "
                        "offline pipeline (parse -> relate -> index -> dump -> "
                        "generate) over already-downloaded data; `all` is "
                        "download followed by rebuild. Every step is incremental, "
                        "so a no-change re-run is cheap")
    p.add_argument("basefiles", nargs="*",
                   help="ids to act on (empty = all stale); for download, names "
                        "harvest sub-scopes, e.g. 'prop' or 'acts'")
    p.add_argument("-f", "--force", action="store_true",
                   help="rebuild the named stage even if fresh")
    p.add_argument("--no-deps", action="store_true",
                   help="run only the named stage, not its upstream deps")
    p.add_argument("--ignore-code-changes", action="store_true",
                   help="treat outputs as fresh even when the recipe code changed "
                        "(parse/generate, and the extraction/index code behind "
                        "relate/index) -- rebuild only on input-data changes "
                        "(dev convenience; off in production)")
    p.add_argument("--aggregates-only", action="store_true",
                   help="generate: rewrite only the corpus-wide pages (frontpage "
                        "+ browse indexes) from the catalog, skipping the "
                        "per-document render")
    p.add_argument("-j", "--jobs", type=int, default=None,
                   help="parallel workers for the parallelisable steps (parse, "
                        "index); default = number of CPU cores, `-j1` to serialise")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="print the plan, do nothing")
    p.add_argument("--port", type=int, default=8000,
                   help="port for `serve` -- site + API in one process (default 8000)")
    p.add_argument("--host", default="127.0.0.1", metavar="ADDR",
                   help="interface for `serve` to bind (default 127.0.0.1, "
                        "localhost only; use 0.0.0.0 to expose on the LAN)")
    p.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD",
                   help="eurlex download: only discover documents dated on/after "
                        "this (overrides the per-sector watermark for this run)")
    p.add_argument("--lang", metavar="CODES",
                   help="eurlex download: comma-separated languages (default swe,eng)")
    p.add_argument("--source", dest="discovery", choices=("sparql", "soap"),
                   default="sparql",
                   help="eurlex download: discovery backend (default sparql)")
    p.add_argument("--only", metavar="BASEFILE",
                   help="forarbete download: fetch just this one document "
                        "(needs exactly one doctype scope)")
    p.add_argument("--riksmote", metavar="YYYY/YY",
                   help="forarbete download bet: narrow the harvest to one "
                        "riksmöte, e.g. 2025/26 (bet scope only)")
    p.add_argument("--limit", type=int, metavar="N",
                   help="import-legacy (avg/forarbete): import at most N "
                        "documents (a test slice)")
    p.add_argument("--rot13", action="store_true",
                   help="mkpatch: store the patch rot13-obfuscated, so a "
                        "redaction of personal data is not plain-text googleable "
                        "in the committed patch")
    args = p.parse_args(argv)

    RUN.dry_run, RUN.force, RUN.no_deps = args.dry_run, args.force, args.no_deps
    RUN.ignore_code_changes = args.ignore_code_changes
    RUN.aggregates_only = args.aggregates_only
    RUN.since, RUN.lang, RUN.source = args.since, args.lang, args.discovery
    RUN.only = args.only
    RUN.riksmote = args.riksmote
    RUN.limit = args.limit
    RUN.rot13 = args.rot13
    # the parallelisable steps default to all cores; -j1 serialises
    jobs = args.jobs if args.jobs is not None else (os.cpu_count() or 1)

    # A pipeline invocation is wrapped in the run ledger: mint a run id, prune old
    # runs, emit run-start, and (try/finally, so a crash or Ctrl-C still lands it)
    # run-end. serve/status/runs read or serve; --dry-run writes nothing -- none get
    # a run id, and the no-run-id invariant makes every runlog emission below a
    # no-op for them.
    global RUN_ID, RUN_ERRORS
    # reset unconditionally so a second in-process main() (e.g. a --dry-run or a
    # non-pipeline verb after a pipeline run) never inherits the prior run's id
    # or error tally
    RUN_ID = None
    RUN_ERRORS = 0
    if args.action not in ("serve", "status", "runs") and not RUN.dry_run:
        RUN_ID = runlog.make_run_id(os.getpid())
        runlog.prune(RUNS)
        runlog.emit_run_start(RUNS, RUN_ID, ["lagen", *argv], os.getpid())
    t0 = time.perf_counter()
    ok = False
    try:
        _dispatch(args, p, jobs)
        ok = True          # only a clean return counts; any exception or a
                           # SystemExit(nonzero) from _dispatch leaves ok False
    finally:
        if RUN_ID is not None:
            # ok from the success flag, folded with THIS run's error total
            # (RUN_ERRORS) -- not the corpus-wide currently-failing count, which
            # lives in errors.json and the /ops overview
            runlog.emit_run_end(RUNS, RUN_ID, time.perf_counter() - t0,
                                ok and RUN_ERRORS == 0, RUN_ERRORS)


def _cmd_runs(limit):
    """`lagen all runs [N]`: print the newest N run summaries from the ledger
    (neither a stage nor a source action, so intercepted before the dispatch loop
    and excluded from run-ledger wrapping)."""
    runs = runlog.read_runs(RUNS)
    if limit:
        runs = runs[:limit]
    if not runs:
        print("no runs recorded yet (%s)" % RUNS)
        return
    for r in runs:
        secs = "%.1fs" % r["secs"] if r["secs"] is not None else "-"
        print("%s  %-8s %9s  %2d seg  %d err  %s"
              % (r["run"], r["status"], secs, r["segments"], r["errors"],
                 " ".join(r["argv"])))


def cmd_patch_show(args, p):
    """`lagen <source> patch-show <basefile>` -- print a document's intermediate
    source text (the format its patch targets: plain text for sfs, innehåll HTML
    for dv, Formex XML for eurlex), with any existing patch already applied, to
    stdout. Redirect it to a file, hand-edit that file, then feed it back to
    `mkpatch` to author a minimal patch."""
    if args.source not in patchsource._INTERMEDIATE:
        p.error("source %r has no patchable intermediate (patchable: %s)"
                % (args.source, ", ".join(patchsource.patchable_sources())))
    if len(args.basefiles) != 1:
        p.error("patch-show needs exactly one basefile")
    basefile = args.basefiles[0]
    text, label = patchsource.current(args.source, basefile)
    sys.stderr.write("# %s %s -- intermediate format: %s%s\n"
                     % (args.source, basefile, label,
                        " (patch applied)" if patch.has_patch(args.source, basefile)
                        else ""))
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def cmd_mkpatch(args, p):
    """`lagen <source> mkpatch <basefile> <edited-file> [description]` -- author a
    patch from a hand-edited copy of the intermediate text. Diffs the pristine
    intermediate against `<edited-file>` and writes the minimal unified diff to
    the document's patch location (`patches/<source>/…`). `--rot13` stores it
    obfuscated (redactions of personal data). An edited file identical to the
    pristine text removes any existing patch."""
    if args.source not in patchsource._INTERMEDIATE:
        p.error("source %r has no patchable intermediate (patchable: %s)"
                % (args.source, ", ".join(patchsource.patchable_sources())))
    if not 2 <= len(args.basefiles) <= 3:
        p.error("mkpatch needs: <basefile> <edited-file> [description]")
    basefile, edited_path = args.basefiles[0], args.basefiles[1]
    description = args.basefiles[2] if len(args.basefiles) == 3 else ""
    pristine, label = patchsource.intermediate(args.source, basefile)
    edited = Path(edited_path).read_text(encoding="utf-8")
    if RUN.dry_run:
        print(patch.make_patch_text(pristine, edited, description)
              or "mkpatch: no differences; nothing to write")
        return
    path = patch.create_patch(args.source, basefile, pristine, edited,
                              description=description, rot13=RUN.rot13)
    if path is None:
        print("mkpatch %s %s: no differences; removed any existing patch"
              % (args.source, basefile))
    else:
        print("mkpatch %s %s: wrote %s patch %s (%s intermediate)"
              % (args.source, basefile, "rot13" if RUN.rot13 else "plain",
                 path, label))


def _dispatch(args, p, jobs):
    """Route one parsed invocation to its command. Split out of main() so main
    can wrap the whole dispatch in a single run-start/run-end try/finally."""
    if args.action == "patch-show":
        cmd_patch_show(args, p)
        return
    if args.action == "mkpatch":
        cmd_mkpatch(args, p)
        return
    # generate is corpus-wide by default, but `lagen <source> generate <id> ...`
    # targets just those documents (and leaves the aggregate pages alone)
    if args.action == "generate":
        # `all generate` = the whole corpus (+ aggregates); `<source> generate` =
        # that source's pages (incl. synthesized stubs, which have no artifact
        # file); `<source> generate <ids>` = just those docs. A scoped run skips
        # the corpus-wide aggregate pages and the auto-relate.
        if args.source == "all":
            if args.basefiles:
                p.error("`all generate <ids>` needs a specific source, e.g. "
                        "`lagen eurlex generate 32022L2555`")
            cmd_generate(jobs=jobs)
        elif args.source not in SOURCES:
            p.error("unknown source %r (have: %s)"
                    % (args.source, ", ".join(SOURCES)))
        elif args.basefiles:
            cmd_generate(only={str(layout.artifact(args.source, bf))
                               for bf in args.basefiles}, jobs=jobs)
        else:
            cmd_generate(source=args.source, jobs=jobs)
        return
    if args.action == "serve":
        cmd_serve(args.host, args.port)
        return
    if args.action == "runs":
        _cmd_runs(int(args.basefiles[0]) if args.basefiles else None)
        return

    names = list(SOURCES) if args.source == "all" else [args.source]
    if any(n not in SOURCES for n in names):
        p.error("unknown source %r (have: %s)" % (args.source, ", ".join(SOURCES)))

    if args.action in ("rebuild", "all"):
        had_errors = cmd_all(names, jobs, whole_corpus=args.source == "all",
                             download=args.action == "all")
        if had_errors:
            sys.exit(1)
        return
    if args.action == "relate":
        cmd_relate(names)
        return
    if args.action == "index":
        cmd_index(names, jobs)
        return
    if args.action == "dump":
        cmd_dump(names)
        return

    had_errors = False
    for name in names:
        source = SOURCES[name]
        if args.action == "status":
            cmd_status(source)
            continue
        if args.action == "download":
            scopes = args.basefiles
            if source.harvest is not None and (
                    not scopes or all(s in source.scopes for s in scopes)):
                # bulk discovery, optionally narrowed to named sub-scopes
                # (forarbete doctypes / eurlex sectors). The per-doc stage only
                # refetches known ids; new docs come only from the bulk sweep,
                # so this must NOT fall back to list_basefiles().
                if source.origin:
                    label = "%s %s" % (name, "/".join(scopes)) if scopes else name
                    print("Downloading %s from %s" % (label, source.origin),
                          flush=True)
                t0 = time.perf_counter()
                try:
                    source.harvest(scopes)
                    _emit_segment("download", name, time.perf_counter() - t0,
                                  status="ok")
                except Exception:  # noqa: BLE001 — per-source resilience point: one source's harvest failure must not abort the remaining sources; printed + nonzero exit at end (rule:no-catch-log-continue)
                    traceback.print_exc()
                    _emit_segment("download", name, time.perf_counter() - t0,
                                  status="errors", errors=1)
                    had_errors = True
                continue
            if scopes and source.scopes and "download" not in source.stages:
                bad = [s for s in scopes if s not in source.scopes]
                p.error("unknown %s scope(s): %s (have: %s)"
                        % (name, ", ".join(bad), ", ".join(sorted(source.scopes))))
            if not scopes and source.harvest is None:
                if args.source == "all" and "download" not in source.stages:
                    continue
                p.error("source %r has no bulk harvest" % name)
            # scopes are document ids -> fall through to the per-doc download stage
        if args.action in source.actions:
            t0 = time.perf_counter()
            source.actions[args.action](args.basefiles)
            _emit_segment(args.action, name, time.perf_counter() - t0, status="ok")
            continue
        if args.action not in source.stages:
            p.error("source %r has no action %r (have: %s)"
                    % (name, args.action,
                       ", ".join([*source.stages, *source.actions])))
        # a full-source run of a watermark-gated per-doc stage gets the same
        # coarse "up to date -- skipped" shortcut cmd_all uses, so a direct
        # `lagen sfs parse` with nothing changed skips the per-doc scan too
        if not args.basefiles and args.action in ("parse", "versions"):
            store = load_watermarks()
            errs, recorded = _run_stage_gated(source, args.action, jobs, store)
            if recorded:
                save_watermarks(store)
            had_errors |= errs
            continue
        basefiles = args.basefiles or source.list_basefiles()
        result = run_action(source, args.action, basefiles, jobs)
        report(source, args.action, result, len(basefiles),
               full_source=not args.basefiles)
        had_errors |= bool(result.errors)
    if had_errors:                 # report every source first, then signal failure
        sys.exit(1)


if __name__ == "__main__":
    main()
