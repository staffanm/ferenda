"""Faceted navigation over the catalog -- the single source of truth shared by
the REST API (`/facets`, `/documents?facet=`) and the static browse pages, so the
facet logic lives in one place and every consumer sees the same API-shaped
buckets.

A flat per-source listing ("every EU act", "every law") is too large to be
useful, so each source is sliced by one or two ordered *facet levels* -- a law's
subject initial, a case's court + year, an EU act's type + year. A document's
*path* is the tuple of its level keys; the leaf bucket (the full path) is what a
single browse page lists ("Rättsfall från Högsta domstolen 2024", "Författningar
som börjar på A").

`tree(con, source)` returns the navigator (ordered buckets + counts + the default
landing bucket); `group(con, source)` returns the documents of every leaf bucket
in one pass (what the static generate consumes). The two share one catalog scan.
"""

import re
from collections import namedtuple
from datetime import date

from . import catalog, layout

# a catalog row reduced to what facet-key extraction needs (its host-stripped
# local id is precomputed once, since most extractors slice it)
Row = namedtuple("Row", "uri local kind label title display")


# --------------------------------------------------------------------------
# ordering helpers -- each returns a sorted copy of a list of bucket keys
# --------------------------------------------------------------------------

# Swedish alphabet: Å Ä Ö sort after Z (not as A/O), the '#' non-letter bucket last
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZÅÄÖ"


def _by_letter(keys):
    return sorted(keys, key=lambda k: (_ALPHABET.find(k), k) if k in _ALPHABET
                  else (len(_ALPHABET), k))


def _by_year_desc(keys):
    """Newest year first; the 'okänt' (year-less) bucket always last."""
    return sorted(keys, key=lambda k: (0, -int(k)) if k.isdigit() else (1, 0))


def _by_alpha(keys):
    return sorted(keys)


def _curated(order):
    """Order by a fixed sequence; anything outside it trails, alphabetically."""
    rank = {k: i for i, k in enumerate(order)}
    return lambda keys: sorted(keys, key=lambda k: (rank.get(k, len(order)), k))


# --------------------------------------------------------------------------
# per-source key extraction
# --------------------------------------------------------------------------

# strip an SFS title down to its subject so it files under the subject initial
# (lagen.nu's "börjar på A"), not under the document-type word that opens almost
# every title ("Förordning …", "Lag …"): 'Lag (2008:1302) om avtal …' -> 'avtal …'
_SFS_EDITORIAL = re.compile(r"/[^/]*/")          # /Rubriken upphör att gälla …/
_SFS_DESIGNATION = re.compile(
    r"^(lag(en)?|förordning(en)?|kungörelse(n)?|tillkännagivande(t)?|cirkulär(et)?|"
    r"brev(et)?|reglemente(t)?|instruktion(en)?|stadga(n)?|kungl\.? ?maj:ts)\b", re.I)
_SFS_CONNECTOR = re.compile(r"^(om|med|angående|för|till|av)\s+", re.I)


def _sfs_split(title):
    """Split a (whitespace-normalised, editorial-stripped) SFS title into the
    leading designation/number/connector that is *dropped* for sorting and the
    subject it sorts under: 'Lag (2008:1302) om avtal …' -> ('Lag (2008:1302) om ',
    'avtal …'). The prefix is shown subdued, the subject emphasised, so a reader
    sees where the sort key begins."""
    full = re.sub(r"\s+", " ", _SFS_EDITORIAL.sub("", title)).strip()
    rest = _SFS_DESIGNATION.sub("", full).strip()
    rest = re.sub(r"^\(\d{4}:\d+\)\s*", "", rest)    # the SFS number that follows it
    rest = _SFS_CONNECTOR.sub("", rest).strip()
    prefix = full[:len(full) - len(rest)] if rest and full.endswith(rest) else ""
    return prefix, rest


def _sfs_sortname(title):
    return _sfs_split(title)[1]


# parliamentary primary law -- a lag, a balk (Brottsbalk, Jordabalk, …), or one of
# the grundlagar -- shown at full weight; secondary instruments (förordning,
# kungörelse, tillkännagivande, …) are subdued in the listing. The grundlagar open
# with their own designation, not "Lag"/"Balk", so they're pinned by SFS id.
_GRUNDLAGAR = {"1974:152", "1949:105", "1991:1469", "1810:0926", "2014:801"}
_SFS_STATUTE_END = ("lag", "lagen", "balk", "balken")


def _sfs_is_statute(title, local):
    """Whether an SFS is parliamentary primary law -- a lag, a balk, or one of the
    grundlagar -- as opposed to a förordning/kungörelse/etc. The designation is the
    phrase before the SFS number; a lag/balk ends in just that, however compound
    ('Lag', 'Förvaltningslag', 'Radio- och tv-lag', 'Plan- och bygglag',
    'Brottsbalk'). The grundlagar open with their own designation, so they're
    pinned by SFS id. Drives the listing's visual hierarchy."""
    head = re.sub(r"\s+", " ", _SFS_EDITORIAL.sub("", title)).strip()
    designation = head.split("(", 1)[0].strip().lower()
    return local in _GRUNDLAGAR or designation.endswith(_SFS_STATUTE_END)


def _initial(s):
    c = s[:1].upper()
    return c if c in _ALPHABET else "#"


def _sfs_initial(r):
    return _initial(_sfs_sortname(r.title or ""))


def _begrepp_initial(r):
    return _initial(r.title or r.label or "")


def _fs_series(r):
    return r.local.split("/")[0].upper()         # 'fffs/2013:10' -> 'FFFS'


def _fs_year(r):
    m = re.search(r"(\d{4})", r.local.split("/", 1)[-1])
    return m.group(1) if m else "okänt"


def _avg_org(r):
    return r.kind                                # 'jo' | 'jk' (the organ)


def _avg_year(r):
    """Decision year from the diarienummer: JO '2340-2025' carries it last;
    JK's new form '2024/8082' first; JK's old form '3497-06-40' as a two-digit
    year (century cutoff >50 -> 19xx, the legacy JKStore rule)."""
    dnr = r.local.split("/", 2)[-1]              # 'avg/jo/2340-2025' -> dnr
    m = re.search(r"-(\d{4})$", dnr)
    if m:
        return m.group(1)
    m = re.match(r"(\d{4})/", dnr)
    if m:
        return m.group(1)
    m = re.match(r"\d+-(\d{2})-", dnr)
    if m:
        yy = int(m.group(1))
        return str((1900 if yy > 50 else 2000) + yy)
    return "okänt"


# the case sources (publication series / court), in browse order. The published
# referat carry a lowercase series segment ('dom/nja/…'); the *raw* avgöranden --
# the court's own version, harvested months before its editor referat and folded
# in once that arrives -- carry an uppercase court-code prefix ('dom/HDO_…'). The
# prefix names the court, so a raw avgörande is filed beside its eventual referat
# (HDO -> nja, MMOD -> mod, the kammarrätt codes -> rk, …). PBR and RHN have no
# referat series of their own, so they get their own bucket.
DV_COURTS = {
    "nja":  "NJA – Högsta domstolen",
    "hfd":  "HFD – Högsta förvaltningsdomstolen",
    "ra":   "RÅ – Regeringsrätten",
    "rh":   "RH – Hovrätterna",
    "ad":   "AD – Arbetsdomstolen",
    "mod":  "MÖD – Mark- och miljööverdomstolen",
    "mig":  "MIG – Migrationsöverdomstolen",
    "md":   "MD – Marknadsdomstolen",
    "pmod": "PMÖD – Patent- och marknadsöverdomstolen",
    "rk":   "RK – Kammarrätterna",
    "pbr":  "PBR – Patentbesvärsrätten",
    "rhn":  "Rättshjälpsnämnden",
    "övriga": "Övriga",
}

# raw-avgörande court-code prefix ('dom/HDO_10868_25' -> 'HDO') -> the canonical
# court bucket it belongs in (its referat series, or its own bucket)
RAW_COURT = {
    "HDO": "nja", "MMOD": "mod", "PMOD": "pmod", "MDO": "md",
    "HVS": "rh", "HYOD": "rh",                       # hovrätt avgöranden
    "KST": "rk", "KGG": "rk", "KJO": "rk", "KSU": "rk",   # kammarrätterna
    "PBR": "pbr", "RHN": "rhn",
}

# raw courts whose id is 'CODE_<YEAR>_<num>' (the year leads) rather than the usual
# 'CODE[_TYPE]_<num>_<YEAR>' (the year trails after the målnummer)
RAW_YEAR_FIRST = {"MDO", "PBR"}


def _dv_court(r):
    parts = r.local.split("/")
    seg = parts[1] if len(parts) > 1 else ""
    if seg.lower() in DV_COURTS:                     # 'dom/nja/…' -- a referat
        return seg.lower()
    return RAW_COURT.get(seg.split("_")[0], "övriga")    # 'dom/HDO_…' -- raw


def _two_digit_year(yy):
    """A 2-digit case year, pivoted at 40 so '99' -> 1999, '25' -> 2025."""
    n = int(yy)
    return str((2000 if n <= 40 else 1900) + n)


def _dv_year(r):
    """The decision year. A referat carries a clean 4-digit year in its label
    ('NJA 2011 s. 357'), or -- HFD target-number labels -- a 2-digit målnummer
    suffix ('HFD 1017-25'). A raw avgörande's label is målnummer-laden (a case
    number can look like a year: 'HDO B 2043-24'), so its year is read from the
    unambiguous uri segment instead -- the trailing one, or the leading one for
    the year-first courts."""
    parts = r.local.split("/")
    seg = parts[1] if len(parts) > 1 else ""
    if seg.lower() in DV_COURTS:                     # referat
        label = r.label or ""
        # a real year is followed by a space/':'/'ref' -- never '-'; that excludes
        # a HFD målnummer that happens to look like a year ('HFD 1673-25' -> 25)
        m = re.search(r"\b(1[6-9]\d\d|20\d\d)\b(?!-)", label)
        if m:
            return m.group(1)
        m = re.search(r"-(\d\d)\b", label)
        return _two_digit_year(m.group(1)) if m else "okänt"
    code, *rest = seg.split("_")                     # raw: 'HDO','10868','25'
    cand = (rest[0] if code in RAW_YEAR_FIRST else rest[-1]) if rest else ""
    if re.fullmatch(r"\d{4}", cand):
        return cand
    if re.fullmatch(r"\d\d", cand):
        return _two_digit_year(cand)
    return "okänt"


def _fa_type(r):
    return r.local.split("/")[0]                  # 'prop/2020/21:22' -> 'prop'


def _fa_year(r):
    m = re.search(r"(1[6-9]\d\d|20\d\d)", r.local)
    return m.group(1) if m else "okänt"


def _eu_celex(r):
    # 'ext/celex/32016R0679' -> '32016R0679'; a treaty carries a '/TXT' document
    # suffix ('ext/celex/11992M/TXT'), so take the segment after 'celex/', not the last
    return r.local[len("ext/celex/"):].split("/")[0]


def _eu_kind(r):
    # the catalog's stored doctype is authoritative -- re-deriving it from the
    # CELEX here diverged from what the rest of the app shows (lost the treaties)
    return r.kind


def _eu_year(r):
    m = re.match(r"\d(\d{4})", _eu_celex(r))      # sector digit, then 4-digit year
    return m.group(1) if m else "okänt"


# a CELEX corrigendum (…R(NN)) corrects an act rather than being one; it is left
# out of the browse, exactly as the old flat listing did (still reachable via
# search and the citations that point at it)
_EU_CORRIGENDUM = re.compile(r"R\(\d+\)$")


# --------------------------------------------------------------------------
# the per-source facet schemes
# --------------------------------------------------------------------------

class Level:
    """One facet axis: how to derive a document's bucket key, how to display and
    slug that key, and how to order the keys."""

    def __init__(self, name, key, order, label=None, slug=None):
        self.name = name                 # navigator heading ("Domstol", "År")
        self.key = key                   # Row -> bucket key
        self.order = order               # [key] -> [key] sorted
        self._label = label or (lambda k: k)
        self._slug = slug or _slug

    def label(self, key):
        return self._label(key)

    def slug(self, key):
        return self._slug(key)


def _slug(key):
    """A URL path segment for a bucket key: lower-cased, non-alphanumerics to '-'.
    Swedish letters survive (å/ä/ö) so the alphabet buckets stay distinct."""
    return re.sub(r"[^0-9a-zåäö]+", "-", key.lower()).strip("-") or "-"


def _map_label(mapping):
    return lambda k: mapping.get(k, k)


SCHEMES = {
    "sfs": [Level("Bokstav", _sfs_initial, _by_letter)],
    "begrepp": [Level("Bokstav", _begrepp_initial, _by_letter)],
    "foreskrift": [
        Level("Serie", _fs_series, _by_alpha),
        Level("År", _fs_year, _by_year_desc),
    ],
    "avg": [
        Level("Organ", _avg_org, _curated(["jo", "jk"]),
              label=_map_label({"jo": "Justitieombudsmannen (JO)",
                                "jk": "Justitiekanslern (JK)"})),
        Level("År", _avg_year, _by_year_desc),
    ],
    "dv": [
        Level("Domstol", _dv_court, _curated(list(DV_COURTS)),
              label=_map_label(DV_COURTS)),
        Level("År", _dv_year, _by_year_desc),
    ],
    "forarbete": [
        Level("Typ", _fa_type,
              _curated(["prop", "sou", "ds", "dir", "skr", "lr", "fm", "so"]),
              label=_map_label({"prop": "Propositioner", "sou": "SOU", "ds": "Ds",
                                "dir": "Kommittédirektiv", "skr": "Skrivelser",
                                "lr": "Lagrådsremisser", "fm": "Förordningsmotiv",
                                "so": "Internationella överenskommelser"})),
        Level("År", _fa_year, _by_year_desc),
    ],
    "eurlex": [
        Level("Typ", _eu_kind,
              _curated(["regulation", "directive", "decision", "judgment",
                        "treaty", "act"]),
              label=_map_label({"regulation": "Förordningar", "directive": "Direktiv",
                                "decision": "Beslut", "judgment": "Avgöranden",
                                "treaty": "Fördrag", "act": "Övriga rättsakter"})),
        Level("År", _eu_year, _by_year_desc),
    ],
}


def sources():
    return list(SCHEMES)


def is_browsable(source, local):
    """Whether a document belongs in the browse at all -- an EU corrigendum
    corrects an act rather than being one, so it is omitted (still reachable via
    search and the citations that point at it)."""
    return not (source == "eurlex" and _EU_CORRIGENDUM.search(local))


def browse_label(source, row):
    """The handle shown for a document in a listing -- the same reader-facing
    heading the page and search hits use (catalog.display_title, stamped onto the
    `display` column at relate): an act's short name + acronym where it has them,
    else its title; a law/concept by name; everything else by its identifier.
    Falls back to label/local for a row predating the column (display still
    NULL until its source is re-related)."""
    return row.display or row.label or row.local


def browse_doc(source, row):
    """A leaf-bucket document entry for the browse model. Every source carries
    `uri`/`url`/`display`; a statute additionally carries the split title
    (`pre` subdued + `key` emphasised), whether it is primary law (`subdued`
    when not), and its `year` -- what the listing renders and filters on."""
    doc = {"uri": row.uri, "url": layout.page_url(row.uri),
           "display": browse_label(source, row)}
    if source == "sfs":
        pre, key = _sfs_split(row.title or "")
        doc.update(pre=pre, key=key or doc["display"],
                   subdued=not _sfs_is_statute(row.title or "", row.local),
                   year=row.local.split(":", 1)[0])
    return doc


def _rows(con, source):
    """The catalog rows of `source` that belong in the browse, as `Row`s. A
    repealed statute whose repeal date has passed is omitted (still reachable by
    direct link and search) -- the listing shows only law in force."""
    expired = catalog.expired_uris(con, date.today().isoformat())
    for uri, _src, kind, label, title, _url, _path, display in \
            catalog.documents(con, source):
        local = catalog.local(uri)
        if uri not in expired and is_browsable(source, local):
            yield Row(uri, local, kind, label, title, display)


def _path(levels, row):
    return tuple(lv.key(row) for lv in levels)


# --------------------------------------------------------------------------
# the two public scans
# --------------------------------------------------------------------------

def group(con, source):
    """Every leaf bucket's documents in one catalog pass: `{path_tuple: [Row, …]}`,
    each list ordered for display (by label/title). `path_tuple` has one element
    per facet level. This is what the static generate iterates."""
    levels = SCHEMES[source]
    buckets = {}
    for row in _rows(con, source):
        buckets.setdefault(_path(levels, row), []).append(row)
    for rows in buckets.values():
        rows.sort(key=_doc_sort)
    return buckets


def _natural(s):
    """A natural-order sort key so '2024:2' precedes '2024:10' (numeric runs
    compare as numbers, not lexically)."""
    return [(1, int(t)) if t.isdigit() else (0, t.lower())
            for t in re.split(r"(\d+)", s) if t]


def _doc_sort(row):
    """Within a leaf bucket: laws and concepts read alphabetically by subject;
    everything else by its identifier, naturally ordered (the bucket is already
    one year/court, so the number is what distinguishes the entries)."""
    primary = _sfs_sortname(row.title or "").lower() or row.label or row.local
    return (_natural(primary), _natural(row.local))


def tree(con, source, buckets=None):
    """The navigator for `source`: ordered buckets with counts (nested for a
    two-level scheme) and the default landing bucket. API-shaped -- the `/facets`
    response and the browse navigator are both built from this. Pass an existing
    `group()` result as `buckets` to share the single catalog scan.

        {source, levels:[name,…], default:[key,…],
         buckets:[{key,label,slug,count, children:[…]|None}, …]}
    """
    levels = SCHEMES[source]
    if buckets is None:
        buckets = group(con, source)
    counts = {path: len(rows) for path, rows in buckets.items()}

    nodes = _level_nodes(levels, counts, prefix=())
    default = []
    cur = nodes
    while cur:
        default.append(cur[0]["key"])
        cur = cur[0]["children"]
    return {"source": source, "levels": [lv.name for lv in levels],
            "default": default, "buckets": nodes}


def _level_nodes(levels, counts, prefix):
    """Recursively build the ordered bucket nodes at depth `len(prefix)`."""
    depth = len(prefix)
    level = levels[depth]
    here = {}
    for path, n in counts.items():
        if path[:depth] == prefix:
            here[path[depth]] = here.get(path[depth], 0) + n
    nodes = []
    for key in level.order(list(here)):
        child_prefix = prefix + (key,)
        children = (_level_nodes(levels, counts, child_prefix)
                    if depth + 1 < len(levels) else None)
        nodes.append({"key": key, "label": level.label(key), "slug": level.slug(key),
                      "count": here[key], "children": children})
    return nodes


def browse_view(con, source):
    """The full browse model for a source: the navigator (`tree`) with each leaf
    bucket's ordered, display-labelled documents attached. One catalog scan; this
    is the single payload the static-site generator consumes per source (it has
    no other access to the data store)."""
    grouped = group(con, source)
    view = tree(con, source, grouped)

    def attach(nodes, prefix):
        for n in nodes:
            keypath = prefix + (n["key"],)
            if n["children"] is not None:
                attach(n["children"], keypath)
            else:
                n["documents"] = [browse_doc(source, r)
                                  for r in grouped.get(keypath, [])]

    attach(view["buckets"], ())
    return view
