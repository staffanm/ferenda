"""The rs source's registration: myndigheternas rättsliga ställningstaganden.

Six agencies publish their own numbered series; identity is the agency's own
number, not a diarienummer. Ställningstaganden arrive only through the bulk
harvest -- there is no per-document download stage, so parse runs over
whatever is on disk (the avg/föreskrift rule).
"""

import functools
from pathlib import Path

from ..lib import compress, layout, markup, util
from ..lib import stage as protocol
from ..lib.errors import SkipDocument
from ..lib.pdftext import pdf_intermediate
from ..lib.stage import (
    CASENUMBER_CODE,
    CITATION_DATA,
    Source,
    parse_stage,
    patch_input,
    scoped_harvest,
)
from . import agencies, download, parse, render

HERE = Path(__file__).parent

RS_CODE = (HERE / "parse.py", HERE / "model.py", HERE / "agencies.py",
           HERE / "download.py", HERE / "skv.py",
           HERE.parent / "lib" / "pdftext.py",
           HERE.parent / "lib" / "lagrum.py",
           HERE.parent / "lib" / "emdref.py", *CITATION_DATA, *CASENUMBER_CODE,
           HERE.parent / "lib" / "artifact.py")


def rs_list():
    return sorted(bf for org in agencies.ORGS
                  for bf in compress.list_basefiles(layout.RS_DOWNLOADED, org))


def rs_record(basefile):
    return util.record_path(layout.RS_DOWNLOADED, basefile.split("/", 1)[0],
                            basefile)


def rs_inputs(basefile):
    """The record JSON plus the document -- the PDF six agencies published it
    as, or Skatteverkets page. Re-downloading either re-stales the parse. A
    repealed Konkurrensverket entry publishes no document, so the document is an
    input only where there is one."""
    paths = [rs_record(basefile)]
    document = download.body_path(layout.RS_DOWNLOADED, basefile)
    if compress.exists(document):
        paths.append(document)
    return paths + patch_input("rs", basefile)


def rs_harvest(scopes):
    """Bulk harvest of the agencies' rättsliga ställningstaganden (scopes =
    agency codes; empty = every agency). Skatteverket needs the paced Camoufox
    transport and runs one at a time beside the parallel HTTP agencies;
    `--no-download-browser` leaves it out of an empty-scope run. Naming it
    explicitly still harvests it. `--force` refetches every document;
    `--only fk/2025:01` fetches a single ställningstagande (needs its agency
    scope)."""
    if not scopes and protocol.RUN.download_browser:
        scopes = list(agencies.ORGS)
    elif not scopes:
        skipped = agencies.BROWSER_ORGS
        scopes = list(agencies.DEFAULT_ORGS)
        print("rs download: skipping %d browser-shielded agenc%s (%s) -- "
              "--no-download-browser" % (
                  len(skipped), "y" if len(skipped) == 1 else "ies",
                  ", ".join(skipped)))
    # seven agencies, seven hosts: fan them out; `serial=` keeps the
    # browser-shielded ones off each other
    return scoped_harvest("rs", download, layout.RS_DOWNLOADED, scopes,
                          noun="agency",
                          example="lagen rs download fk --only fk/2025:01",
                          label="every agency",
                          limit=protocol.RUN.limit, jobs=protocol.RUN.jobs)


def rs_intermediate(basefile):
    """A rättsligt ställningstagande's PDF as pdftohtml XML -- or, for the
    agency that publishes web pages rather than PDFs (Skatteverket), the page
    itself, normalised to one block element per line the way `rs.parse` does
    before applying the patch. Every agency publishes a document, except for the
    repealed Konkurrensverket entries that keep only their förteckning row --
    and those have no text to patch."""
    path = download.body_path(layout.RS_DOWNLOADED, basefile)
    if not compress.exists(path):
        raise SkipDocument("%s: the agency published no document for it"
                           % basefile)
    if agencies.BY_ORG[basefile.split("/", 1)[0]].page_body:
        return markup.block_lines(compress.read_text(path))
    return pdf_intermediate(path)


# No per-document download stage (the foreskrift/avg rule): ställningstaganden
# arrive only through the bulk `rs_harvest` sweep, so parse runs over whatever is
# on disk; relate/index/dump/generate act on the artifacts by source name.
SOURCES: tuple[Source, ...] = (Source("rs", rs_list, {
    "parse": parse_stage("rs", parse.parse, layout.RS_DOWNLOADED,
                          inputs=rs_inputs, code=RS_CODE),
},
    render=render.render,
    intermediate=(rs_intermediate,
                  "pdftohtml XML (skv: the ställningstagande's own web page)"),
    artifacts=functools.partial(layout.artifacts, "rs"),
    harvest=rs_harvest,
    origin=agencies.BY_ORG["fk"].listing,
    scopes=frozenset(agencies.ORGS),
    notes="download flag: --only org/nummer (fetch one; needs its agency scope)\n"
          "scopes are the myndigheter: " + ", ".join(
              "%s (%s)" % (a.org, a.name) for a in agencies.REGISTRY)
          + "; empty = every agency; --no-download-browser leaves out the "
          "Camoufox agency (skv)\n"
          "identity is the agency's own number, not a diarienummer -- a "
          "ställningstagande is published as a numbered item in the agency's "
          "series (IMYRS 2024:1, FKRS 2025:01, RS/028/2021). Skatteverket is "
          "the exception: it numbers no series and cites its own positions by "
          "dnr, so there the dnr is the identity\n"
          "the skv scope is 2,614 documents published as web pages rather than "
          "PDFs, behind the same F5/Shape challenge SKVFS sits behind\n"
          "the fk scope reads each document's Serienummer out of its PDF (the "
          "listing retypes it, and once wrongly), so a first run fetches every "
          "PDF and later runs reuse the number the record was filed under\n"
          "the migr scope harvests through the Lifos database over an "
          "AIA-completed TLS chain (the site sends no intermediate); its series "
          "also holds rättsliga kommentarer (RK/…)\n"
          "the fi and kkv listings keep upphävda ställningstaganden, which are "
          "stored and rendered as withdrawn rather than dropped"),)
