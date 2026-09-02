"""The guidance source's registration: vägledning from EU-level bodies (see
`issuers.py`).

A scope is one upstream walk, not one series. The guidance arrives only
through the bulk harvest -- there is no per-document download stage (the
föreskrift/avg/rs rule).
"""

import functools
from pathlib import Path

from ..lib import compress, layout, util
from ..lib import stage as protocol
from ..lib.pdftext import pdf_intermediate
from ..lib.stage import (
    CITATION_DATA,
    Source,
    parse_stage,
    patch_input,
    scoped_harvest,
)
from . import download, edpb_download, eurlex_download, issuers, parse, render

HERE = Path(__file__).parent

GUIDANCE_CODE = (HERE / "parse.py", HERE / "model.py", HERE / "issuers.py",
                 HERE / "edpb_data.py", HERE / "edpb_download.py",
                 HERE / "eba_download.py", HERE / "easa_download.py",
                 HERE / "acer_download.py", HERE / "enisa_download.py",
                 HERE / "esma_download.py", HERE / "berec_download.py",
                 HERE / "edps_download.py", HERE / "eiopa_download.py",
                 HERE / "eurlex_download.py", HERE / "euipo_download.py",
                 HERE / "download.py",
                 HERE.parent / "lib" / "pdftext.py",
                 HERE.parent / "lib" / "lagrum.py",
                 HERE.parent / "lib" / "formex.py",
                 HERE.parent / "lib" / "emdref.py", *CITATION_DATA,
                 HERE.parent / "lib" / "artifact.py")


def guidance_list():
    # one directory per issuing body under the shared root; the basefile leads
    # with the issuer, so listing per body gives the whole source
    return sorted(bf for utgivare in issuers.KODER
                  for bf in compress.list_basefiles(layout.GUIDANCE_DOWNLOADED,
                                                    utgivare))


def guidance_inputs(basefile):
    """The record JSON plus the document -- re-downloading either re-stales the
    parse. Every guidance record names a document (the harvest writes no record
    without one).

    Where the document came from CELLAR rather than the body's own site it is a
    directory of manifestations (one file per language), not a single PDF, so
    the whole directory's contents are the input."""
    utgivare = basefile.split("/", 1)[0]
    record = util.record_path(layout.GUIDANCE_DOWNLOADED, utgivare, basefile)
    if utgivare in parse.EURLEX_KODER:
        content = sorted(eurlex_download.content_dir(
            layout.GUIDANCE_DOWNLOADED, basefile).glob("*"))
    else:
        content = [edpb_download.pdf_path(layout.GUIDANCE_DOWNLOADED, basefile)]
    return [record, *content] + patch_input("guidance", basefile)


def guidance_harvest(scopes):
    """Bulk harvest of EU-level guidance (scopes = ``<utgivare>/<serie>``;
    empty = all). `--force` refetches every document; `--only
    edpb/riktlinjer/05-2020` fetches a single document (needs its scope)."""
    # three upstreams (the EDPB site, the Commission newsroom, the EBA):
    # fan them out the way rs/avg/foreskrift do
    return scoped_harvest("guidance", download, layout.GUIDANCE_DOWNLOADED,
                          scopes, noun="scope",
                          example="lagen guidance download edpb/riktlinjer "
                                  "--only edpb/riktlinjer/05-2020",
                          label=" + ".join(download.SCOPES),
                          limit=protocol.RUN.limit, jobs=protocol.RUN.jobs)


def _guidance_scope_label(scope):
    """What one download scope covers, for the source's help: the series where
    the scope names one, and the issuing body where one walk covers all of that
    body's series."""
    utgivare, _, serie = scope.partition("/")
    issuer = issuers.BY_KOD[utgivare]
    return issuers.BY_SERIE[(utgivare, serie)].label if serie \
        else issuer.namn


# No per-document download stage (the foreskrift/avg/rs rule): the guidance
# arrives only through the bulk `guidance_harvest` sweep.
def guidance_intermediate(basefile):
    """An EDPB vägledning's PDF as pdftohtml XML. Every record names a document
    -- the harvest writes none without one -- so an absent file is a broken
    store, not a document-less entry."""
    return pdf_intermediate(
        edpb_download.pdf_path(layout.GUIDANCE_DOWNLOADED, basefile))


SOURCES: tuple[Source, ...] = (Source("guidance", guidance_list, {
    "parse": parse_stage("guidance", parse.parse,
                          layout.GUIDANCE_DOWNLOADED,
                          inputs=guidance_inputs, code=GUIDANCE_CODE),
},
    render=render.render,
    intermediate=(guidance_intermediate, "pdftohtml XML"),
    artifacts=functools.partial(layout.artifacts, "guidance"),
    harvest=guidance_harvest,
    origin=download.ORIGIN,
    scopes=frozenset(download.SCOPES),
    notes="download flag: --only utgivare/serie/nummer (fetch one; needs its "
          "scope)\n"
          "a scope is one upstream walk, not one series: a bare utgivare "
          "where one walk covers all of that body's series, <utgivare>/<serie> "
          "where the series come off different upstreams -- " + ", ".join(
              "%s (%s)" % (scope, _guidance_scope_label(scope))
              for scope in download.SCOPES)
          + "; empty = all\n"
          "identity is the issuing body's own number, never a CELEX -- 122 "
          "förarbeten cite an ECB-yttrande as CON/2013/82 and none as "
          "52013AB0082 (Riktlinjer 05/2020, EBA/GL/2021/05, WP 248)\n"
          "the Swedish version is published wherever the body has issued one "
          "and the English one otherwise; the record says which\n"
          "edpb/wp is a closed corpus of artikel 29-gruppens vägledningar, "
          "harvested from the Commission newsroom (the EDPB's own pages for "
          "them carry no document) -- a run re-resolves them only under "
          "--force, each costing a 10-28 MB language ZIP"),)
