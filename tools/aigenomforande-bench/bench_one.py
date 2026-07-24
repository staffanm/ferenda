"""Run the ai-genomforande pass for one prop against whatever endpoint/model
LLM_BASE_URL/BERGET_MODEL select, and write the payload + stats + wall time +
token usage as JSON. Never touches the annstore -- benchmark output only.

usage: bench_one.py <prop-basefile> <outfile.json>
"""
import json
import os
import sys
import time
import traceback

from accommodanda.forarbete import aigenomforande as A
from accommodanda.lib import compress, layout, llm

if os.environ.get("BENCH_MAX_TOKENS"):    # e.g. Kimi's long reasoning chains
    A.MAX_TOKENS = int(os.environ["BENCH_MAX_TOKENS"])
if os.environ.get("BENCH_BATCH_CHARS"):   # whole-law batching experiments;
    # batches() binds BATCH_CHARS as a default arg at def time, so patch the
    # function, not the constant
    _batches, _budget = A.batches, int(os.environ["BENCH_BATCH_CHARS"])
    A.batches = lambda entries, budget=None: _batches(entries, _budget)

prop, outfile = sys.argv[1], sys.argv[2]
art = json.loads(compress.read_bytes(layout.artifact("forarbete", prop)))
celexes = [c for c in A.detect_directives(art)
           if compress.exists(layout.artifact("eurlex", c))]
result = {"prop": prop, "model": llm.DEFAULT_MODEL, "api": llm.API_URL,
          "celexes": celexes}
t0 = time.monotonic()
try:
    payload, stats = A.annotate(
        art, celexes,
        progress=lambda i, n, label: print(
            "  [%s %s] batch %d/%d %s" % (prop, llm.DEFAULT_MODEL, i + 1, n,
                                          label), flush=True))
    result |= {"payload": payload, "stats": stats}
except Exception:
    # benchmark harness: a failed run (batch rejected twice, endpoint error)
    # is itself a datapoint to record, not a reason to lose the other runs
    result["error"] = traceback.format_exc()
result |= {"elapsed": time.monotonic() - t0, "usage": llm.USAGE}
with open(outfile, "w") as f:
    json.dump(result, f, ensure_ascii=False)
print("%s %s: %.1fs, %s" % (prop, llm.DEFAULT_MODEL, result["elapsed"],
                            result.get("stats") or "ERROR"), flush=True)
