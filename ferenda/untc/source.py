"""The untc source's registration: the UN Treaty Collection's MTDSG status
pages.

A curated instrument list drives one HTML scrape per treaty; the stored record
is the raw page, so parse is offline. The curated data file is a parse input
(editing it re-derives that artifact). The whole chain is the shared
`simple_source` shape."""

import functools
from pathlib import Path

from ..lib import layout
from ..lib.stage import Source, origin, record_inputs, simple_source
from . import download, parse, render

HERE = Path(__file__).parent

UNTC_CODE = (HERE / "parse.py", HERE / "model.py",
             HERE / "data" / "treaties.json")


SOURCES: tuple[Source, ...] = (simple_source(
    "untc", download, parse.parse, layout.UNTC_DOWNLOADED, UNTC_CODE,
    render=render.render,
    artifacts=functools.partial(layout.artifacts, "untc"),
    inputs=record_inputs("untc", functools.partial(download.page_path,
                                                   layout.UNTC_DOWNLOADED),
                         extra=(HERE / "data" / "treaties.json",)),
    origin=origin(download.DETAIL),
    dry_label="the curated UN Treaty Collection list",
    notes="download flags: --only <MTDSG-id, e.g. XXIII-1>, --limit N\n"
          "one static-HTML scrape per curated treaty; --force refreshes status"),)
