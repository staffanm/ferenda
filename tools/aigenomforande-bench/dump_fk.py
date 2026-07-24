"""Dump each eligible 2025/26 prop's candidate FK entries + tagged directive
catalog to plain files, so ground-truth readers and the evaluator work from
the exact same inputs the ai-genomforande pass sees.

Writes, per prop, under scratchpad/fk/:
  <n>.txt           - directives block + every candidate entry as [G<i>] law | where
  <n>.entries.json  - the candidate entries in G-id order (for resolving ids)
  <n>.catalog.json  - the directive catalog (tag, celex, label, articles)
"""
import json
import sys
from pathlib import Path

from accommodanda.forarbete import aigenomforande as A
from accommodanda.lib import compress, layout

PROPS = [3, 16, 28, 43, 84, 108, 118, 124, 129, 146, 159, 183, 186, 202,
         240, 253, 262, 265, 278, 303]
OUT = Path(__file__).parent / "fk"
OUT.mkdir(exist_ok=True)

for n in PROPS:
    prop = "prop/2025-26-%d" % n
    art = json.loads(compress.read_bytes(layout.artifact("forarbete", prop)))
    celexes = [c for c in A.detect_directives(art)
               if compress.exists(layout.artifact("eurlex", c))]
    catalog = A.build_catalog(celexes, art)
    entries = A.candidate_entries(art)
    blocks = []
    for i, e in enumerate(entries, 1):
        blocks.append("[G%d] %s | %s\n%s" % (i, e.get("law") or "?",
                                             A._where(e), e["kommentar"]))
    (OUT / ("%d.txt" % n)).write_text(
        "DIREKTIV OCH ARTIKLAR:\n%s\n\nFÖRFATTNINGSKOMMENTAR (%d kandidater):\n\n%s"
        % (A.directives_block(catalog), len(entries), "\n\n".join(blocks)))
    (OUT / ("%d.entries.json" % n)).write_text(json.dumps(entries, ensure_ascii=False))
    (OUT / ("%d.catalog.json" % n)).write_text(json.dumps(
        [{k: v for k, v in d.items() if k != "valid"} for d in catalog],
        ensure_ascii=False))
    size = sum(len(e["kommentar"]) for e in entries)
    print("%s: %d candidates, %d chars, directives %s" %
          (prop, len(entries), size, ",".join(celexes)), file=sys.stderr)
