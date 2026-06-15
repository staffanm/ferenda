"""Identity indexer for court decisions (DV).

Reconciles the two raw DV stores into one canonical identity per real
legal case, so the downstream parser can merge representations instead of
emitting the same case twice under two unlinked ids:

  site/data/dv/downloaded/{COURT}/*.doc(x)   legacy feed (Word originals)
  site/data/domstol/downloaded/{COURT}/*.json  new courts' API (JSON)

This is entity resolution, not a winner-takes-all fallback: every source
record is kept and attached to its canonical case, and identity agreement
is *manufactured* here (the sources do not natively share ids -- UUIDs vs
filename-derived ids, and differing court codes) rather than assumed.

Linkage keys (a case is the connected component over shared keys):

- målnummer, court-scoped: ("M", canonical_court, normalized_malnr)
- referatnummer, global:    ("R", normalized_referat)

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

  python -m accommodanda.dv_identity [DVDIR] [DOMSTOLDIR] [--out index.json]
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

DV_DEFAULT = "site/data/dv/downloaded"
DOMSTOL_DEFAULT = "site/data/domstol/downloaded"

# legacy court dir code -> code used by the new API (others are identical)
COURT_CANON = {"REG": "REGR", "MÖD": "MOD", "MMD": "MMOD",
               "MIG": "MIOD", "PMD": "PMOD"}


def canonical_court(code):
    return COURT_CANON.get(code, code)


def norm_malnr(s):
    return re.sub(r"\s+", "", s).upper()


def norm_referat(s):
    return "".join(c for c in s.upper() if c.isalnum())


def legacy_identity(court, filename):
    """(malnummer, referat) lists derived from a legacy filename, or
    (None, None) if it is not a recognizable case document."""
    stem = re.sub(r"\.(docx?)$", "", filename, flags=re.I)
    if stem == filename:  # not a .doc/.docx
        return None, None
    # HDO notisfall: the _N here is the notis number, not an attachment
    m = re.match(r"(\d{4})_not_(\d+)(?:_\d+)?$", stem)
    if court == "HDO" and m:
        return [], ["NJA %s not %s" % m.groups()]
    stem = re.sub(r"_\d+$", "", stem)  # drop attachment-variant suffix
    if court == "ADO":
        m = re.match(r"(\d{4})-(\d+)$", stem)
        if m:
            return [], ["AD %s nr %s" % m.groups()]
    return [stem], []


def keys(court, malnummer, referat):
    out = {("M", court, norm_malnr(m)) for m in malnummer if norm_malnr(m)}
    out |= {("R", norm_referat(r)) for r in referat if norm_referat(r)}
    return out


def scan_api(domstoldir):
    records = []
    for path in sorted(Path(domstoldir).rglob("*.json")):
        d = json.loads(path.read_text())
        court = canonical_court(d["domstol"]["domstolKod"])
        records.append({
            "store": "domstol", "court": court, "path": str(path),
            "uuid": d["id"],
            "malnummer": [m.strip() for m in d.get("malNummerLista", [])],
            "referat": [r.strip() for r in d.get("referatNummerLista", [])],
            "avgorandedatum": d.get("avgorandedatum"),
            "has_innehall": bool(d.get("innehall")),
            "bilagor": len(d.get("bilagaLista", [])),
        })
    return records


def scan_legacy(dvdir):
    records, unrecognized = [], []
    for path in sorted(Path(dvdir).rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".doc", ".docx"):
            continue
        court = canonical_court(path.parent.name)
        malnummer, referat = legacy_identity(path.parent.name, path.name)
        if malnummer is None:
            unrecognized.append(str(path))
            continue
        records.append({"store": "dv", "court": court, "path": str(path),
                        "malnummer": malnummer, "referat": referat})
    return records, unrecognized


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
    key_owner = {}  # linkage key -> a representative record index
    for i, rec in enumerate(records):
        uf.find(i)
        for key in keys(rec["court"], rec["malnummer"], rec["referat"]):
            if key in key_owner:
                uf.union(i, key_owner[key])
            else:
                key_owner[key] = i

    groups = defaultdict(list)
    for i, rec in enumerate(records):
        groups[uf.find(i)].append(rec)

    cases = []
    for members in groups.values():
        malnummer = dedup_malnr(m for r in members for m in r["malnummer"])
        referat = dedup(r for rec in members for r in rec["referat"])
        courts = sorted({r["court"] for r in members})
        dates = sorted({r["avgorandedatum"] for r in members
                        if r.get("avgorandedatum")})
        stores = sorted({r["store"] for r in members})
        cases.append({
            "canonical_id": canonical_id(courts, malnummer, referat),
            "courts": courts,
            "malnummer": malnummer,
            "referat": referat,
            "avgorandedatum": dates[0] if dates else None,
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


INDEX_DEFAULT = "site/data/dv/identity-index.json"


def reindex(dvdir=DV_DEFAULT, domstoldir=DOMSTOL_DEFAULT, out=INDEX_DEFAULT):
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
    Path(out).write_text(json.dumps(cases, ensure_ascii=False, indent=2))
    report(cases, unrecognized)
    print("index written to %s" % out)
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("dvdir", nargs="?", default=DV_DEFAULT)
    parser.add_argument("domstoldir", nargs="?", default=DOMSTOL_DEFAULT)
    parser.add_argument("--out", default=INDEX_DEFAULT)
    args = parser.parse_args()
    reindex(args.dvdir, args.domstoldir, args.out)


if __name__ == "__main__":
    main()
