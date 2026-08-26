"""Score remisser ai-analyze layers against the hand-built answer key.

Usage: evaluate.py <ärende basefile> <layers-root> [label]
       evaluate.py sou/2026-20 ~/repos/lagen-wiki/ann "current"

Keys are the `.ann.key` layers in the curated store (see README); `layers-root`
is the annstore root holding the `.ann` layers to judge -- the live store, or a
scratch root written by an experimental run, which is how two prompts are
compared on identical answers.

Reports section recall and precision, whether the sentiment falls inside the
interval a careful reader would accept, whether the quote is the on-point
sentence, and -- the measure that matters most -- whether an answer that
criticises something has that criticism represented at all.
"""

import json
import re
import sys
from pathlib import Path

from ferenda.lib import annstore, layout

ARENDE, LAYERS = sys.argv[1], Path(sys.argv[2])
LABEL = sys.argv[3] if len(sys.argv) > 3 else "layers"


def keys():
    """Every `.ann.key` for the ärende, with the basefile it judges. Keyed off
    the artifact tree rather than a glob of the store, so a key orphaned by a
    renamed answer surfaces as a missing layer rather than being scored."""
    for art in sorted((layout.ARTIFACT / "remisser" / ARENDE).glob("*.json.br")):
        bf = "remisser/%s/%s" % (ARENDE, art.name.split(".")[0])
        bf = bf[len("remisser/"):]
        p = annstore.path("remisser", bf, suffix=".ann.key")
        if p.exists():
            yield bf, json.loads(p.read_text())


def norm(s):
    """Whitespace-normalised, case-folded text for quote comparison -- the PDF
    extraction re-spaces freely, so byte equality would understate agreement."""
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


def overlaps(model_quote, key_quotes):
    """True when the model's quote and one of the key's on-point sentences are
    the same passage: either contains the other after normalisation. Containment
    (not equality) because the two may cut the sentence at different points."""
    m = norm(model_quote)
    if not m:
        return False
    return any(m in norm(k) or norm(k) in m for k in key_quotes if norm(k))


def layer_for(basefile):
    """The layer for one answer under `LAYERS`, or None when the run produced
    none. The path is the annstore layout, not a guess -- probing two candidates
    hid which root was actually being scored."""
    p = LAYERS / "remisser" / (basefile + ".ann")
    return json.loads(p.read_text()) if p.exists() else None


tot = dict.fromkeys(
    ("answers", "key_secs", "model_secs", "hit", "sent_ok", "sent_n",
     "quote_ok", "quote_n", "type_ok", "type_n",
     "crit_answers", "crit_found", "no_layer"), 0)
missed_crit, sec_rows = [], []

drifted = []
for bf, key in keys():
    layer = layer_for(bf)
    tot["answers"] += 1
    if layer is None:
        tot["no_layer"] += 1
        continue
    if key.get("meta", {}).get("inputs") and annstore.drifted(key["meta"]["inputs"]):
        # the answer has been reparsed since the key was authored, so its
        # on-point sentences are quoted from text that no longer exists
        drifted.append(bf)
    kmap = {s["forarbete_id"]: s for s in key.get("sections", [])}
    mmap = {s["forarbete_id"]: s for s in layer.get("segments", [])}
    tot["key_secs"] += len(kmap)
    tot["model_secs"] += len(mmap)
    hit = set(kmap) & set(mmap)
    tot["hit"] += len(hit)
    sec_rows.append((bf, len(kmap), len(mmap), len(hit)))

    for sid in hit:
        k, m = kmap[sid], mmap[sid]
        tot["sent_n"] += 1
        # a small tolerance: the key's interval is a judgement, not a measurement
        if k["sentiment_min"] - 0.15 <= m["sentiment"] <= k["sentiment_max"] + 0.15:
            tot["sent_ok"] += 1
        tot["quote_n"] += 1
        if overlaps(m.get("quote"), k.get("on_point", [])):
            tot["quote_ok"] += 1
        if "quote_type" in m:
            tot["type_n"] += 1
            # a key missing this field is an incomplete key, not evidence of
            # agreement -- defaulting it would score the harness's own gap as a
            # pass (rule:fail-fast)
            assert "reason_stated" in k, (
                "%s section %s: key has no reason_stated" % (bf, sid))
            if (m["quote_type"] == "grund") == k["reason_stated"]:
                tot["type_ok"] += 1

    if (key.get("has_criticism") or {}).get("present"):
        tot["crit_answers"] += 1
        # `_validate` guarantees both keys on every layer it writes; defaulting
        # over them would score a malformed layer as "no criticism" and quietly
        # mis-measure the number this harness exists for (rule:fail-fast)
        sents = ([s["sentiment"] for s in layer["segments"]]
                 + [layer["overall"]["sentiment"]])
        if any(x < -0.15 for x in sents):
            tot["crit_found"] += 1
        else:
            missed_crit.append((bf, (key["has_criticism"].get("summary") or "")[:95]))


def pct(a, b):
    return "n/a" if not b else "%5.1f%% (%d/%d)" % (100 * a / b, a, b)


print("=== %s vs answer key: %d answers scored, %d without a layer\n"
      % (LABEL, tot["answers"] - tot["no_layer"], tot["no_layer"]))
print("SECTION SELECTION")
print("  recall    (key sections the model found)      %s" % pct(tot["hit"], tot["key_secs"]))
print("  precision (model sections that are in the key)%s" % pct(tot["hit"], tot["model_secs"]))
print("\nON THE SECTIONS BOTH AGREE ON")
print("  sentiment inside the accepted interval       %s" % pct(tot["sent_ok"], tot["sent_n"]))
print("  quote is the on-point sentence               %s" % pct(tot["quote_ok"], tot["quote_n"]))
print("  quote_type matches whether a reason exists   %s" % pct(tot["type_ok"], tot["type_n"]))
print("\nCRITICISM RECALL  (the question that matters most)")
print("  answers the key says criticise something     %d" % tot["crit_answers"])
print("  ... where the layer has any negative segment %s" % pct(tot["crit_found"], tot["crit_answers"]))
if missed_crit:
    print("\n  criticism the layer missed entirely:")
    for bf, summary in missed_crit:
        print("    %-46s %s" % (bf.split("/", 2)[-1], summary))
if drifted:
    print("\n%d KEYS ARE STALE -- the answer was reparsed after the key was "
          "authored, so its on-point sentences may no longer occur:" % len(drifted))
    for bf in drifted[:10]:
        print("    %s" % bf.split("/", 2)[-1])

print("\nPER-ANSWER SECTIONS (key / model / matched)")
for bf, k, m, h in sorted(sec_rows, key=lambda r: -r[1])[:60]:
    print("  %-46s %3d %3d %3d" % (bf.split("/", 2)[-1], k, m, h))
