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
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .sfs import load_inputs
from .sfs import download as sfs_download
from .dv import download as dv_download
from .dv import identity as dv_identity
from .forarbete import download as fa_download
from .forarbete import parse as fa_parse
from .wiki import parse as wiki_parse
from .dv.parse import api_member, parse_api_record, slug, to_artifact
from .lib import catalog, render
from .lib.errors import SkipDocument
from .lib.lagrum import LagrumParser, load_namedlaws
from .sfs.nf import to_normalform

POLITENESS = 0.3   # seconds between per-document network fetches
ROOT = Path(__file__).parent.parent
DATA = ROOT / "site" / "data"
MANIFEST = DATA / ".build" / "manifest.json"
CATALOG = DATA / "catalog.sqlite"
GENERATED = DATA / "generated"
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
    harvest: Callable[[], None] | None = None   # bulk download (discovery)


SOURCES: dict = {}


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
        and entry["version"] == recipe_version(stage.code)


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


def _progress(action, done, total, merged):
    """Live one-line counter on stderr (the per-document loop is otherwise
    silent until the final report). Throttled to every 50 docs + the last."""
    if done % 50 and done != total:
        return
    verb = "planned" if RUN.dry_run else "ran"
    count = len(merged.planned) if RUN.dry_run else len(merged.done)
    sys.stderr.write("\r  %s %d/%d  %s %d  err %d "
                     % (action, done, total, verb, count, len(merged.errors)))
    sys.stderr.flush()


def run_action(source, action, basefiles, jobs):
    manifest = load_manifest()
    merged = Result()
    total = done = 0
    total = len(basefiles)

    def absorb(res):
        nonlocal done
        _absorb(merged, res)
        done += 1
        _progress(action, done, total, merged)

    if jobs > 1 and not RUN.dry_run:
        jobspec = [(source.name, action, bf) for bf in basefiles]
        with ProcessPoolExecutor(max_workers=jobs, initializer=_worker_init,
                                 initargs=(manifest, RUN)) as pool:
            for res in pool.map(_worker, jobspec, chunksize=16):
                absorb(res)
    else:
        for bf in basefiles:
            absorb(build_one(source, action, bf, manifest))
    if total:
        sys.stderr.write("\n")
    if merged.updates and not RUN.dry_run:
        manifest.update(merged.updates)
        save_manifest(manifest)
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

SFS_ROOT = DATA / "sfs"
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


def _sfs_paths(basefile):
    year, nr = basefile.split(":", 1)
    return year, nr.replace(" ", "_")


def sfs_downloaded(basefile):
    year, nr = _sfs_paths(basefile)
    return SFS_ROOT / "downloaded" / year / ("%s.html" % nr)


def sfs_source(basefile):
    """The new beta API _source (its own tree, parallel to the legacy HTML)."""
    year, nr = _sfs_paths(basefile)
    return SFS_ROOT / "source" / year / ("%s.json" % nr)


def sfs_register(basefile):
    year, nr = _sfs_paths(basefile)
    return SFS_ROOT / "register" / year / ("%s.html" % nr)


def sfs_inputs(basefile):
    """Freshness inputs: the JSON _source when present (the new beta API),
    else the legacy SFST + SFSR HTML pair."""
    if sfs_source(basefile).exists():
        return [sfs_source(basefile)]
    return [sfs_downloaded(basefile), sfs_register(basefile)]


def sfs_artifact(basefile):
    year, nr = _sfs_paths(basefile)
    return SFS_ROOT / "artifact" / year / ("%s.json" % nr)


def sfs_download_run(basefile):
    """Fetch one named act's consolidated _source from the beta database,
    archiving any superseded consolidation (the old download_single). New-act
    *discovery* is sfs_harvest (bare `lagen sfs download`), not this."""
    source = sfs_download.fetch_one(_sfs_session(), basefile)
    if source is None:
        raise RuntimeError("no published act %s in the beta database" % basefile)
    sfs_download.save_document(SFS_ROOT, source)
    time.sleep(POLITENESS)


def sfs_harvest():
    """Bulk discovery harvest -- a search_after sweep of the whole corpus, the
    only way to find acts not yet on disk (the old download_new). Incremental
    by default (stops at the first page with nothing new); `--force` walks the
    entire corpus oldest-first. Throttled and self-logging (per page)."""
    if RUN.dry_run:
        print("sfs download: would harvest the corpus into %s"
              % (SFS_ROOT / "source"))
        return
    seen, new, updated = sfs_download.sync(SFS_ROOT, full=RUN.force)
    print("sfs download: %d seen, %d new, %d updated" % (seen, new, updated))


def sfs_parse_run(basefile):
    doc, register, sfst_header = load_inputs(
        sfs_source(basefile), sfs_downloaded(basefile),
        sfs_register(basefile), basefile)
    nf = to_normalform(doc, basefile,
                       refparser=LagrumParser(_namedlaws(), basefile),
                       register=register, sfst_header=sfst_header)
    sfs_artifact(basefile).write_text(
        json.dumps(nf, ensure_ascii=False, indent=2, sort_keys=True))


def sfs_list():
    """Every basefile with a source: the new beta JSON (source/) or the
    legacy SFST HTML (downloaded/)."""
    return sorted({"%s:%s" % (p.parent.name, p.stem.replace("_", " "))
                   for p in (SFS_ROOT / "source").glob("*/*.json")}
                  | {"%s:%s" % (p.parent.name, p.stem.replace("_", " "))
                     for p in (SFS_ROOT / "downloaded").glob("*/*.html")})


SOURCES["sfs"] = Source("sfs", sfs_list, {
    # download has no input files (the input is the remote DB) and its output
    # is valid regardless of the fetcher's version, so inputs/code stay empty:
    # an act on disk is "fresh" until --force re-fetches it.
    "download": Stage("download", sfs_download_run, sfs_source),
    "parse": Stage("parse", sfs_parse_run, sfs_artifact,
                   inputs=sfs_inputs, code=SFS_CODE),
}, harvest=sfs_harvest)


# --------------------------------------------------------------------------
# DV source
# --------------------------------------------------------------------------

DV_ROOT = DATA / "dv"
DOMSTOL_DOWNLOADED = DATA / "domstol" / "downloaded"
DV_INDEX = DV_ROOT / "identity-index.json"
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
    return DV_ROOT / "artifact" / ("%s.json" % slug(basefile))


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


def dv_harvest():
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
              % (DOMSTOL_DOWNLOADED, DV_INDEX))
        return
    seen, changed = dv_download.sync(DOMSTOL_DOWNLOADED, full=RUN.force)
    print("dv download: %d records seen, %d new/changed" % (seen, changed))
    if changed or not DV_INDEX.exists():
        print("dv download: rebuilding identity index ...")
        dv_identity.reindex(dvdir=str(DV_ROOT / "downloaded"),
                            domstoldir=str(DOMSTOL_DOWNLOADED),
                            out=str(DV_INDEX))
        _dv_cases.cache_clear()
    else:
        print("dv download: no new records, identity index left as is")


def dv_parse_run(basefile):
    av = parse_api_record(json.loads(dv_record(basefile).read_text()))
    dv_artifact(basefile).write_text(json.dumps(
        to_artifact(av, canonical_id=basefile), ensure_ascii=False, indent=2))


SOURCES["dv"] = Source("dv", lambda: sorted(_dv_cases()), {
    "download": Stage("download", dv_download_run, dv_record),
    "parse": Stage("parse", dv_parse_run, dv_artifact,
                   inputs=lambda bf: [dv_record(bf)], code=DV_CODE),
}, harvest=dv_harvest)


# --------------------------------------------------------------------------
# förarbete source (preparatory works from regeringen.se)
# --------------------------------------------------------------------------

FA_ROOT = DATA / "forarbete"
FA_CODE = (PKG / "forarbete" / "parse.py", PKG / "forarbete" / "model.py",
           PKG / "lib" / "lagrum.py")


def fa_record(basefile):
    typ, slug = basefile.split("/", 1)
    return FA_ROOT / typ / (slug + ".json")


def fa_artifact(basefile):
    typ, slug = basefile.split("/", 1)
    return FA_ROOT / typ / "artifact" / (slug + ".json")


def fa_list():
    """Every harvested record as 'type/slug' (the artifact subdir excluded by
    the single-level glob)."""
    return sorted("%s/%s" % (p.parent.name, p.stem)
                  for p in FA_ROOT.glob("*/*.json"))


def fa_harvest():
    """Bulk harvest of all regeringen.se types (the old download_new)."""
    if RUN.dry_run:
        print("forarbete download: would harvest regeringen.se into %s" % FA_ROOT)
        return
    totals = fa_download.sync(str(FA_ROOT), full=RUN.force)
    for typ, (seen, new) in totals.items():
        print("forarbete %s: %d seen, %d new" % (typ, seen, new))


def fa_parse_run(basefile):
    record = json.loads(fa_record(basefile).read_text())
    fa_artifact(basefile).write_text(json.dumps(
        fa_parse.to_artifact(fa_parse.parse_record(record, FA_ROOT)),
        ensure_ascii=False, indent=2))


SOURCES["forarbete"] = Source("forarbete", fa_list, {
    "parse": Stage("parse", fa_parse_run, fa_artifact,
                   inputs=lambda bf: [fa_record(bf)], code=FA_CODE),
}, harvest=fa_harvest)


# --------------------------------------------------------------------------
# wiki sources: kommentar (SFS commentary) + begrepp (concept glossary), both
# parsed from the MediaWiki dump
# --------------------------------------------------------------------------

WIKI_ROOT = DATA / "mediawiki" / "downloaded"
WIKI_CODE = (PKG / "wiki" / "parse.py", PKG / "lib" / "wikitext.py",
             PKG / "lib" / "lagrum.py")


def _wiki_slug(basefile):
    return "".join(c if c.isalnum() else "_" for c in basefile).strip("_")


def kommentar_record(basefile):
    return Path(wiki_parse.kommentar_index(str(WIKI_ROOT))[basefile])


def kommentar_artifact(basefile):
    return DATA / "kommentar" / "artifact" / (_wiki_slug(basefile) + ".json")


def kommentar_parse_run(basefile):
    art = wiki_parse.kommentar_artifact(str(kommentar_record(basefile)))
    kommentar_artifact(basefile).write_text(
        json.dumps(art, ensure_ascii=False, indent=2))


def begrepp_record(basefile):
    return Path(wiki_parse.begrepp_index(str(WIKI_ROOT))[basefile])


def begrepp_artifact(basefile):
    return DATA / "begrepp" / "artifact" / (_wiki_slug(basefile) + ".json")


def begrepp_parse_run(basefile):
    art = wiki_parse.begrepp_artifact(str(begrepp_record(basefile)))
    begrepp_artifact(basefile).write_text(
        json.dumps(art, ensure_ascii=False, indent=2))


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
    "dv": lambda: sorted((DV_ROOT / "artifact").glob("*.json")),
    "forarbete": lambda: sorted(FA_ROOT.glob("*/artifact/*.json")),
    "kommentar": lambda: sorted((DATA / "kommentar" / "artifact").glob("*.json")),
    "begrepp": lambda: sorted((DATA / "begrepp" / "artifact").glob("*.json")),
}


def cmd_relate(names):
    """(Re)build each named source's rows in the shared catalog from its
    artifacts on disk -- documents + the citation edges they carry inline."""
    for name in names:
        paths = ARTIFACTS[name]()

        def progress(seen, docs, edges):
            sys.stderr.write("\r  relate %s: %d/%d seen, %d docs, %d links "
                             % (name, seen, len(paths), docs, edges))
            sys.stderr.flush()

        docs, edges = catalog.rebuild(CATALOG, name, paths, progress=progress)
        sys.stderr.write("\n")
        print("relate %s: %d documents, %d links" % (name, docs, edges))
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


def cmd_generate():
    """Render every catalogued document to static HTML, with live outbound
    links and inbound annotations queried from the catalog, plus a frontpage.
    Auto-runs `relate` first for any source whose artifacts are newer than the
    catalog -- relate is generate's upstream dependency."""
    stale = stale_sources()
    if stale:
        print("catalog stale for %s -- relating first" % ", ".join(stale))
        cmd_relate(stale)

    def progress(done, total):
        sys.stderr.write("\r  generate %d/%d pages " % (done, total))
        sys.stderr.flush()

    n = render.generate_site(CATALOG, GENERATED, progress=progress)
    sys.stderr.write("\n")
    print("generate: %d pages -> %s" % (n, GENERATED))
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


def main(argv=None):
    p = argparse.ArgumentParser(prog="lagen", description=__doc__.split("\n")[0])
    p.add_argument("source", help="source name (%s) or 'all'"
                   % ", ".join(SOURCES))
    p.add_argument("action",
                   help="download | parse | relate | generate | serve | status")
    p.add_argument("basefiles", nargs="*", help="ids; empty = all stale")
    p.add_argument("-f", "--force", action="store_true",
                   help="rebuild the named stage even if fresh")
    p.add_argument("--no-deps", action="store_true",
                   help="run only the named stage, not its upstream deps")
    p.add_argument("-j", "--jobs", type=int, default=1, help="parallel workers")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="print the plan, do nothing")
    p.add_argument("--port", type=int, default=8000,
                   help="port for `serve` (default 8000)")
    args = p.parse_args(argv)

    RUN.dry_run, RUN.force, RUN.no_deps = args.dry_run, args.force, args.no_deps

    # corpus-wide derived actions: source is irrelevant, run exactly once
    if args.action == "generate":
        cmd_generate()
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

    for name in names:
        source = SOURCES[name]
        if args.action == "status":
            cmd_status(source)
            continue
        if args.action == "download" and not args.basefiles:
            # no basefile = harvest the whole corpus (discovery). The per-doc
            # stage can only refetch known ids; new docs are found only by the
            # bulk sweep, so this must NOT fall back to list_basefiles().
            if source.harvest is None:
                p.error("source %r has no bulk harvest" % name)
            source.harvest()
            continue
        if args.action not in source.stages:
            p.error("source %r has no action %r (have: %s)"
                    % (name, args.action, ", ".join(source.stages)))
        basefiles = args.basefiles or source.list_basefiles()
        result = run_action(source, args.action, basefiles, args.jobs)
        report(source, args.action, result, len(basefiles))
        if result.errors:
            sys.exit(1)


if __name__ == "__main__":
    main()
