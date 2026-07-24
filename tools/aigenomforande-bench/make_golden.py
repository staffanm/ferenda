"""Convert the subagent-authored ground-truth mappings (golden_raw/<n>.json)
into .ann.golden layers in the curated store, running each mapping through the
SAME validation the ai-genomforande pass applies (real entry, catalogued
directive, articles resolving to the inventory, quote occurring in the entry's
commentary) and the same edge fan-out -- so the golden layer is shape-identical
to a generated .ann layer. Reports every dropped mapping for adjudication.
"""
import json
import sys
from datetime import date
from pathlib import Path

from accommodanda.forarbete import aigenomforande as A
from accommodanda.forarbete import kommentar
from accommodanda.lib import annstore, compress, layout
from accommodanda.lib.util import normalize_fold as _norm

SP = Path(__file__).parent
PROPS = [3, 16, 28, 43, 84, 108, 118, 124, 129, 146, 159, 183, 186, 202,
         240, 253, 262, 265, 278, 303]

for n in PROPS:
    raw_path = SP / "golden_raw" / ("%d.json" % n)
    if not raw_path.exists():
        print("prop %d: NO golden_raw file yet, skipping" % n, file=sys.stderr)
        continue
    prop = "prop/2025-26-%d" % n
    entries = json.loads((SP / "fk" / ("%d.entries.json" % n)).read_text())
    catalog = json.loads((SP / "fk" / ("%d.catalog.json" % n)).read_text())
    for d in catalog:
        d["valid"] = set(d["articles"])
    by_celex = {d["celex"]: d for d in catalog}
    by_id = {"G%d" % i: e for i, e in enumerate(entries, 1)}
    art = json.loads(compress.read_bytes(layout.artifact("forarbete", prop)))

    raw = json.loads(raw_path.read_text())
    edges, dropped = [], []
    for m in raw.get("mappings", []):
        entry, d = by_id.get(m.get("entry")), by_celex.get(m.get("celex"))
        quote = (m.get("quote") or "").strip()
        if entry is None or d is None or not quote:
            dropped.append((m, "unknown entry/celex or empty quote"))
            continue
        resolved = A._articles(m.get("articles") or [], d["valid"])
        if resolved is None:
            dropped.append((m, "articles do not resolve to inventory"))
            continue
        if _norm(quote)[:A.QUOTE_KEY] not in _norm(entry["kommentar"]):
            dropped.append((m, "quote not found in entry commentary"))
            continue
        pins, bases = resolved
        item = {"entry": m["entry"], "tag": d["tag"], "articles": bases,
                "pinpoints": pins, "partial": bool(m.get("partial")),
                "quote": quote}
        edges += A.edges_for(item, by_id, catalog)

    payload = {
        "meta": {"status": "golden", "model": "claude subagents (opus/sonnet)",
                 "generated": date.today().isoformat(),
                 "inputs": {**annstore.artifact_input("forarbete", prop),
                            **{k: v for d in catalog for k, v in
                               annstore.artifact_input("eurlex", d["celex"]).items()}}},
        "genomforande": {"directives": [d["uri"] for d in catalog],
                         "proposition": art["uri"], "edges": edges}}
    out = annstore.path("forarbete", prop, ".ann.golden")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    para = len({(e["law"], e["chapter"], e["paragraf"]) for e in edges})
    print("%s: %d raw -> %d edges over %d paragrafer, %d dropped -> %s"
          % (prop, len(raw.get("mappings", [])), len(edges), para,
             len(dropped), out))
    for m, why in dropped:
        print("   DROPPED %s %s %s: %s | quote: %.60s"
              % (m.get("entry"), m.get("celex"), m.get("articles"), why,
                 m.get("quote") or ""), file=sys.stderr)
