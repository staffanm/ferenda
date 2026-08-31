"""Which gällande act, or which EU regulation/directive, answers at
<slug>.{lagen,förordningen,direktivet}.nu (PRD-subdomains.md) -- generated
straight from the curated name tables the citation engine already reads, not
a separately maintained list.

The whole-act kind is generated (PRD-subdomains.md section 6); the standalone
kind is curated in the lagen-wiki content repo, but even there nothing is
separately *listed* -- a `site/subdomain/<zone>/<slug>.md` file existing is
itself the registration (`standalone_rows`). The chapter kind still needs an
explicit registry (a target act/fragment, not a page of its own) and is not
implemented yet.
"""

import json
import re
import unicodedata
from pathlib import Path

from .lib import compress, layout
from .lib.lagrum import load_namedlaws
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
        acts, standalone = whole_act_rows(), standalone_rows()
        collisions = acts.keys() & standalone.keys()
        if collisions:
            raise ValueError(
                f"{sorted(collisions)!r} would be both a generated whole-act "
                "row and a curated standalone page"
            )
        rows = {**acts, **standalone}
    generated_root = Path(generated_root)
    sub_root = generated_root / "_sub"
    sub_root.mkdir(parents=True, exist_ok=True)

    served = {}
    map_entries: dict[str, str] = {}
    for host, target in sorted(rows.items()):
        relpath = layout.page_relpath(target.lstrip("/"))
        source = compress.resolve(generated_root / relpath)
        if source is None:
            continue

        label, _, zone = host.partition(".")
        ascii_zone = _ZONE_TWINS.get(zone, zone)
        slug = f"{_ascii_fold(label)}.{ascii_zone}"
        link_dir = sub_root / slug
        link_dir.mkdir(exist_ok=True)
        suffix = source.name[len(Path(relpath).name):]  # "" or ".br"/".gz"
        link = link_dir / f"index.html{suffix}"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(source)
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
    # listed as a promise. Only the whole-act half exists yet; the chapter
    # and standalone kinds (curated in lagen-wiki) are a caller's job to
    # merge in once that half is implemented.
    (generated_root / "subdomains.json").write_text(
        json.dumps(served, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    map_lines = [f"{host} {slug};" for host, slug in sorted(map_entries.items())]
    (generated_root / "subdomains.map").write_text(
        "\n".join(map_lines) + "\n" if map_lines else "", encoding="utf-8"
    )
