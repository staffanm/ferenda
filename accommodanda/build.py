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

(Also runnable as `python -m accommodanda.build …`.)
"""

import argparse
import functools
import hashlib
import http.server
import json
import socketserver
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import config
from .sfs import load_inputs
from .sfs import download as sfs_download
from .dv import download as dv_download
from .dv import identity as dv_identity
from .forarbete import download as fa_download
from .forarbete import parse as fa_parse
from .forarbete import kommentar as fa_kommentar
from .forarbete import genomforande as fa_genomforande
from .eurlex import annotate as eurlex_annotate
from .eurlex import bulk as eurlex_bulk
from .eurlex import download as eurlex_download
from .eurlex import parse as eurlex_parse
from .wiki import parse as wiki_parse
from .dv.parse import api_member, parse_api_record, to_artifact
from .lib import catalog, layout, render, util
from .lib.errors import SkipDocument
from .lib.lagrum import LagrumParser, load_namedlaws
from .sfs.nf import to_normalform

POLITENESS = 0.3   # seconds between per-document network fetches
ROOT = Path(__file__).parent.parent          # repo source tree (curated resources)
DATA = config.DATA                            # corpus location (config.yml: data_root)
MANIFEST = DATA / ".build" / "manifest.json"
CATALOG = DATA / "catalog.sqlite"
GENERATED = layout.GENERATED
NAMEDLAWS_TTL = ROOT / "lagen" / "nu" / "res" / "extra" / "sfs.ttl"


# --------------------------------------------------------------------------
# stage / source protocol
# --------------------------------------------------------------------------

@dataclass
class Stage:
    name: str
    run: Callable[[str], None]            # recipe: read inputs, write output
    output: Callable[[str], Path]         # basefile -> produced file
    inputs: Callable[[str], list] = lambda bf: []   # dependency files
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


def is_fresh(manifest, source, stage, basefile):
    out = stage.output(basefile)
    if not out.exists():
        return False
    if not stage.inputs(basefile) and not stage.code:
        # nothing to version the output against (e.g. download: the "input" is
        # a remote service, not a file) -- an existing output is by definition
        # up to date, whether the driver or the bulk harvester produced it
        return True
    entry = manifest.get(manifest_key(source.name, stage.name, basefile))
    return bool(entry) \
        and entry["inputs"] == hash_files(stage.inputs(basefile)) \
        and (RUN.ignore_code_changes
             or entry["version"] == recipe_version(stage.code))


# --------------------------------------------------------------------------
# the driver
# --------------------------------------------------------------------------

@dataclass
class Result:
    planned: list = field(default_factory=list)   # (stage, basefile)
    done: list = field(default_factory=list)
    errors: list = field(default_factory=list)     # (stage, basefile, msg)
    updates: dict = field(default_factory=dict)    # manifest key -> entry


def ensure(source, stage_name, basefile, manifest, res, force, no_deps):
    """Bring (stage, basefile) up to date, recursing into its dependency
    first (unless --no-deps). `force` applies to the named stage only; the
    dependency is still freshness-checked. Returns True on success."""
    stage = source.stages[stage_name]
    if stage.depends and not no_deps:
        if not ensure(source, stage.depends, basefile, manifest, res,
                      False, no_deps):
            return False
    if not force and is_fresh(manifest, source, stage, basefile):
        return True
    res.planned.append((stage_name, basefile))
    if RUN.dry_run:
        return True
    try:
        stage.output(basefile).parent.mkdir(parents=True, exist_ok=True)
        stage.run(basefile)
    except SkipDocument as e:
        # a deliberately empty document (removed/expired): write an empty
        # artifact so it is considered built and not retried every run
        stage.output(basefile).write_bytes(b"")
    except Exception as e:
        res.errors.append((stage_name, basefile, "%s: %s"
                           % (type(e).__name__, e)))
        return False
    res.updates[manifest_key(source.name, stage_name, basefile)] = {
        "inputs": hash_files(stage.inputs(basefile)),
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


RUN = RunOptions()


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


# --------------------------------------------------------------------------
# manifest persistence
# --------------------------------------------------------------------------

def load_manifest():
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def save_manifest(manifest):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    tmp.replace(MANIFEST)


# --------------------------------------------------------------------------
# SFS source
# --------------------------------------------------------------------------

SFS_ROOT = layout.SFS_ROOT
PKG = Path(__file__).parent
SFS_CODE = tuple(PKG / "sfs" / ("%s.py" % m) for m in (
    "__init__", "extract", "reader", "tokenizer", "assembler", "model", "nf",
    "register")) + (PKG / "lib" / "lagrum.py",)


@functools.cache
def _namedlaws():
    return load_namedlaws(NAMEDLAWS_TTL)


@functools.cache
def _sfs_session():
    return sfs_download.make_session(sfs_download.USER_AGENT)


def sfs_downloaded(basefile):
    return layout.sfs_sfst(basefile)


def sfs_source(basefile):
    """The new beta API _source (its own tree, parallel to the legacy HTML)."""
    return layout.sfs_source(basefile)


def sfs_register(basefile):
    return layout.sfs_sfsr(basefile)


def sfs_inputs(basefile):
    """Freshness inputs: the JSON _source when present (the new beta API),
    else the legacy SFST + SFSR HTML pair."""
    if sfs_source(basefile).exists():
        return [sfs_source(basefile)]
    return [sfs_downloaded(basefile), sfs_register(basefile)]


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
              % (layout.SFS_DOWNLOADED / "source"))
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


def sfs_list():
    """Every *regular* SFS basefile with a source: the new beta JSON
    (source/) or the legacy SFST HTML (downloaded/).

    Acts whose year segment is non-numeric -- amendments to government-agency
    regulations carrying a letter prefix, e.g. 'N2026:3' -- are harvested and
    stored but excluded here: they don't belong in the SFS-centric publication
    and will be picked up by the myndfskr (myndighetsföreskrifter) port."""
    return sorted({"%s:%s" % (p.parent.name, p.stem.replace("_", " "))
                   for p in layout.SFS_DOWNLOADED.glob("*/*.json")
                   if p.parent.name.isdigit()}
                  | {"%s:%s" % (p.parent.name, p.stem.replace("_", " "))
                     for p in (layout.SFS_DOWNLOADED / "sfst").glob("*/*.html")})


SOURCES["sfs"] = Source("sfs", sfs_list, {
    # download has no input files (the input is the remote DB) and its output
    # is valid regardless of the fetcher's version, so inputs/code stay empty:
    # an act on disk is "fresh" until --force re-fetches it.
    "download": Stage("download", sfs_download_run, sfs_source),
    "parse": Stage("parse", sfs_parse_run, sfs_artifact,
                   inputs=sfs_inputs, code=SFS_CODE),
}, harvest=sfs_harvest, origin=_origin(sfs_download.ENDPOINT))


# --------------------------------------------------------------------------
# DV source
# --------------------------------------------------------------------------

DOM_DOWNLOADED = layout.DOM_DOWNLOADED            # dv api records (primary)
DV_LEGACY_DOWNLOADED = layout.DV_LEGACY_DOWNLOADED  # legacy raw feed
DV_INDEX = layout.DOM_INDEX
DV_CODE = (PKG / "dv" / "parse.py", PKG / "dv" / "model.py",
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
    return Path(api_member(_dv_cases()[basefile])["path"])


def dv_download_run(basefile):
    """Re-fetch one named case's API record (by the uuid the identity index
    already holds) and its attachments. New-case *discovery* is dv_harvest
    (bare `lagen dv download`) + identity reindex -- a case has no uuid to
    fetch until the harvest has seen it, so it can't enter through here."""
    member = api_member(_dv_cases()[basefile])
    record = dv_download.fetch_record(_dv_session(), member["uuid"])
    out = dv_record(basefile)
    dv_download.write_atomic(out, json.dumps(
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
        return
    seen, changed = dv_download.sync(DOM_DOWNLOADED, full=RUN.force)
    print("dv download: %d records seen, %d new/changed" % (seen, changed))
    if changed or not DV_INDEX.exists():
        dv_reindex()
    else:
        print("dv download: no new records, identity index left as is")


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


def dv_parse_run(basefile):
    record = json.loads(dv_record(basefile).read_text())
    av = parse_api_record(record)
    # the case's public publication-search page is keyed by the record's
    # gruppKorrelationsnummer (the publication group), not derivable from basefile
    grupp = record.get("gruppKorrelationsnummer")
    write_artifact("dv", basefile, to_artifact(av, canonical_id=basefile),
                   source_url=layout.dv_source_url(grupp) if grupp else None)


SOURCES["dv"] = Source("dv", lambda: sorted(_dv_cases()), {
    "download": Stage("download", dv_download_run, dv_record),
    "parse": Stage("parse", dv_parse_run, dv_artifact,
                   inputs=lambda bf: [dv_record(bf)], code=DV_CODE),
}, harvest=dv_harvest, origin=_origin(dv_download.API),
   actions={"reindex": dv_reindex})


# --------------------------------------------------------------------------
# förarbete source (preparatory works from regeringen.se)
# --------------------------------------------------------------------------

FA_ROOT = layout.FA_ROOT
FA_CODE = (PKG / "forarbete" / "parse.py", PKG / "forarbete" / "model.py",
           PKG / "forarbete" / "kommentar.py", PKG / "lib" / "lagrum.py")


def fa_record(basefile):
    return layout.fa_record(basefile)


def fa_artifact(basefile):
    return layout.artifact("forarbete", basefile)


def fa_list():
    """Every harvested record as 'type/slug' (the artifact subdir excluded by
    the single-level glob)."""
    return sorted("%s/%s" % (p.parent.name, p.stem)
                  for p in layout.FA_DOWNLOADED.glob("*/*.json"))


def fa_harvest(scopes):
    """Bulk harvest of regeringen.se (the old download_new). `scopes` narrows it
    to the named doctypes (prop/sou/ds/...); empty = all. `--only BASEFILE`
    (with exactly one scope) fetches just that one document, walking the listing
    until it is found."""
    if RUN.only and len(scopes) != 1:
        sys.exit("forarbete --only needs exactly one doctype, e.g. "
                 "`lagen forarbete download prop --only 2025/26:28`")
    if RUN.dry_run:
        print("forarbete download: would harvest %s into %s"
              % (RUN.only or ", ".join(scopes) or "all types",
                 layout.FA_DOWNLOADED))
        return
    totals = fa_download.sync(str(layout.FA_DOWNLOADED), types=scopes or None,
                              full=RUN.force, only=RUN.only)
    for typ, (seen, new) in totals.items():
        print("forarbete %s: %d seen, %d new" % (typ, seen, new))


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


SOURCES["forarbete"] = Source("forarbete", fa_list, {
    "parse": Stage("parse", fa_parse_run, fa_artifact,
                   inputs=lambda bf: [fa_record(bf)], code=FA_CODE),
}, harvest=fa_harvest, origin=_origin(fa_download.BASE),
   scopes=frozenset(fa_download.TYPES),
   notes="download flag: --only BASEFILE (fetch one document; needs one scope)")


# --------------------------------------------------------------------------
# EUR-Lex source (EU treaties, legislation, case law from CELLAR; CELEX ids)
# --------------------------------------------------------------------------

EURLEX_ROOT = layout.EURLEX_ROOT
EURLEX_CODE = (PKG / "eurlex" / "parse.py", PKG / "eurlex" / "parse_html.py",
               PKG / "eurlex" / "parse_pdf.py", PKG / "eurlex" / "lang.py",
               PKG / "eurlex" / "model.py", PKG / "lib" / "lagrum.py")


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
                   inputs=eurlex_content, depends="download", code=EURLEX_CODE),
}, harvest=eurlex_harvest, origin=_origin(eurlex_download.SOAP_ENDPOINT),
   scopes=frozenset(eurlex_download.SECTORS),
   actions={"unpack-bulk": eurlex_unpack, "ai-annotate": eurlex_ai_annotate},
   notes="download flags: --since YYYY-MM-DD, --lang swe,eng, --source sparql|soap\n"
         "unpack-bulk <dir|zip>: import a CELLAR bulk legislation dump\n"
         "ai-annotate <CELEX>: LLM-author the editorial .ann layer (sector-3 acts)")


# --------------------------------------------------------------------------
# wiki sources: kommentar (SFS commentary) + begrepp (concept glossary), both
# parsed from the MediaWiki dump
# --------------------------------------------------------------------------

WIKI_ROOT = layout.WIKI_ROOT
WIKI_CODE = (PKG / "wiki" / "parse.py", PKG / "lib" / "wikitext.py",
             PKG / "lib" / "lagrum.py")


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


SOURCES["kommentar"] = Source(
    "kommentar",
    lambda: sorted(wiki_parse.kommentar_index(str(WIKI_ROOT))),
    {"parse": Stage("parse", kommentar_parse_run, kommentar_artifact,
                    inputs=lambda bf: [kommentar_record(bf)], code=WIKI_CODE)})

SOURCES["begrepp"] = Source(
    "begrepp",
    lambda: sorted(wiki_parse.begrepp_index(str(WIKI_ROOT))),
    {"parse": Stage("parse", begrepp_parse_run, begrepp_artifact,
                    inputs=lambda bf: [begrepp_record(bf)], code=WIKI_CODE)})


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
    "sfs": lambda: sorted((SFS_ROOT / "artifact").glob("*/*.json")),
    "dv": lambda: sorted((layout.DOM_ROOT / "artifact").glob("*.json")),
    "forarbete": lambda: sorted((FA_ROOT / "artifact").glob("*/*.json")),
    "kommentar": lambda: sorted((layout.KOMMENTAR_ROOT / "artifact").glob("*.json")),
    "begrepp": lambda: sorted((layout.BEGREPP_ROOT / "artifact").glob("*.json")),
    "eurlex": lambda: sorted((EURLEX_ROOT / "artifact").glob("*/*.json")),
}


def cmd_relate(names):
    """(Re)build each named source's rows in the shared catalog from its
    artifacts on disk -- documents + the citation edges they carry inline."""
    for name in names:
        paths = ARTIFACTS[name]()

        def progress(seen, total, docs, edges, current):
            util.status(seen, total, "relate %s  %d docs, %d links  %s"
                        % (name, docs, edges, current))

        docs, edges = catalog.rebuild(CATALOG, name, paths, progress=progress)
        sys.stderr.write("\n")
        print("relate %s: %d documents, %d links" % (name, docs, edges))
    # cross-document post-pass: pin each förarbete genomför-direktiv statement to
    # the SFS paragraf it transposes (needs the whole catalog, so it runs last).
    con = catalog.connect(CATALOG)
    pinned = fa_genomforande.resolve(con)
    con.close()
    print("relate: %d genomför-direktiv relations pinned to SFS paragrafs" % pinned)
    print("catalog: %s" % CATALOG)


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
                 PKG / "lib" / "wikitext.py", PKG / "lib" / "layout.py")


def cmd_generate(only=None):
    """Render every catalogued document to static HTML, with live outbound
    links and inbound annotations queried from the catalog, plus a frontpage.
    Auto-runs `relate` first for any source whose artifacts are newer than the
    catalog -- relate is generate's upstream dependency.

    Incremental like parse: a page is re-rendered only when its prerequisite
    artifacts (itself + the documents citing it + the documents it cites) or the
    render code changed. `--force` rebuilds all; `--ignore-code-changes` ignores
    the render-code version (rebuild only on data changes).

    `only`, a set of artifact path strings, restricts the run to those documents
    (`lagen <source> generate <id>`), leaving the corpus-wide aggregate pages as
    they are -- a fast single-document re-render during development.

    `--aggregates-only` rewrites just the corpus-wide pages (frontpage + browse
    indexes) from the current catalog, skipping the per-document render -- a
    seconds-long refresh after a frontpage/browse change, not a full rebuild."""
    if RUN.aggregates_only:
        con = catalog.connect(CATALOG)
        render.render_aggregates(con, GENERATED)
        con.close()
        print("generate: rebuilt frontpage + browse indexes -> %s" % GENERATED)
        return

    # a full generate auto-relates any stale source first (relate is its upstream
    # dependency); a targeted single-document render skips that corpus-wide scan
    # and uses the catalog as-is -- run `lagen <source> relate` to refresh it
    stale = [] if only is not None else stale_sources()
    if stale:
        print("catalog stale for %s -- relating first" % ", ".join(stale))
        cmd_relate(stale)

    manifest = load_manifest()
    code_version = recipe_version(GENERATE_CODE)
    updates = {}
    own_hash = {}                # artifact path -> content hash, memoized per run

    def page_signature(art_path, dep_digest):
        # only the page's OWN artifact is content-hashed (it changes when the doc
        # is re-parsed); its neighbours enter via dep_digest as a set of
        # relationships, not their contents -- an immutable case re-appearing
        # unchanged must not invalidate every law it cites. A sibling `.ann`
        # editorial layer (eurlex ai-annotate) is hashed in too, so authoring or
        # editing it re-renders just that page.
        p = str(art_path)
        if p not in own_hash:
            fp = Path(p)
            ann = fp.with_suffix(".ann")
            own_hash[p] = hashlib.sha256(
                (fp.read_bytes() if fp.exists() else b"")
                + (ann.read_bytes() if ann.exists() else b"")).hexdigest()
        return hashlib.sha256((own_hash[p] + dep_digest).encode()).hexdigest()

    def fresh(uri, out_path, art_path, dep_digest):
        if RUN.force or not out_path.exists():
            return False
        entry = manifest.get(manifest_key("generate", "page", uri))
        return bool(entry) \
            and entry["inputs"] == page_signature(art_path, dep_digest) \
            and (RUN.ignore_code_changes or entry["version"] == code_version)

    def record(uri, art_path, dep_digest):
        updates[manifest_key("generate", "page", uri)] = {
            "inputs": page_signature(art_path, dep_digest), "version": code_version}

    def progress(done, total, current="", rendered=0):
        util.status(done, total, "generate  %d rendered  %s" % (rendered, current))

    total, rendered = render.generate_site(CATALOG, GENERATED, progress=progress,
                                           fresh=fresh, record=record, only=only)
    sys.stderr.write("\n")
    if updates:
        manifest.update(updates)
        save_manifest(manifest)
    if only is not None and not total:
        print("generate: no catalogued document matched %d requested id(s) -- "
              "parse/relate them first" % len(only))
        return
    print("generate: %d pages (%d rendered, %d fresh)%s -> %s"
          % (total, rendered, total - rendered,
             " [targeted; aggregates untouched]" if only is not None else "",
             GENERATED))
    if only is None:
        print("serve with: lagen all serve   (then open http://localhost:8000/)")


def cmd_serve(port=8000):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(GENERATED))
    socketserver.TCPServer.allow_reuse_address = True
    # bind loopback only: this is a local preview, not a public file server
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print("serving %s at http://localhost:%d/  (Ctrl-C to stop)"
              % (GENERATED, port))
        httpd.serve_forever()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_status(source):
    manifest = load_manifest()
    basefiles = source.list_basefiles()
    print("%s: %d basefiles" % (source.name, len(basefiles)))
    for name, stage in source.stages.items():
        fresh = stale = missing = 0
        for bf in basefiles:
            if not stage.output(bf).exists():
                missing += 1
            elif is_fresh(manifest, source, stage, bf):
                fresh += 1
            else:
                stale += 1
        print("  %-10s %6d fresh  %6d stale  %6d missing"
              % (name, fresh, stale, missing))


def report(source, action, result, requested):
    verb = "would run" if RUN.dry_run else "ran"
    skipped = requested - len({bf for _, bf in result.planned}) \
        - len({bf for _, bf, _ in result.errors})
    print("%s %s (%d basefiles): %s %d, skipped (fresh) %d, errors %d" % (
        source.name, action, requested, verb, len(result.planned),
        skipped, len(result.errors)))
    for stage, bf, msg in result.errors[:20]:
        print("  ERROR %s %s: %s" % (stage, bf, msg))


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
    p = argparse.ArgumentParser(prog="lagen", description=__doc__.split("\n")[0])
    p.add_argument("source", help="source name (%s) or 'all'"
                   % ", ".join(SOURCES))
    p.add_argument("action",
                   help="download | parse | relate | generate | serve | status "
                        "| a source action (e.g. dv reindex)")
    p.add_argument("basefiles", nargs="*",
                   help="ids to act on (empty = all stale); for download, names "
                        "harvest sub-scopes, e.g. 'prop' or 'acts'")
    p.add_argument("-f", "--force", action="store_true",
                   help="rebuild the named stage even if fresh")
    p.add_argument("--no-deps", action="store_true",
                   help="run only the named stage, not its upstream deps")
    p.add_argument("--ignore-code-changes", action="store_true",
                   help="treat outputs as fresh even when the parsing code "
                        "changed -- rebuild only docs whose input data changed, "
                        "are missing, or failed (dev convenience; off in production)")
    p.add_argument("--aggregates-only", action="store_true",
                   help="generate: rewrite only the corpus-wide pages (frontpage "
                        "+ browse indexes) from the catalog, skipping the "
                        "per-document render")
    p.add_argument("-j", "--jobs", type=int, default=1, help="parallel workers")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="print the plan, do nothing")
    p.add_argument("--port", type=int, default=8000,
                   help="port for `serve` (default 8000)")
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
    args = p.parse_args(argv)

    RUN.dry_run, RUN.force, RUN.no_deps = args.dry_run, args.force, args.no_deps
    RUN.ignore_code_changes = args.ignore_code_changes
    RUN.aggregates_only = args.aggregates_only
    RUN.since, RUN.lang, RUN.source = args.since, args.lang, args.discovery
    RUN.only = args.only

    # generate is corpus-wide by default, but `lagen <source> generate <id> ...`
    # targets just those documents (and leaves the aggregate pages alone)
    if args.action == "generate":
        only = None
        if args.basefiles:
            if args.source not in SOURCES:
                p.error("`generate <ids>` needs a specific source, e.g. "
                        "`lagen eurlex generate 32022L2555` (not %r)" % args.source)
            only = {str(layout.artifact(args.source, bf)) for bf in args.basefiles}
        cmd_generate(only)
        return
    if args.action == "serve":
        cmd_serve(args.port)
        return

    names = list(SOURCES) if args.source == "all" else [args.source]
    if any(n not in SOURCES for n in names):
        p.error("unknown source %r (have: %s)" % (args.source, ", ".join(SOURCES)))

    if args.action == "relate":
        cmd_relate(names)
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
                source.harvest(scopes)
                continue
            if scopes and source.scopes and "download" not in source.stages:
                bad = [s for s in scopes if s not in source.scopes]
                p.error("unknown %s scope(s): %s (have: %s)"
                        % (name, ", ".join(bad), ", ".join(sorted(source.scopes))))
            if not scopes and source.harvest is None:
                p.error("source %r has no bulk harvest" % name)
            # scopes are document ids -> fall through to the per-doc download stage
        if args.action in source.actions:
            source.actions[args.action](args.basefiles)
            continue
        if args.action not in source.stages:
            p.error("source %r has no action %r (have: %s)"
                    % (name, args.action,
                       ", ".join([*source.stages, *source.actions])))
        basefiles = args.basefiles or source.list_basefiles()
        result = run_action(source, args.action, basefiles, args.jobs)
        report(source, args.action, result, len(basefiles))
        had_errors |= bool(result.errors)
    if had_errors:                 # report every source first, then signal failure
        sys.exit(1)


if __name__ == "__main__":
    main()
