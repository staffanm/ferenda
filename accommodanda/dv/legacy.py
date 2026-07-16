"""Parse a legacy court decision (Word .doc/.docx) into the Avgorande model.

Consumes the flat (text, bold) paragraph stream from word.read() and
splits it into header / metadata / body / footer the way the documents are
laid out: a bold court name and referat at the top, bold "Label:" / value
pairs in a metadata table, a bold "REFERAT" marker, the body, and bold
"Sökord:" / "Litteratur:" in the footer.

Identity (canonical referat, court) is supplied by the identity index;
everything the index lacks for legacy-only cases -- avgörandedatum,
målnummer, and the curated fields (Rubrik→sammanfattning, Lagrum,
Rättsfall, Sökord) -- is recovered here from the document itself.

  python -m accommodanda.dv.legacy FILE                 # one Word file -> artifact
  python -m accommodanda.dv.legacy --index INDEX [--limit N]  # batch + report
"""

import argparse
import functools
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import TypedDict

from ..lib import layout, util
from . import word
from .identity import bundle_identity
from .model import Avgorande, Lagrum, Rubrik, Stycke
from .parse import RE_NUMPARA, is_heading, to_artifact

# Footer labels that end the body region.
_FOOTER_LABELS = {"Sökord", "Litteratur"}

_MONTHS = {
    "januari": 1, "februari": 2, "mars": 3, "april": 4,
    "maj": 5, "juni": 6, "juli": 7, "augusti": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}
_HDO_NOTIS_START = re.compile(
    r"(?:Den\s+(?P<day>\d+)\s*:[ae]\.\s+)?"
    r"(?:(?P<ordinal>\d+)\.\s*)?"
    r"\((?P<malnr>\w[ \xa0]\d+-\d+)\)\s*"
    r"(?P<title>.*)",
    re.I,
)
_HDO_NOTIS_NUMBER = re.compile(r"Nr\s+(?P<ordinal>\d+)$", re.I)
_ADMIN_NOTIS_START = re.compile(
    r".*Lnr:(?:RÅ|HFD|REG)?\s*\d{4}\s*not\s*"
    r"(?P<ordinal>\d(?:\s*\d)*)(?=\s+G:|\s*$)",
    re.I,
)
_ADMIN_DISPLAY = re.compile(r"Not(?:is)?\.?\s*\d+[a-c]?\.?\s*", re.I)
_HFD_MODERN_START = re.compile(r"Not\s+(?P<ordinal>\d+)$", re.I)
_HFD_MODERN_HEAD = re.compile(
    r"Högsta förvaltningsdomstolen meddelade(?: den)?\s+"
    r"(?P<day>\d+)\s+(?P<month>\w+)\s+(?P<year>\d{4})\s+"
    r"(?:följande\s+)?(?P<typ>dom|beslut)\s+"
    r"\((?:mål nr\s+)?(?P<malnr>[\d\-–]+(?:\s+och\s+[\d\-–]+)*)\)\.?",
    re.I,
)


class _NotisChunk(TypedDict):
    ordinal: int
    month: int | None
    day: int | None
    paras: list[word.Para]


class _NotisBundleIndex(TypedDict):
    path: str
    size: int
    sha256: str
    court: str
    year: int
    first: int
    last: int
    ordinals: list[int]


def parse_head_body(paras):
    """list[Para] -> (head dict, list[Para] body). Values are lists of strings."""
    head = {}
    body = []
    section = "header"
    label = None
    for p in paras:
        text = p.text
        if section == "header":
            if not text:
                continue
            if "|" in text and "Domstol" not in head:
                parts = [x.strip() for x in text.split("|") if x.strip()]
                head["Domstol"], head["Referat"] = parts[0], parts[1]
                section = "meta"
            elif "Domstol" not in head:
                head["Domstol"] = text
            elif p.bold and text.endswith(":"):
                # A few verdict-only publications have no referat heading.  In
                # those files the metadata table follows the court name
                # directly; treating its first label as a referat loses both
                # the label and the actual målnummer value.
                section = "meta"
                label = text[:-1].strip()
                head.setdefault(label, [])
            else:
                head["Referat"] = text
                section = "meta"
            continue
        if text == "REFERAT":
            section = "body"
            label = None
            continue
        if section in ("meta", "footer"):
            if not text:
                continue
            if p.bold and text.endswith(":"):
                label = text[:-1].strip()
                head.setdefault(label, [])
            elif label is not None:
                head[label].append(text)
            continue
        if section == "body":
            stripped = text.rstrip(":")
            if p.bold and stripped in _FOOTER_LABELS and text.endswith(":"):
                section = "footer"
                label = stripped
                head.setdefault(label, [])
            elif text:
                body.append(p)
    return head, body


def _classify(p):
    """A body Para -> Rubrik or Stycke."""
    m = RE_NUMPARA.match(p.text)
    if m:
        return Stycke(text=m.group(2).strip(), ordinal=m.group(1))
    if (p.bold and len(p.text) <= 80) or is_heading(p.text):
        return Rubrik(text=p.text)
    return Stycke(text=p.text)


# "Ö 2475-12" and "PMT 7498-16" are one målnummer, not a series token plus a
# number.  Other courts list several målnummer separated by comma, semicolon,
# whitespace or "och".
_MALNR_SPLIT = re.compile(r"\s+och\s+|[,;]|\s+")


_MALNR_PREFIX = re.compile(r"^[A-ZÅÄÖ]+$", re.I)
_MALNR_NUMBER = re.compile(r"^\d+(?:[-/]\d+)+$")


def _split_malnummer(value):
    # Split first, then re-glue any alphabetic series token to the following
    # numeric token.  The series vocabulary is open (A, Ö, PMT, PMÖÄ, ...), so
    # enumerating three historically common prefixes silently corrupts newer
    # Patent- och marknadsöverdomstolen identities.
    parts = [x.strip() for x in _MALNR_SPLIT.split(value) if x.strip()]
    merged, i = [], 0
    while i < len(parts):
        if (i + 1 < len(parts) and _MALNR_PREFIX.fullmatch(parts[i])
                and _MALNR_NUMBER.fullmatch(parts[i + 1])):
            merged.append(parts[i] + parts[i + 1])
            i += 2
        else:
            merged.append(parts[i])
            i += 1
    return merged


def _first(head, key):
    vals = head.get(key)
    return vals[0] if vals else None


def _split_referat(value):
    """Split the old header's ``page (löpnummer)`` compound identity."""
    if not value:
        return []
    match = re.fullmatch(
        r"(.+?)\s+\(\s*([A-ZÅÄÖ]{1,4}\s+\d{4}(?::|\s).+?)\s*\)", value)
    return [part.strip() for part in match.groups()] if match else [value]


def parse_legacy_file(path, case=None):
    """A legacy Word file -> Avgorande. `case` is the identity-index entry,
    used for canonical referat/court/málnummer when present."""
    path = Path(path)
    if bundle_identity(path.name):
        return parse_notis_bundle(path, case)
    if not path.stat().st_size and case:
        return parse_notis_bundle(_bundle_for_placeholder(path), case)
    head, body = parse_head_body(word.read(path))
    return build_avgorande(head, body, case, sources=["dv"])


def _bundle_for_placeholder(path):
    match = re.match(r"(\d{4})_not_(\d+)\.docx?$", path.name, re.I)
    assert match, "%s: zero-byte legacy file is not a notis placeholder" % path
    year, ordinal = (int(value) for value in match.groups())
    candidates = []
    for candidate in layout.DV_NOTIS_BUNDLES.glob(
            "%s/%d/*.doc*" % (path.parent.name, year)):
        identity = bundle_identity(candidate.name)
        if identity and identity[2] <= ordinal <= identity[3]:
            candidates.append(
                (identity[3] - identity[2], candidate.name, candidate))
    assert candidates, "%s: no collection Word file covers this placeholder" % path
    return min(candidates)[2]


def _split_notis_paras(court, year, paras):
    """Split one collection-file paragraph stream into ordinal chunks."""
    chunks: dict[int, list[_NotisChunk]] = {}
    current: _NotisChunk | None = None
    month = day = None
    pending_ordinal = None
    start_re = (_HDO_NOTIS_START if court == "HDO" else
                _HFD_MODERN_START if court == "HFD" and year >= 2016 else
                _ADMIN_NOTIS_START)
    for para in paras:
        text = para.text.strip()
        if court == "HDO" and text.lower() in _MONTHS:
            month = _MONTHS[text.lower()]
            continue
        number = _HDO_NOTIS_NUMBER.match(text) if court == "HDO" else None
        if number:
            if current:
                chunks.setdefault(current["ordinal"], []).append(current)
                current = None
            pending_ordinal = int(number.group("ordinal"))
            continue
        match = start_re.match(text)
        ordinal = (match.groupdict().get("ordinal") if match else None)
        ordinal = int(re.sub(r"\s+", "", ordinal)) if ordinal else pending_ordinal
        if match and ordinal is not None:
            if current:
                chunks.setdefault(current["ordinal"], []).append(current)
            if court == "HDO" and match.groupdict().get("day"):
                day = int(match.group("day"))
            current = {
                "ordinal": ordinal,
                "month": month,
                "day": day,
                "paras": [para],
            }
            pending_ordinal = None
        elif current:
            current["paras"].append(para)
    if current:
        chunks.setdefault(current["ordinal"], []).append(current)
    return chunks


@functools.cache
def _read_notis_bundle(path):
    identity = bundle_identity(Path(path).name)
    assert identity, "%s is not a recognized notis bundle" % path
    court, year, _, _ = identity
    chunks = _split_notis_paras(court, year, word.read(path))
    assert chunks, "%s contains no recognizable notiser" % path
    return chunks


def _case_notis_ordinal(case):
    assert case, "a collection Word file requires a canonical notis identity"
    ordinals = {
        int(match.group(1))
        for referat in case.get("referat", [])
        if (match := re.search(r"\bnot\s+(\d+)$", referat, re.I))
    }
    assert len(ordinals) == 1, "%s: cannot determine one notis ordinal" % case
    return ordinals.pop()


def _short_summary(text):
    if ". -" in text:
        return text.split(". -", 1)[0].strip() + "."
    match = re.search(r"\.\s+[A-ZÅÄÖ]", text)
    return text[:match.start() + 1] if match else text


def _parse_hdo_notis(chunk, year):
    first, *rest = chunk["paras"]
    match = _HDO_NOTIS_START.match(first.text)
    assert match, "HDO notis chunk has no start marker"
    title = match.group("title").strip()
    head = {
        "Domstol": "Högsta domstolen",
        "Målnummer": [match.group("malnr")],
    }
    if title:
        head["Rubrik"] = [_short_summary(title) if year == 2003 else title]
        rest.insert(0, word.Para(title, False, first.in_table))
    if chunk["month"] and chunk["day"]:
        head["Avgörandedatum"] = ["%d-%02d-%02d" % (
            year, chunk["month"], chunk["day"])]
    return head, [para for para in rest if para.text]


def _glue_wrapped(paras):
    """Join the visual line-paragraphs used by old REG/HFD binary Word files."""
    out = []
    parts = []
    for para in paras:
        text = para.text.strip()
        if not text:
            continue
        parts.append(text)
        if para.bold or re.search(r"[.!?)]$", text):
            out.append(word.Para(" ".join(parts), para.bold, para.in_table))
            parts = []
    if parts:
        out.append(word.Para(" ".join(parts), False, False))
    return out


def _iso_short_date(year, month, day):
    year = int(year)
    if year < 100:
        year += 1900 if year >= 50 else 2000
    return "%04d-%02d-%02d" % (year, int(month), int(day))


def _parse_old_admin_notis(chunk, court):
    paras = [para for para in chunk["paras"] if para.text]
    display = next((i for i, para in enumerate(paras)
                    if _ADMIN_DISPLAY.match(para.text)), None)
    assert display is not None, "%s not %d has no display heading" % (
        court, chunk["ordinal"])
    header_text = "\n".join(para.text for para in paras[:display])
    body = _glue_wrapped(paras[display:])
    match = _ADMIN_DISPLAY.match(body[0].text)
    assert match, "glued notis body lost its display heading"
    body[0] = word.Para(body[0].text[match.end():].strip(), False,
                        body[0].in_table)
    title = _short_summary(body[0].text)
    head = {
        "Domstol": ("Regeringsrätten" if court == "REG"
                    else "Högsta förvaltningsdomstolen"),
        "Rubrik": [title],
    }
    malnummer = re.search(r"\bD:\s*(\d+)\s+(\d{4})\b", header_text)
    if not malnummer:
        malnummer = re.search(r"\b[AD][:-]\s*(\d+)[- ](\d{2,4})\b",
                              header_text)
    if malnummer:
        head["Målnummer"] = ["%s-%s" % malnummer.groups()]
    avgdatum = re.search(r"\bA:\s*(\d{2,4})[- ](\d{2})[- ](\d{2})\b",
                         header_text)
    if avgdatum:
        head["Avgörandedatum"] = [_iso_short_date(*avgdatum.groups())]
    sokord = re.search(r"Uppslagsord:\s*(.*)", header_text)
    if sokord:
        head["Sökord"] = [
            value.strip() for value in sokord.group(1).split(";")
            if value.strip()
        ]
    lagrum = re.search(r"Lagrum:\s*\n?([^\n]+)", header_text)
    if lagrum and lagrum.group(1).strip().lower() != "ej angivet":
        head["Lagrum"] = [
            value.strip()
            for value in re.split(r"[;,]\s*", lagrum.group(1))
            if value.strip()
        ]
    return head, [para for para in body if para.text]


def _parse_modern_hfd_notis(chunk):
    paras = [para for para in chunk["paras"][1:] if para.text]
    assert paras, "modern HFD notis has no body"
    matches = [
        (i, match)
        for i, para in enumerate(paras)
        if (match := _HFD_MODERN_HEAD.match(para.text))
    ]
    assert matches, "modern HFD notis has an unrecognized decision heading"
    match = matches[0][1]
    dates = {
        (item.group("year"), item.group("month").lower(), item.group("day"))
        for _, item in matches
    }
    assert len(dates) == 1, "one HFD notis contains different decision dates"
    month = _MONTHS[match.group("month").lower()]
    malnummer = [
        value.strip().replace("–", "-")
        for _, item in matches
        for value in re.split(r"\s+och\s+", item.group("malnr"))
    ]
    head = {
        "Domstol": "Högsta förvaltningsdomstolen",
        "Målnummer": malnummer,
        "Avgörandedatum": ["%s-%02d-%02d" % (
            match.group("year"), month, int(match.group("day")))],
        "Rubrik": ["%s den %s %s %s i mål %s" % (
            match.group("typ").capitalize(), match.group("day"),
            match.group("month").lower(), match.group("year"),
            " och ".join(malnummer))],
    }
    heading_indexes = {i for i, _ in matches}
    return head, [para for i, para in enumerate(paras)
                  if i not in heading_indexes]


def parse_notis_bundle(path, case):
    """One canonical case from a multi-notis Word collection file."""
    identity = bundle_identity(Path(path).name)
    assert identity, "%s is not a notis collection file" % path
    court, year, _, _ = identity
    ordinal = _case_notis_ordinal(case)
    chunks = _read_notis_bundle(Path(path))
    assert ordinal in chunks, "%s does not contain notis %d" % (path, ordinal)
    parsed = []
    for chunk in chunks[ordinal]:
        if court == "HDO":
            parsed.append(_parse_hdo_notis(chunk, year))
        elif court == "HFD" and year >= 2016:
            parsed.append(_parse_modern_hfd_notis(chunk))
        else:
            parsed.append(_parse_old_admin_notis(chunk, court))
    head, body = parsed[0]
    for extra_head, extra_body in parsed[1:]:
        if extra_head.get("Rubrik"):
            body.append(word.Para(extra_head["Rubrik"][0], True, False))
        body.extend(extra_body)
        for key in ("Målnummer", "Sökord", "Lagrum"):
            if extra_head.get(key):
                head.setdefault(key, [])
                head[key].extend(value for value in extra_head[key]
                                 if value not in head[key])
    return build_avgorande(head, body, case, sources=["dv"])


def write_notis_index(root, identities):
    """Write the exact placeholder-to-bundle identity inventory.

    Bundle filename ranges are only approximate: withdrawn numbers and two
    shifted REG ranges make them unsafe as identities. The old per-case
    placeholders are the exact publication ledger; the headings parsed from the
    Word files prove which bundle actually contains each ledger entry.
    """
    root = Path(root)
    actual = {}
    bundles: list[_NotisBundleIndex] = []
    for path in sorted(root.rglob("*.doc*")):
        court, year, first, last = bundle_identity(path.name)
        ordinals = sorted(_read_notis_bundle(path))
        relpath = path.relative_to(root).as_posix()
        for ordinal in ordinals:
            actual.setdefault((court, year, ordinal), []).append(relpath)
        bundles.append({
            "path": relpath,
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "court": court,
            "year": year,
            "first": first,
            "last": last,
            "ordinals": ordinals,
        })
    identities = set(identities)
    missing = identities - set(actual)
    assert not missing, "%d notis placeholders lack a Word bundle: %s" % (
        len(missing), sorted(missing)[:20])
    payload = {
        "version": 1,
        "placeholder_count": len(identities),
        "bundles": [
            {**bundle,
             "ordinals": [ordinal for ordinal in bundle["ordinals"]
                          if (bundle["court"], bundle["year"], ordinal)
                          in identities]}
            for bundle in bundles
        ],
    }
    util.write_atomic(root / "index.json", json.dumps(
        payload, ensure_ascii=False, indent=2).encode())
    return len(identities), len(set(actual) - identities)


def write_direct_index(root):
    """Parse and inventory every non-empty, per-case legacy Word original.

    Filenames usually carry a målnummer, but some carry an opaque local name
    while the header identifies an API-backed referat. Persisting the extracted
    identity beside the Word bytes keeps ordinary reindex runs JVM-free and
    makes any changed/missing original fail hash validation.
    """
    root = Path(root)
    documents = []
    for path in sorted(root.rglob("*.doc*")):
        if (not path.stat().st_size or
                path.is_relative_to(root / "notis-bundles")):
            continue
        avgorande = parse_legacy_file(path)
        documents.append({
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "court": path.parent.name,
            "malnummer": avgorande.malnummer,
            "referat": avgorande.referat,
            "avgorandedatum": avgorande.avgorandedatum,
        })
    payload = {"version": 1, "document_count": len(documents),
               "documents": documents}
    util.write_atomic(root / layout.DV_LEGACY_INDEX.name, json.dumps(
        payload, ensure_ascii=False, indent=2).encode())
    return len(documents)


def build_avgorande(head, body, case=None, sources=None):
    """Map an extracted (head, body) onto Avgorande, preferring the identity
    index's canonical identity fields over the document's where present."""
    case = case or {}

    referat = case.get("referat") or _split_referat(head.get("Referat"))
    if (head.get("Domstol", "").casefold() == "arbetsdomstolen"
            and len(referat) == 1
            and re.fullmatch(r"\d{4}\s+nr\s+\d+", referat[0], re.I)):
        # One old AD original (2016 nr 10) omits the series in its displayed
        # header.  The publisher identifies the otherwise complete referat;
        # retaining the bare text would mint /dom/2016_nr_10 instead of the old
        # public /dom/ad/2016:10 identity.
        referat = ["AD " + referat[0]]
    malnummer = case.get("malnummer")
    if not malnummer:
        malnummer = [
            item
            for value in head.get("Målnummer", [])
            for item in _split_malnummer(value)
        ]
    sokord = head.get("Sökord") or []
    if len(sokord) == 1:
        sokord = [s.strip() for s in re.split(r"[;,]", sokord[0]) if s.strip()]

    return Avgorande(
        court=(case.get("courts") or [None])[0] or head.get("Domstol"),
        court_namn=head.get("Domstol"),
        malnummer=malnummer,
        referat=referat,
        avgorandedatum=case.get("avgorandedatum") or _first(head, "Avgörandedatum"),
        publiceringsform=None,
        typ=None,
        rattsomrade=[],
        nyckelord=sokord,
        lagrum=[Lagrum(referens=l) for l in head.get("Lagrum", [])],
        forarbeten=[],
        sammanfattning=" ".join(head.get("Rubrik", [])) or None,
        related=head.get("Rättsfall", []),
        body=[_classify(p) for p in body],
        sources=sources or [],
    )


def legacy_original(case):
    """The preferred non-empty legacy original, or None.

    A notis identity can have both its zero-byte historical placeholder and one
    or more shared collection files. Prefer the narrowest collection range so
    overlapping update zips do not make source choice depend on path order.
    """
    originals = []
    for member in case["members"]:
        if member["store"] != "dv":
            continue
        path = util.load_relpath(layout.DATA, member["path"])
        if path.exists() and path.stat().st_size:
            span = member.get("bundle_last", 0) - member.get("bundle_first", 0)
            originals.append((span, member["path"], member))
    return min(originals)[2] if originals else None


def legacy_member(case):
    """The preferred original, else the first zero-byte notisfall placeholder.

    A missing collection file must remain visible as a parse error; silently
    removing that identity would make a successful run overstate DV coverage.
    """
    return legacy_original(case) or next(
        (member for member in case["members"] if member["store"] == "dv"), None)


def cmd_index(args):
    cases = json.loads(Path(args.index).read_text())
    cases = [(c, legacy_original(c)) for c in cases]
    cases = [(c, m) for c, m in cases if m]
    if args.limit:
        cases = cases[:args.limit]
    counts = Counter()
    blockstats = Counter()
    failures = []
    for case, member in cases:
        try:
            av = parse_legacy_file(util.load_relpath(layout.DATA, member["path"]), case)
        except Exception as e:  # noqa: BLE001 — stats harness: failure tallied, corpus scan continues (rule:no-catch-log-continue)
            failures.append((case["canonical_id"], "%s: %s" % (type(e).__name__, e)))
            continue
        counts["parsed"] += 1
        blockstats["rubrik"] += sum(isinstance(b, Rubrik) for b in av.body)
        blockstats["stycke"] += sum(isinstance(b, Stycke) for b in av.body)
        if not av.body:
            counts["empty_body"] += 1
    print("%d legacy cases with an original: %d parsed, %d empty body, %d failed"
          % (len(cases), counts["parsed"], counts["empty_body"], len(failures)))
    print("blocks: %d rubrik, %d stycke" % (blockstats["rubrik"], blockstats["stycke"]))
    for cid, err in failures[:20]:
        print("  FAIL %s: %s" % (cid, err))


def cmd_file(args):
    av = parse_legacy_file(args.file)
    cid = av.referat[0] if av.referat else None
    json.dump(to_artifact(av, cid), sys.stdout, ensure_ascii=False, indent=2)
    print()


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("file", nargs="?")
    parser.add_argument("--index")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.index:
        cmd_index(args)
    elif args.file:
        cmd_file(args)
    else:
        parser.error("need FILE or --index")


if __name__ == "__main__":
    main()
