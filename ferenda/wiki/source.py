"""The two wiki sources' registration: kommentar (SFS/EU commentary) and
begrepp (the concept glossary), both parsed from the markdown in the
git-backed content repo (`WIKI_ROOT`).

They share one recipe (`WIKI_CODE`) because one parser reads both. kommentar
publishes no page of its own -- a commentary is an annotation rendered into
its host act's rail -- so it registers no renderer and holds no search units;
begrepp does both.
"""

import functools
import sys
from pathlib import Path

from ..lib import annstore, catalog, compress, layout, llm, util
from ..lib import stage as protocol
from ..lib.stage import (
    CASENUMBER_CODE,
    CITATION_DATA,
    POLITENESS,
    Source,
    Stage,
    write_artifact,
)
from . import annotate, guidance_discover, parse, render

HERE = Path(__file__).parent

WIKI_ROOT = layout.WIKI_ROOT
WIKI_CODE = (HERE / "parse.py", HERE.parent / "lib" / "markdown.py",
             HERE.parent / "lib" / "lagrum.py",
             HERE.parent / "lib" / "emdref.py",
             *CITATION_DATA, *CASENUMBER_CODE,
             HERE.parent / "lib" / "eu_structure.py")


def kommentar_record(basefile):
    return Path(parse.kommentar_index(str(WIKI_ROOT))[basefile])


def kommentar_parse_run(basefile):
    art = parse.kommentar_artifact(str(kommentar_record(basefile)))
    write_artifact("kommentar", basefile, art)


def begrepp_record(basefile):
    return Path(parse.begrepp_index(str(WIKI_ROOT))[basefile])


def begrepp_parse_run(basefile):
    art = parse.begrepp_artifact(str(begrepp_record(basefile)))
    write_artifact("begrepp", basefile, art)


def kommentar_anchor_warnings(con, basefiles=()):
    """Section anchors in kommentar artifacts that resolve to no node in the act
    they annotate -- a mistyped `## Artikel N` / `## N kap M §` whose commentary
    and guidance would silently never surface in any rail (PRD Step 3). Returns
    `[(basefile, host_uri, [dangling anchors])]`; a host act absent from the
    corpus is skipped (its anchors can't be checked against a missing artifact).
    `basefiles` restricts the scan to those ids."""
    want = set(basefiles)
    out = []
    root = catalog.data_root(con)              # stored paths are data_root-relative
    for (path,) in con.execute(
            "SELECT path FROM documents WHERE source = 'kommentar' AND path <> ''"):
        komm = compress.read_json(root / path)
        if want and komm.get("basefile") not in want:
            continue
        row = con.execute("SELECT path FROM documents WHERE uri = ? AND path <> ''",
                          (komm.get("annotates"),)).fetchone()
        if not row:
            continue
        bad = parse.dangling_anchors(komm, compress.read_json(root / row[0]))
        if bad:
            out.append((komm.get("basefile"), komm.get("annotates"), bad))
    return out


def kommentar_relate_cross(con):
    """Kommentar's part of relate's cross-document block: the anchor audit over
    every commentary at once, worded as the lines relate prints. It runs there
    because relate is what writes the `links` rows -- the graph exists for the
    first time and the catalog is already open."""
    return ({}, [
        "WARNING kommentar %s annotates %s but has no matching node for %s "
        "-- check the heading numbering" % (bf, host, ", ".join(anchors))
        for bf, host, anchors in kommentar_anchor_warnings(con)])


def kommentar_validate(basefiles=()):
    """`lagen kommentar validate [basefiles…]` -- report commentary section anchors
    that don't resolve to a node in the annotated act (PRD Step 3 validation), so a
    mistyped heading is caught instead of silently dropping its rail content. Reads
    the catalog; run `lagen kommentar relate` first if it is stale."""
    assert layout.CATALOG.exists(), (
        "no catalog at %s -- run `lagen kommentar relate` first" % layout.CATALOG)
    con = catalog.connect(layout.CATALOG)
    warnings = kommentar_anchor_warnings(con, basefiles)
    con.close()
    for bf, host, anchors in warnings:
        print("kommentar %s -> %s: no matching node for %s"
              % (bf, host, ", ".join(anchors)))
    print("kommentar validate: %d file(s) with dangling anchors" % len(warnings))


def kommentar_ai_annotate(basefiles):
    """`lagen kommentar ai-annotate <basefile> ...` -- the Step-4 AI guidance
    linker: read the external guidance PDFs a commentary file declares in its
    `guidance:` frontmatter and LLM-derive, per article, which guidance section
    explains it. Writes a `.ann` layer into the curated store (lib.annstore; the
    AI-created layer, kept separate from the hand-edited markdown). One-shot per
    id: the LLM is never called from parse/relate/generate."""
    if not basefiles:
        sys.exit("usage: lagen kommentar ai-annotate <basefile> [<basefile> ...]")
    for basefile in basefiles:
        llm.start_record()   # one provenance window per layer (lib.annstore stamps meta.run)
        if protocol.RUN.dry_run:
            print("kommentar ai-annotate: would annotate %s -> %s"
                  % (basefile, annstore.path("kommentar", basefile)))
            continue
        out = annotate.annotate(basefile, WIKI_ROOT, force=protocol.RUN.force)
        print("kommentar ai-annotate %s: wrote %s" % (basefile, out))


def kommentar_discover_guidance(args):
    """`lagen kommentar discover-guidance [<limit>]` -- crawl the configured
    Commission guidance sites (their sitemaps) and (re)build the `CELEX ->
    guidance-page` index, so `propose-guidance <CELEX>` can auto-find an act's
    page(s) instead of a hand-known URL. The site rate-limits (429s a random slice
    of every run), so the index *merges across runs* and converges -- re-run to
    fill the gaps; `--force` starts a clean, authoritative index. `<limit>` caps
    pages (a quick check). No LLM."""
    limit = int(args[0]) if args else None
    if protocol.RUN.dry_run:
        print("kommentar discover-guidance: would crawl %d site(s) -> %s"
              % (len(guidance_discover.GUIDANCE_SITES),
                 guidance_discover.INDEX_PATH))
        return

    def progress(done, total, url):
        util.status(done, total, "discover-guidance  %s" % url)

    index, stats = guidance_discover.build_index(
        progress=progress, limit=limit, force=protocol.RUN.force, delay=POLITENESS)
    sys.stderr.write("\n")
    path = guidance_discover.write_index(index)
    missed = stats["total"] - stats["fetched"]
    print("kommentar discover-guidance: fetched %d/%d page(s) this run "
          "(%d rate-limited), index now %d act(s) -> %s"
          % (stats["fetched"], stats["total"], missed, len(index), path))
    if missed:
        print("  re-run `lagen kommentar discover-guidance` to fill the %d "
              "rate-limited page(s); the index merges across runs" % missed)


def kommentar_propose_guidance(args):
    """`lagen kommentar propose-guidance <dg-page-url | CELEX> [<CELEX>]` -- Track-B
    guidance proposer (no LLM): scrape a Commission guidance page for the guidance
    PDFs it links and print a draft `guidance:` frontmatter block to review and
    paste into the act's kommentar markdown, whence `ai-annotate` links it. Given a
    URL it scrapes that page (the optional CELEX cross-checks the page's EUR-Lex
    link); given a CELEX it looks the page(s) up in the `discover-guidance` index. A
    person still decides which candidates are genuine guidance on the act."""
    if not args:
        sys.exit("usage: lagen kommentar propose-guidance "
                 "<dg-page-url | CELEX> [<CELEX>]")
    arg = args[0]
    if arg.lower().startswith("http"):
        targets, expect = [arg], (args[1] if len(args) > 1 else None)
    else:
        targets, expect = guidance_discover.pages_for(arg), arg
        if not targets:
            sys.exit("no guidance page indexed for %s -- pass the page URL, or run "
                     "`lagen kommentar discover-guidance` first" % arg)
        print("# %s -> %d guidance page(s) from the index"
              % (arg, len(targets)), file=sys.stderr)
    for policy_url in targets:
        celexes, resolved, skipped = guidance_discover.propose(policy_url)
        print("# %s\n# act on this page (EUR-Lex): %s"
              % (policy_url, ", ".join(sorted(celexes)) or "none found"),
              file=sys.stderr)
        if expect and expect not in celexes:
            print("# WARNING: expected CELEX %s not among the page's EUR-Lex links"
                  % expect, file=sys.stderr)
        for title, url, _ in skipped:
            print("# no PDF resolved (check by hand): %s -- %s" % (title, url),
                  file=sys.stderr)
        print("# --- review before pasting: keep only genuine guidance ON the act, "
              "drop factsheets / impact assessments / general policy ---")
        print(guidance_discover.frontmatter_block(resolved))


_KOMMENTAR = Source(
    "kommentar",
    lambda: sorted(parse.kommentar_index(str(WIKI_ROOT))),
    {"parse": Stage("parse", kommentar_parse_run,
                    functools.partial(layout.artifact, "kommentar"),
                    inputs=lambda bf: [kommentar_record(bf)], code=WIKI_CODE)},
    artifacts=functools.partial(layout.artifacts, "kommentar"),
    # no `render`: a commentary is an annotation rendered into its host act's
    # rail, and /kommentar/<id> serves no page. Its rows stay in `documents`
    # (validate and relate read them) but hold no search units either -- a hit
    # on one would be a dead link with a useless title ("Kommentar").
    searchable=False,
    relate_cross=kommentar_relate_cross,
    # the ai-annotate guidance layers ride the host act's rail
    layers=lambda: sorted(annstore.tree("kommentar").rglob("*.ann")),
    actions={"validate": kommentar_validate, "ai-annotate": kommentar_ai_annotate,
             "discover-guidance": kommentar_discover_guidance,
             "propose-guidance": kommentar_propose_guidance},
    notes="validate: report commentary section anchors with no matching node in "
          "the annotated act (also warned during relate)\n"
          "ai-annotate <basefile>: LLM-link the declared guidance PDFs to the "
          "act's articles, written as a .ann sidecar\n"
          "discover-guidance: crawl the Commission guidance sites to (re)build the "
          "CELEX -> guidance-page index\n"
          "propose-guidance <dg-page-url | CELEX> [<CELEX>]: scrape a Commission "
          "guidance page (or look it up by CELEX) for a draft `guidance:` block "
          "(no LLM)")


_BEGREPP = Source(
    "begrepp",
    lambda: sorted(parse.begrepp_index(str(WIKI_ROOT))),
    {"parse": Stage("parse", begrepp_parse_run,
                    functools.partial(layout.artifact, "begrepp"),
                    inputs=lambda bf: [begrepp_record(bf)], code=WIKI_CODE)},
    render=render.render,
    artifacts=functools.partial(layout.artifacts, "begrepp"))


SOURCES: tuple[Source, ...] = (_KOMMENTAR, _BEGREPP)
