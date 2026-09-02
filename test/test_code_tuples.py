"""The `*_CODE` recipe tuples, guarded against going stale.

`lib/corpus.py`'s `RELATE_CODE`/`INDEX_CODE`/`DUMP_CODE`/`CORR_CODE`/
`GENERATE_CODE` and every `Stage.code` in `stage.SOURCES` are hand-listed
"output-affecting" files: `freshness.hash_files` hashes them, and their hash is
the recipe version that decides whether a step re-runs.

`hash_files` **skips a path that does not exist**. So a moved or deleted module
left in a tuple is silent: the hash still changes for the other members, but the
listed file contributes nothing and the tuple quietly stops covering what it
claims to. Editing it never re-stales the step again.

Three checks, in the order a stale entry is found:

  a. every listed path exists (a hard failure, naming the tuple and the path);
  b. every listed **.py** file is a head module or is import-reachable from one
     (an ast walk over `ferenda/`) -- this is what catches a module that moved
     out from under the tuple. Non-`.py` entries are data (templates, assets,
     `*.json` snapshots, `*.txt` prompts): only their existence is checked;
  c. the closure members a tuple does *not* list are reported, never failed --
     an over-broad closure is normal (a source's `source.py` reaches its
     downloader and its renderer, which parse does not run), so a human
     adjudicates that list.
"""

import ast
import functools
import inspect
import warnings
from pathlib import Path

import ferenda.build  # noqa: F401 -- importing build is what fills stage.SOURCES
from ferenda.lib import corpus, stage

PKG = Path(stage.__file__).parent.parent

# The entry point each corpus-wide tuple's step actually calls. Everything else
# a tuple lists must be reachable from one of these by import.
HEADS = {
    "RELATE_CODE": ("lib/catalog.py",),
    "INDEX_CODE": ("lib/search.py",),
    "DUMP_CODE": ("lib/dump.py",),
    # the sources' own cross-pass code joins through `Source.cross_code`
    # (gathered below), each file its own head
    "CORR_CODE": ("lib/hierarki.py",),
    # page.py renders a document page; the faceted browse, the subdomain
    # projections, the API's own pages and the statistics charts are their own
    # entry points, as is every source's `*/render.py` (added below)
    "GENERATE_CODE": ("lib/page.py", "site/browse.py", "site/subdomains.py",
                      "api/app.py", "stats/charts.py"),
}

# Listed by a recipe and deliberately never imported: `lib/poi.py` is a client
# that runs all Java through the `poi_worker` subprocess, which nothing may
# import (rule:no-infunction-imports names the same split). Its edits still have
# to re-stale dv parse, so it stays in the tuple.
NEVER_IMPORTED = {PKG / "lib" / "poi_worker.py"}


@functools.cache
def _imports(path: Path) -> frozenset[str]:
    """The first-party module names `path` imports, resolved absolutely. Each
    `from X import a, b` also yields `X.a`/`X.b`, since a name may be a
    submodule rather than an attribute; `_module_path` drops what is not one."""
    parts = ("ferenda." + ".".join(path.relative_to(PKG).with_suffix("").parts)
             ).split(".")
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # relative: anchor on the package
                base = ".".join(parts[:-node.level])
                if node.module:
                    base += "." + node.module
            elif (node.module or "").startswith("ferenda"):
                base = node.module
            else:
                continue
            out.add(base)
            out.update("%s.%s" % (base, a.name) for a in node.names)
    return frozenset(m for m in out if m.startswith("ferenda"))


def _module_path(name: str) -> Path | None:
    """The file a dotted first-party module name names, or None."""
    parts = name.split(".")[1:]
    if not parts:
        return None
    rel = Path(*parts)
    for cand in (PKG / rel.with_suffix(".py"), PKG / rel / "__init__.py"):
        if cand.exists():
            return cand
    return None


def _closure(heads):
    """Every first-party module reachable from `heads` by import."""
    seen, stack = set(), list(heads)
    while stack:
        path = stack.pop()
        if path in seen or not path.exists():
            continue
        seen.add(path)
        stack += [p for m in _imports(path) if (p := _module_path(m)) not in seen
                  and p is not None]
    return seen


def _recipes():
    """Every hand-listed recipe tuple as (label, heads, paths).

    A source stage's heads are the source's *own* modules the tuple names (a
    source is a program: its files are what the stage runs) plus the module the
    stage's `run` callable is written in; the `lib/` members it lists must be
    reachable from those."""
    out = []
    for name, heads in HEADS.items():
        tup = getattr(corpus, name)
        out.append((name, [PKG / h for h in heads]
                    + [p for p in tup if p.name == "render.py"], list(tup)))
    for source in sorted(stage.SOURCES.values(), key=lambda s: s.name):
        for st in source.stages.values():
            own = [p for p in st.code
                   if p.suffix == ".py" and p.relative_to(PKG).parts[0] == source.name]
            out.append(("%s %s stage.code" % (source.name, st.name),
                        own + [Path(inspect.getsourcefile(st.run))], list(st.code)))
        if source.cross_code:
            out.append(("%s cross_code" % source.name, list(source.cross_code),
                        list(source.cross_code)))
    return out


RECIPES = _recipes()


def test_every_listed_path_exists():
    """`hash_files` skips a path that is gone, so a stale entry is silent."""
    missing = ["%s: %s" % (label, p.relative_to(PKG))
               for label, _heads, paths in RECIPES for p in paths if not p.exists()]
    assert not missing, "recipe tuples list files that do not exist:\n  " \
        + "\n  ".join(missing)


def test_every_python_entry_is_reachable_from_a_head():
    """A `.py` entry no head imports any more is dead weight in the recipe --
    the module it used to name has moved, merged or gone."""
    stale = []
    for label, heads, paths in RECIPES:
        reach = _closure(heads) | set(heads) | NEVER_IMPORTED
        stale += ["%s: %s" % (label, p.relative_to(PKG))
                  for p in paths if p.suffix == ".py" and p not in reach]
    assert not stale, "recipe entries no head module imports:\n  " + "\n  ".join(stale)


def test_report_closure_members_absent_from_the_tuple():
    """Informational: what a head reaches that its tuple does not list. Never a
    failure -- a closure over-reaches (a source's `source.py` imports its
    downloader and its renderer, which parse does not run), so a human decides
    which of these actually change the step's output."""
    lines = []
    for label, heads, paths in RECIPES:
        absent = sorted(str(p.relative_to(PKG))
                        for p in _closure(heads) - set(paths)
                        if p.name != "__init__.py")
        if absent:
            lines.append("%s (%d): %s" % (label, len(absent), ", ".join(absent)))
    print("\nclosure members absent from their recipe tuple:")
    for line in lines:
        print("  " + line)
    warnings.warn("%d recipe tuples reach first-party modules they do not list; "
                  "run this test with -s for the per-tuple lists" % len(lines),
                  stacklevel=1)
