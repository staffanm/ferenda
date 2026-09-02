"""The icrc source's registration: the international humanitarian law treaties
the ICRC publishes.

The stored record is the whole JSON:API envelope (metadata + authentic text +
states parties), so parse is offline and needs no separate body -- the
parser/model are the only recipe. The whole chain is the shared
`simple_source` shape."""

import functools
from pathlib import Path

from ..lib import layout
from ..lib.stage import Source, origin, record_inputs, simple_source
from . import download, parse, render

HERE = Path(__file__).parent

ICRC_CODE = (HERE / "parse.py", HERE / "model.py",
             HERE.parent / "lib" / "treatyref.py",
             HERE.parent / "lib" / "treaty_ids.py",
             HERE.parent / "lib" / "data" / "treaty_names.json")


SOURCES: tuple[Source, ...] = (simple_source(
    "icrc", download, parse.parse, layout.ICRC_DOWNLOADED, ICRC_CODE,
    render=render.render,
    artifacts=functools.partial(layout.artifacts, "icrc"),
    inputs=record_inputs("icrc", functools.partial(download.record_path,
                                                   layout.ICRC_DOWNLOADED)),
    origin=origin(download.SITE),
    dry_label="all ICRC IHL treaties",
    notes="download flags: --only <ICRC-treaty-number>, --limit N\n"
          "one JSON:API list call plus one included fetch per treaty"),)
