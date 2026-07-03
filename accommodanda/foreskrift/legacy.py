"""One-time import of the two harvest-blocked föreskrift corpora (REWRITE.md §7g
priority 6): Skatteverket's SKVFS (behind an F5 bot-defense) and Socialstyrelsen's
SOSFS / HSLF-FS (a React SPA). Both need a bot-evading harvest posture nobody has
built, so no downloader is ported -- the frozen legacy tree is materialized once
into the föreskrift record layout, after which the ordinary `parse` stage and the
whole derived layer treat each regulation like a harvested one (no network).

**Point at the bytes, don't copy them** (§7g): an import record references the
frozen regulation PDF in place through a ``files.regulation.legacy`` path relative
to ``config.LEGACY_ROOT`` (resolved at parse time by :func:`parse.body_path`), so
nothing is duplicated. Each record also carries a ``source: "<corpus>-legacy"``
marker the precedence rule reads.

**Precedence** (:func:`lib.legacy_import.should_write`): a record written by a
*future* live harvester (data.skatteverket.se, socialstyrelsen.se -- neither
built yet) has no ``source`` key and always wins; an import never overwrites it.
The corpus's own prior import (same ``source``) is left untouched on a plain
re-run (so the artifact keeps its mtime and parse stays fresh) and rewritten
only under ``force``. There is a single frozen source per fs, so unlike the
förarbete corpora no body-format tie-break is passed.

The frozen tree layout mirrors the old pipeline everywhere: ``entries/<dir>/<n>.json``
carries the authoritative post-sanitization ``basefile`` (+ ``orig_url`` ->
source_url, ``title``) and ``downloaded/<dir>/<n>/index.{html,pdf}`` holds the
files -- the ``index.pdf`` is the regulation, the ``index.html`` a landing/listing
page (not the regulation text). So a document with a real ``index.pdf`` gets a
body; an html-only document (48 in skvfs, mostly the pre-2004 RSFS series) becomes
a **metadata-only** record -- a real catalog document at its URI, its body left
empty (the föreskrift parser is PDF-only; the landing HTML is not the law).

Three entry classes are handled by construction: a **null basefile** (a failed
download stub) is skipped; a **konsolidering/** basefile (Socialstyrelsen's
consolidated texts) is skipped with a logged count -- its identity is a 3-part
``konsolidering/{fs}/{year}:{n}`` namespace that does not fit the vertical's
``{fs}/{year}:{n}`` URI/layout, and its ``index.pdf`` is in fact HTML (unparseable
by the PDF pipeline), so wiring the model's Consolidation primitive through it is
disproportionate (a future SOSFS harvester would carry consolidations natively);
every other entry is routed to its own fs (SKVFS vs RSFS, SOSFS vs HSLF-FS) by the
basefile's own prefix.
"""

import json
from pathlib import Path

from ..lib import legacy_import
from ..lib.util import document_extension, record_path, write_atomic
from .agencies import LEGACY_CORPORA, REGISTRY


def _source_tag(corpus):
    """The `source` precedence marker an import stamps on its records."""
    return corpus + "-legacy"


def _identifier(fs, basefile):
    """The printed FS designation ("SKVFS 2012:5", "HSLF-FS 2016:39") from the
    agency's `designation` and the basefile's year:num."""
    return "%s %s" % (REGISTRY[fs].designation, basefile.split("/", 1)[1])


def _report(counts, corpus, log):
    log("foreskrift import-legacy %s: %d imported (%d pdf-body, %d metadata-only), "
        "%d null-stub, %d konsolidering, %d skipped-existing, %d skipped-live"
        % (corpus, counts["imported"], counts["pdf"], counts["metadata_only"],
           counts["null_stub"], counts["konsolidering"], counts["skipped_existing"],
           counts["skipped_live"]))
    return counts


def import_corpus(corpus, source_path, root, limit=None, force=False, log=print):
    """Import the frozen ``corpus`` tree at ``source_path`` into the föreskrift
    records under ``root``: per document a record ``<fs>/<slug>.json`` referencing
    its frozen regulation PDF in place (or a metadata-only record where the tree
    holds only a landing HTML). The fs (skvfs/rsfs, or sosfs/hslffs) comes from each
    entry's authoritative basefile, so the URI agrees with a citation by
    construction. Idempotent (a record already imported from this corpus is left
    untouched on a plain re-run; ``force`` rewrites it); a future live-harvest
    record is never overwritten. ``limit`` caps the run (a test slice). Returns the
    counts dict."""
    assert corpus in LEGACY_CORPORA, \
        "unknown föreskrift legacy corpus %r (have: %s)" \
        % (corpus, ", ".join(LEGACY_CORPORA))
    fs_codes = LEGACY_CORPORA[corpus]
    entries_dir = Path(source_path) / "entries"
    downloaded = Path(source_path) / "downloaded"
    assert entries_dir.is_dir() and downloaded.is_dir(), \
        "%s is not a frozen %s tree (need entries/ + downloaded/)" % (source_path, corpus)
    counts = dict(imported=0, pdf=0, metadata_only=0, null_stub=0,
                  konsolidering=0, skipped_existing=0, skipped_live=0)
    for entrypath in legacy_import.iter_entries(entries_dir):
        if limit is not None and counts["imported"] >= limit:
            break
        entry = json.loads(entrypath.read_text("utf-8"))
        basefile = entry.get("basefile")
        if basefile is None:                          # a failed-download stub
            counts["null_stub"] += 1
            continue
        if basefile.startswith("konsolidering/"):     # a consolidated text (see module docstring)
            counts["konsolidering"] += 1
            continue
        fs = basefile.split("/", 1)[0]
        assert fs in fs_codes, \
            "%s: basefile %r fs %r not in corpus %s" % (entrypath, basefile, fs, corpus)

        recpath = record_path(root, fs, basefile)
        existing = legacy_import.read_record(recpath)
        if not legacy_import.should_write(existing, _source_tag(corpus), force):
            assert existing is not None       # should_write(None, ...) is always True
            counts["skipped_live" if "source" not in existing else "skipped_existing"] += 1
            continue

        pdf = legacy_import.docdir(downloaded, entrypath, entries_dir) / "index.pdf"
        # only reference a real PDF (magic-sniffed): some index.pdf files are in
        # fact HTML/.doc (a mislabelled asset), which the PDF parser cannot read --
        # those become metadata-only, like the html-only documents.
        if pdf.exists() and document_extension(pdf.read_bytes()[:8]) == ".pdf":
            reg = {"legacy": legacy_import.rel(pdf), "url": entry.get("orig_url")}
            counts["pdf"] += 1
        else:
            reg = None
            counts["metadata_only"] += 1
        record = {
            "fs": fs, "basefile": basefile,
            "identifier": _identifier(fs, basefile),
            "title": entry.get("title"),
            "publisher": REGISTRY[fs].publisher,
            "url": entry.get("orig_url"),
            "source": _source_tag(corpus),
            "files": {"regulation": reg, "consolidation": [], "amendment": [],
                      "memo": [], "attachment": []},
        }
        write_atomic(recpath, json.dumps(record, ensure_ascii=False, indent=2))
        counts["imported"] += 1
    return _report(counts, corpus, log)
