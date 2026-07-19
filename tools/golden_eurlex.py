"""EUR-Lex metadata cross-check (REWRITE.md §7d).

There is no legacy oracle for EUR-Lex (the old pipeline never supported it
beyond an experimental module), so this validates the *carried* metadata
fields -- CELEX, date, title, OJ reference, ECLI and doctype -- against the
authoritative CELLAR metadata itself, frozen to a retained snapshot so a
compare run is reproducible and diffable offline.

Two modes:

  python tools/golden_eurlex.py snapshot     # network: draw the sample, fetch
                                             # CELLAR metadata, freeze it
  python tools/golden_eurlex.py compare      # offline change detector against
                                             # the frozen snapshot

The sample is deterministic (evenly spaced over each stratum's sorted CELEX
list, no randomness), stratified over what the corpus actually holds: treaties,
regulations, directives, corrigenda and judgments. Decisions and consolidated
acts are absent by harvest design (sector 3 is enumerated R/L only), so they
are outside the corpus, not missing from the sample.

Following the golden methodology this is a change detector: a difference is
evidence to investigate, not an assumed regression. Systematic differences
that have been investigated and explained are adjudicated by the named rules
in ADJUDICATION_RULES (the golden_sfs ledger pattern); exact + adjudicated is
a passing document. `compare --reparse` parses the sampled documents from the
raw downloaded tree instead of reading the stored artifacts, so the check
exercises the current parser rather than a possibly stale artifact tree.
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from accommodanda.eurlex.download import (
    SPARQL,
    list_basefiles,
    sparql_select,
)
from accommodanda.eurlex.parse import parse_dir
from accommodanda.lib import compress, layout
from accommodanda.lib.net import HARVESTER_UA, make_session

SNAPSHOT_DEFAULT = "test/files/eurlex/cellar-snapshot.json"

PREFIXES = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
            "PREFIX owl: <http://www.w3.org/2002/07/owl#> ")
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
CHUNK = 100          # CELEX per query; the OJ owl:sameAs join dislikes big VALUES
OJ_PREFIX = "http://publications.europa.eu/resource/oj/"
RT_PREFIX = "http://publications.europa.eu/resource/authority/resource-type/"

# how many documents each stratum contributes (treaties and the sector-5 strays
# are small enough to take whole; None = all)
STRATA_QUOTA = {"treaty": None, "regulation": 120, "directive": 100,
                "corrigendum": 80, "judgment": 150, "other": None}

RE_CORRIGENDUM = re.compile(r"R\(\d+\)$")

# CELLAR resource-type authority code -> our CELEX-derived doctype. Codes that
# legitimately map to a *different* doctype in our deliberately coarse model
# are adjudicated below, not listed here.
RESOURCE_TYPES = {
    "REG": "regulation", "REG_IMPL": "regulation", "REG_DEL": "regulation",
    "REG_FINANC": "regulation",
    "DIR": "directive", "DIR_IMPL": "directive", "DIR_DEL": "directive",
    "JUDG": "judgment",
    "TREATY": "treaty",
}


def stratum(celex):
    if celex.startswith("1"):
        return "treaty"
    if celex.startswith("6"):
        return "judgment"
    if celex.startswith("3"):
        if RE_CORRIGENDUM.search(celex):
            return "corrigendum"
        return {"R": "regulation", "L": "directive"}.get(celex[5], "other")
    return "other"


def draw_sample(celexes, quotas=STRATA_QUOTA):
    """stratum -> evenly spaced CELEX picks over the stratum's sorted list --
    deterministic, so a re-drawn sample from an unchanged corpus is identical."""
    by_stratum = {}
    for celex in sorted(celexes):
        by_stratum.setdefault(stratum(celex), []).append(celex)
    sample = {}
    for name, members in sorted(by_stratum.items()):
        n = quotas.get(name)
        if n is None or n >= len(members):
            sample[name] = members
        else:
            step = (len(members) - 1) / (n - 1)
            sample[name] = sorted({members[round(i * step)] for i in range(n)})
    return sample


# --------------------------------------------------------------------------
# snapshot -- fetch the authoritative CELLAR metadata for the sample
# --------------------------------------------------------------------------

def _literals(values):
    return " ".join('"%s"^^<%s>' % (v, XSD_STRING) for v in values)


def _work_query(celexes):
    return (PREFIXES +
            "SELECT ?celex ?d ?rt ?ecli ?oj WHERE { VALUES ?celex { %s } "
            "?w cdm:resource_legal_id_celex ?celex . "
            "OPTIONAL { ?w cdm:work_date_document ?d } "
            "OPTIONAL { ?w cdm:work_has_resource-type ?rt } "
            "OPTIONAL { ?w cdm:case-law_ecli ?ecli } "
            "OPTIONAL { ?w cdm:resource_legal_published_in_official-journal ?ojr . "
            '?ojr owl:sameAs ?oj . FILTER(STRSTARTS(STR(?oj), "%s")) } }'
            % (_literals(celexes), OJ_PREFIX))


def _title_query(celexes):
    return (PREFIXES +
            "SELECT ?celex ?lang ?title WHERE { VALUES ?celex { %s } "
            "?w cdm:resource_legal_id_celex ?celex . "
            "?e cdm:expression_belongs_to_work ?w ; "
            "cdm:expression_uses_language ?lc ; cdm:expression_title ?title . "
            "BIND(LCASE(REPLACE(STR(?lc), '.*/', '')) AS ?lang) "
            'FILTER(?lang IN ("swe", "eng")) }' % _literals(celexes))


def fetch_snapshot(session, celexes):
    """celex -> its authoritative CELLAR metadata, every field a sorted list
    (CELLAR can legitimately carry several values -- e.g. an act published in
    more than one OJ, duplicate ECLI triples across notices)."""
    docs = {c: {"dates": set(), "types": set(), "eclis": set(), "oj": set(),
                "titles": {"swe": set(), "eng": set()}} for c in celexes}
    ordered = sorted(celexes)
    for i in range(0, len(ordered), CHUNK):
        chunk = ordered[i:i + CHUNK]
        print("  fetching %d-%d of %d ..." % (i + 1, i + len(chunk), len(ordered)),
              file=sys.stderr, flush=True)
        for row in sparql_select(session, _work_query(chunk)):
            doc = docs[row["celex"]["value"]]
            if "d" in row:
                doc["dates"].add(row["d"]["value"][:10])
            if "rt" in row:
                doc["types"].add(row["rt"]["value"].removeprefix(RT_PREFIX))
            if "ecli" in row:
                doc["eclis"].add(row["ecli"]["value"])
            if "oj" in row:
                doc["oj"].add(row["oj"]["value"].removeprefix(OJ_PREFIX))
        for row in sparql_select(session, _title_query(chunk)):
            docs[row["celex"]["value"]]["titles"][row["lang"]["value"]].add(
                normalize_text(row["title"]["value"]))
    return {celex: {"dates": sorted(doc["dates"]),
                    "types": sorted(doc["types"]),
                    "eclis": sorted(doc["eclis"]),
                    "oj": sorted(doc["oj"]),
                    "titles": {lang: sorted(vals)
                               for lang, vals in doc["titles"].items() if vals}}
            for celex, doc in docs.items()}


def snapshot(out_path, quotas=STRATA_QUOTA):
    celexes = list_basefiles(layout.EURLEX_DOWNLOADED)
    sample = draw_sample(celexes, quotas)
    for name, members in sorted(sample.items()):
        print("  %-12s %5d sampled" % (name, len(members)), file=sys.stderr)
    picked = sorted(c for members in sample.values() for c in members)
    session = make_session(HARVESTER_UA)
    docs = fetch_snapshot(session, picked)
    payload = {"fetched": date.today().isoformat(), "endpoint": SPARQL,
               "sample": sample, "docs": docs}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1,
                                   sort_keys=True) + "\n", encoding="utf-8")
    empty = [c for c in picked if not any(docs[c][k] for k in
                                          ("dates", "types", "eclis", "oj"))
             and not docs[c]["titles"]]
    print("snapshot: %d documents -> %s (%d with no CELLAR metadata at all)"
          % (len(picked), out_path, len(empty)))
    for celex in empty:
        print("  no CELLAR metadata: %s" % celex)


# --------------------------------------------------------------------------
# compare -- the offline change detector
# --------------------------------------------------------------------------

def normalize_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_date(value):
    """Artifact dates come from Formex DATE@ISO (YYYYMMDD); the snapshot's are
    ISO with dashes. Compare in dashed form."""
    if value and re.fullmatch(r"\d{8}", value):
        return "%s-%s-%s" % (value[:4], value[4:6], value[6:8])
    return value


def oj_ref(oj_id):
    """A snapshot OJ identifier to the artifact's 'series number' form:
    'JOL_2016_119_R' -> 'L 119'; an extra ("Isolated") edition, marked by a
    trailing _I ('JOC_2019_066_I'), keeps the Formex 'C 66I' form."""
    m = re.match(r"JO([A-Z])_\d{4}_(\d+)(?:_([A-Z]))?", oj_id)
    if not m:
        return None
    return "%s %d%s" % (m.group(1), int(m.group(2)),
                        "I" if m.group(3) == "I" else "")


def load_artifact(celex, reparse):
    """The artifact metadata for a sampled CELEX: the stored artifact, or under
    --reparse a fresh parse of the raw downloaded content (validating current
    code instead of the possibly stale artifact tree). None if the document has
    no artifact / no parseable content."""
    if reparse:
        return parse_dir(layout.eurlex_dir(celex), celex)
    path = layout.artifact("eurlex", celex)
    if not compress.exists(path):
        return None
    return json.loads(compress.read_bytes(path))


def _canonical(text):
    """The aggressive title normal form for the surface-form rule: casefolded
    letters and digits only, so punctuation/spacing/case rendering differences
    between Formex flattening and CELLAR's catalogue string disappear while any
    wording difference survives."""
    return re.sub(r"[^0-9a-zà-ÿ]+", "", (text or "").casefold())


# EEA-relevance boilerplate, which CELLAR's catalogue title omits or orders
# differently from the document's own title line
_BOILERPLATE = ("textavbetydelseförees", "textwitheearelevance", "eestext")


def _sans_boilerplate(canonical):
    for phrase in _BOILERPLATE:
        canonical = canonical.replace(phrase, "")
    return canonical


def _near(value, ref, threshold=0.9):
    """Whether the canonical forms are near-identical by difflib ratio -- the
    rendering-variance test for titles whose *identity* is already guaranteed
    by the CELEX join (Formex expands OJ references and dates that CELLAR's
    catalogue string abbreviates; a genuinely wrong or garbage title scores far
    below the threshold). autojunk must be off: on long letter-only strings
    difflib's popularity heuristic junks every common letter and the ratio
    collapses for near-identical titles."""
    cv = _canonical(value)
    return any(SequenceMatcher(None, cv, _canonical(r),
                               autojunk=False).ratio() >= threshold
               for r in ref)


# the OJ-reference rendering differences between a document's own title line
# and CELLAR's catalogue string, folded onto the canonical form: the spelled-
# out journal name vs its acronym, month names vs numbers, the 'av den'/'of'
# connectors, and a trailing OJ document number ('2010/C 181/01') only one
# side carries
_OJNORM_SUBS = (
    ("europeiskagemenskapernasofficiellatidning", "egt"),
    ("europeiskaunionensofficiellatidning", "eut"),
    ("officialjournaloftheeuropeancommunities", "oj"),
    ("officialjournaloftheeuropeanunion", "oj"),
    ("januari", "1"), ("februari", "2"), ("mars", "3"), ("april", "4"),
    ("maj", "5"), ("juni", "6"), ("juli", "7"), ("augusti", "8"),
    ("september", "9"), ("oktober", "10"), ("november", "11"),
    ("december", "12"),
    ("january", "1"), ("february", "2"), ("march", "3"),
    ("may", "5"), ("june", "6"), ("july", "7"), ("august", "8"),
    ("october", "10"),
    # 'av den 8 november' vs 'den 8 november' vs '8.11.': both connectors go
    # (removal is applied to both sides, so a stray in-word hit cannot create
    # a false equality)
    ("avden", ""), ("den", ""), ("nr", ""),
)


def _ojnorm(canonical):
    for phrase, sub in _OJNORM_SUBS:
        canonical = canonical.replace(phrase, sub)
    canonical = re.sub(r"(?<=\d)of(?=\d)", "", canonical)
    return re.sub(r"(?:19|20)\d{2}c\d{2,5}$", "", canonical)


# Per-document adjudications: investigated one-off differences, keyed
# (field, celex). The value documents what was found; the compare counts the
# entry as adjudicated:ledger.
LEDGER = {
    ("date", "32004L0038"):
        "Formex bib dated 2004-04-30 (OJ L 158 publication); the directive "
        "was adopted 2004-04-29 (its own title says so) -- source bib quirk",
    ("date", "32022R0363"):
        "CELLAR work date 2022-01-02 contradicts the act's own dated title "
        "('av den 24 januari 2022') -- upstream data issue, artifact correct",
    # the 2016 consolidated-treaty corrigenda are booklets whose first title
    # element is the reader's note; the real title exists only as CELLAR's
    # catalogue construct
    ("title", "12016E/TXTR(02)"):
        "booklet manifestation titles itself 'TILL LÄSAREN'",
    ("title", "12016M/TXTR(02)"):
        "booklet manifestation titles itself 'TILL LÄSAREN'",
    # the combined TEU+TFEU booklet: the manifestation's first title is the
    # TEU's while this CELEX denotes the TFEU part -- known coarseness of
    # parsing a shared booklet per treaty CELEX
    ("title", "12012E/TXT"):
        "combined TEU+TFEU booklet; first title (TEU) grabbed for the TFEU "
        "CELEX",
    ("title", "61997CC0378"):
        "source digitisation typos ('Förslag rill avgörande', '19999') in the "
        "document's own title line",
    ("title", "62003TJ0164"):
        "catalogue renames the court post-2009 ('Tribunalens dom'); the "
        "document says 'Förstainstansrättens dom'",
    ("title", "32024R2979R(01)"):
        "the corrigendum's own title line quotes implementing regulation "
        "2024/2977's subject; CELLAR carries 2024/2979's -- upstream document "
        "quirk",
    # the older caselaw HTML manifestations carry no distinct title line; the
    # extraction surfaces a summary/body paragraph. The judgment page heading
    # is the case number/name (eucasenaming), so display is unaffected.
    ("title", "62004TJ0226"): "caselaw html: no title line, body text surfaces",
    ("title", "62007CJ0294"): "caselaw html: no title line, body text surfaces",
    ("title", "62008CJ0328"): "caselaw html: no title line, body text surfaces",
    ("title", "62016TJ0303"): "caselaw html: no title line, body text surfaces",
    ("title", "62019CC0422"): "caselaw html: no title line, body text surfaces",
    ("title", "62024CC0565"): "caselaw html: no title line, body text surfaces",
    ("title", "62025CC0157"): "caselaw html: no title line, body text surfaces",
}


# Named adjudication rules: systematic, investigated differences that our
# deliberately coarse model produces by design. Each is a predicate over
# (celex, value, reference-values); the first matching name adjudicates the
# difference. Anything unmatched stays an unexplained diff to investigate.
ADJUDICATION_RULES = {
    "doctype": (
        # a corrigendum CELEX ('...R(NN)') classifies under its parent act's
        # type -- doctype() reads the CELEX descriptor, and the corrigendum
        # page should group with the act it corrects
        ("corrigendum-under-parent-type",
         lambda celex, value, ref: RE_CORRIGENDUM.search(celex)),
        # sector 6 is uniformly 'judgment' in our model; CELLAR distinguishes
        # AG opinions (descriptor CC/TC) and orders
        ("sector6-uniform-judgment",
         lambda celex, value, ref: celex.startswith("6")),
        # sector-1 documents are uniformly 'treaty' in our model; CELLAR types
        # accession acts, protocols, charters etc. more finely
        ("sector1-uniform-treaty",
         lambda celex, value, ref: celex.startswith("1")),
    ),
    "date": (
        # a consolidated treaty text's Formex bibliography is dated by its OJ
        # publication; CELLAR's work date is the signature date (11979H/TXT:
        # signed 1979-05-28, published 1979-11-19). Both are real dates of the
        # document's lifecycle; the page shows the publication of the text it
        # renders.
        ("treaty-publication-vs-signature",
         lambda celex, value, ref: celex.startswith("1")),
    ),
    "title": (
        # same words, different surface rendering (case, punctuation, spacing:
        # 'TREATY...' vs 'Treaty...', 'livsmedel(Text' vs 'livsmedel (Text')
        ("surface-form",
         lambda celex, value, ref: any(_canonical(value) == _canonical(r)
                                       for r in ref)),
        # the '(Text av betydelse för EES)' boilerplate: CELLAR's catalogue
        # title drops it or orders it after '(kodifierad version)' differently
        ("eea-boilerplate-placement",
         lambda celex, value, ref: any(
             _sans_boilerplate(_canonical(value)) == _sans_boilerplate(_canonical(r))
             for r in ref)),
        # a judgment's CELLAR title is a catalogue construct -- the document
        # heading plus parties, case number and language notes joined by '#';
        # the artifact carries the document's own heading, which the catalogue
        # string extends
        ("judgment-catalogue-title-extends-heading",
         lambda celex, value, ref: celex.startswith("6") and value and any(
             _canonical(r).startswith(_canonical(value)) for r in ref)),
        # same title, differently rendered details: Formex expands the OJ
        # references and dates CELLAR abbreviates ('Official Journal of the
        # European Communities 45 of 14 June 1962' vs 'OJ 45, 14.6.1962'), old
        # ECR page headers abbreviate dates and parties, an accession treaty's
        # catalogue title inserts '(signed on ...)'. Identity is guaranteed by
        # the CELEX join; a wrong/garbage title scores far below the ratio.
        ("surface-rendering-near-match",
         lambda celex, value, ref: _near(value, ref)),
        # a consolidated-treaty booklet's Formex title block concatenates the
        # front matter (title + annex/protocol listing); CELLAR's catalogue
        # title is its head
        ("treaty-frontmatter-extends-title",
         lambda celex, value, ref: celex.startswith("1") and any(
             _canonical(value).startswith(_canonical(r)) for r in ref)),
        # the OJ-reference inside a corrigendum/erratum title: the document
        # spells the journal name and date out, the catalogue abbreviates
        # ('Europeiska unionens officiella tidning L 153 av den 14 juni 2007'
        # vs 'EUT L 153, 14.6.2007') -- equal after the OJ normal form
        ("ojref-rendering",
         lambda celex, value, ref: any(
             _ojnorm(_canonical(value)) == _ojnorm(_canonical(r))
             or SequenceMatcher(None, _ojnorm(_canonical(value)),
                                _ojnorm(_canonical(r)),
                                autojunk=False).ratio() >= 0.93
             for r in ref)),
        # CELLAR's catalogue title opens with the document's own title and
        # extends it ('#'-joined parties / declarations / annex listings) --
        # for any document family, on the OJ normal form. The length floor
        # keeps a trivially short extracted fragment from free-riding.
        ("catalogue-title-extends-document-title",
         lambda celex, value, ref: len(_canonical(value)) >= 20 and any(
             _ojnorm(_canonical(r)).startswith(_ojnorm(_canonical(value)))
             for r in ref)),
        # some old ECR cases' only Formex manifestation is the hearing report
        # (root REPORT.HEARING) -- the artifact title honestly names what the
        # document is; CELLAR's title names the judgment the CELEX denotes
        ("manifestation-is-hearing-report",
         lambda celex, value, ref: celex.startswith("6")
         and _canonical(value).startswith(("reportforthehearing",
                                           "förhandlingsrapport"))),
        # an old ECR judgment's title is the running page header ('JUDGMENT OF
        # 16. 12. 1963 -- CASE 1/63 MACCHIORLATI DALMAS v HIGH AUTHORITY');
        # CELLAR's is the catalogue construct with full court/party names.
        # Both open by naming the same document kind; identity is guaranteed
        # by the CELEX join, so the rest is rendering.
        ("judgment-header-vs-catalogue",
         lambda celex, value, ref: celex.startswith("6")
         and (kind := next((k for k in ("judgment", "opinion", "order", "dom",
                                        "beslut", "förslagtillavgörande")
                            if _canonical(value).startswith(k)), None))
         is not None
         and any(_canonical(r).startswith(kind) for r in ref)),
    ),
    "oj": (),
    "ecli": (),
}


def compare_doc(celex, art, doc):
    """[(field, status, artifact-value, snapshot-values)] for one document.
    status: 'exact' (agreement, incl. both absent), 'adjudicated:<rule>',
    'no-snapshot' (CELLAR carries nothing for the field), 'absent' (the
    manifestation yielded no value where CELLAR has one -- a coverage
    statistic, kept apart from a wrong-value 'diff')."""
    lang = art.get("lang")
    results = []

    def judge(field, value, reference):
        if value in reference or (value is None and not reference):
            results.append((field, "exact", value, reference))
            return
        if not reference:
            results.append((field, "no-snapshot", value, reference))
            return
        if value is None:
            results.append((field, "absent", value, reference))
            return
        if (field, celex) in LEDGER:
            results.append((field, "adjudicated:ledger", value, reference))
            return
        for name, rule in ADJUDICATION_RULES[field]:
            if rule(celex, value, reference):
                results.append((field, "adjudicated:" + name, value, reference))
                return
        results.append((field, "diff", value, reference))

    judge("date", normalize_date(art.get("date")), doc["dates"])
    judge("title", normalize_text(art.get("title")) or None,
          doc["titles"].get(lang, []))
    judge("oj", art.get("oj"),
          sorted({r for oj in doc["oj"] if (r := oj_ref(oj))}))
    judge("ecli", art.get("ecli"), doc["eclis"])
    judge("doctype", art.get("doctype"),
          sorted({RESOURCE_TYPES[t] for t in doc["types"]
                  if t in RESOURCE_TYPES}) or ["<%s>" % t for t in doc["types"]])
    return results


def compare(snapshot_path, reparse=False, show=10):
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    docs = payload["docs"]
    strata = {c: name for name, members in payload["sample"].items()
              for c in members}
    print("snapshot %s (%d documents, fetched %s)%s"
          % (snapshot_path, len(docs), payload["fetched"],
             " -- reparsing from downloaded tree" if reparse else ""))

    counts = {f: Counter() for f in ("date", "title", "oj", "ecli", "doctype")}
    missing = []
    examples = {}
    for celex in sorted(docs):
        art = load_artifact(celex, reparse)
        if art is None:
            missing.append(celex)
            continue
        assert art["celex"] == celex, \
            "artifact %s carries celex %r" % (celex, art["celex"])
        for field, status, value, reference in compare_doc(celex, art, docs[celex]):
            counts[field][status] += 1
            if status == "diff":
                examples.setdefault(field, []).append((celex, value, reference))

    compared = len(docs) - len(missing)
    print("\ncompared %d documents (%d sampled without artifact/content)"
          % (compared, len(missing)))
    for celex in missing:
        print("  no artifact: %s [%s]" % (celex, strata.get(celex, "?")))
    unexplained = 0
    for field, c in counts.items():
        adjudicated = sum(n for s, n in c.items() if s.startswith("adjudicated:"))
        unexplained += c["diff"]
        print("  %-8s exact %5d  adjudicated %4d  absent %4d  no-snapshot %4d"
              "  diff %4d"
              % (field, c["exact"], adjudicated, c["absent"], c["no-snapshot"],
                 c["diff"]))
        for s, n in sorted(c.items()):
            if s.startswith("adjudicated:"):
                print("           %6d %s" % (n, s))
        for celex, value, reference in examples.get(field, [])[:show]:
            print("      diff %s [%s]: artifact=%r cellar=%r"
                  % (celex, strata.get(celex, "?"), value,
                     reference if len(str(reference)) < 200
                     else [r[:90] + "..." for r in reference]))
    print("\nunexplained differences: %d" % unexplained)
    return unexplained


def main():
    assert __doc__ is not None
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("snapshot", "compare"))
    ap.add_argument("--snapshot", default=SNAPSHOT_DEFAULT,
                    help="frozen CELLAR metadata snapshot (default %(default)s)")
    ap.add_argument("--reparse", action="store_true",
                    help="compare: parse from the downloaded tree instead of "
                         "reading stored artifacts")
    ap.add_argument("--show", type=int, default=10, help="example diffs per field")
    args = ap.parse_args()
    path = Path(args.snapshot)
    if args.mode == "snapshot":
        snapshot(path)
    else:
        if not path.exists():   # raise, not assert: a mistyped --snapshot must
                                # fail loudly, not report a false-clean golden
            raise SystemExit("no snapshot at %s -- run `golden_eurlex.py "
                             "snapshot` first" % path)
        compare(path, reparse=args.reparse, show=args.show)


if __name__ == "__main__":
    main()
