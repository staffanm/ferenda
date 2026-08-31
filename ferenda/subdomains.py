"""Which gällande act, or which EU regulation/directive, answers at
<slug>.{lagen,förordningen,direktivet}.nu (PRD-subdomains.md) -- generated
straight from the curated name tables the citation engine already reads, not
a separately maintained list.

Only the *whole-act* kind is generated (PRD-subdomains.md section 6); the
chapter and standalone kinds are curated in the lagen-wiki content repo and
are not this module's concern.
"""

import json
import re
import unicodedata
from pathlib import Path

from .lib import compress, layout
from .lib.lagrum import load_namedlaws

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


def _ascii_fold(label):
    """`label` with its combining marks dropped after NFKD normalization --
    "upphovsrätts" -> "upphovsratts" (PRD-subdomains.md section 4, O5: the
    diacritic-free twin, computed here rather than listed)."""
    decomposed = unicodedata.normalize("NFKD", label)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def write_sub_tree(generated_root, rows=None):
    """Write `generated/_sub/<slug>/index.html[.br|.gz]` as a symlink to each
    whole act's own generated file, and `generated/subdomains.map` for nginx
    (PRD-subdomains.md section 5) -- two on-disk projections of one
    enumeration, not two sources of truth. The symlink keeps whatever
    compression suffix the source already has (`compress.resolve`) rather
    than decompressing: nginx serves the `.br` sibling directly, stamping
    its own `Content-Encoding` (`docker/nginx/subdomains.conf`).

    The on-disk directory name, and the ASCII-fold twin's own map entry, use
    the folded slug (`upphovsratts`); the map's *keys* are the A-label a
    browser actually sends in `Host`/SNI for a non-ASCII slug -- nginx never
    sees the U-label a reader types (section 4).

    An act with no generated page at all -- not yet built, or the row is
    stale -- is skipped, not an error: this runs after the whole-act rows are
    already fixed, and a missing page is a normal, transient build state, not
    a defect in the row itself."""
    rows = whole_act_rows() if rows is None else rows
    generated_root = Path(generated_root)
    sub_root = generated_root / "_sub"
    sub_root.mkdir(parents=True, exist_ok=True)

    served = {}
    map_lines = []
    for host, target in sorted(rows.items()):
        relpath = layout.page_relpath(target.lstrip("/"))
        source = compress.resolve(generated_root / relpath)
        if source is None:
            continue

        label, _, zone = host.partition(".")
        slug = _ascii_fold(label)
        link_dir = sub_root / slug
        link_dir.mkdir(exist_ok=True)
        suffix = source.name[len(Path(relpath).name):]  # "" or ".br"/".gz"
        link = link_dir / f"index.html{suffix}"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(source)

        served[host] = target
        a_label = label.encode("idna").decode("ascii")
        map_lines.append(f"{a_label}.{zone} {slug};")
        if slug != a_label:
            map_lines.append(f"{slug}.{zone} {slug};")

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
    (generated_root / "subdomains.map").write_text(
        "\n".join(map_lines) + "\n" if map_lines else "", encoding="utf-8"
    )
