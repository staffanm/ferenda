"""The coe source's registration: the Council of Europe Treaty Office
instruments (CETS/ETS numbers), one web-service search plus each official
English PDF.

Its whole chain is the shared `simple_source` shape -- one bulk sync over the
publisher's own list and a parse that reads the stored record in one call --
so the registration is the source (rule:sources-are-programs)."""

import functools
from pathlib import Path

from ..lib import compress, layout
from ..lib.stage import Source, origin, patch_input, simple_source
from . import download, parse, render

HERE = Path(__file__).parent

COE_CODE = (HERE / "parse.py", HERE / "model.py",
            HERE.parent / "lib" / "treatyref.py",
            HERE.parent / "lib" / "treaty_ids.py",
            HERE.parent / "lib" / "data" / "treaty_names.json",
            HERE.parent / "lib" / "coe.py",
            HERE.parent / "lib" / "pdftext.py",
            HERE.parent / "lib" / "artifact.py")


def coe_inputs(basefile):
    record_path = download.record_path(layout.COE_DOWNLOADED, basefile)
    paths = [record_path]
    if compress.exists(record_path):
        record = compress.read_json(record_path)
        paths.append(download.body_path(layout.COE_DOWNLOADED, record))
    return paths + patch_input("coe", basefile)


SOURCES: tuple[Source, ...] = (simple_source(
    "coe", download, parse.parse, layout.COE_DOWNLOADED, COE_CODE,
    render=render.render, artifacts=functools.partial(layout.artifacts, "coe"),
    inputs=coe_inputs, origin=origin(download.FULL_LIST),
    dry_label="all Treaty Office instruments",
    notes="download flags: --only <CETS-number>, --limit N\n"
          "one Treaty Office web-service search plus each official English PDF"),)
