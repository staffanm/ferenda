"""The hudoc source's registration: the European Court of Human Rights' case
law from HUDOC.

Two collections (judgments, decisions), each walked under its own watermark;
two smaller sweeps ride along with an unbounded download, because both read
the same endpoint and both produce inputs a later stage needs."""

import functools
from pathlib import Path

from ..lib import layout
from ..lib import stage as protocol
from ..lib.datasets import EMD_CASES
from ..lib.stage import (
    POLITENESS,
    Source,
    origin,
    parse_stage,
    record_inputs,
)
from . import casenames, download, parse, render, summaries, translations

HERE = Path(__file__).parent

HUDOC_CODE = (HERE / "parse.py", HERE / "model.py", HERE / "summaries.py",
              HERE / "citations.py", HERE / "treaties.py",
              HERE.parent / "lib" / "treatyref.py",
              HERE.parent / "lib" / "treaty_ids.py",
              HERE.parent / "lib" / "data" / "treaty_names.json",
              HERE.parent / "lib" / "coe.py",
              HERE.parent / "lib" / "artifact.py")

# the summary sidecar is a parse input, and one file per case rather than a
# shared index precisely so a summary harvest re-stales only the cases whose
# summary moved
hudoc_inputs = record_inputs(
    "hudoc",
    functools.partial(download.record_path, layout.HUDOC_DOWNLOADED),
    functools.partial(download.body_path, layout.HUDOC_DOWNLOADED),
    functools.partial(summaries.sidecar_path, layout.HUDOC_DOWNLOADED))


def _hudoc_languages():
    """The one keyword hudoc's sync takes beyond the shared five: `--lang
    ENG,FRE` (case- and space-insensitive), else the module's own default."""
    return {"languages":
            tuple(lang.strip().upper() for lang in protocol.RUN.lang.split(","))
            if protocol.RUN.lang else download.DEFAULT_LANGUAGES}


def hudoc_harvest(scopes):
    """Harvest the named collections -- `lagen hudoc download decisions`, or no
    scope for both. Each walks under its own watermark, so naming one leaves the
    other's harvest state untouched.

    Two smaller harvests ride along, because both read the same endpoint and
    both produce inputs a later stage needs: the Court's own Case-Law
    Information Notes become the `clin/` sidecars `hudoc parse` folds into each
    case's artifact, and Domstolsverkets Swedish translations become the
    `commentary/hudoc/` drafts `kommentar parse` reads. Each costs one index
    walk and no body fetch, so they belong in the ordinary download rather than
    in a command someone has to remember (the expensive `ai-*` passes are the
    ones that stay explicit).

    A bounded run skips them: `--only` fetches one document, and `--limit` says
    to do a bounded amount of work, while both of these walk their whole index.
    Neither is bounded and neither would honour the cap."""
    collections = tuple(scopes) or download.DEFAULT_COLLECTIONS
    bounded = bool(protocol.RUN.only or protocol.RUN.limit)
    if protocol.RUN.dry_run:
        print("hudoc download: would download %s into %s"
              % (protocol.RUN.only or "HUDOC %s" % "/".join(collections),
                 layout.HUDOC_DOWNLOADED))
        if not bounded:
            translations.propose(layout.HUDOC_DOWNLOADED,
                                       layout.WIKI_ROOT, dry_run=True)
        return
    seen, changed = download.sync(
        layout.HUDOC_DOWNLOADED, full=protocol.RUN.force, only=protocol.RUN.only, limit=protocol.RUN.limit,
        delay=POLITENESS, collections=collections, **_hudoc_languages())
    print("hudoc download: %d seen, %d changed" % (seen, changed))
    if bounded:
        print("hudoc download: bounded run -- the Court's summaries and the "
              "Swedish translations are left for an unbounded one")
        return seen, changed
    summaries.sync(layout.HUDOC_DOWNLOADED, delay=POLITENESS)
    translations.propose(layout.HUDOC_DOWNLOADED, layout.WIKI_ROOT)
    # the case harvest's numbers; the two ride-along sweeps produce inputs for
    # other stages, not hudoc documents, so they do not add to the count
    return seen, changed


def hudoc_casenames(args=()):
    """Refresh the ECHR case snapshot (`lagen hudoc casenames`): rewrite
    hudoc/data/casenames.json from the records on disk, the join surface the
    citation engine resolves "Osman mot Förenade kungariket" through. No
    network -- run it after a harvest that changed the corpus."""
    if protocol.RUN.dry_run:
        print("hudoc casenames: would rebuild %s from %s"
              % (EMD_CASES, layout.HUDOC_DOWNLOADED))
        return
    cases, appnos = casenames.write(layout.HUDOC_DOWNLOADED)
    print("hudoc casenames: %d case names, %d application numbers -> %s"
          % (cases, appnos, EMD_CASES))


SOURCES: tuple[Source, ...] = (Source(
    "hudoc", lambda: download.list_basefiles(layout.HUDOC_DOWNLOADED), {
        "parse": parse_stage("hudoc", parse.parse,
                              layout.HUDOC_DOWNLOADED,
                              inputs=hudoc_inputs, code=HUDOC_CODE),
    },
    render=render.render,
    artifacts=functools.partial(layout.artifacts, "hudoc"),
    actions={"casenames": hudoc_casenames},
    harvest=hudoc_harvest, origin=origin(download.BASE),
    scopes=frozenset(download.COLLECTIONS),
    notes="download flags: --lang ENG[,FRE], --only <HUDOC-itemid>, --limit N\n"
          "scopes are the collections: judgments (Grand Chamber + Chamber, "
          "21,672), decisions (33,633); empty = both\n"
          "default language is ENG; --force refreshes stored metadata and "
          "bodies; bodies are fetched by a small worker pool (4 in flight)\n"
          "the walk is sliced by year because HUDOC serves no result past "
          "start=10000 -- an unsliced walk stopped at 2009 and left two thirds "
          "of the judgments unharvested\n"
          "a decision is where the Court says why a complaint never reaches "
          "the merits; 922 of the 1,088 Swedish cases are decisions\n"
          "an unbounded download also links the Court's own Case-Law "
          "Information Note from each case it summarises (metadata only, "
          "joined on application number + date) and drafts the "
          "commentary/hudoc/ files that link Domstolsverkets 87 Swedish "
          "translations from the judgments they translate; --only and --limit "
          "skip both, and --dry-run previews the drafts\n"
          "both joins need a single-language store: every language expression "
          "of a case repeats its application numbers, its date and its ECLI"),)
