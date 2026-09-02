"""The site source's registration: lagen.nu's editorial chrome -- the curated
frontpage, the /om about pages and sitenews -- authored as markdown in the same
lagen-wiki content repo (`site/`).

It is parsed to artifacts and rendered during generate, but -- like remisser --
it carries no citation graph, so it registers no `artifacts` lister and is
never related/indexed/dumped.
"""

import functools
from pathlib import Path

from ..lib import layout
from ..lib.stage import Source, Stage, write_artifact
from . import parse, render

HERE = Path(__file__).parent

SITE_CODE = (HERE / "parse.py", HERE / "model.py",
             HERE.parent / "lib" / "markdown.py")

WIKI_ROOT = layout.WIKI_ROOT


def site_record(basefile):
    return parse.record(str(WIKI_ROOT), basefile)


def site_parse_run(basefile):
    write_artifact("site", basefile, parse.artifact(basefile, str(WIKI_ROOT)))


def site_write_pages(dest, *, whole_corpus):
    """Write the editorial pages (frontpage, /om, sitenews) into the generated
    tree. They are artifact-backed but carry no catalog rows, so `generate_site`
    never reaches them -- this writer is the whole of `lagen site generate`, and
    a full-corpus run calls it too. Driven purely by which artifacts exist, so
    the whole-corpus flag makes no difference: an empty site source writes
    nothing either way."""
    render.write_site(dest)


SOURCES: tuple[Source, ...] = (Source(
    "site",
    lambda: parse.list_basefiles(str(WIKI_ROOT)),
    {"parse": Stage("parse", site_parse_run,
                    functools.partial(layout.artifact, "site"),
                    inputs=lambda bf: [site_record(bf)], code=SITE_CODE)},
    write_pages=site_write_pages,
    owns_frontpage=render.has_frontpage,
    # not catalog rows: a re-parsed editorial edit must reopen generate's gate
    layers=lambda: list(layout.artifacts("site"))),)
