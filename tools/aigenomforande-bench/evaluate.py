"""Score every benchmark run against the .ann.golden ground truth.

An edge is compared on (law, chapter, paragraf, directive, article) -- the
base-article granularity `genomforande.resolve` pins. Pinpoint agreement is
scored separately on the true positives. Cost is computed from the recorded
token usage at the per-model EUR/MT prices.
"""
import json
from pathlib import Path

from ferenda.lib import annstore

SP = Path(__file__).parent
PROPS = [3, 16, 28, 43, 84, 108, 118, 124, 129, 146, 159, 183, 186, 202,
         240, 253, 262, 265, 278, 303]
CONFIGS = ["local", "gptoss", "kimi", "mistral", "glm", "gemma", "gemma-local"]
# EUR per million tokens (input, output)
PRICE = {"local": (0, 0), "gptoss": (0.2, 0.75), "kimi": (0.75, 3.5),
         "mistral": (1.5, 5.0), "glm": (1.4, 4.4), "gemma": (0.25, 0.5), "gemma-local": (0, 0)}


def keys(edges):
    """{(law, chapter, paragraf, directive, article)} plus a pinpoint lookup."""
    ks, pins = set(), {}
    for e in edges:
        for a in e["articles"]:
            k = (e.get("law"), e.get("chapter"), e.get("paragraf"),
                 e["directive"], a)
            ks.add(k)
            pp = [p for p in e.get("pinpoints", [])
                  if p.split(".")[0].split()[0] == a]
            pins.setdefault(k, set()).update(pp)
    return ks, pins


golden = {}
for n in PROPS:
    p = annstore.path("forarbete", "prop/2025-26-%d" % n, ".ann.golden")
    if p.exists():
        golden[n] = json.loads(p.read_text())["genomforande"]["edges"]

total = {c: {"tp": 0, "fp": 0, "fn": 0, "pin_match": 0, "pin_total": 0,
             "sec": 0.0, "in_tok": 0, "out_tok": 0, "errors": []}
         for c in CONFIGS}
per_prop = {}

for n in sorted(golden):
    gk, gpins = keys(golden[n])
    row = {"golden": len(gk)}
    for c in CONFIGS:
        f = SP / "bench" / c / ("%d.json" % n)
        if not f.exists():
            row[c] = None
            continue
        d = json.loads(f.read_text())
        t = total[c]
        t["sec"] += d["elapsed"]
        t["in_tok"] += d["usage"]["prompt_tokens"]
        t["out_tok"] += d["usage"]["completion_tokens"]
        if "error" in d:
            t["errors"].append(n)
            row[c] = "ERR"
            continue
        mk, mpins = keys(d["payload"]["genomforande"]["edges"])
        tp, fp, fn = len(mk & gk), len(mk - gk), len(gk - mk)
        t["tp"] += tp
        t["fp"] += fp
        t["fn"] += fn
        for k in mk & gk:
            if gpins.get(k):
                t["pin_total"] += 1
                if gpins[k] == mpins.get(k, set()):
                    t["pin_match"] += 1
        row[c] = (tp, fp, fn)
    per_prop[n] = row

print("Per-prop (tp, fp, fn) against golden:")
fmt = "%-6s %6s  " + " ".join("%-15s" for _ in CONFIGS)
print(fmt % ("prop", "golden", *CONFIGS))
for n, row in per_prop.items():
    print(fmt % (n, row["golden"], *[str(row.get(c)) for c in CONFIGS]))

print("\nAggregate:")
print("%-8s %6s %6s %6s %7s %7s %7s %7s %9s %8s %8s %9s %s"
      % ("config", "tp", "fp", "fn", "prec", "rec", "F1", "F0.5", "pinpoint",
         "time", "in_MT", "out_MT", "cost_EUR"))
for c in CONFIGS:
    t = total[c]
    prec = t["tp"] / (t["tp"] + t["fp"]) if t["tp"] + t["fp"] else 0
    rec = t["tp"] / (t["tp"] + t["fn"]) if t["tp"] + t["fn"] else 0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
    # F0.5 weights precision twice as heavily as recall -- for the legal
    # margin note a wrong genomför-claim is worse than a missing one
    f05 = (1.25 * prec * rec / (0.25 * prec + rec)) if prec + rec else 0
    pin = ("%d/%d" % (t["pin_match"], t["pin_total"])) if t["pin_total"] else "-"
    cost = (t["in_tok"] * PRICE[c][0] + t["out_tok"] * PRICE[c][1]) / 1e6
    print("%-8s %6d %6d %6d %6.1f%% %6.1f%% %6.1f%% %6.1f%% %9s %7.0fs %8.3f %8.3f %9.3f %s"
          % (c, t["tp"], t["fp"], t["fn"], prec * 100, rec * 100, f1 * 100,
             f05 * 100, pin, t["sec"], t["in_tok"] / 1e6, t["out_tok"] / 1e6,
             cost, ("errors: %s" % t["errors"]) if t["errors"] else ""))
