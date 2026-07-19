"""Parse a legacy court decision into the Avgorande model.

Two legacy body formats, one entry point (`parse_legacy_file`):

* Word referat (.doc/.docx): the flat (text, bold) paragraph stream from
  word.read(), split into header / metadata / body / footer the way the
  documents are laid out: a bold court name and referat at the top, bold
  "Label:" / value pairs in a metadata table, a bold "REFERAT" marker, the
  body, and bold "Sökord:" / "Litteratur:" in the footer.

* Notisfall (.xml): the legacy feed shipped notiser as zero-byte Word files;
  the text survives only in the old pipeline's frozen intermediate XML
  (imported into the store by `lagen dv import-legacy`). Two flavors: a
  TRIPS-era `<para>` stream headed by "R4 M:REGR … Lnr:RÅ1997not50" /
  "G:… D:målnr A:date" lines, and an OOXML-run `<w:p>` stream -- HD's
  carrying no header at all, just a month heading and a "Den 9:e. N.
  (Ö 4629-01) rubrik…" lead paragraph.

Identity (canonical referat, court) is supplied by the identity index;
everything the index lacks for legacy-only cases -- avgörandedatum,
målnummer, and the curated fields (Rubrik→sammanfattning, Lagrum,
Rättsfall, Sökord/Uppslagsord) -- is recovered here from the document
itself.

`import_identities`/`import_notiser` (the `lagen dv import-legacy` verb)
migrate the frozen oracle facts this store needs from the old checkout:
the distilled-RDF identities sidecar and the notis intermediate bodies.

  python -m accommodanda.dv.legacy FILE                 # one file -> artifact
  python -m accommodanda.dv.legacy --index INDEX [--limit N]  # batch + report
"""

import argparse
import bz2
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import DCTERMS, RDF

from ..lib import layout, util
from ..lib import poi as word
from .identity import IDENTITIES, NOTIS_SERIES, canonical_court
from .model import Avgorande, Hanvisning, Lagrum, Rubrik, Stycke
from .parse import RE_NUMPARA, is_heading, to_artifact

# Footer labels that end the body region.
_FOOTER_LABELS = {"Sökord", "Litteratur"}


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


# "Ö 2475-12" is one målnummer, not ["Ö", "2475-12"]; other courts list
# several målnummer separated by comma/semicolon/space/"och".
_MALNR_SPLIT = re.compile(r"\s+och\s+|[,;]|\s+")


_MALNR_PREFIXES = ("Ö", "B", "T")


def _split_malnummer(value):
    if value[:2] in ("Ö ", "B ", "T "):
        # A single "Ö 2475-12" is one identifier (the space is internal), but
        # the same prefix also bundles several målnummer, e.g. "Ö 2475-12 och
        # Ö 2477-12" -- split on the usual separators first, then re-glue
        # each stray prefix letter to the number that follows it.
        parts = [x.strip() for x in _MALNR_SPLIT.split(value) if x.strip()]
        merged, i = [], 0
        while i < len(parts):
            if parts[i] in _MALNR_PREFIXES and i + 1 < len(parts):
                merged.append(parts[i] + parts[i + 1])
                i += 2
            else:
                merged.append(parts[i])
                i += 1
        return merged
    return [x.strip() for x in _MALNR_SPLIT.split(value) if x.strip()]


def _first(head, key):
    vals = head.get(key)
    return vals[0] if vals else None


def parse_legacy_file(path, case=None):
    """A legacy file (Word referat or notis intermediate XML) -> Avgorande.
    `case` is the identity-index entry, used for canonical
    referat/court/målnummer when present."""
    path = Path(path).resolve()
    # store-relative provenance for store files; the CLI also takes strays
    source = (util.store_relpath(path, layout.DATA)
              if path.is_relative_to(layout.DATA) else str(path))
    if path.suffix.lower() == ".xml":
        return parse_notis(path.read_text(), path.parent.name, path.name,
                           case, sources=[source])
    head, body = parse_head_body(word.read(path))
    return build_avgorande(head, body, case, sources=[source])


def build_avgorande(head, body, case=None, sources=None):
    """Map an extracted (head, body) onto Avgorande, preferring the identity
    index's canonical identity fields over the document's where present."""
    case = case or {}

    referat = case.get("referat") or ([head["Referat"]] if head.get("Referat") else [])
    malnummer = case.get("malnummer")
    if not malnummer:
        malnummer = _split_malnummer(_first(head, "Målnummer")) if head.get("Målnummer") else []
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
        sammanfattning=(" ".join(head.get("Rubrik", []))
                        or case.get("referatrubrik") or None),
        related=[Hanvisning(fritext=r) for r in head.get("Rättsfall", [])],
        # a legacy Litteratur line packs several works separated by ";"
        litteratur=[w.strip() for line in head.get("Litteratur", [])
                    for w in line.split(";") if w.strip()],
        body=[_classify(p) for p in body],
        sources=sources or [],
    )


# ---------------------------------------------------------------------------
# Notisfall (frozen intermediate XML)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# the xmlns declarations most intermediates carry; a handful froze without
# them (unbound `w:` prefixes), repaired at import time by re-adding these
_OOXML_NS = (' xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
             ' xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
             '2006/main"'
             ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
             '2006/relationships"')

COURT_NAMN = {"REGR": "Regeringsrätten", "HFD": "Högsta förvaltningsdomstolen",
              "HDO": "Högsta domstolen"}

# the TRIPS-era register header, spaced ("R4 M:REGR Unr:g Lnr:RÅ1997not50")
# or concatenated ("R4M:HFDUnr:gLnr:HFD 2012 not 30"); D: is the målnummer,
# A: the decision date (2- or 4-digit year)
RE_TRIPS_MALNR = re.compile(r"D:\s*(.+?)\s*A:")
RE_TRIPS_DATE = re.compile(r"A:\s*(\d{2}|\d{4})-(\d{2})-(\d{2})")
# HD's month-compilation lead: "Den 9:e. 1. (Ö 4629-01) V.B. och D.B. mot …"
RE_HDO_LEAD = re.compile(r"^Den\s+(\d{1,2}):?e?\.\s+\d+\.\s*(.*)", re.S)
RE_PAREN_MALNR = re.compile(r"\(([ÖBT]\s?\d+-\d+)\)")
MONTH_ORDINAL = {name: i + 1 for i, name in enumerate(
    ("januari", "februari", "mars", "april", "maj", "juni", "juli",
     "augusti", "september", "oktober", "november", "december"))}
# TRIPS record markers ("*REGI") interleaved with the text
RE_TRIPS_MARKER = re.compile(r"^\*[A-Z]+$")

_NOTIS_LABELS = ("Uppslagsord", "Lagrum", "Rättsfall", "Litteratur",
                 "Ledamöter och föredragande")


def notis_paras(text):
    """Intermediate-XML text -> list[Para]. Handles both frozen flavors: the
    TRIPS `<para>` stream (plain text) and the OOXML `<w:p>` run stream (bold
    runs marked)."""
    root = ET.fromstring(text)
    if root.find("para") is not None:
        return [word.Para(" ".join((p.text or "").split()), False, False)
                for p in root.iter("para")]
    out = []
    for p in root.iter(_W + "p"):
        joined = " ".join("".join(t.text or "" for t in p.iter(_W + "t")).split())
        # <w:b/> means bold, but an explicit w:val of 0/false *un-bolds* an
        # inherited style -- that form must not classify the para as a rubrik
        bold = any(el.tag == _W + "b"
                   and el.get(_W + "val", "1").lower() not in ("0", "false",
                                                               "off", "none")
                   for el in p.iter())
        out.append(word.Para(joined, bold, False))
    return out


def _trips_year(yy):
    year = int(yy)
    return year if year >= 1000 else (1900 + year if year >= 50 else 2000 + year)


def parse_notis_head(paras, filename):
    """The notis head dict (same label->values shape parse_head_body yields)
    plus the body Paras. The head region ends where the notis text starts:
    the "Not N." lead (TRIPS flavor) or HD's "Den 9:e. N." / first body
    paragraph (month-compilation flavor)."""
    head: dict[str, list[str]] = {}
    body = []
    leftover = []
    m = re.match(r"(\d{4})_not_(\d+)", filename)
    assert m, "notis filename without year_not_n identity: %s" % filename
    year, notisnr = m.groups()
    values = None      # the value list of the label being read, if any
    month = day = None  # HD's month-compilation date parts
    in_body = False

    def trips_header(text):
        malnr = RE_TRIPS_MALNR.search(text)
        if malnr:
            head["Målnummer"] = [malnr.group(1)]
        date = RE_TRIPS_DATE.search(text)
        if date:
            head["Avgörandedatum"] = ["%04d-%s-%s" % (
                _trips_year(date.group(1)), date.group(2), date.group(3))]

    for p in paras:
        text = p.text
        if not text or RE_TRIPS_MARKER.match(text):
            values = None
            continue
        if in_body:
            body.append(p)
            continue
        if re.match(r"Not\s+%s\s*\." % notisnr, text):
            in_body = True
            body.append(p)
            continue
        lead = RE_HDO_LEAD.match(text)
        if lead:
            day = int(lead.group(1))
            in_body = True
            body.append(word.Para(lead.group(2), p.bold, p.in_table))
            continue
        if text.lower() in MONTH_ORDINAL:
            month = MONTH_ORDINAL[text.lower()]
            continue
        if "Lnr:" in text or (text.startswith(("R4", "G:"))
                              and RE_TRIPS_MALNR.search(text)):
            trips_header(text)   # the TRIPS register line(s), joined or split
            continue
        matched = next((lbl for lbl in _NOTIS_LABELS
                        if text.startswith(lbl + ":")), None)
        if matched:
            values = head.setdefault(matched, [])
            rest = text[len(matched) + 1:].strip()
            if rest:
                values.append(rest)
        elif values is not None:
            values.append(text)
        else:
            leftover.append(p)
    # a notis without its "Not N." / "Den N:e" lead (HD's month compilations
    # often drop it) has no marked body start: the unconsumed, unlabeled
    # paragraphs are the body, in order
    if not in_body:
        body = leftover
    if month and day:
        head["Avgörandedatum"] = ["%s-%02d-%02d" % (year, month, day)]
    return head, body


def parse_notis(text, courtdir, filename, case=None, sources=None):
    """One notis intermediate XML -> Avgorande. Identity comes from the
    filename/index (the same YYYY_not_N rule the identity scan applies);
    målnummer and date from the TRIPS header or HD's month/day lead when the
    document carries them, else from the index (distilled-oracle enriched)."""
    case = case or {}
    court = canonical_court(courtdir)
    series = NOTIS_SERIES[courtdir]
    head, body = parse_notis_head(notis_paras(text), filename)
    m = re.match(r"(\d{4})_not_(\d+)", filename)
    assert m, "notis filename without year_not_n identity: %s" % filename
    referat = case.get("referat") or ["%s %s not %s" % (series, *m.groups())]
    malnummer = case.get("malnummer") or []
    if not malnummer and head.get("Målnummer"):
        malnummer = _split_malnummer(head["Målnummer"][0])
    if not malnummer:
        parens = RE_PAREN_MALNR.search(" ".join(p.text for p in body[:1]))
        if parens:
            malnummer = [parens.group(1)]
    uppslagsord = [w.strip() for line in head.get("Uppslagsord", [])
                   for w in re.split(r";", line) if w.strip()]
    return Avgorande(
        court=court,
        court_namn=COURT_NAMN[court],
        malnummer=malnummer,
        referat=referat,
        avgorandedatum=(case.get("avgorandedatum")
                        or _first(head, "Avgörandedatum")),
        publiceringsform="notis",
        typ=None,
        rattsomrade=[],
        nyckelord=uppslagsord,
        lagrum=[Lagrum(referens=part.strip())
                for line in head.get("Lagrum", [])
                for part in line.split(";") if part.strip()],
        forarbeten=[],
        # the notis document itself has no rubrik; the frozen oracle's
        # published one (from the identity index) is the authoritative summary
        sammanfattning=case.get("referatrubrik"),
        related=[Hanvisning(fritext=r) for r in head.get("Rättsfall", [])],
        litteratur=[w.strip() for line in head.get("Litteratur", [])
                    for w in line.split(";") if w.strip()],
        body=[_classify(p) for p in body],
        sources=sources or [],
    )


# ---------------------------------------------------------------------------
# One-time import of the frozen legacy facts (lagen dv import-legacy)

RPUBL = "http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"
_REFERAT_TYPES = (URIRef(RPUBL + "Rattsfallsreferat"),
                  URIRef(RPUBL + "Rattsfallsnotis"))
_REFERAT_AV = URIRef(RPUBL + "referatAvDomstolsavgorande")
_MALNUMMER = URIRef(RPUBL + "malnummer")
_AVGORANDEDATUM = URIRef(RPUBL + "avgorandedatum")
_REFERATRUBRIK = URIRef(RPUBL + "referatrubrik")


def _distilled_identity(rdf_path):
    """{court-agnostic identity facts} from one old distilled RDF, or None if
    the graph holds no referat-typed document."""
    g = Graph()
    g.parse(str(rdf_path))
    doc = next((s for t in _REFERAT_TYPES for s in g.subjects(RDF.type, t)),
               None)
    if doc is None:
        return None
    verdicts = list(g.objects(doc, _REFERAT_AV))
    dates = sorted({str(v) for verdict in verdicts
                    for v in g.objects(verdict, _AVGORANDEDATUM)})
    rubriks = sorted({" ".join(str(v).split())
                      for v in g.objects(doc, _REFERATRUBRIK)})
    return {
        "referat": sorted({" ".join(str(v).split())
                           for v in g.objects(doc, DCTERMS.identifier)}),
        "malnummer": sorted({" ".join(str(v).split()) for verdict in verdicts
                             for v in g.objects(verdict, _MALNUMMER)}),
        "avgorandedatum": dates[0] if len(dates) == 1 else None,
        "referatrubrik": rubriks[0] if len(rubriks) == 1 else None,
    }


def import_identities(distilled, dvdir, log=print):
    """Distill the frozen oracle's identity facts (referat, målnummer, date
    per case) from `distilled` (the old checkout's dv/distilled RDF tree)
    into the `legacy-identities.json` sidecar the identity scan reads."""
    out = []
    skipped = 0
    for rdf_path in sorted(Path(distilled).glob("*/*.rdf")):
        if "#" in rdf_path.name or not rdf_path.is_file():
            continue   # editor junk beside the frozen tree
        ident = _distilled_identity(rdf_path)
        if ident is None or not ident["referat"]:
            skipped += 1
            continue
        ident["court"] = canonical_court(rdf_path.parent.name)
        out.append(ident)
    sidecar = Path(dvdir) / IDENTITIES
    util.write_atomic(sidecar, json.dumps(out, ensure_ascii=False, indent=1))
    log("dv import-legacy: %d oracle identities -> %s (%d graphs without a "
        "referat document)" % (len(out), sidecar, skipped))
    return out


def import_notiser(intermediate, dvdir, log=print):
    """Copy the frozen notis bodies (`intermediate/{COURT}/YYYY_not_N.xml.bz2`)
    into the store as plain XML beside the zero-byte Word originals the legacy
    feed shipped. The store copy is the parseable source of record; a handful
    frozen with unbound `w:` prefixes are repaired by re-adding the xmlns
    declarations their siblings carry."""
    copied = repaired = 0
    for src in sorted(Path(intermediate).glob("*/*_not_*.xml.bz2")):
        if src.name.startswith(".") or "#" in src.name or not src.is_file():
            continue
        court = src.parent.name
        assert court in NOTIS_SERIES, (
            "notis intermediate under unexpected court dir %s" % court)
        text = bz2.decompress(src.read_bytes()).decode("utf-8")
        try:
            ET.fromstring(text)
        except ET.ParseError:
            assert text.startswith("<body>"), (
                "%s: unparseable and not the known bare-<body> form" % src)
            text = text.replace("<body>", "<body%s>" % _OOXML_NS, 1)
            ET.fromstring(text)   # must parse after repair
            repaired += 1
        dest = Path(dvdir) / court / (src.name[:-len(".xml.bz2")] + ".xml")
        util.write_atomic(dest, text)
        copied += 1
    log("dv import-legacy: %d notis bodies -> %s (%d xmlns-repaired)"
        % (copied, dvdir, repaired))
    return copied


def legacy_original(case):
    """The first legacy member with a non-empty original file, or None.
    Notisfall members have a zero-byte original (the body lives only in the
    frozen intermediate) and are excluded here."""
    for member in case["members"]:
        if member["store"] != "dv":
            continue
        path = util.load_relpath(layout.DATA, member["path"])
        if path.exists() and path.stat().st_size:
            return member
    return None


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
