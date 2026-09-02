"""The icc source's registration: the International Criminal Court's
substantive decisions.

The icc-cpi.int /decisions facets scope the harvest, Legal Tools resolves each
to metadata + PDF; the stored record + PDF are the parse inputs, and the
curated decision-type list is a recipe input. The whole chain is the shared
`simple_source` shape."""

import functools
from pathlib import Path

from ..lib import layout
from ..lib.stage import Source, origin, record_inputs, simple_source
from . import download, parse, render

HERE = Path(__file__).parent

ICC_CODE = (HERE / "parse.py", HERE / "model.py", HERE / "treaties.py",
            HERE / "data" / "decision_types.json",
            HERE.parent / "lib" / "treatyref.py",
            HERE.parent / "lib" / "treaty_ids.py",
            HERE.parent / "lib" / "data" / "treaty_names.json",
            HERE.parent / "lib" / "pdftext.py",
            HERE.parent / "lib" / "artifact.py")


SOURCES: tuple[Source, ...] = (simple_source(
    "icc", download, parse.parse, layout.ICC_DOWNLOADED, ICC_CODE,
    render=render.render,
    artifacts=functools.partial(layout.artifacts, "icc"),
    inputs=record_inputs("icc",
                         functools.partial(download.record_path,
                                           layout.ICC_DOWNLOADED),
                         functools.partial(download.body_path,
                                           layout.ICC_DOWNLOADED),
                         extra=(HERE / "data" / "decision_types.json",)),
    origin=origin(download.ICC),
    dry_label="the curated ICC substantive decisions",
    notes="download flags: --only <ICC-doc-number, e.g. ICC-01/04-02/06-2359>, --limit N\n"
          "scope: substantive Rome-Statute decisions; text via the ICC Legal Tools API"),)
