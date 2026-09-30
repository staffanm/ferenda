"""The kkvdomar source's registration: the kammarrätternas avgöranden in public
procurement cases, from Konkurrensverkets domstolsdatabas.

The database's listing scopes the harvest to the kammarrätter; each case page
names the decision PDF. The stored record + PDF are the parse inputs. The whole
chain is the shared `simple_source` shape, made patchable: a decision is a
verdict, and a redaction is a patch on its PDF text."""

import functools
from pathlib import Path

from ..lib import casenumbers, compress, layout
from ..lib.pdftext import pdf_intermediate
from ..lib.stage import (
    CASENUMBER_CODE,
    CITATION_DATA,
    Source,
    origin,
    patch_input,
    simple_source,
)
from . import download, parse, render

HERE = Path(__file__).parent

KKVDOMAR_CODE = (HERE / "parse.py", HERE / "model.py", HERE / "download.py",
                 HERE.parent / "lib" / "pdftext.py",
                 HERE.parent / "lib" / "lagrum.py",
                 HERE.parent / "lib" / "emdref.py",
                 HERE.parent / "lib" / "casenaming.py",
                 HERE.parent / "lib" / "artifact.py",
                 *CITATION_DATA, *CASENUMBER_CODE)


def kkvdomar_inputs(basefile):
    """The record, the decision PDF where the case names one (14 of the 3,835
    cases name none), the document's patch -- and the index of decisions an HFD
    referat supersedes, for the decisions it names. A decision that joins the
    index adds the file to its inputs, and that re-stales its parse."""
    root = layout.KKVDOMAR_DOWNLOADED
    body = download.body_path(root, basefile)
    return ([download.record_json(root, basefile)]
            + ([body] if compress.exists(body) else [])
            + ([download.superseded_path(root)]
               if basefile in download.superseded(root) else [])
            + patch_input("kkvdomar", basefile))


def kkvdomar_intermediate(basefile):
    return pdf_intermediate(download.body_path(layout.KKVDOMAR_DOWNLOADED,
                                               basefile))


SOURCES: tuple[Source, ...] = (simple_source(
    "kkvdomar", download, parse.parse, layout.KKVDOMAR_DOWNLOADED,
    KKVDOMAR_CODE,
    render=render.render,
    artifacts=functools.partial(layout.artifacts, "kkvdomar"),
    inputs=kkvdomar_inputs,
    intermediate=(kkvdomar_intermediate, "pdftohtml XML"),
    origin=origin(download.LISTING),
    # the decisions cite each other by court and case number, which resolves
    # only through the case-number snapshot
    after={"parse": (functools.partial(casenumbers.after_parse, "kkvdomar"),)},
    dry_label="the kammarrätternas avgöranden in Konkurrensverkets domstolsdatabas",
    notes="download flags: --only <court/målnummer/date, e.g. "
          "kst/6426-25/2026-03-12>, --limit N\n"
          "scope: the kammarrätter only (the database's instans 2), 2016 on\n"
          "a routine run walks the listing 60 days past the last harvest; "
          "--force walks all of it and fetches every case again"),)
