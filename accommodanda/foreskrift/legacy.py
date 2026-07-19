"""One-time import of the frozen lagen.nu myndfs corpora into the föreskrift
record layout (rewrite-parity: the legacy-corpus sweep).

The old pipeline harvested ~30 agency författningssamlingar whose documents
the live harvest no longer sees: repealed regulations the agencies purged
from their sites (PMFS 2019:2 left polisen.se when PMFS 2022:1 replaced it),
whole predecessor series (RPSFS, LSFS, LMVFS, KBMFS, RTVFS), and the frozen
samlingar the new engine cannot reach (SJVFS, SVKFS). Those documents remain
valuable for determining what the law was at a point in time, so each legacy
document absent from the live corpus is imported as its own record: the body
PDF copied beside a synthesized record JSON whose `files.regulation.url`
carries the original source URL (even where it now 404s) and whose
``"source": "myndfs-legacy"`` key marks the provenance for presentation
disclaimers. A document the live harvest carries -- as a base record or as
an amendment under one -- is never touched: live always wins.

The record's title (and thereby the 05a `andrar`-from-title extraction)
comes from the frozen corpus's distilled RDF; body-structure metadata
(beslutsdatum, publisher, bemyndigande) is the parser's job, read from the
PDF masthead like any harvested document.
"""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..lib import compress
from .agencies import REGISTRY
from .harvest import record_path

LEGACY_SOURCE = "myndfs-legacy"

# the frozen fs corpora under LEGACY_ROOT (ferenda.old/data/<corpus>); each
# holds downloaded/<series>-<year>/<num>/index.pdf + entries + distilled.
# A corpus can hold several series (pmfs also carries RPSFS; sjvfs LSFS;
# lvfs the post-2015 HSLF-FS ids) -- each entry's own basefile decides where
# a document lands, so the corpus list is just where to look.
CORPORA = ("afs", "bolfs", "bfs", "difs", "dvfs", "eifs", "elsakfs", "fffs",
           "ffs", "imyfs", "kfmfs", "kovfs", "kvfs", "lifs", "lmfs", "lvfs",
           "memyfs", "migrfs", "mprtfs", "msbfs", "myhfs", "nfs", "pmfs",
           "rafs", "rgkfs", "rifs", "sifs", "sjvfs", "skvfs", "sosfs",
           "stfs", "svkfs")

RDF_NS = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
DCT_NS = "{http://purl.org/dc/terms/}"
RPUBL_NS = "{http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#}"

RE_BASEFILE = re.compile(r"^([a-zåäö-]+)/(\d{4}):(\w+)$")


def live_coverage(root):
    """(base basefiles, amendment identifiers) the live corpus already
    carries. An identifier is normalized to its compact upper form
    ('PMFS2019:2') for the amendment join, since agencies vary the spacing."""
    bases, amendments = set(), set()
    for p in compress.glob(Path(root), "*/*.json"):
        if p.name.startswith("."):
            continue
        rec = json.loads(compress.read_text(p))
        bases.add(rec["basefile"])
        for am in rec.get("files", {}).get("amendment", []):
            if am.get("identifier"):
                amendments.add(am["identifier"].upper().replace(" ", ""))
    return bases, amendments


def doc_title(rdf_path):
    """The document's dcterms:title from its distilled RDF: the title on the
    first resource whose rdf:about has no fragment. Only the document
    resource is fragment-less in these graphs -- a Paragraf/Stycke always
    carries one -- so no type check is needed to skip section headings."""
    if not rdf_path.exists():
        return None
    for elem in ET.parse(rdf_path).getroot():
        about = elem.get(RDF_NS + "about") or ""
        if "#" in about:
            continue
        node = elem.find(DCT_NS + "title")
        if node is not None and node.text:
            return " ".join(node.text.split())
    return None


def legacy_docs(corpus_dir, unrecognized=None):
    """Yield (basefile, body_path, rdf_path, source_url) for each document of
    one frozen corpus, from its entries tree (the entry's own basefile decides
    the series -- pmfs' entries also carry RPSFS ids). An entry the old
    pipeline downloaded but never parsed carries ``"basefile": null``; its
    basefile is recovered from the entry path (``afs-1987/2.json`` ->
    ``afs/1987:2``, the same series-year/num convention throughout). An entry
    whose basefile cannot be recovered is recorded on `unrecognized` (when
    given) instead of vanishing from the completeness audit."""
    for entry_path in sorted((corpus_dir / "entries").glob("*/*.json")):
        entry = json.loads(entry_path.read_text())
        basefile = entry.get("basefile")
        if not basefile:
            series, _, year = entry_path.parent.name.rpartition("-")
            if series and year.isdigit():
                basefile = "%s/%s:%s" % (series, year, entry_path.stem)
        if not basefile or not RE_BASEFILE.match(basefile):
            if unrecognized is not None:
                unrecognized.append(str(entry_path))
            continue
        rel = Path(entry_path.parent.name) / entry_path.stem
        yield (basefile,
               corpus_dir / "downloaded" / rel / "index.pdf",
               corpus_dir / "distilled" / rel.with_suffix(".rdf"),
               entry.get("orig_url"))


def import_corpus(corpus_dir, root, bases, amendments, limit=None):
    """Import one frozen corpus's not-live documents into `root`. Returns
    (seen, imported, skipped_covered, skipped_bodyless, skipped_nonpdf,
    unrecognized). `bases`/`amendments` is the live coverage -- mutated as
    records are written, so a document imported from one corpus is covered
    for the next (lvfs and sifs both carry post-merger ids)."""
    seen = imported = covered = bodyless = nonpdf = 0
    unrecognized: list[str] = []
    for basefile, body, rdf, source_url in legacy_docs(Path(corpus_dir),
                                                       unrecognized):
        seen += 1
        ident_compact = basefile.upper().replace("/", " ").replace(" ", "")
        if basefile in bases or ident_compact in amendments:
            covered += 1
            continue
        if not body.exists():
            bodyless += 1
            continue
        data = body.read_bytes()
        if not data.startswith(b"%PDF"):
            nonpdf += 1        # an HTML page stored as index.pdf: not importable
            continue           # as a regulation body -- reported, left frozen
        if limit and imported >= limit:
            break
        fs, rest = basefile.split("/", 1)
        # the printed designation, exactly as the live harvest mints it
        # (harvest.py: ``agency.designation or agency.fs.upper()``) -- RA-FS
        # and SvKFS are not their upper-cased slugs
        agency = REGISTRY.get(fs)
        identifier = "%s %s" % (
            (agency.designation if agency and agency.designation
             else fs.upper()), rest)
        slug = "%s-%s" % (fs, rest.replace(":", "-"))
        name = slug + "-regulation.pdf"
        compress.write_download(Path(root) / fs / name, data)
        record = {
            "fs": fs,
            "basefile": basefile,
            "identifier": identifier,
            "title": doc_title(rdf),
            "url": source_url,
            "source": LEGACY_SOURCE,
            "files": {"regulation": {"name": name, "url": source_url,
                                     "identifier": identifier},
                      "consolidation": [], "amendment": [], "memo": [],
                      "attachment": []},
        }
        compress.write_download(
            record_path(str(root), fs, basefile),
            json.dumps(record, ensure_ascii=False, indent=2))
        bases.add(basefile)
        imported += 1
    return seen, imported, covered, bodyless, nonpdf, len(unrecognized)
