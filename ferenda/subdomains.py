"""Which gällande act, or which EU regulation/directive, answers at
<slug>.{lagen,förordningen,direktivet}.nu (PRD-subdomains.md) -- generated
straight from the curated name tables the citation engine already reads, not
a separately maintained list.

The whole-act kind is generated (PRD-subdomains.md section 6); the chapter
kind is too, from the same `namedlaws.json` -- a name is a whole-act name or
a span name exactly as its entry says (a bare `label`/`abbr`, or a `spans`
entry naming *part* of the act, `lib.lagrum.load_named_spans`), cut to a
hostname by the same `_cut` rule either way. The standalone kind is curated
in the lagen-wiki content repo instead, but even there nothing is separately
*listed* -- a `site/subdomain/<zone>/<slug>.md` file existing is itself the
registration (`standalone_rows`); unlike a span, a standalone page (a curated
easter egg, not a name for part of a real act) has no act to derive a name
from.
"""

import json
import os
import re
import unicodedata
from pathlib import Path

from .lib import catalog, compress, layout
from .lib.lagrum import load_named_spans, load_namedlaws
from .lib.page import Site
from .lib.render import edit_meta
from .sfs import render as sfs_render
from .site import parse as site_parse

SFS_NAMEDLAWS = Path(__file__).parent / "sfs" / "data" / "namedlaws.json"
EU_NAMEDACTS = Path(__file__).parent / "eurlex" / "data" / "namedacts.json"

# A hostname label: lowercase ASCII plus the Swedish letters this codebase
# treats as citable running text. A name that survives suffix-cutting into
# anything else -- a space (two-word names), an apostrophe -- is not a single
# hostname label, so it is skipped rather than mangled; the IDNA encoding a
# genuine non-ASCII slug needs (PRD-subdomains.md section 4) is a later step.
_SLUG_OK = re.compile(r"^[a-zåäö0-9-]+$")


def _cut(name, suffix):
    """`name` with a trailing `suffix` removed and any leftover separator
    trimmed, lower-cased -- "Dataskyddsförordningen" + "förordningen" ->
    "dataskydds". `None` if `name` doesn't carry the suffix, or what's left
    isn't a plain hostname label."""
    name = name.lower()
    if not name.endswith(suffix):
        return None
    slug = name[: -len(suffix)].rstrip("-")
    return slug if slug and _SLUG_OK.match(slug) else None


def _add(rows, host, target, source):
    """Record one row, raising if two different sources would produce the
    same hostname (rule:fail-fast) -- a build-time error a person resolves in
    the source table, not a silent pick between the two."""
    if host in rows and rows[host] != (target, source):
        raise ValueError(
            f"{host!r} would point at both {rows[host][0]!r} (from "
            f"{rows[host][1]!r}) and {target!r} (from {source!r})"
        )
    rows[host] = (target, source)


def whole_act_rows():
    """slug.zone -> target URI, for every currently-gällande SFS act or EU
    regulation/directive whose curated name matches its zone's suffix
    (PRD-subdomains.md section 1). `load_namedlaws` already resolves a name
    to whichever act carries it *today* -- a name an act no longer holds
    (`sjölagen` meant 1891:35_s.1 until 1994-10-01, then a later act) is
    excluded for free, with no separate repeal check needed."""
    rows: dict[str, tuple[str, str]] = {}

    for name, sfsid in load_namedlaws(SFS_NAMEDLAWS).items():
        if slug := _cut(name, "lagen"):
            _add(rows, f"{slug}.lagen.nu", f"/{sfsid}", name)
        elif slug := _cut(name, "förordningen"):
            _add(rows, f"{slug}.förordningen.nu", f"/{sfsid}", name)

    namedacts = json.loads(EU_NAMEDACTS.read_text(encoding="utf-8"))
    for celex, entry in namedacts.items():
        if not isinstance(entry, dict) or celex.startswith("1"):
            continue  # the "_comment" string, and sector-1 treaties (out of scope here)

        labels = entry.get("label") or []
        for label in [labels] if isinstance(labels, str) else labels:
            if slug := _cut(label, "förordningen"):
                _add(rows, f"{slug}.förordningen.nu", f"/celex/{celex}", label)

        if celex[5:6] != "L":
            continue  # sector-3 CELEX type letter: L = directive, R = regulation, …

        abbrs = entry.get("abbr") or []
        for abbr in [abbrs] if isinstance(abbrs, str) else abbrs:
            slug = abbr.lower()
            if _SLUG_OK.match(slug):
                _add(rows, f"{slug}.direktivet.nu", f"/celex/{celex}", abbr)

    return {host: target for host, (target, _source) in rows.items()}


def standalone_rows(wiki_root=None):
    """slug.zone -> target URI, one row per `site/subdomain/<zone>/<slug>.md`
    file in the wiki content repo (PRD-subdomains.md section 8). The file
    existing *is* the registration -- nothing else lists it, so there is
    nothing to drift out of sync with it."""
    wiki_root = layout.WIKI_ROOT if wiki_root is None else wiki_root
    rows = {}
    for basefile in site_parse.list_basefiles(str(wiki_root)):
        if basefile.startswith("subdomain/"):
            zone, slug = basefile[len("subdomain/"):].rsplit("/", 1)
            rows[f"{slug}.{zone}"] = f"/{basefile}"
    return rows


def _named_spans():
    """host -> (name, NamedSpan), for every `namedlaws.json` span whose name
    cuts to a hostname the same way a whole act's name does ("hyreslagen" ->
    "hyres", exactly as "avtalslagen" -> "avtals", `whole_act_rows`). Reuses
    `_cut`/`_add`: a span is not a separately registered subdomain, it is
    what a `namedlaws.json` entry's `spans` key already says."""
    rows: dict[str, tuple] = {}
    for name, span in load_named_spans(SFS_NAMEDLAWS).items():
        if slug := _cut(name, "lagen"):
            _add(rows, f"{slug}.lagen.nu", (name, span), name)
        elif slug := _cut(name, "förordningen"):
            _add(rows, f"{slug}.förordningen.nu", (name, span), name)
    return {host: pair for host, (pair, _source) in rows.items()}


def named_span_rows():
    """slug.zone -> "/lawid#first-last" (PRD-subdomains.md section 6) -- the
    routing half of `_named_spans()`, the shape `write_sub_tree` needs
    alongside `whole_act_rows`/`standalone_rows`. `first`/`last` are dash-
    joined here purely as this module's own internal wire format between
    this and `write_chapter_pages`/`write_sub_tree` (which dispatch on
    whether a target carries a `#fragment` at all) -- `namedlaws.json` itself
    keeps them as separate fields; no node id ever contains a dash."""
    return {host: f"/{span.lawid}#{span.first}-{span.last}"
            for host, (_name, span) in _named_spans().items()}


def write_chapter_pages(generated_root, con):
    """Render each `_named_spans()` target to its own file,
    `generated/subdomain/<zone>/<slug>.html` -- the one subdomain kind
    `write_sub_tree` cannot just symlink to an existing page, since the
    target is part of a document, not a document of its own.

    Needs a live catalog connection, unlike every other function in this
    module: `sfs.render.render_chapter`'s rail (kommentar, citations) reads
    it via a `lib.page.Site`. `namedlaws.json` only ever names an SFS act
    today, so this only knows how to render that one source's spans --
    widening it is real work for whenever a span of some other source's act
    is actually asked for, not before.

    `render_chapter` itself stays ignorant of editing (it is also called by
    tests against a bare artifact); the `<meta name="lagen-doc">` that turns
    on editor.js's kommentar ✎ buttons is grafted on here, the same
    `edit_meta` call `render_document` makes for every full-document page --
    the node ids kommentar keys on (an act's kaprubrik/paragraf anchors) do
    not change when only part of the act is shown."""
    generated_root = Path(generated_root)
    site = Site.from_catalog(con)
    for host, (name, span) in _named_spans().items():
        art_path = layout.artifact("sfs", span.lawid)
        if not compress.exists(art_path):
            continue
        art = compress.read_json(art_path)
        html = sfs_render.render_chapter(art, site, span.first, span.last,
                                         name, span.reason)
        ref = catalog.local(art["uri"])
        meta = edit_meta("kommentar", ref, art["uri"], source="sfs", basefile=ref)
        html = html.replace("</head>", meta + "</head>", 1)
        label, _, zone = host.partition(".")
        dest = generated_root / "subdomain" / zone / (label + ".html")
        dest.parent.mkdir(parents=True, exist_ok=True)
        compress.write_text(dest, html, compress.PAGE_ENCODINGS)


def _ascii_fold(label):
    """`label` with its combining marks dropped after NFKD normalization --
    "upphovsrätts" -> "upphovsratts" (PRD-subdomains.md section 4, O5: the
    diacritic-free twin, computed here rather than listed)."""
    decomposed = unicodedata.normalize("NFKD", label)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# förordningen.nu and forordningen.nu are two registered domains for the same
# zone (PRD-subdomains.md section 4) -- every row in one belongs in both.
# lagen.nu and direktivet.nu have no such registered twin.
_ZONE_TWINS = {"förordningen.nu": "forordningen.nu"}


def write_sub_tree(generated_root, rows=None):
    """Write `generated/_sub/<slug>/index.html[.br|.gz]` as a symlink to each
    whole act's own generated file, and `generated/subdomains.map` for nginx
    (PRD-subdomains.md section 5) -- two on-disk projections of one
    enumeration, not two sources of truth. The symlink keeps whatever
    compression suffix the source already has (`compress.resolve`) rather
    than decompressing: nginx serves the `.br` sibling directly, stamping
    its own `Content-Encoding` (`docker/nginx/subdomains.conf`).

    The symlink target is relative (`os.path.relpath`), not the absolute path
    `compress.resolve` returns: this writer runs inside the `ferenda`
    container, which mounts the data root at `/app/site/data`, while nginx
    mounts the same host directory at `/usr/share/nginx/generated` -- an
    absolute target resolves for whichever container wrote it and is a
    dangling link for every other one reading it over the same bind mount.

    The on-disk directory name is the ascii-folded *host* (label and zone),
    not just the label -- two different zones sharing a label
    ("dataskyddslagen" and "Dataskyddsförordningen" both fold to
    "dataskydds") must not collide on one directory, silently pointing one
    zone's subdomain at the other's act. The map's *keys* fan out over every
    ASCII-reachable spelling of the host: the reader's U-label idna-encoded,
    its ascii-fold twin, and -- for förordningen.nu -- the same two again
    under its registered ascii-twin zone forordningen.nu. All of them are one
    A-label a browser can actually put in `Host`/SNI; nginx never sees the
    U-label a reader types (section 4).

    An act with no generated page at all -- not yet built, or the row is
    stale -- is skipped, not an error: this runs after the whole-act rows are
    already fixed, and a missing page is a normal, transient build state, not
    a defect in the row itself."""
    if rows is None:
        parts = {"whole-act": whole_act_rows(), "standalone": standalone_rows(),
                 "chapter": named_span_rows()}
        rows = {}
        for kind, part in parts.items():
            collisions = rows.keys() & part.keys()
            if collisions:
                raise ValueError(
                    f"{sorted(collisions)!r} would be both an earlier kind "
                    f"and a {kind} row"
                )
            rows.update(part)
    generated_root = Path(generated_root)
    sub_root = generated_root / "_sub"
    sub_root.mkdir(parents=True, exist_ok=True)

    served = {}
    map_entries: dict[str, str] = {}
    for host, target in sorted(rows.items()):
        label, _, zone = host.partition(".")
        if "#" in target:
            # chapter kind: `write_chapter_pages` already rendered its own
            # file at this path, never the target act's whole page.
            # page_relpath strips a fragment, so resolving `target` directly
            # here would silently symlink to the WHOLE act instead of just
            # its span -- wrong content behind a working-looking link, not a
            # missing page.
            relpath = layout.page_relpath(f"subdomain/{zone}/{label}")
        else:
            relpath = layout.page_relpath(target.lstrip("/"))
        source = compress.resolve(generated_root / relpath)
        if source is None:
            continue

        ascii_zone = _ZONE_TWINS.get(zone, zone)
        slug = f"{_ascii_fold(label)}.{ascii_zone}"
        link_dir = sub_root / slug
        link_dir.mkdir(exist_ok=True)
        suffix = source.name[len(Path(relpath).name):]  # "" or ".br"/".gz"
        link = link_dir / f"index.html{suffix}"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(os.path.relpath(source, link.parent))
        served[host] = target

        zones = {zone, _ZONE_TWINS.get(zone, zone)}
        labels = {label, _ascii_fold(label)}
        for z in zones:
            for lbl in labels:
                a_label_host = f"{lbl}.{z}".encode("idna").decode("ascii")
                if a_label_host in map_entries and map_entries[a_label_host] != slug:
                    raise ValueError(
                        f"{a_label_host!r} would point at both "
                        f"{map_entries[a_label_host]!r} and {slug!r}"
                    )
                map_entries[a_label_host] = slug

    # The inspectable artifact (section 6): what is actually live, not merely
    # what the source tables say should be -- a row whose generated page
    # doesn't exist yet is absent from both this and the nginx map, not
    # listed as a promise.
    (generated_root / "subdomains.json").write_text(
        json.dumps(served, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    map_lines = [f"{host} {slug};" for host, slug in sorted(map_entries.items())]
    (generated_root / "subdomains.map").write_text(
        "\n".join(map_lines) + "\n" if map_lines else "", encoding="utf-8"
    )
