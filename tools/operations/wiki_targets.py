"""Which documents a push to the content repo re-staled.

`lagen-wiki` holds six kinds of input, and a change to each re-stales a
different, usually very small, set of documents:

    site/<page>.md        one site page's parse
    site/media/…          nothing to parse -- generate copies the tree
    commentary/…/<x>.md   one kommentar document
    concept/<x>.md        one begrepp document
    patches/<source>/…    the parse of the one document that source patches
    ann/<source>/…        the generate of the one document it annotates

The whole point is to name the *document*, not the source. `lagen sfs
generate 1962:700` refreshes that statute's parse (the patch is a declared
input, see `sfs.source.sfs_inputs`), relates only if its catalog row actually
disagrees, and renders that one page -- `build._prepare_targeted_generate`.
A source-wide run instead pays that source's whole freshness scan.

**Nothing here parses a filename.** Every rule that turns an identity into a
path already exists as a forward function -- `site.parse.record`,
`wiki.parse.kommentar_index`, `wiki.parse.begrepp_index`, `layout.patch`,
`annstore.path` -- so this inverts those rather than reproducing them. That
matters more than it looks: `util.basefile_slug` maps `/`, `:` and space all
onto `-` or `_`, so a patch named `1962-700.patch` is genuinely ambiguous
between `1962:700` and `1962/700` and cannot be read back. Inverting the
forward function is exact, and it follows a changed rule automatically.

A path that resolves to no document is NOT silently dropped. It widens to
that source's whole rebuild, and a path under no known directory widens to
everything -- being slow is recoverable, publishing a stale page is not
(rule:fail-fast).

Usage:
    wiki_targets.py <changed-path> [<changed-path> ...]

prints one shell command per line, and nothing at all when a push touched
only files no page is built from.
"""

import shlex
import sys
from pathlib import Path

from ferenda import build, config
from ferenda.lib import layout
from ferenda.site import parse as site_parse
from ferenda.wiki import parse as wiki_parse

# what generate copies wholesale rather than parsing (site/render.py); a
# changed screencast needs the copy, which is the site source's own generate
MEDIA = "site/media"

# The one exception to "an unrecognised path means the whole corpus". This repo
# runs CI of its own, and its workflow files are read by GitHub, never by
# ferenda -- no page is built from them, provably, so widening to a corpus-wide
# rebuild for a CI edit buys nothing. Kept to exactly this: a README or a stray
# report is *probably* not an input either, but "probably" is the wrong word to
# hang a published page on (see `targets`'s else branch).
NOT_CONTENT = (".github/",)


def _rel(path, root):
    """`path` as a `root`-relative posix string, or None if it is outside."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return None


def _invert(pairs):
    """{path -> basefile} from an index of {basefile -> path}."""
    return {Path(p).resolve().as_posix(): bf for bf, p in pairs}


def targets(changed, wiki_root=None):
    """`(source, basefile)` for each changed path, `(source, None)` where the
    source is known but the document is not, and `(None, None)` for a path that
    could have re-staled anything. Returns a sorted, de-duplicated list."""
    root = str(wiki_root or config.WIKI_ROOT)
    found, cache = set(), {}
    site_index = _invert((bf, site_parse.record(root, bf))
                         for bf in site_parse.list_basefiles(root))
    kommentar = _invert(wiki_parse.kommentar_index(root).items())
    begrepp = _invert(wiki_parse.begrepp_index(root).items())

    for raw in changed:
        rel = _rel(raw if Path(raw).is_absolute() else Path(root) / raw, root)
        if rel is None:
            found.add((None, None))
            continue
        full = (Path(root) / rel).resolve().as_posix()
        head = rel.split("/")[0]
        if rel.startswith(MEDIA + "/"):
            # the tree is copied, not parsed: nothing to re-parse, but the copy
            # only happens inside a site generate
            found.add(("site", None))
        elif head == "site":
            found.add(("site", site_index.get(full)) if full in site_index
                      else ("site", None))
        elif head == "commentary":
            found.add(("kommentar", kommentar.get(full))
                      if full in kommentar else ("kommentar", None))
        elif head == "concept":
            found.add(("begrepp", begrepp.get(full))
                      if full in begrepp else ("begrepp", None))
        elif head in ("patches", "ann"):
            found.add(_document_of(head, rel, cache))
        elif rel.startswith(NOT_CONTENT):
            continue                 # CI config -- nothing is built from it
        else:
            # README, a report, something new -- no page is built from it that
            # this knows of, so make no claim either way
            found.add((None, None))
    return sorted(found, key=lambda t: (t[0] or "", t[1] or ""))


def _artifact_of(kind, rel):
    """The artifact path a `patches/<source>/…` or `ann/<source>/…` file
    belongs to, computed rather than searched.

    Both trees mirror the artifact tree by construction, so the mirror can be
    walked backwards in one step:

        layout.patch(s, bf)   = PATCHES / s              / relpath(s, bf) + suffix
        layout.artifact(s, bf)= ARTIFACT / SOURCE_DIR[s] / relpath(s, bf) + ".json"
        annstore.path(s, bf)  = ANN / <artifact-relative path>.with_suffix(suffix)

    -- the same `relpath(source, basefile)` on every line. Strip the tree's
    prefix and the layer's suffix and what is left is the document's storage
    path, which names it as exactly as its basefile does: `cmd_generate` keys
    on artifact paths and derives them from basefiles, not the other way about
    (build.py:960).

    Note the two suffix rules differ, and getting them the same way round
    matters: a patch *appends* to the whole name (`…_442.json` would become
    `…_442.json.patch`, but relpath carries no extension, so it is `…_442` +
    `.desc`), while a layer *replaces* the extension (`.json` -> `.ann`).
    """
    parts = rel.split("/")
    if len(parts) < 3:
        return None, None            # patches/README.md and friends
    if kind == "patches":
        source, tail = parts[1], "/".join(parts[2:])
        for suffix in (".rot18.patch", ".patch", ".desc"):
            if tail.endswith(suffix):
                tail = tail[:-len(suffix)]
                break
        else:
            return None, None        # not a layer this knows
        directory = layout.SOURCE_DIR.get(source)
        if directory is None:
            return None, None
        return source, layout.ARTIFACT / directory / (tail + ".json")
    # ann/<source-dir>/…: the tree is keyed by SOURCE_DIR, not by source name
    directory, tail = parts[1], "/".join(parts[2:])
    source = next((name for name, d in layout.SOURCE_DIR.items()
                   if d == directory), None)
    if source is None:
        return None, None
    return source, (layout.ARTIFACT / directory / tail).with_suffix(".json")


def _basefiles_by_artifact(source, cache):
    """`{artifact path -> basefile}` for one source, built once per run.

    The remaining scan, and it is per *source* rather than per changed file: a
    push touching forty patches of one source pays for one pass, not forty.
    It cannot be avoided entirely -- `util.basefile_slug` maps `/`, `:` and
    space all onto `-` or `_`, so `NJA_1990_s_442` does not say whether the
    basefile was "NJA 1990 s. 442" or "NJA 1990 s 442"; only the source's own
    list of documents settles it.
    """
    if source not in cache:
        cache[source] = {
            str(layout.artifact(source, bf)): bf
            for bf in build.SOURCES[source].list_basefiles()}
    return cache[source]


def _document_of(kind, rel, cache):
    """`(source, basefile)` behind a patch or layer path."""
    source, art = _artifact_of(kind, rel)
    if source is None:
        return (None, None)
    return (source, _basefiles_by_artifact(source, cache).get(str(art)))


WHOLE = "lagen all rebuild --ignore-code-changes"


def commands(changed, wiki_root=None):
    """The shell commands to run, one per line -- the narrowest set that covers
    every change, with nothing that another line already does.

    A wider command subsumes a narrower one over the same ground, so running
    both is pure waste: a corpus rebuild does what every other line would, and
    `lagen site rebuild` does what `lagen site generate sitenews` would. A push
    that adds a screencast *and* edits a news item is one command, not three.
    """
    found = targets(changed, wiki_root)
    if any(source is None for source, _ in found):
        return [WHOLE]
    # A document-scoped generate selects by catalog row, so it can only name a
    # document the catalog holds. `site`, `stats` and `remisser` are parsed and
    # deliberately never catalogued (layout.CATALOGUED_SOURCES), and asking for
    # one by name answers "no catalogued document matched 1 requested id(s)"
    # and renders nothing -- which is what a live push did on 2026-09-07. Their
    # whole-source generate is the narrowest form that exists; site is 33 pages,
    # so that costs little.
    for i, (source, basefile) in enumerate(found):
        if basefile is not None and source not in layout.CATALOGUED_SOURCES:
            found[i] = (source, None)
    # sources whose whole rebuild is already in the plan; their per-document
    # lines are redundant
    wide = {source for source, basefile in found if basefile is None}
    out = {"lagen %s rebuild" % source for source in wide}
    out |= {"lagen %s generate %s" % (source, shlex.quote(basefile))
            for source, basefile in found
            if basefile is not None and source not in wide}
    return sorted(out)


def main(argv):
    if not argv:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    for line in commands(argv):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
