"""Identity indexer for court decisions (DV).

Reconciles the two raw DV stores into one canonical identity per real
legal case, so the downstream parser can merge representations instead of
emitting the same case twice under two unlinked ids:

  site/data/downloaded/dv/{COURT}/*.doc(x)   legacy feed (Word originals)
  site/data/domstol/downloaded/{COURT}/*.json  new courts' API (JSON)

This is entity resolution, not a winner-takes-all fallback: every source
record is kept and attached to its canonical case, and identity agreement
is *manufactured* here (the sources do not natively share ids -- UUIDs vs
filename-derived ids, and differing court codes) rather than assumed.

Linkage keys (a case is the connected component over shared keys):

- referatnummer, global: ("R", normalized_referat) -- always strong;
- målnummer, court-scoped: ("M", canonical_court, normalized_malnr) -- only
  cross-store when it identifies one API and one legacy component.

Målnummer is not unique over time even within one court: AD 1993 nr 22 and AD
1994 nr 13 are distinct published decisions under A 112-92. Therefore two API
records are never fused solely by M. Legacy attachment variants may share M;
after grouping those, M links legacy to API only when the match is unambiguous.

The API records carry explicit malNummerLista/referatNummerLista, so their
keys are reliable. Legacy identity comes from the filename; for almost
every court the stem is a målnummer that matches an API målnummer after
normalization. Two courts encode the *referat* in the filename instead and
get a reconstructed referat key:

- ADO  1993-100      -> "AD 1993 nr 100"
- HDO  2003_not_1    -> "NJA 2003 not 1"  (notisfall)

Error modes are asymmetric and both reported: under-linking leaves the same
case as two single-source components (a duplicate downstream); over-linking
fuses distinct cases (a component spanning >1 court is flagged).

Rebuilt from the records already on disk (no network) via `lagen dv reindex`.
"""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..lib import compress, layout, util

# legacy court dir code -> code used by the new API (others are identical)
COURT_CANON = {"REG": "REGR", "MÖD": "MOD", "MMD": "MMOD",
               "MIG": "MIOD", "PMD": "PMOD"}


def canonical_court(code):
    return COURT_CANON.get(code, code)


def norm_malnr(s):
    return re.sub(r"\s+", "", s).upper()


RE_REFERAT_FORMS = re.compile(
    r"([A-ZÅÄÖ]+)\s*((?:19|20)\d{2})\s*(?::|ref\.?|nr)\s*(\d+)$", re.I)


def norm_referat(s):
    # "RÅ 1994:69", "RÅ 1994 ref. 69" and "AD 1993 nr 100" spellings of one
    # published identity normalize to the same key (the old RDF uses the colon
    # form where the API spells out "ref."). NJA page vs löpnummer identity is
    # untouched: the page form ("NJA 2016 s. 540") never matches this shape.
    m = RE_REFERAT_FORMS.match(s.strip())
    if m:
        return "".join(m.groups()).upper()
    return "".join(c for c in s.upper() if c.isalnum())


# courts whose notisfall filenames (YYYY_not_N) encode the published notis
# identity; the value is the publication series the referat is minted in
NOTIS_SERIES = {"HDO": "NJA", "REG": "RÅ", "HFD": "HFD"}


def legacy_identity(court, filename):
    """(malnummer, referat) lists derived from a legacy filename, or
    (None, None) if it is not a recognizable case document."""
    stem = re.sub(r"\.(docx?|xml)$", "", filename, flags=re.I)
    if stem == filename:  # not a case document
        return None, None
    # notisfall: the _N here is the notis number, not an attachment
    m = re.match(r"(\d{4})_not_(\d+)(?:_\d+)?$", stem)
    if court in NOTIS_SERIES and m:
        return [], ["%s %s not %s" % (NOTIS_SERIES[court], *m.groups())]
    if court == "ADO":
        # the dash form (1993-100, with optional attachment variant _N) and
        # the late underscore form (2022_48) both encode year + referat number
        m = (re.match(r"(\d{4})-(\d+)(?:_\d+)?$", stem)
             or re.match(r"(\d{4})_(\d+)$", stem))
        if m:
            return [], ["AD %s nr %s" % m.groups()]
    stem = re.sub(r"_\d+$", "", stem)  # drop attachment-variant suffix
    return [stem], []


def keys(court, malnummer, referat):
    out: set[tuple] = {("M", court, norm_malnr(m)) for m in malnummer if norm_malnr(m)}
    # NJA's `YYYY:N` is an editorial löpnummer, not always a case identity:
    # one numbered referat can contain several separately published decisions
    # with different canonical page forms (eg s. 341 and s. 346 both carry
    # NJA 2016:31). When a record has a page referat, only that published page
    # form is a strong R key. The löpnummer remains metadata on the artifact.
    strong_referat = ([r for r in referat if re.search(r"\bs\.?\s*\d+", r, re.I)]
                      if any(re.search(r"\bs\.?\s*\d+", r, re.I) for r in referat)
                      else referat)
    out |= {("R", norm_referat(r)) for r in strong_referat if norm_referat(r)}
    return out


def scan_api(domstoldir):
    records = []
    for path in sorted(compress.glob(Path(domstoldir), "**/*.json")):
        if path.name.startswith("."):
            continue   # not a record: the .watermark.json harvest marker, junk
        d = json.loads(compress.read_text(path))
        court = canonical_court(d["domstol"]["domstolKod"])
        records.append({
            "store": "domstol", "court": court,
            "path": util.store_relpath(path, layout.DATA),
            "uuid": d["id"],
            "grupp": d.get("gruppKorrelationsnummer"),
            "malnummer": [m.strip() for m in d.get("malNummerLista", [])],
            "referat": [r.strip() for r in d.get("referatNummerLista", [])],
            "avgorandedatum": d.get("avgorandedatum"),
            "has_innehall": bool(d.get("innehall")),
            "bilagor": len(d.get("bilagaLista", [])),
        })
    return records


# frozen-oracle identities beside the legacy files (written by
# `lagen dv import-legacy` from the old pipeline's distilled RDF): the referat
# identity a filename alone cannot supply, keyed by court + målnummer/referat
IDENTITIES = "legacy-identities.json"


def scan_legacy(dvdir):
    records, unrecognized = [], []
    for path in sorted(Path(dvdir).rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".doc", ".docx",
                                                             ".xml"):
            continue
        court = canonical_court(path.parent.name)
        malnummer, referat = legacy_identity(path.parent.name, path.name)
        if malnummer is None:
            unrecognized.append(str(path))
            continue
        records.append({"store": "dv", "court": court,
                        "path": util.store_relpath(path, layout.DATA),
                        "malnummer": malnummer, "referat": referat})
    sidecar = Path(dvdir) / IDENTITIES
    if sidecar.exists():
        enrich_legacy(records, json.loads(sidecar.read_text()))
    return records, unrecognized


def enrich_legacy(records, identities):
    """Attach frozen-oracle identity facts to filename-derived legacy records.

    A record whose filename yields only a målnummer gains the referat the old
    pipeline published it under -- but only when the (court, målnummer) key is
    unambiguous over the oracle: målnummer is reused across years (AD 1993 nr
    22 and AD 1994 nr 13 share A 112-92), and guessing would publish one
    decision's text under another referat's URI. A record whose filename yields
    the referat (notisfall, ADO) gains the målnummer and date the document
    itself often lacks."""
    by_malnr, by_referat = defaultdict(list), {}
    for ident in identities:
        for m in ident["malnummer"]:
            by_malnr[(ident["court"], norm_malnr(m))].append(ident)
        for r in ident["referat"]:
            by_referat[(ident["court"], norm_referat(r))] = ident
    for rec in records:
        if rec["referat"]:
            hits = [by_referat.get((rec["court"], norm_referat(r)))
                    for r in rec["referat"]]
            hits = [h for h in hits if h]
        else:
            found = {id(h): h for m in rec["malnummer"]
                     for h in by_malnr.get((rec["court"], norm_malnr(m)), ())}
            distinct = {tuple(h["referat"]) for h in found.values()}
            hits = list(found.values()) if len(distinct) == 1 else []
        if not hits:
            continue
        rec["referat"] = dedup(rec["referat"]
                               + [r for h in hits for r in h["referat"]])
        # Oracle målnummer is *metadata*, never a linkage key: distinct
        # decisions reuse a målnummer (AD 1993 nr 22 / AD 1994 nr 13 under
        # A 112-92), so letting it mint M keys fuses referats. It is kept
        # apart from the filename-derived list keys() reads.
        rec["oracle_malnummer"] = dedup_malnr(
            m for h in hits for m in h["malnummer"])
        dates = sorted({h["avgorandedatum"] for h in hits
                        if h.get("avgorandedatum")})
        if len(dates) == 1:
            rec["avgorandedatum"] = dates[0]
        rubriks = sorted({h["referatrubrik"] for h in hits
                          if h.get("referatrubrik")})
        if len(rubriks) == 1:
            rec["referatrubrik"] = rubriks[0]


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def build_index(api_records, legacy_records):
    records = api_records + legacy_records
    uf = UnionFind()
    referat_owner = {}  # strong R key -> a representative record index
    malnummer_records = defaultdict(list)
    for i, rec in enumerate(records):
        uf.find(i)
        for key in keys(rec["court"], rec["malnummer"], rec["referat"]):
            if key[0] == "R":
                if key in referat_owner:
                    uf.union(i, referat_owner[key])
                else:
                    referat_owner[key] = i
            else:
                malnummer_records[key].append(i)

    for indices in malnummer_records.values():
        # Multiple frozen files with one M are attachment variants of the same
        # legacy case; their filename-derived identity is the same by design.
        legacy = [i for i in indices if records[i]["store"] == "dv"]
        for i in legacy[1:]:
            uf.union(legacy[0], i)

    component_referat = defaultdict(set)
    for i, rec in enumerate(records):
        component_referat[uf.find(i)].update(
            norm_referat(r) for r in rec["referat"] if norm_referat(r))

    for indices in malnummer_records.values():
        # M is a cross-store bridge only when it is unambiguous after the strong
        # R links and legacy attachment grouping. Distinct API components with
        # the same M stay distinct; guessing would publish one decision's text
        # under another referat's URI.
        api_roots = {uf.find(i) for i in indices
                     if records[i]["store"] == "domstol"}
        legacy_roots = {uf.find(i) for i in indices
                        if records[i]["store"] == "dv"}
        if len(api_roots) == len(legacy_roots) == 1:
            api_root, legacy_root = next(iter(api_roots)), next(iter(legacy_roots))
            # When both components already publish referat identities and they
            # disagree, M is weaker contradictory evidence, not a bridge (one
            # API record lists both RH 2016:61 and :62 case numbers under :62
            # while the old feed correctly publishes one decision per referat).
            if (component_referat[api_root] and component_referat[legacy_root]
                    and component_referat[api_root].isdisjoint(
                        component_referat[legacy_root])):
                continue
            uf.union(api_root, legacy_root)
            component_referat[uf.find(api_root)] = (
                component_referat[api_root] | component_referat[legacy_root])

    groups = defaultdict(list)
    for i, rec in enumerate(records):
        groups[uf.find(i)].append(rec)

    cases = []
    for members in groups.values():
        malnummer = dedup_malnr(m for r in members
                                for m in r["malnummer"]
                                + r.get("oracle_malnummer", []))
        referat = dedup(r for rec in members for r in rec["referat"])
        courts = sorted({r["court"] for r in members})
        dates = sorted({r["avgorandedatum"] for r in members
                        if r.get("avgorandedatum")})
        stores = sorted({r["store"] for r in members})
        # the frozen oracle's published summary (rpubl:referatrubrik), carried
        # for legacy-only cases whose document body has no rubrik of its own
        rubriks = sorted({r["referatrubrik"] for r in members
                          if r.get("referatrubrik")}, key=len, reverse=True)
        cases.append({
            "canonical_id": canonical_id(courts, malnummer, referat),
            "courts": courts,
            "malnummer": malnummer,
            "referat": referat,
            "avgorandedatum": dates[0] if dates else None,
            "referatrubrik": rubriks[0] if rubriks else None,
            "sources": sorted(stores),
            "members": sorted(members, key=lambda r: (r["store"], r["path"])),
        })
    return cases


def dedup(items):
    """Order-preserving dedup by normalized referat, keeping the longest
    surface form (the API's spaced form over a terse one)."""
    best = {}
    for item in items:
        k = norm_referat(item)
        if k and (k not in best or len(item) > len(best[k])):
            best[k] = item
    return sorted(best.values())


def dedup_malnr(items):
    best = {}
    for item in items:
        k = norm_malnr(item)
        if k and (k not in best or len(item) > len(best[k])):
            best[k] = item
    return sorted(best.values())


def grupp_map(cases):
    """gruppKorrelationsnummer -> canonical case id, over the identity index.
    The grupp is the publication group a hanvisad publicering names, so this is
    the authoritative resolution for a related-case reference whose fritext the
    citation grammar cannot read. A grupp claimed by more than one canonical
    case (a handful of split groups) is dropped: guessing would link the wrong
    decision, and the fritext route remains for those."""
    claims = defaultdict(set)
    for case in cases:
        for member in case["members"]:
            if member.get("grupp"):
                claims[member["grupp"]].add(case["canonical_id"])
    return {grupp: next(iter(ids))
            for grupp, ids in claims.items() if len(ids) == 1}


def canonical_id(courts, malnummer, referat):
    if referat:
        return referat[0]
    court = courts[0] if courts else "?"
    return "%s %s" % (court, malnummer[0]) if malnummer else court


def report(cases, unrecognized):
    by_sources = Counter(tuple(c["sources"]) for c in cases)
    linked = by_sources[("domstol", "dv")]
    multi_court = [c for c in cases if len(c["courts"]) > 1]
    multi_file = sum(1 for c in cases if len(c["members"]) > 1)
    print("%d canonical cases" % len(cases))
    print("  both sources (linked): %d" % linked)
    print("  domstol (API) only:    %d" % by_sources[("domstol",)])
    print("  dv (legacy) only:      %d" % by_sources[("dv",)])
    print("  multi-record cases:    %d" % multi_file)
    print("  unrecognized legacy files: %d" % len(unrecognized))
    if multi_court:
        print("  !! %d components span >1 court (likely over-linked):"
              % len(multi_court))
        for c in multi_court[:10]:
            print("     %s  courts=%s" % (c["canonical_id"], c["courts"]))
    by_court = Counter(c["courts"][0] for c in cases if c["courts"])
    print("  per court (canonical):",
          dict(sorted(by_court.items(), key=lambda kv: -kv[1])))


def reindex(dvdir, domstoldir, out):
    """Rebuild the whole identity index from the two raw stores and write it to
    `out`. A full rebuild, not an incremental update: build_index is a global
    union-find over every record, so a single new record can merge components.
    Reads only raw record metadata + legacy filenames -- no document parsing."""
    api_records = scan_api(domstoldir)
    legacy_records, unrecognized = scan_legacy(dvdir)
    print("scanned %d API records, %d legacy files (%d unrecognized)"
          % (len(api_records), len(legacy_records), len(unrecognized)))
    cases = build_index(api_records, legacy_records)
    cases.sort(key=lambda c: (c["avgorandedatum"] or "", c["canonical_id"]))
    util.write_atomic(out, json.dumps(cases, ensure_ascii=False, indent=2))
    report(cases, unrecognized)
    print("index written to %s" % out)
    return cases
