"""The stats source's registration: the corpus-wide measurements behind
/statistik.

It has nothing to download and no document to parse -- it reads the finished
corpus (catalog + artifact trees) and writes one artifact, which `generate`
renders. Like site it carries no citation graph and registers no `artifacts`
lister.
"""

import functools
import sys
from pathlib import Path

from ..lib import compress, layout
from ..lib.stage import Source, Stage, write_artifact, write_artifact_to
from . import compute, render

HERE = Path(__file__).parent

STATS_CODE = (HERE / "compute.py", HERE / "scan.py", HERE / "model.py")


def stats_compute_run(basefile):
    """Measure the corpus and write the artifact. Deliberately not incremental:
    every measurement is a fact about the *whole* corpus, so there is no subset
    of it that could be refreshed on its own -- the freshness question is "has
    anything anywhere changed", and the inputs that would answer it are the
    entire artifact tree, far too large to hash per run.

    The stage therefore declares no `inputs` and is marked `always=True`, so
    every invocation re-measures with or without `--force`. The mark is
    load-bearing, not decoration: a no-inputs stage is otherwise judged fresh on
    its recipe hash alone, which is constant between edits to stats/ -- so
    compute would run once, record a manifest entry, and be skipped for ever
    after, freezing /statistik at whatever the corpus looked like that day.

    Each run also archives the measurement under its own date
    (`layout.stats_snapshot`): the live artifact answers "how big is the corpus
    now", and only the archive can answer "how has it changed" -- which is the
    question a corpus measurement is really for. The same bytes are written to
    both, so a snapshot needs no separate reader."""
    report = compute.compute(
        layout.CATALOG,
        progress=lambda stage: sys.stderr.write("stats: scanning %s\n" % stage))
    art = report.to_artifact()
    write_artifact("stats", basefile, art)
    snapshot = layout.stats_snapshot(report.generated)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    write_artifact_to(snapshot, art)


def stats_write_pages(dest, *, whole_corpus):
    """Render /statistik from the computed artifact -- one artifact, one page, no
    catalog rows, so `generate_site` never reaches it. Without this a rebuild
    recomputed the measurements and then published the previous run's page:
    fresh numbers on disk, stale numbers on screen.

    A full-corpus run renders it only if the artifact exists, and that is not
    defensiveness: a corpus that has never had `stats compute` run simply has no
    such page. Asked for by name (`lagen stats generate`), the missing artifact
    is an error, and `write_stats` rightly raises rather than inventing a page."""
    if whole_corpus and not compress.exists(
            layout.artifact("stats", render.ARTIFACT_BASEFILE)):
        return
    render.write_stats(dest)


SOURCES: tuple[Source, ...] = (Source(
    "stats", lambda: [render.ARTIFACT_BASEFILE],
    # phase="dump": the measurements read the catalog `relate` just rebuilt
    # *and* the artifact trees `parse` just wrote, so compute runs after both,
    # and before the generate that renders /statistik from what it writes. It
    # cannot ride the rebuild's leading parse/versions loop (the obvious place,
    # and where a stage merely *named* "parse" would land it): that loop runs
    # before relate, so the measurements would be taken against the previous
    # run's catalog and /statistik would publish figures one rebuild out of date.
    {"compute": Stage("compute", stats_compute_run,
                      functools.partial(layout.artifact, "stats"),
                      code=STATS_CODE, always=True, phase="dump")},
    write_pages=stats_write_pages,
    notes="compute: measure the whole corpus (catalog + artifact trees) into "
          "artifact/stats/statistik.json -- minutes, not incremental. Runs as "
          "part of any rebuild that names the source (after dump, before "
          "generate); a rebuild of some other source does not pay for it\n"
          "generate: render that artifact to /statistik"),)
