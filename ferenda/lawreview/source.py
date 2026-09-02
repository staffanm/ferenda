"""The lawreview source's registration: journal articles (see `journals.py`).

Ten scopes -- nine journals plus the lawpub platform -- each its own host, so
the harvest fans out one worker per scope. The articles are mined for the
citations they make, never published: the source registers no renderer and
holds no search units. There is no per-document download stage (the
föreskrift/avg/rs rule).
"""

import functools
from pathlib import Path

from ..lib import compress, harvest, layout, util
from ..lib import stage as protocol
from ..lib.stage import (
    CASENUMBER_CODE,
    CITATION_DATA,
    Source,
    parse_stage,
    patch_input,
    require_single_scope,
    scoped_harvest,
)
from . import download, journals, parse

HERE = Path(__file__).parent

LAWREVIEW_CODE = (HERE / "parse.py", HERE / "model.py", HERE / "journals.py",
                  HERE / "download.py", HERE / "lawpub.py",
                  HERE.parent / "lib" / "pdftext.py",
                  HERE.parent / "lib" / "lagrum.py",
                  HERE.parent / "lib" / "emdref.py", *CASENUMBER_CODE,
                  *CITATION_DATA,
                  HERE.parent / "lib" / "artifact.py")


def lawreview_list():
    # one directory per scope under the shared root; the basefile leads
    # with the scope, so listing per scope gives the whole source (the
    # nine journals plus the lawpub platform)
    return sorted(bf for scope in download.SCOPES
                  for bf in compress.list_basefiles(
                      layout.LAWREVIEW_DOWNLOADED, scope))


def lawreview_inputs(basefile):
    """The record JSON plus the document -- re-downloading either re-stales the
    parse. The svjt document is the article's own page; the jp document is the
    issue's PDF, and its record's listing-page metadata is the only text the
    harvest could not take from the PDF itself."""
    journal = basefile.split("/", 1)[0]
    record = util.record_path(layout.LAWREVIEW_DOWNLOADED, journal, basefile)
    if journal != "lawpub" and journals.BY_KOD[journal].html_document:
        content = [harvest.page_path(layout.LAWREVIEW_DOWNLOADED, basefile)]
    else:
        content = [harvest.pdf_path(layout.LAWREVIEW_DOWNLOADED, basefile)]
    return [record, *content] + patch_input("lawreview", basefile)


def lawreview_harvest(scopes):
    """Bulk harvest of the nine journals (scopes = journal codes; empty =
    all). `--force` refetches every document; `--only svjt/2026-104` or
    `--only jp/2025-01-01` fetches a single article (needs its journal
    scope)."""
    example = "lagen lawreview download svjt --only svjt/2026-104"
    require_single_scope("lawreview", scopes, "journal", example)
    # one banner per scope in the run, each with its own host: the journals
    # are separate upstreams and the run reads only the hosts it names, so a
    # banner that listed every journal's origin would state requests the run
    # never makes. A dry run makes none of them, so it prints no banner.
    if not protocol.RUN.dry_run:
        for scope in (scopes or download.SCOPES):
            util.harvest_start("lawreview %s" % scope,
                               ", ".join(download.SCOPE_ORIGINS[scope]))
    # nine journals, nine hosts: they fan out one worker per journal,
    # regardless of the machine's worker count, so all nine walk in
    # parallel (they ride different hosts and pace themselves), and a
    # failing host is reported and re-run alone -- it does not take the
    # others down. No `jobs=`: that fan-out is the sync's own default.
    return scoped_harvest("lawreview", download, layout.LAWREVIEW_DOWNLOADED,
                          scopes, noun="journal", example=example,
                          label="+".join(download.SCOPES),
                          limit=protocol.RUN.limit)


# No per-document download stage (the foreskrift/avg/rs rule): the articles
# arrive only through the bulk `lawreview_harvest` sweep, so parse runs over
# whatever is on disk; relate/index/dump act on the artifacts by source name.
# The articles are not published (no renderer, no browse tree, unsearched):
# the catalog rows exist so the citation scan of their full text feeds the
# "Artiklar" rail, where a line links out to the journal's own page.
SOURCES: tuple[Source, ...] = (Source("lawreview", lawreview_list, {
    "parse": parse_stage("lawreview", parse.parse,
                          layout.LAWREVIEW_DOWNLOADED,
                          inputs=lawreview_inputs, code=LAWREVIEW_CODE),
},
    artifacts=functools.partial(layout.artifacts, "lawreview"),
    # no `render`: the articles have no page on this site (their rail lines
    # link out to the journals), and no search units either -- a hit on one
    # would be a dead link. The citations they make are the whole point.
    searchable=False,
    harvest=lawreview_harvest,
    self_banner=True,
    scopes=frozenset(download.SCOPES),
    notes="download flag: --only journal/nummer (fetch one; needs its "
          "journal scope). One known exception: the 1941 article promoted on "
          "the 1916 archive page cannot be named by --only (the year page it "
          "derives lists no such card); a full svjt sweep refreshes it\n"
          "scopes are the journals: " + ", ".join(
              "%s (%s)" % (j.kod, j.namn)
              for j in journals.JOURNALS)
          + "; empty = all\n"
          "svjt's document is the article's own page (the PDF, where the "
          "journal publishes one, is a copy of it); jp's is the issue's "
          "PDF, its metadata read off the issue page\n"
          "lod's document is the article's own page; only the volumes "
          "Lovdata publishes as pages (2022-) are walked, the earlier "
          "volumes being full-issue PDFs\n"
          "jp's host rate-limits with HTTP 466, which the harvest rides out "
          "on its own\n"
          "the journals fan out one worker per scope (separate hosts); "
          "--jobs does not apply here\n"
          "the lawpub scope is the lawpub.se platform, not a journal: one "
          "paginated listing of several publishers' open-access articles, "
          "walked newest-first on its watermark; --only lawpub/<nummer|doi> "
          "fetches one. FT and SIPLR publish on their own hosts and on the "
          "platform, so one article can arrive under two basefiles"),)
