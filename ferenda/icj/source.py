"""The icj source's registration: the International Court of Justice's
decisions.

The Court's /decisions view scopes the harvest to judgments, advisory opinions
and provisional-measures orders; each decision's page range from the printed
I.C.J. Reports is the body. The OCR vocabulary is a recipe input -- rebuilding
it (tools/corpus/icj_vocabulary.py) changes how every scanned decision reads.
The whole chain is the shared `simple_source` shape."""

import functools
from pathlib import Path

from ..lib import layout
from ..lib.stage import Source, origin, record_inputs, simple_source
from . import download, parse, render

HERE = Path(__file__).parent

ICJ_CODE = (HERE / "parse.py", HERE / "model.py", HERE / "ocr.py",
            HERE / "treaties.py", HERE / "reports.py",
            HERE / "data" / "vocabulary.txt",
            HERE.parent / "lib" / "treatyref.py",
            HERE.parent / "lib" / "treaty_ids.py",
            HERE.parent / "lib" / "data" / "treaty_names.json",
            HERE.parent / "lib" / "pdftext.py",
            HERE.parent / "lib" / "artifact.py")


SOURCES: tuple[Source, ...] = (simple_source(
    "icj", download, parse.parse, layout.ICJ_DOWNLOADED, ICJ_CODE,
    render=render.render,
    artifacts=functools.partial(layout.artifacts, "icj"),
    inputs=record_inputs("icj",
                         functools.partial(download.record_path,
                                           layout.ICJ_DOWNLOADED),
                         functools.partial(download.body_path,
                                           layout.ICJ_DOWNLOADED),
                         extra=(HERE / "data" / "vocabulary.txt",)),
    origin=origin(download.ICJ),
    dry_label="the ICJ's judgments, advisory opinions and provisional-measures orders",
    notes="download flags: --only <decision stem, e.g. 070-19860627-JUD-01-00>, --limit N\n"
          "scope: 255 of the Court's 877 decisions; the ~620 time-limit orders are out\n"
          "the PDFs are Cloudflare-walled and fetched through headful Chrome"),)
