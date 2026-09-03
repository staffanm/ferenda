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

from . import catalog, datasets, eu_structure, labels, lagrum, layout

# a catalog row reduced to what facet-key extraction needs (its host-stripped
# local id is precomputed once, since most extractors slice it)
Row = namedtuple(
    "Row", "uri local kind label title display date short_id short_title description",
    defaults=[None, None, None, None])


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
# the editorial interpolation stripper lives with `labels.sfs_is_statute`, which
# reads the same designation off the same title (rule:second-use-goes-to-lib)
_SFS_EDITORIAL = labels.SFS_EDITORIAL
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


def _initial(s):
    c = s[:1].upper()
    return c if c in _ALPHABET else "#"


def _sfs_initial(r):
    return _initial(_sfs_sortname(r.title or ""))


def _begrepp_initial(r):
    return _initial(r.title or r.label or "")


# the hand-edited författningssamling registry (designation, official name,
# succession) -- foreskrift/data/series.json via lib/datasets
FS_SERIES = datasets.load_fs_series()


def _fs_live_map(series):
    """slug -> the slug that carries its documents today, following the
    succession chain (F8): difs -> imyfs, säifs -> srvfs -> msbfs -> mcffs.
    A cycle in the hand-edited data is a curation error, caught here."""
    live = {}
    for slug in series:
        chain = [slug]
        while series.get(chain[-1], {}).get("successor"):
            nxt = series[chain[-1]]["successor"]
            if nxt in chain:
                raise ValueError("fs succession cycle: %s"
                                 % " -> ".join(chain + [nxt]))
            chain.append(nxt)
        live[slug] = chain[-1]
    return live


_FS_LIVE = _fs_live_map(FS_SERIES)


def fs_series_info(key):
    """The registry entry for a series bucket key ('AAFS'), or {} for a series
    the registry does not know (its raw code then stands in everywhere)."""
    return FS_SERIES.get(key.lower(), {})


def fs_live_series(slug):
    """The fs slug that carries `slug`'s föreskrifter today, following the whole
    succession chain (säifs -> srvfs -> msbfs -> mcffs). `slug` itself where the
    series is still its own -- the inverse of `fs_predecessors`."""
    return _FS_LIVE.get(slug, slug)


def fs_predecessors(key):
    """(slug, registry entry) of the series whose documents now list under
    `key`'s ('IMYFS' -> the DIFS row), ordered by designation. The slug rides
    along so the browse page's succession note can name only predecessors the
    bucket actually holds documents from."""
    return sorted(((slug, FS_SERIES[slug]) for slug in FS_SERIES
                   if slug != key.lower() and _FS_LIVE[slug] == key.lower()),
                  key=lambda p: p[1]["designation"])


def _fs_series(r):
    slug = r.local.split("/")[0]                 # 'fffs/2013:10' -> 'fffs'
    return _FS_LIVE.get(slug, slug).upper()      # a succeeded series folds in


def _fs_label(key):
    return fs_series_info(key).get("designation", key)


def _fs_order(keys):
    """Alphabetical by printed designation (ÅFS under Å, after Z), so the nav
    reads in the order a Swedish reader scans."""
    return sorted(keys, key=lambda k: ([(_ALPHABET.find(c), c)
                                        for c in _fs_label(k).upper()],
                                       _fs_label(k)))


def _fs_year(r):
    m = re.search(r"(\d{4})", r.local.split("/", 1)[-1])
    return m.group(1) if m else "okänt"


def _avg_org(r):
    return r.kind                    # 'jo' | 'jk' | 'arn' | 'imy' | 'kkv'


def _avg_year(r):
    """Decision year from the diarienummer: ARN 'YYYY-NNNN' carries it first;
    JO '2340-2025' last; JK's new form '2024/8082' first; JK's old form
    '3497-06-40' as a two-digit year (century cutoff >50 -> 19xx, the legacy
    JKStore rule). ARN and JO share the 4-4 shape but order it oppositely, so
    ARN is keyed on the organ rather than the dnr shape. An IMY number
    ('IMY-2024-2904') carries the year the *ärende* was opened, which is often
    not the year it was decided, and a KKV number ('558/2026') the year the case
    was registered, which for a long investigation is years before its decision;
    both are therefore keyed on the decision date."""
    dnr = r.local.split("/", 2)[-1]              # 'avg/jo/2340-2025' -> dnr
    if r.kind == "imy":
        return _dated_year(r)
    if r.kind == "kkv":
        # decided-in year where the case has a decision date; where it has none
        # -- the curated-only cases, whose account dates them by a span rather
        # than a day -- the case number's own year, which is when the case was
        # registered. Approximate, but it files them near their neighbours
        # instead of stranding several hundred documents in "okänt"
        dated = _dated_year(r)
        return dated if dated != "okänt" else dnr.rsplit("/", 1)[-1]
    if r.kind == "arn":                          # 'avg/arn/1992-3657' -> 1992
        return dnr[:4]
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


def _rs_year(r):
    """The year a rättsligt ställningstagande browses under: its beslutsdatum
    where the document states one, else the year its own number carries.

    The number is the fallback rather than the rule because half these series
    number by the year the statement was *decided* and half revise in place --
    a Migrationsverket RS/028/2021 currently in version 3.0 belongs under the
    year that version was fastställd, not under 2021. The two number shapes are
    'år:löpnummer' (four agencies) and Kronofogdens 'löpnummer/tvåsiffrigt år'
    ('1/23'), which is why the year is not simply the leading digits."""
    if r.date and re.match(r"\d{4}", r.date):
        return r.date[:4]
    number = r.local.split("/", 2)[-1]              # 'rs/fk/2025-01' -> '2025-01'
    m = re.match(r"(\d{4})[-:]", number)
    if m:
        return m.group(1)
    m = re.match(r"\d{1,3}-(\d{2})\b", number)      # Kronofogdens '1-23-VER'
    if m:
        return str(2000 + int(m.group(1)))
    m = re.search(r"-(\d{4})$", number)             # Migrationsverkets 'RS-028-2021'
    return m.group(1) if m else "okänt"


def _dated_year(r):
    return r.date[:4] if r.date and re.match(r"\d{4}", r.date) else "okänt"


def _guidance_utgivare(r):
    """Which body issued it: the URI's own segment after the source
    ("guidance/edpb/riktlinjer/05-2020" -> "edpb"). Read off the address
    rather than the catalog `kind`, which this source spends on the series."""
    return r.local.split("/")[1]


def _guidance_year(r):
    """The year an EU-level vägledning browses under: the year it was adopted where
    the catalog dates it, else the year its own number carries.

    The number is the fallback rather than the rule because these documents are
    re-adopted: Riktlinjer 05/2021 is numbered for 2021 and its current version
    was adopted in 2023, and a reader looking for what the EDPB says now is
    looking for the later year. The endorsed artikel 29-gruppens vägledningar
    have no year in their number at all ('edpb/wp/248'), so those rely wholly on
    the adoption date the cover states."""
    if r.date and re.match(r"\d{4}", r.date):
        return r.date[:4]
    m = re.search(r"(?:-|/)(\d{4})(?:-\d+)?$", r.local)   # …/05-2020, …/gl/2021-05
    return m.group(1) if m else "okänt"


# the case sources (publication series / court), in browse order. The published
# referat carry a lowercase series segment ('dom/nja/…'); the *raw* avgöranden --
# the court's own version, harvested months before its editor referat and folded
# in once that arrives -- carry an uppercase court-code prefix ('dom/HDO_…'). The
# prefix names the court, so a raw avgörande is filed beside its eventual referat
# (HDO -> nja, MMOD -> mod, the kammarrätt codes -> rk, …). PBR and RHN have no
# referat series of their own, so they get their own bucket.
DV_COURTS = {
    "nja":  "Högsta domstolen (NJA)",
    # HFD and its pre-2011 self, Regeringsrätten (RÅ), are one court renamed --
    # one combined bucket (R1); the 'ra' series aliases into it below
    "hfd":  "Högsta förvaltningsdomstolen (HFD, tidigare RÅ)",
    "rh":   "Hovrätterna (RH)",
    "ad":   "Arbetsdomstolen (AD)",
    "mod":  "Mark- och miljööverdomstolen (MÖD)",
    "mig":  "Migrationsöverdomstolen (MIG)",
    "md":   "Marknadsdomstolen (MD)",
    "pmod": "Patent- och marknadsöverdomstolen (PMÖD)",
    "rk":   "Kammarrätterna (RK)",
    "pbr":  "Patentbesvärsrätten (PBR)",
    "rhn":  "Rättshjälpsnämnden",
    "övriga": "Övriga",
}

# a referat series segment that files under a *different* bucket than its own
# name -- Regeringsrätten's referat (dom/ra/…) join the HFD bucket (R1)
SERIES_ALIAS = {"ra": "hfd"}

# every lowercase referat series segment (the browse buckets plus the aliased
# ones) -- used to tell a referat uri ('dom/nja/…') from a raw avgörande
REFERAT_SERIES = set(DV_COURTS) | set(SERIES_ALIAS)

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

# a verdict with no referat yet gets the legacy published URI shape
# 'dom/{slug}/{malnummer}/{avgorandedatum}' (casenaming.verdict_uri /
# COURT_URI_SLUG). Its slug is lowercase and, for most courts, *not* the referat
# series name -- so an HD verdict ('dom/hd/Ö4337-25/2026-07-14') must be mapped
# to its series bucket explicitly or it falls into övriga (R2). Every slug in
# lib.casenaming.COURT_URI_SLUG must appear here (guarded by a test).
VERDICT_BUCKET = {
    "ad": "ad", "hd": "nja", "hfd": "hfd", "regr": "hfd",
    "hgo": "rh", "hnn": "rh", "hon": "rh", "hsb": "rh", "hsv": "rh", "hvs": "rh",
    "kgg": "rk", "kjo": "rk", "kst": "rk", "ksu": "rk",
    "md": "md", "mig": "mig", "mmd": "mod", "mod": "mod",
    "pbr": "pbr", "pmod": "pmod", "rhn": "rhn",
}


def _dv_court(r):
    parts = r.local.split("/")
    seg = parts[1] if len(parts) > 1 else ""
    s = seg.lower()
    if s in REFERAT_SERIES:                          # 'dom/nja/…' -- a referat
        return SERIES_ALIAS.get(s, s)
    if s in VERDICT_BUCKET:                          # 'dom/hd/…' -- a verdict, no referat
        return VERDICT_BUCKET[s]
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
    # a verdict URI ('dom/{slug}/{malnummer}/{YYYY-MM-DD}') carries its year in
    # the trailing avgörandedatum -- unambiguous, so read it there for every
    # court (the label is målnummer-laden and has no clean year) (R2)
    if len(parts) == 4 and re.fullmatch(r"\d{4}-\d\d-\d\d", parts[3]):
        return parts[3][:4]
    if seg.lower() in REFERAT_SERIES:                # referat
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
    # 'celex/32016R0679' -> '32016R0679'; a treaty carries a '/TXT' document
    # suffix ('celex/11992M/TXT'), so take the segment after 'celex/', not the last
    return r.local[len(lagrum.CELEX_LOCAL):].split("/")[0]


def _catalog_kind(r):
    # the catalog's stored doctype is authoritative -- re-deriving it (e.g. from
    # the CELEX) here diverged from what the rest of the app shows (lost the
    # treaties); shared by the eurlex, coe and hudoc schemes
    return r.kind


def _eu_kind(r):
    # a court order (CO) is a ruling, so it files with the judgments; an AG opinion
    # (CC) that reaches the browse -- one with no judgment yet -- files on its own
    return "judgment" if r.kind == "order" else r.kind


def _eu_year(r):
    m = re.match(r"\d(\d{4})", _eu_celex(r))      # sector digit, then 4-digit year
    return m.group(1) if m else "okänt"


# EU primary law groups by treaty family, not year (E1): the CELEX document-type
# letter after the 4-digit year identifies the family. E = TEC/TFEU, M = TEU,
# P = the Charter, A = Euratom; U/D/C/L are the amending treaties (Single European
# Act, Amsterdam, Nice, Lisbon) and R the 1975 treaty amending the financial
# provisions, V the never-ratified Constitution; W a withdrawal agreement; the
# enlargement letters (B/H/I/N/T/S/J) are accession treaties; 'ME' the combined
# consolidated publication. Anything unrecognised falls to 'other'.
_TREATY_FAMILY = {"E": "tfeu", "M": "teu", "P": "charter", "A": "euratom",
                  "U": "amending", "D": "amending", "C": "amending",
                  "L": "amending", "R": "amending", "V": "other",
                  "W": "withdrawal", "G": "other",
                  "B": "accession", "H": "accession", "I": "accession",
                  "N": "accession", "T": "accession", "S": "accession",
                  "J": "accession"}
# the curated Fördrag reading order: the constitutional core first (Charter, then
# the two consolidated core treaties), then the combined publication, then the
# supporting/derived treaties
_TREATY_ORDER = ["teu", "tfeu", "charter", "combined", "euratom", "amending",
                 "accession", "withdrawal", "other"]
_TREATY_LABEL = {
    "teu": "Fördraget om Europeiska unionen (EU-fördraget)",
    "tfeu": "Fördraget om Europeiska unionens funktionssätt (EUF-fördraget)",
    "charter": "EU:s rättighetsstadga",
    "combined": "Konsoliderade fördrag",
    "euratom": "Euratomfördraget",
    "amending": "Ändringsfördrag",
    "accession": "Anslutningsfördrag",
    "withdrawal": "Utträdesavtal",
    "other": "Övriga fördrag",
}


# the families that are one consolidated text republished after each amending
# treaty: their browse entry is the latest consolidation alone, the earlier ones
# being previous versions of the same text (see _keep_latest_consolidation).
# The rest are distinct instruments, each listed
_TREATY_CONSOLIDATED = frozenset({"teu", "tfeu", "charter", "combined", "euratom"})

# what the Fördrag page groups under (site/browse reads it): the current
# consolidated treaties lead as one unheaded run -- each entry's curated name
# says which treaty it is -- then the distinct instruments under their family
TREATY_FORMS = ((("current", None),)
                + tuple((k, _TREATY_LABEL[k]) for k in _TREATY_ORDER
                        if k not in _TREATY_CONSOLIDATED))


def _treaty_family(celex):
    """The treaty family bucket for a sector-1 CELEX (see _TREATY_FAMILY)."""
    return "combined" if celex[5:7] == "ME" else _TREATY_FAMILY.get(celex[5:6], "other")


def _treaty_variant(row):
    """What the Fördrag listing groups a treaty under (`TREATY_FORMS`): a
    consolidated text is `current` (only the latest lists), an instrument its
    family."""
    family = _treaty_family(_eu_celex(row))
    return "current" if family in _TREATY_CONSOLIDATED else family


def _twin_celex(local):
    # CELLAR serves a treaty under two ids, '12010M' and '12010M/TXT'
    return local[:-4] if local.endswith("/TXT") else local + "/TXT"


def _drop_treaty_twins(rows):
    """One entry per treaty where CELLAR served it under two ids -- '12010M' and
    '12010M/TXT' are downloaded and parsed as two artifacts of the same text
    (seven such pairs). The twin the curated name is keyed on (labels'
    treaties.json) lists; the unnamed one stays reachable by its own URL."""
    named = {r.local for r in rows if r.kind == "treaty" and r.short_title}
    return [r for r in rows
            if not (r.kind == "treaty" and not r.short_title
                    and _twin_celex(r.local) in named)]


def _keep_latest_consolidation(rows):
    """A consolidated treaty text lists once, as its newest consolidation: the
    2016 TEU, not nine versions of it. The earlier ones are previous wordings of
    the same text -- what the versioning of an act carries on the act's own page
    -- and they stay reachable by their own URL and by search."""
    latest = {}
    for r in rows:
        if r.kind == "treaty":
            family = _treaty_family(_eu_celex(r))
            if family in _TREATY_CONSOLIDATED:
                latest[family] = max(latest.get(family, ""), _eu_year(r))
    return [r for r in rows
            if r.kind != "treaty"
            or _treaty_family(_eu_celex(r)) not in _TREATY_CONSOLIDATED
            or _eu_year(r) == latest[_treaty_family(_eu_celex(r))]]


def _eu_second(r):
    """The eurlex second facet axis: a treaty groups by family (its year is not the
    reader's handle on it), everything else by year."""
    return _treaty_family(_eu_celex(r)) if r.kind == "treaty" else _eu_year(r)


def _eu_second_order(keys):
    # under a treaty parent the keys are families (curated order); under any other
    # type they are years (newest first)
    if all(k in _TREATY_LABEL for k in keys):
        return [k for k in _TREATY_ORDER if k in keys]
    return _by_year_desc(keys)


def _eu_second_label(key):
    return _TREATY_LABEL.get(key, key)


# Who enacted an act, read off the head of its official title -- the words
# before the date: "Europaparlamentets och rådets förordning (EU) 2024/792 av
# den 29 februari 2024 om …", "93/51/EEG: Kommissionens beslut av den …",
# "Regulation (EEC) No 1612/68 of the Council of 15 October 1968 on …". The
# CELEX carries no issuer and the CELLAR notice kept per document is three
# triples, so the title is the one place it is written. Measured over the
# catalog (2026-09-03): of 64 000 titled regulations 49 fell to `other`
# ("Komissionens" misspelt, the 1960s "Rådens förordning" of two Councils, an
# ECB act -- the misspellings are matched below), of 2 000 decisions 2. An act
# held only as a scanned PDF has no title at all (67 000 regulations, every one
# pre-2005): those file as `untitled`, since nothing says who enacted them.
# Parliament is tested first: its acts name the Council too.
_EU_TITLE_DATE = re.compile(
    r"\b(?:av den(?: den)?|of)\s+\d{1,2}(?:\s+\w+\s+|\.\d{1,2}\.)\d{4}\b", re.I)
_EU_ISSUERS = (("ep", re.compile(r"parl(?:ia|a|e)ment", re.I)),
               ("council", re.compile(r"\bcouncils?\b|\bråde(?:ts?|ns)\b", re.I)),
               ("commission", re.compile(r"\bcomm?ission|\bkomm?ission", re.I)))
# the site's listing groups, in reading order, for an act type; `%s` takes the
# type's plural ("förordningar"). An act with no title files last under a
# heading that says why its entry is a bare CELEX
ISSUER_FORMS = (("ep", "Europaparlamentets och rådets %s"), ("council", "Rådets %s"),
                ("commission", "Kommissionens %s"), ("other", "Övriga %s"),
                ("untitled", "Utan titel"))
# the CELEX court letter -> the site's listing group for a court decision
_EU_COURTS = {"C": "cj", "T": "gc", "F": "cst"}
COURT_FORMS = (("cj", "EU-domstolen"), ("gc", "Tribunalen"),
               ("cst", "Personaldomstolen"))


def _eu_issuer(row):
    """Who enacted an act (`ISSUER_FORMS` keys), from the head of its title."""
    if not row.title or row.title == row.label:      # the catalog stores the CELEX as title of an untitled act
        return "untitled"
    m = _EU_TITLE_DATE.search(row.title)
    head = row.title[:m.start()] if m else row.title[:120]
    return next((key for key, pat in _EU_ISSUERS if pat.search(head)), "other")


def _eu_variant(row):
    """What an eurlex listing groups an entry under (dv's `variant`, second
    use): an act by who enacted it, a court decision by which court, a treaty
    by `_treaty_variant`. None for an AG opinion (all the Court's) and for
    anything else."""
    if row.kind == "treaty":
        return _treaty_variant(row)
    if row.kind in ("judgment", "order"):
        return _EU_COURTS[_eu_celex(row)[5:6]]     # a sector-6 CELEX is C/T/F
    if row.kind in ("regulation", "directive", "decision"):
        return _eu_issuer(row)
    return None


# a CELEX corrigendum (…R(NN)) corrects an act rather than being one; it is left
# out of the browse, exactly as the old flat listing did (still reachable via
# search and the citations that point at it)
_EU_CORRIGENDUM = re.compile(r"R\(\d+\)$")


# --------------------------------------------------------------------------
# the per-source facet schemes
# --------------------------------------------------------------------------

class _Level:
    """One facet axis: how to derive a document's bucket key, how to display and
    slug that key, and how to order the keys."""

    def __init__(self, name, key, order, label=None, labels=None, slug=None,
                 kind_axis=False, only_above=None):
        self.name = name                 # navigator heading ("Domstol", "År")
        self.key = key                   # Row -> bucket key
        self.order = order               # [key] -> [key] sorted
        # a bucket label is either a table (the usual case -- readable, so
        # `kind_labels` can derive the flat map from SCHEMES rather than keep a
        # second one that drifts, N4) or a function, where it is computed
        self.labels = labels or {}
        self._label = label or self.labels.get
        self._slug = slug or _slug
        # True where this axis's bucket key *is* the catalog `kind` -- those are
        # the axes `kind_labels()` merges into the flat map the search facets
        # need. dv's Domstol axis is derived from the uri (its kind is 'case'),
        # so it is not one, and föreskrift's Serie axis reads series.json.
        self.kind_axis = kind_axis
        # Offer this axis only under a top-level bucket holding more than
        # `only_above` documents. The rule avg/rs/föreskrift set: a by-year
        # selector earns its place once a body's output is too long to read in
        # one list, and under that it only adds a click. The gate is the
        # top-level bucket (the utgivare), not the immediate parent -- what a
        # reader is deciding is whether *this body's* output needs splitting.
        self.only_above = only_above

    def label(self, key):
        return self._label(key) or key

    def slug(self, key):
        return self._slug(key)


def _slug(key):
    """A URL path segment for a bucket key: lower-cased, non-alphanumerics to '-'.
    Swedish letters survive (å/ä/ö) so the alphabet buckets stay distinct."""
    return re.sub(r"[^0-9a-zåäö]+", "-", key.lower()).strip("-") or "-"


# The ICJ's three decision kinds and their display order, named here because
# the facet axis below and the folkrätt landing both need them and a second
# copy of the labels drifted the day it was written.
ICJ_KIND_ORDER = ["dom", "rådgivande yttrande", "beslut"]
ICJ_KIND_LABELS = {"dom": "Domar",
                   "rådgivande yttrande": "Rådgivande yttranden",
                   "beslut": "Interimistiska beslut"}


SCHEMES = {
    "sfs": [_Level("Bokstav", _sfs_initial, _by_letter)],
    "begrepp": [_Level("Bokstav", _begrepp_initial, _by_letter)],
    "foreskrift": [
        _Level("Serie", _fs_series, _fs_order, label=_fs_label),
        _Level("År", _fs_year, _by_year_desc),
    ],
    "avg": [
        _Level("Organ", _avg_org, _curated(["jo", "jk", "arn", "imy", "kkv"]),
              kind_axis=True,
              labels=({"jo": "Justitieombudsmannen (JO)",
                                "jk": "Justitiekanslern (JK)",
                                "arn": "Allmänna reklamationsnämnden (ARN)",
                                "imy": "Integritetsskyddsmyndigheten (IMY)",
                                "kkv": "Konkurrensverket (KKV)"})),
        _Level("År", _avg_year, _by_year_desc),
    ],
    "rs": [
        _Level("Myndighet", _catalog_kind, kind_axis=True,
              order=_curated(["skv", "fk", "migr", "kfm", "imy", "fi", "kkv"]),
              labels=({"skv": "Skatteverket",
                                "fk": "Försäkringskassan (FKRS)",
                                "migr": "Migrationsverket (RS/RK)",
                                "kfm": "Kronofogdemyndigheten",
                                "imy": "Integritetsskyddsmyndigheten (IMYRS)",
                                "fi": "Finansinspektionen",
                                "kkv": "Konkurrensverket"})),
        _Level("År", _rs_year, _by_year_desc),
    ],
    # Utgivare first: this source carries several bodies, and "Riktlinjer"
    # means a different series under each. The year axis is offered only where a
    # body's own output runs past 100 documents -- the avg/rs/föreskrift rule.
    "guidance": [
        _Level("Utgivare", _guidance_utgivare,
              _curated(["edpb", "edps", "eba", "esma", "eiopa", "ecb",
                        "esrb", "easa", "acer", "enisa", "berec", "euipo"]),
              labels=({"edpb": "Europeiska dataskyddsstyrelsen (EDPB)",
                       "edps": "Europeiska datatillsynsmannen (EDPS)",
                       "eba": "Europeiska bankmyndigheten (EBA)",
                       "esma": "Europeiska värdepappersmyndigheten (Esma)",
                       "eiopa": "Europeiska försäkrings- och "
                                "tjänstepensionsmyndigheten (Eiopa)",
                       "ecb": "Europeiska centralbanken (ECB)",
                       "esrb": "Europeiska systemrisknämnden (ESRB)",
                       "easa": "EU:s byrå för luftfartssäkerhet (EASA)",
                       "acer": "EU:s byrå för energitillsyn (ACER)",
                       "enisa": "EU:s cybersäkerhetsbyrå (ENISA)",
                       "berec": "Organet för europeiska "
                                "regleringsmyndigheter för elektronisk "
                                "kommunikation (Berec)",
                       "euipo": "EU:s immaterialrättsmyndighet (EUIPO)"})),
        _Level("Serie", _catalog_kind, kind_axis=True,
              order=_curated(["riktlinjer", "rekommendationer", "wp",
                        "gl", "rec", "amc-gm", "amc", "gm",
                        "ramriktlinjer", "yttranden", "rapporter",
                        "varumarke", "formgivning", "gi"]),
              labels=({
                  "riktlinjer": "Riktlinjer",
                  "rekommendationer": "Rekommendationer",
                  "wp": "Artikel 29-gruppens vägledningar",
                  "gl": "Riktlinjer",
                  "rec": "Rekommendationer",
                  # EASA's own abbreviations, and the only ones its readers
                  # use: acceptable means of compliance and guidance material
                  "amc-gm": "AMC & GM",
                  "amc": "AMC",
                  "gm": "GM",
                  # ACER's ramriktlinjer are not riktlinjer in the EDPB/EBA
                  # sense: they state the principles a coming nätföreskrift
                  # must follow, so they keep their own label
                  "ramriktlinjer": "Ramriktlinjer",
                  "yttranden": "Yttranden",
                  # the ECB's yttranden carry the CON prefix its citation does;
                  # the ESRB numbers every document type in one sequence, so
                  # its documents are catalogued under the body itself
                  "con": "Yttranden",
                  "esrb": "Rekommendationer, varningar och beslut",
                  # ENISA divides its output into no series at all, so its one
                  # kod says what the whole of it is: rapporter
                  "rapporter": "Rapporter",
                  # EUIPO's three produktfamiljer: one granskningsriktlinje
                  # per IP right, named after the right rather than after the
                  # document type, because every EUIPO series is riktlinjer
                  "varumarke": "Riktlinjer för varumärken",
                  "formgivning": "Riktlinjer för formgivningar",
                  "gi": "Riktlinjer för geografiska beteckningar"})),
        _Level("År", _guidance_year, _by_year_desc, only_above=100),
    ],
    "hudoc": [
        _Level("Dokumenttyp", _catalog_kind, kind_axis=True,
              order=_curated(["judgment", "decision", "communicated-case",
                        "advisory-opinion", "legal-summary", "resolution",
                        "case-law"]),
              labels=({"judgment": "Domar", "decision": "Beslut",
                                "communicated-case": "Kommunicerade mål",
                                "advisory-opinion": "Rådgivande yttranden",
                                "legal-summary": "Rättsfallssammanfattningar",
                                "resolution": "Resolutioner",
                                "case-law": "Övrig praxis"})),
        _Level("År", _dated_year, _by_year_desc),
    ],
    "coe": [
        _Level("Typ", _catalog_kind, _curated(["treaty", "protocol"]), kind_axis=True,
              labels=({"treaty": "Fördrag", "protocol": "Protokoll"})),
        _Level("År", _dated_year, _by_year_desc),
    ],
    "icrc": [
        _Level("Typ", _catalog_kind, kind_axis=True,
              order=_curated(["treaty", "protocol", "declaration"]),
              labels=({"treaty": "Fördrag", "protocol": "Protokoll",
                                "declaration": "Deklarationer"})),
        _Level("År", _dated_year, _by_year_desc),
    ],
    "untc": [
        _Level("Typ", _catalog_kind, _curated(["treaty", "protocol"]), kind_axis=True,
              labels=({"treaty": "Fördrag", "protocol": "Protokoll"})),
        _Level("År", _dated_year, _by_year_desc),
    ],
    "icj": [
        # the Court's three decision kinds, in the order a reader wants them:
        # the binding judgments first, then the advisory opinions, then the
        # provisional-measures orders. Declared once here -- the folkrätt
        # landing reads it back through `scheme_kind_labels` rather than
        # keeping the second copy that used to sit in `lib/render`
        # (rule:second-use-goes-to-lib)
        _Level("Typ", _catalog_kind, kind_axis=True,
              order=_curated(ICJ_KIND_ORDER),
              labels=ICJ_KIND_LABELS),
        _Level("År", _dated_year, _by_year_desc),
    ],
    "icc": [
        _Level("Typ", _catalog_kind, kind_axis=True,
              order=_curated(["judgment", "sentence", "confirmation", "arrest-warrant",
                        "appeal-judgment", "appeal-interlocutory",
                        "appeal-reparations", "reparations", "investigation",
                        "admissibility", "prosecutor-review", "sentence-review"]),
              labels=({"judgment": "Domar", "sentence": "Straffmätning",
                                "confirmation": "Åtalsbekräftelse",
                                "arrest-warrant": "Häktning",
                                "appeal-judgment": "Överklagandedomar",
                                "appeal-interlocutory": "Interimistiska överklaganden",
                                "appeal-reparations": "Överklaganden (gottgörelse)",
                                "reparations": "Gottgörelse",
                                "investigation": "Utredningstillstånd",
                                "admissibility": "Tillåtlighet",
                                "prosecutor-review": "Åklagaromprövning",
                                "sentence-review": "Straffomprövning"})),
        _Level("År", _dated_year, _by_year_desc),
    ],
    "dv": [
        _Level("Domstol", _dv_court, _curated(list(DV_COURTS)),
              labels=(DV_COURTS)),
        _Level("År", _dv_year, _by_year_desc),
    ],
    "forarbete": [
        _Level("Typ", _fa_type, kind_axis=True,
              order=_curated(["prop", "sou", "ds", "dir", "bet", "rskr", "skr",
                              "lr", "pm", "fm", "so"]),
              labels=({"prop": "Propositioner", "sou": "SOU", "ds": "Ds",
                                "dir": "Kommittédirektiv", "bet": "Betänkanden",
                                "rskr": "Riksdagsskrivelser", "skr": "Skrivelser",
                                "lr": "Lagrådsremisser", "pm": "Promemorior",
                                "fm": "Förordningsmotiv",
                                "so": "Internationella överenskommelser"})),
        _Level("År", _fa_year, _by_year_desc),
    ],
    "eurlex": [
        # Fördrag (the constitutional texts) lead, then the legislative acts,
        # then case law -- the reader's mental order (E1). Fördrag are grouped
        # by treaty family (not year) at the second axis; see _eu_second.
        _Level("Typ", _eu_kind, kind_axis=True,
              order=_curated(["treaty", "directive", "regulation", "decision",
                        "judgment", "opinion", "act"]),
              labels=({"regulation": "Förordningar", "directive": "Direktiv",
                                "decision": "Beslut", "judgment": "Avgöranden",
                                "opinion": "Generaladvokatens förslag",
                                "treaty": "Fördrag", "act": "Övriga rättsakter"})),
        _Level("År", _eu_second, _eu_second_order, label=_eu_second_label),
    ],
}


def sources():
    return list(SCHEMES)


# Sources whose facet scheme serves the API and the search buckets only: their
# reader-facing browse is the folkrätt landing's complete treaty/decision
# listing, so generate writes no faceted tree of their own. Declared here as
# data -- next to the schemes it qualifies -- rather than as a skip list inside
# browse.py, so there is one authority for which schemes become pages (the two
# used to disagree: the API answered /browse for four sources whose pages were
# never generated).
UNGENERATED = frozenset({"coe", "icrc", "untc", "icc", "icj"})
assert UNGENERATED <= set(SCHEMES), "UNGENERATED names a source with no scheme"


def browsable():
    """The sources generate writes browse trees for: every scheme except the
    folkrätt-landing set above."""
    return [s for s in SCHEMES if s not in UNGENERATED]


# What each source is called to a reader -- the one table, read by the browse
# chrome and the frontpage (`lib/render`, which imports this module; the reverse
# would cycle) and by the search facets. It lived in `lib/render` alone until a
# second copy here drifted on two entries within a day of being written, which
# is the same failure `kind_labels` exists to prevent one axis over. The sources
# with no browse scheme (kommentar, remisser) still surface as search buckets,
# so they are named here too.
SOURCE_LABELS = {
    "sfs": "Författningar", "dv": "Rättsfall", "forarbete": "Förarbeten",
    "foreskrift": "Myndighetsföreskrifter", "avg": "Myndighetsavgöranden",
    "rs": "Rättsliga ställningstaganden", "eurlex": "EU-rättsakter",
    # the EU-rätt browse selector names the source `render.GUIDANCE_AXIS_LABEL`
    # instead: a heading over every body's series cannot call them riktlinjer
    "guidance": "EU-vägledning",
    "hudoc": "Europadomstolens praxis", "coe": "Europarådets fördrag",
    "icrc": "Internationell humanitär rätt", "untc": "FN-fördrag",
    "icc": "Internationella brottmålsdomstolen",
    "icj": "Internationella domstolen",
    "kommentar": "Lagkommentarer", "begrepp": "Begrepp",
    "remisser": "Remissvar",
}

# A facet bucket counts documents, so its label is plural ("Betänkanden"); a
# single search hit names one document, so it wants the singular. Only the kinds
# whose two forms differ are listed -- everything else (SOU, Ds, Direktiv,
# Beslut, Fördrag, and the organ names) is spelled the same either way.
_KIND_SINGULAR = {
    "prop": "Proposition", "bet": "Betänkande", "rskr": "Riksdagsskrivelse",
    "skr": "Skrivelse", "lr": "Lagrådsremiss", "pm": "Promemoria",
    "so": "Internationell överenskommelse", "regulation": "Förordning",
    "judgment": "Avgörande", "lag": "Lag", "forordning": "Förordning",
    "case": "Rättsfall",
    "kommentar": "Lagkommentar", "riktlinjer": "Riktlinje",
    "rekommendationer": "Rekommendation", "declaration": "Deklaration",
    "sentence": "Straffmätningsbeslut", "reparations": "Gottgörelsebeslut",
}


def scheme_kind_labels(source):
    """{catalog kind: reader-facing label} for one source's kind axis, in the
    axis's own display order. `kind_labels()` above merges every source's axis
    into one flat map for the corpus-wide search facets; this answers for a
    single source, which is what a bespoke landing listing needs."""
    for level in SCHEMES[source]:
        if level.kind_axis:
            return dict(level.labels or {})
    raise ValueError("facets: %s has no kind axis" % source)


def kind_labels(singular=False):
    """{catalog kind: reader-facing label} merged from every `kind_axis` level,
    plus the föreskrift series designations. The search facets slice the whole
    corpus by `kind` with no source to qualify it, so they need one flat map --
    derived from SCHEMES rather than restated, since a second copy is what let
    "bet", "pm" and "rskr" reach the reader as raw keys (N4).

    A kind is per-source, not global ('imy' is both an avg organ and an rs
    agency), so a key claimed by two sources keeps the *first* scheme's label
    and drops the parenthetical that would misattribute a mixed bucket: the
    search hit list holds both corpora under one button."""
    # sources whose whole corpus is one kind slice their browse by something
    # else (initial, court), so no kind_axis level names these
    out = {"lag": "Lagar", "forordning": "Förordningar m.m.", "case": "Rättsfall",
           "begrepp": "Begrepp", "kommentar": "Lagkommentarer"}
    for levels in SCHEMES.values():
        for level in levels:
            if not level.kind_axis:
                continue
            for key, label in level.labels.items():
                # first scheme wins; a second claimant drops the parenthetical
                # that would misattribute a bucket holding both corpora
                out[key] = label.split(" (")[0] if key in out else label
    # föreskrift buckets are keyed by series slug; their designations live in the
    # hand-edited registry, and the browse folds a succeeded series into its
    # successor -- search does not, so every slug is named here
    for slug, info in FS_SERIES.items():
        out.setdefault(slug, info["designation"])
    if singular:
        out.update({k: v for k, v in _KIND_SINGULAR.items() if k in out})
    return out


def document_year(source, row):
    """Return the document's publication/decision year for search faceting.

    Browse navigation already has the source-specific year knowledge (court
    identifiers and old JK diarienummer need more than a generic four-digit
    regexp), so the search index reuses it instead of growing a second, subtly
    different set of parsers.  SFS browse is alphabetical and therefore has no
    year level of its own; its stable ``YYYY:number`` identifier supplies it.
    Sources without a meaningful year return ``None`` rather than an ``okänt``
    bucket.
    """
    if source == "sfs":
        match = re.match(r"(\d{4}):", row.local)
        return match.group(1) if match else None
    if source == "eurlex":
        # the browse groups a treaty by family, not year, but the search facet
        # still wants its real year (from the CELEX)
        year = _eu_year(row)
        return year if year != "okänt" else None
    for level in SCHEMES.get(source, ()):
        if level.name == "År":
            year = level.key(row)
            return year if year != "okänt" else None
    return None


def _is_browsable(source, local):
    """Whether a document belongs in the browse at all -- an EU corrigendum
    corrects an act rather than being one, so it is omitted (still reachable via
    search and the citations that point at it)."""
    return not (source == "eurlex" and _EU_CORRIGENDUM.search(local))


def _browse_label(row):
    """The handle shown for a document in a listing -- the same reader-facing
    heading the page and search hits use (catalog_rows.display_title, stamped onto the
    `display` column at relate): an act's short name + acronym where it has them,
    else its title; a law/concept by name; everything else by its identifier.
    Falls back to label/local for a row predating the column (display still
    NULL until its source is re-related)."""
    return row.display or row.label or row.local


def _browse_doc(source, row, repealed=frozenset()):
    """A leaf-bucket document entry for the browse model. Every source carries
    `uri`/`url`/`display`; a statute additionally carries the split title
    (`pre` subdued + `key` emphasised), whether it is primary law (`subdued`
    when not), and its `year` -- what the listing renders and filters on. A
    föreskrift some other regulation's text repeals (`repealed`, the
    rpubl:upphaver targets) stays listed -- point-in-time law determination
    needs it findable -- but subdued, so it never reads as in force."""
    doc = {"uri": row.uri, "url": layout.page_url(row.uri),
           "display": _browse_label(row),
           "short_id": row.short_id or row.label,
           "short_title": row.short_title, "description": row.description}
    if source == "sfs":
        pre, key = _sfs_split(row.title or "")
        # parliamentary primary law -- a lag, a balk, a grundlag -- is shown at
        # full weight, secondary instruments (förordning, kungörelse, …)
        # subdued. The test lives in lib.labels because the catalog stamps it on
        # every SFS row as `kind` and cannot import this module.
        doc.update(pre=pre, key=key or doc["display"],
                   subdued=not labels.sfs_is_statute(row.title or "", row.local),
                   year=row.local.split(":", 1)[0])
    elif source == "dv":
        doc.update(variant=_dv_variant(row.local), date=row.date)
    elif source == "eurlex":
        doc["variant"] = _eu_variant(row)
        if row.kind == "treaty":
            # the curated name is the handle a reader knows a treaty by; its
            # CELEX ("12016M/TXT") says nothing to them, so the entry is the
            # name alone, set the way a statute's title is (dt-only)
            doc.update(pre="", key=row.short_title or doc["display"])
    elif row.uri in repealed:
        doc["subdued"] = True
    return doc


def _dv_variant(local):
    """A case-law entry's form, from its uri path: a *notis* (`dom/nja/2022/not/8`),
    a bare *dom* -- a verdict with no referat, minted at the date-suffixed
    `dom/{slug}/{malnr}/{date}` -- else a *referat* (`dom/nja/2022s75`,
    `dom/rh/2019:12`, …). Drives the Domar/Referat/Notiser grouping."""
    if "/not/" in local:
        return "notis"
    if re.search(r"/\d{4}-\d{2}-\d{2}$", local):
        return "dom"
    return "referat"


# an EU act reissued as corrected revisions carries a '(NN)' suffix on its CELEX
# ('…/TXT', '…/TXT(01)', '…/TXT(02)'); only the highest revision is listed, the
# base and the earlier revisions are dropped from the browse (E2). Still distinct
# from the trailing-'R(NN)' corrigendum handled by _EU_CORRIGENDUM above.
_EU_REVISION = re.compile(r"^(.*)\((\d+)\)$")


def _keep_latest_eu_revision(rows):
    """Collapse each base CELEX to its highest '(NN)' revision (E2)."""
    by_base = {}
    for r in rows:
        m = _EU_REVISION.match(r.local)
        base, rev = (m.group(1), int(m.group(2))) if m else (r.local, -1)
        by_base.setdefault(base, []).append((rev, r))
    return [max(items, key=lambda pair: pair[0])[1] for items in by_base.values()]


def _rows(con, source):
    """The catalog rows of `source` that belong in the browse, as `Row`s. A
    document whose declared expiry has passed is omitted -- a repealed statute,
    a withdrawn rättsligt ställningstagande -- so the listing shows only what
    still states law. It stays reachable by direct link; search omits it too
    (`search.REPEALED_IN_FORCE`), which is where an advanced "search expired"
    would put it back."""
    expired = catalog.expired_uris(con, date.today().isoformat())
    rows = [Row(uri, local, kind, label, title, display, doc_date,
                short_id, short_title, description)
            for uri, _src, kind, label, title, _url, _path, display, doc_date,
                short_id, short_title, description
            in catalog.facet_documents(con, source)
            for local in (catalog.local(uri),)          # bind once, reuse below
            if uri not in expired and _is_browsable(source, local)]
    if source == "eurlex":
        rows = _keep_latest_eu_revision(rows)
        rows = _drop_opinions_with_judgment(rows)
        rows = _keep_latest_consolidation(_drop_treaty_twins(rows))
    yield from rows


def _drop_opinions_with_judgment(rows):
    """An Advocate General's opinion (CELEX ``…CC…``) is browsable only while its
    case has no judgment: once the ``…CJ…`` judgment for the same case exists, the
    opinion is reached from the judgment (and search), not the index (E4)."""
    celex = {_eu_celex(r) for r in rows}
    return [r for r in rows
            if not (_eu_celex(r)[5:7] == "CC"
                    and _eu_celex(r)[:5] + "CJ" + _eu_celex(r)[7:] in celex)]


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
    # a document of a numbered series sorts by that number, not by its subject:
    # the bucket is already one court+year, one samling+year or one agency+year,
    # so the number is what a reader scans (R3). `_doc_sort` -- the subject order
    # -- is for the sources whose buckets are subject buckets (sfs, begrepp) and
    # for those whose documents are known by name rather than by number (a hudoc
    # case). Sorting a numbered series by subject read as random and sometimes
    # was: `_sfs_sortname` finds the digits in an EDPB title, so "Riktlinjer
    # 3/2018" about förordning (EU) 2016/679 sorted on 1679. The EU material --
    # eurlex and the guidance -- reads newest first instead (`_recent_first`)
    for rows in buckets.values():
        sort_rows(source, rows)
    return buckets


def sort_rows(source, rows):
    """Order one bucket's documents for display, in place. Shared by `group`
    and `browse_view`, which re-collects a leaf that spans several buckets and
    has to put the result back in this order."""
    key, reverse = {"dv": (_id_doc_sort, False), "forarbete": (_id_doc_sort, False),
                    "foreskrift": (_id_doc_sort, False), "rs": (_id_doc_sort, False),
                    "avg": (_id_doc_sort, False),
                    "guidance": (_recent_first, True),
                    "eurlex": (_recent_first, True)}.get(source, (_doc_sort, False))
    rows.sort(key=key, reverse=reverse)


def _natural(s):
    """A natural-order sort key so '2024:2' precedes '2024:10' (numeric runs
    compare as numbers, not lexically)."""
    return [(1, int(t)) if t.isdigit() else (0, t.lower())
            for t in re.split(r"(\d+)", s) if t]


def _doc_sort(row):
    """The subject order: laws and concepts read alphabetically by what they are
    about, which is also how their buckets are keyed (initial letter). It is the
    default, so it also carries the sources whose documents are known by name
    rather than by number -- a hudoc case, a treaty.

    It is *not* the identifier order: `_sfs_sortname` reads the title, so a
    document whose title carries digits sorts by those digits. Use
    `_id_doc_sort` for a numbered series."""
    primary = _sfs_sortname(row.title or "").lower() or row.label or row.local
    return (_natural(primary), _natural(row.local))


def _recent_first(row):
    """The newest-first order of the EU material, sorted descending: by the
    document's date, then by the identifier the listing prints (natural order,
    so (EU) 2024/1364 precedes (EU) 2024/436 and Riktlinjer 2/2019 precedes
    1/2019). A year's förordningar open on December's, a year's cases on the
    latest judgment, an EDPB series on what it published last; a treaty family
    on its current consolidation. An undated entry (an empty date sorts last
    descending) falls to the end -- 21 of 3 910 guidance rows, no eurlex row."""
    return (row.date or "", _natural(row.short_id or row.label or row.local))


def _id_doc_sort(row):
    """Within a leaf bucket, order documents by the identifier the listing
    prints -- 'NJA 2019 s. 1021', 'Prop. 2025/26:42', 'AFFS 2025:1' -- so a
    reader scans by number, not by the editor's popular name or the document's
    subject (R3). It is the same string `_browse_doc` puts in the `<dt>`.

    Natural order throughout, so `:2` precedes `:11` and `s. 5` precedes
    `s. 1021`; the uri breaks ties for raw avgöranden that share a bare id.
    (Raw domar are re-sorted by date at render -- a målnummer is a docket
    sequence, not an editorial number.)"""
    return (_natural(row.short_id or row.label or row.local), _natural(row.local))


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


def _level_nodes(levels, counts, prefix, root_count=None):
    """Recursively build the ordered bucket nodes at depth `len(prefix)`.

    `root_count` is how many documents the top-level bucket this recursion is
    inside holds -- what a level's `only_above` is gated on."""
    depth = len(prefix)
    level = levels[depth]
    here = {}
    for path, n in counts.items():
        if path[:depth] == prefix:
            here[path[depth]] = here.get(path[depth], 0) + n
    nodes = []
    for key in level.order(list(here)):
        child_prefix = prefix + (key,)
        below = here[key] if depth == 0 else root_count
        deeper = levels[depth + 1] if depth + 1 < len(levels) else None
        children = (_level_nodes(levels, counts, child_prefix, below)
                    if deeper is not None
                    and (deeper.only_above is None
                         or below > deeper.only_above) else None)
        nodes.append({"key": key, "label": level.label(key), "slug": level.slug(key),
                      "count": here[key], "children": children})
    return nodes


def _fold_fs_amendments(con, grouped):
    """Move every ändringsförfattning out of its own year bucket and under its
    base regulation (F5): the base's entry carries its amendments, listed on
    the base's year. An amendment amending another amendment ("… (ÅFS 2006:3)
    om ändring i … (ÅFS 2005:5)") follows the chain to the regulation that
    stays a top-level entry, so nothing nests under a row that itself folded
    away. An amendment whose base is not in the browse (never parsed, or
    expired out) -- or whose chain is cyclic (corrupt data) -- stays a
    top-level entry. Returns the refolded buckets -- so the year counts
    reflect the nested placement -- plus {base uri: [amendment Row]} for
    `browse_view` to attach."""
    edges = catalog.andrar_edges(con)
    paths = {r.uri: path for path, rows in grouped.items() for r in rows}

    def base_of(uri):
        seen, at = {uri}, uri
        while True:
            nxt = edges.get(at)
            if not nxt or nxt not in paths:
                return at if at != uri else None
            if nxt in seen:
                return None
            seen.add(nxt)
            at = nxt

    nested = {}
    for path in list(grouped):
        kept = []
        for r in grouped[path]:
            base = base_of(r.uri)
            if base:
                nested.setdefault(base, []).append(r)
            else:
                kept.append(r)
        grouped[path] = kept
    for rows in nested.values():
        rows.sort(key=_doc_sort)
    return {path: rows for path, rows in grouped.items() if rows}, nested


# the base ("grund") version of a föreskrift that also has a consolidated one:
# it lives at the consolidated document's uri + this suffix
_FS_BASE_SUFFIX = "/grund"


def _fold_fs_versions(grouped):
    """Drop the base-version row of every föreskrift that also has a
    consolidated one, returning the refolded buckets plus the set of uris that
    *are* consolidated (B4).

    A föreskrift with a konsoliderad version is two catalog rows -- the
    consolidated text at the document's own uri, the text as promulgated at
    `<uri>/grund` -- and both carried the same beteckning and the same title, so
    the listing showed each of 1 650 föreskrifter twice with nothing to choose
    between them. The consolidated one is the answer to "what does this
    föreskrift say", so it is the one that lists, and it says that it is
    consolidated; the base version stays reachable from the document page, which
    already offers it."""
    consolidated = set()
    for path in list(grouped):
        kept = []
        for r in grouped[path]:
            if r.uri.endswith(_FS_BASE_SUFFIX):
                consolidated.add(r.uri[:-len(_FS_BASE_SUFFIX)])
            else:
                kept.append(r)
        grouped[path] = kept
    return {path: rows for path, rows in grouped.items() if rows}, consolidated


def browse_view(con, source):
    """The full browse model for a source: the navigator (`tree`) with each leaf
    bucket's ordered, display-labelled documents attached. One catalog scan; this
    is the single payload the static-site generator consumes per source (it has
    no other access to the data store)."""
    grouped = group(con, source)
    nested = {}
    repealed = frozenset()
    consolidated = frozenset()
    if source == "foreskrift":
        # versions first: a base-version row must not be treated as a document
        # of its own by the amendment fold either
        grouped, consolidated = _fold_fs_versions(grouped)
        grouped, nested = _fold_fs_amendments(con, grouped)
        repealed = catalog.upphaver_targets(con)
    view = tree(con, source, grouped)

    def entry(r):
        doc = _browse_doc(source, r, repealed)
        if r.uri in consolidated:
            doc["consolidated"] = True
        if r.uri in nested:
            doc["amendments"] = [_browse_doc(source, a, repealed)
                                 for a in nested[r.uri]]
        return doc

    def attach(nodes, prefix):
        """Hang each leaf bucket's documents on it.

        A leaf's key path can be *shorter* than `grouped`'s, which always has
        one element per level: `only_above` suppresses a level for the buckets
        below its threshold, so a small utgivare's series bucket is a leaf while
        its rows are still filed under (utgivare, serie, år). Collecting by
        prefix covers both -- for a scheme whose leaves sit at the last level
        exactly one key matches, which is the lookup this replaces."""
        for n in nodes:
            keypath = prefix + (n["key"],)
            if n["children"] is not None:
                attach(n["children"], keypath)
                continue
            rows = [r for path, under in grouped.items()
                    if path[:len(keypath)] == keypath for r in under]
            sort_rows(source, rows)
            n["documents"] = [entry(r) for r in rows]

    attach(view["buckets"], ())
    return view


# --------------------------------------------------------------------------
# flow groups -- the citation graph's node vocabulary
# --------------------------------------------------------------------------

# What one node of a cross-source citation view is (the stats flow diagram and
# the paraGRAF graph explorer both). Mostly the source itself, but two
# places where the source is the wrong unit:
#
# * eurlex holds three kinds of law that behave differently and cite each other
#   -- the founding treaties, the acts made under them, and the Court's case law
#   -- so it splits three ways (`flow_group`).
# * the international-law sources are one kind of thing each: coe/icrc/untc are
#   all treaty text, hudoc/icj/icc are all case law, so they merge two ways.
#
# The labels are shorter than the browse headings on purpose: these are node
# labels beside a mark, not headings ("Föreskrifter", not
# "Myndighetsföreskrifter"). A source missing here is a hard error rather than
# an "övrigt" bucket -- a new source belongs on one side of this map, and
# silently pooling it would make a flow view lie about what cites what.
FLOW_GROUPS = {
    "sfs": "Författningar", "forarbete": "Förarbeten", "dv": "Rättsfall",
    "foreskrift": "Föreskrifter", "avg": "Myndighetsavgöranden",
    "rs": "Ställningstaganden", "kommentar": "Lagkommentarer",
    "begrepp": "Begrepp", "guidance": "EU-vägledning",
    "lawreview": "Tidskriftsartiklar",
    "coe": "Konventioner", "icrc": "Konventioner", "untc": "Konventioner",
    "hudoc": "Folkrättslig praxis", "icj": "Folkrättslig praxis",
    "icc": "Folkrättslig praxis",
}


# every flow node there is, in presentation order (the graph explorer's legend
# and filter): the Swedish material first, the EU's three, the guidance, then
# international law
FLOW_GROUP_NAMES = (
    "Författningar", "Förarbeten", "Rättsfall", "Föreskrifter",
    "Myndighetsavgöranden", "Ställningstaganden", "Lagkommentarer",
    "Tidskriftsartiklar", "Begrepp",
    "EU-rättsakter", "EU-domar", "EU-fördrag", "EU-vägledning",
    "Konventioner", "Folkrättslig praxis")


def flow_group(source, kind):
    """The flow node a document belongs to, from its catalog (source, kind).

    eurlex's three: `eu_structure.CASELAW` is the Court's own set (judgment,
    opinion *and* order -- a hand-written pair here read an order as
    legislation), sector 1 is the treaties, and everything the Union enacts
    under them is one act group (regulation, directive, decision, and the
    `act` residual the recommendations carry)."""
    if source == "eurlex":
        return "EU-domar" if kind in eu_structure.CASELAW \
            else "EU-fördrag" if kind == "treaty" else "EU-rättsakter"
    assert source in FLOW_GROUPS, \
        "no flow group for source %r -- add it to FLOW_GROUPS" % source
    return FLOW_GROUPS[source]
