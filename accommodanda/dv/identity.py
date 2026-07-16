"""Identity indexer for court decisions (DV).

Reconciles the two raw DV stores into one canonical identity per real
legal case, so the downstream parser can merge representations instead of
emitting the same case twice under two unlinked ids:

  site/data/downloaded/dv/{COURT}/*.doc(x)   legacy feed (Word originals)
  site/data/downloaded/dom/{COURT}/*.json      new courts' API (JSON)

This is entity resolution, not a winner-takes-all fallback: every source
record is kept and attached to its canonical case, and identity agreement
is *manufactured* here (the sources do not natively share ids -- UUIDs vs
filename-derived ids, and differing court codes) rather than assumed.

Linkage keys (a case is the connected component over shared keys):

- referatnummer, global: ("R", normalized_referat) -- always strong;
- målnummer, court-scoped: ("M", canonical_court, normalized_malnr) -- only
  cross-store when it identifies one API and one legacy component.

Målnummer is not unique over time even within one court: AD 1993 nr 22 and AD
1994 nr 13 are distinct published decisions under A 112-92. Therefore neither
API nor legacy records are fused solely by M. Legacy attachment variants are
recognized by their shared filename stem; after grouping those, M links legacy
to API only when the match is unambiguous.

The API records carry explicit malNummerLista/referatNummerLista, so their keys
are reliable. Legacy direct identity comes from the hash-checked parsed-header
sidecar when present; filename reconstruction remains the bootstrap used by the
bounded importer and the compatibility path for a tree without that sidecar.
For almost every court the stem is a målnummer. ADO encodes the *referat* in the
filename; zero-byte notis identities come from the exact bundle ledger:

- ADO  1993-100      -> "AD 1993 nr 100"
- HDO/HFD/REG  2003_not_1 -> the matching NJA/HFD/RÅ notis identity

Error modes are asymmetric and both reported: under-linking leaves the same
case as two single-source components (a duplicate downstream); over-linking
fuses distinct cases (an unexpected component spanning >1 court is flagged;
the known 2011 MOD/MMOD succession is counted separately).

Rebuilt from the records already on disk (no network) via `lagen dv reindex`.
"""

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..lib import compress, layout, util

# legacy court dir code -> code used by the new API (others are identical)
COURT_CANON = {"REG": "REGR", "MÖD": "MOD", "MMD": "MMOD",
               "MIG": "MIOD", "PMD": "PMOD", "HYOD": "HSV"}

_NOTIS_REFERAT = {"HDO": "NJA", "HFD": "HFD", "REG": "RÅ"}
_NOTIS_BUNDLE = re.compile(
    r"^(HDO|HFD|REG)_(\d{4})_notis_\s*[A-Z]?(\d+)"
    r"(?:-+[A-Z]?(\d+))?\.docx?$",
    re.I,
)


def canonical_court(code):
    return COURT_CANON.get(code, code)


def norm_malnr(s):
    return re.sub(r"\s+", "", s).upper()


def norm_referat(s):
    return "".join(c for c in s.upper() if c.isalnum())


def notis_referat(court, year, ordinal):
    return "%s %s not %s" % (_NOTIS_REFERAT[court], year, ordinal)


def bundle_identity(filename):
    """A collection-file name -> ``(court, year, first, last)``, or None."""
    match = _NOTIS_BUNDLE.match(Path(filename).name)
    if not match:
        return None
    court, year, first, last = match.groups()
    return court.upper(), int(year), int(first), int(last or first)


def legacy_identity(court, filename):
    """(malnummer, referat) lists derived from a legacy filename, or
    (None, None) if it is not a recognizable case document."""
    stem = re.sub(r"\.(docx?)$", "", filename, flags=re.I)
    if stem == filename:  # not a .doc/.docx
        return None, None
    # Notisfall: the _N here is the published notis number, not an attachment.
    # This applies to all three collections; treating REG/HFD as a normal stem
    # collapses every notis in one year when the generic variant suffix below
    # strips its ordinal.
    m = re.match(r"(\d{4})_not_(\d+)(?:_\d+)?$", stem)
    if court in _NOTIS_REFERAT and m:
        return [], [notis_referat(court, *m.groups())]
    stem = re.sub(r"_\d+$", "", stem)  # drop attachment-variant suffix
    if court == "ADO":
        m = re.match(r"(\d{4})-(\d+)$", stem)
        if m:
            return [], ["AD %s nr %s" % m.groups()]
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
        semantic = {
            key: value for key, value in d.items()
            if key not in {
                "id", "gruppKorrelationsnummer", "publiceringstid", "bilagaLista",
            }
        }
        semantic["bilagaFilnamn"] = sorted(
            item["filnamn"] for item in d.get("bilagaLista", []))
        records.append({
            "store": "domstol", "court": court,
            "path": util.store_relpath(path, layout.DATA),
            "uuid": d["id"],
            "malnummer": [m.strip() for m in d.get("malNummerLista", [])],
            "referat": [r.strip() for r in d.get("referatNummerLista", [])],
            "avgorandedatum": d.get("avgorandedatum"),
            "has_innehall": bool(d.get("innehall")),
            "bilagor": len(d.get("bilagaLista", [])),
            "semantic_fingerprint": hashlib.sha256(json.dumps(
                semantic, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        })
    return records


def scan_legacy(dvdir):
    records, unrecognized = [], []
    dvdir = Path(dvdir)
    direct = {}
    direct_index = dvdir / layout.DV_LEGACY_INDEX.name
    if direct_index.exists():
        payload = json.loads(direct_index.read_text())
        assert payload["version"] == 1, \
            "%s has an unsupported version" % direct_index
        assert payload["document_count"] == len(payload["documents"]), \
            "%s has the wrong document count" % direct_index
        for item in payload["documents"]:
            path = dvdir / item["path"]
            assert item["path"] not in direct, \
                "%s repeats %s" % (direct_index, item["path"])
            assert path.is_file(), "%s lists missing original %s" % (
                direct_index, path)
            assert path.stat().st_size == item["size"], \
                "%s changed size; rebuild %s" % (path, direct_index)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], \
                "%s changed content; rebuild %s" % (path, direct_index)
            direct[item["path"]] = {
                "store": "dv", "court": canonical_court(item["court"]),
                "path": util.store_relpath(path, layout.DATA),
                "malnummer": item["malnummer"], "referat": item["referat"],
                "avgorandedatum": item["avgorandedatum"],
            }
        actual = {
            path.relative_to(dvdir).as_posix()
            for path in dvdir.rglob("*.doc*")
            if path.stat().st_size and
            not path.is_relative_to(dvdir / "notis-bundles")
        }
        assert set(direct) == actual, \
            "%s does not cover the current direct Word originals" % direct_index
    bundle_dir = dvdir / "notis-bundles"
    if bundle_dir.exists():
        index_path = bundle_dir / "index.json"
        assert index_path.is_file(), \
            "%s exists but its exact notis index is missing" % bundle_dir
        index = json.loads(index_path.read_text())
        assert index["version"] == 1, "%s has an unsupported version" % index_path
        for bundle in index["bundles"]:
            path = bundle_dir / bundle["path"]
            assert path.is_file(), "%s lists missing bundle %s" % (
                index_path, path)
            assert path.stat().st_size == bundle["size"], \
                "%s changed size; rebuild %s" % (path, index_path)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == bundle["sha256"], \
                "%s changed content; rebuild %s" % (path, index_path)
            for ordinal in bundle["ordinals"]:
                records.append({
                    "store": "dv",
                    "court": canonical_court(bundle["court"]),
                    "path": util.store_relpath(path, layout.DATA),
                    "malnummer": [],
                    "referat": [notis_referat(
                        bundle["court"], bundle["year"], ordinal)],
                    "bundle_ordinal": ordinal,
                    "bundle_first": bundle["first"],
                    "bundle_last": bundle["last"],
                })
    for path in sorted(dvdir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".doc", ".docx"):
            continue
        # Collection files were expanded from their parsed headings through the
        # exact placeholder ledger above; never manufacture identities from the
        # approximate range printed in their filenames.
        if path.relative_to(dvdir).parts[0] == "notis-bundles":
            continue
        relpath = path.relative_to(dvdir).as_posix()
        if relpath in direct:
            records.append(direct[relpath])
            continue
        court = canonical_court(path.parent.name)
        malnummer, referat = legacy_identity(path.parent.name, path.name)
        if malnummer is None:
            unrecognized.append(str(path))
            continue
        records.append({"store": "dv", "court": court,
                        "path": util.store_relpath(path, layout.DATA),
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


def legacy_attachment_family(record):
    """The source-file identity shared by ``foo.doc``/``foo_1.doc`` variants.

    A målnummer is not an attachment identity: one proceeding can produce
    several separately published decisions.  The old feed's numeric suffix is
    the narrower evidence that two files are variants.  Bundle records are
    deliberately excluded because many notis identities share one collection
    file and are distinguished by ``bundle_ordinal`` instead.
    """
    if (record["store"] != "dv" or "bundle_ordinal" in record
            or not record["malnummer"]):
        return None
    path = Path(record["path"])
    return record["court"], path.parent.as_posix(), re.sub(r"_\d+$", "", path.stem)


def build_index(api_records, legacy_records):
    records = api_records + legacy_records
    uf = UnionFind()
    referat_owner = {}  # strong R key -> a representative record index
    semantic_owner = {}  # exact API republication -> representative record
    attachment_owner = {}  # exact legacy source-file family -> representative
    malnummer_records = defaultdict(list)
    for i, rec in enumerate(records):
        uf.find(i)
        fingerprint = rec.get("semantic_fingerprint")
        if fingerprint:
            if fingerprint in semantic_owner:
                uf.union(i, semantic_owner[fingerprint])
            else:
                semantic_owner[fingerprint] = i
        for key in keys(rec["court"], rec["malnummer"], rec["referat"]):
            if key[0] == "R":
                if key in referat_owner:
                    uf.union(i, referat_owner[key])
                else:
                    referat_owner[key] = i
            else:
                malnummer_records[key].append(i)
        family = legacy_attachment_family(rec)
        if family in attachment_owner:
            uf.union(i, attachment_owner[family])
        elif family is not None:
            attachment_owner[family] = i

    component_referat = defaultdict(set)
    for i, rec in enumerate(records):
        component_referat[uf.find(i)].update(
            norm_referat(value) for value in rec["referat"])

    for indices in malnummer_records.values():
        # M is a cross-store bridge only when it is unambiguous after the strong
        # R links and legacy attachment grouping. Distinct API components with
        # or legacy components with the same M stay distinct; guessing would
        # publish one decision's text under another referat's URI.
        api_roots = {uf.find(i) for i in indices
                     if records[i]["store"] == "domstol"}
        legacy_roots = {uf.find(i) for i in indices
                        if records[i]["store"] == "dv"}
        if len(api_roots) == len(legacy_roots) == 1:
            api_root, legacy_root = next(iter(api_roots)), next(iter(legacy_roots))
            if api_root == legacy_root:
                continue
            # If both components already publish different referat identifiers,
            # M is weaker contradictory evidence, not a bridge.  This occurs in
            # RH 2016:61/62: one API record lists both case numbers under :62,
            # while the old feed correctly publishes one decision per referat.
            if (component_referat[api_root] and component_referat[legacy_root]
                    and component_referat[api_root].isdisjoint(
                        component_referat[legacy_root])):
                continue
            uf.union(api_root, legacy_root)
            root = uf.find(legacy_root)
            component_referat[root] = (
                component_referat.pop(api_root)
                | component_referat.pop(legacy_root, set()))

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
            "canonical_id": canonical_id(courts, malnummer, referat, dates),
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


def canonical_id(courts, malnummer, referat, dates=()):
    if referat:
        return referat[0]
    court = courts[0] if courts else "?"
    identity = "%s %s" % (court, malnummer[0]) if malnummer else court
    # A målnummer identifies the proceeding, not one ruling: courts can publish
    # several decisions under it months apart. The old verdict URI likewise
    # includes avgörandedatum. Legacy filenames carry no date until parse, so
    # undated legacy-only records retain the best identity actually available.
    return "%s %s" % (identity, dates[0]) if dates else identity


def report(cases, unrecognized):
    by_sources = Counter(tuple(c["sources"]) for c in cases)
    linked = by_sources[("domstol", "dv")]
    # The 2011 MÖD series straddles the institutional rename from
    # Miljööverdomstolen to Mark- och miljööverdomstolen. The same published
    # referat can therefore legitimately have one MOD and one MMOD source
    # record; keep the provenance codes but report it separately from an
    # unexpected cross-court union.
    successions = [c for c in cases
                   if set(c["courts"]) == {"MMOD", "MOD"}]
    multi_court = [c for c in cases if len(c["courts"]) > 1
                   and set(c["courts"]) != {"MMOD", "MOD"}]
    multi_file = sum(1 for c in cases if len(c["members"]) > 1)
    print("%d canonical cases" % len(cases))
    print("  both sources (linked): %d" % linked)
    print("  domstol (API) only:    %d" % by_sources[("domstol",)])
    print("  dv (legacy) only:      %d" % by_sources[("dv",)])
    print("  multi-record cases:    %d" % multi_file)
    print("  unrecognized legacy files: %d" % len(unrecognized))
    print("  expected MOD/MMOD succession links: %d" % len(successions))
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
    Reads API metadata plus the validated legacy identity sidecars -- no Word
    parsing during an ordinary reindex."""
    api_records = scan_api(domstoldir)
    legacy_records, unrecognized = scan_legacy(dvdir)
    print("scanned %d API records, %d legacy records (%d unrecognized)"
          % (len(api_records), len(legacy_records), len(unrecognized)))
    cases = build_index(api_records, legacy_records)
    cases.sort(key=lambda c: (c["avgorandedatum"] or "", c["canonical_id"]))
    util.write_atomic(out, json.dumps(cases, ensure_ascii=False, indent=2))
    report(cases, unrecognized)
    print("index written to %s" % out)
    return cases
