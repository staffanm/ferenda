"""Benchmark the regleringshierarki LLM passes (lib.aihierarki) against the
hand-keyed golden for the PRD's ten worked example chains.

    .venv/bin/python tools/hierarki-bench/bench.py --mode batch --label qwen38-batch

Scores three things per run and writes results/<label>.json:

  * task A: subject spans per delegation clause, against the full hand-keyed
    subject lists (recall over required subjects; precision counts a span
    right when it matches a required or an accepted-extra subject)
  * end to end: the produced rows for each chain's concept against the
    golden rungs (a rung marked "hand" -- the source itself says it was
    found by hand -- is reported separately, never in recall)
  * cost: calls, seconds and tokens per task, plus total wall time

Matching is fold-based and containment-tolerant on both sides: the span
"vad som utgör en betydande incident" hits the subject "betydande incident",
and a produced row at K2P5S1 hits the golden rung K2P5. Runs from the repo
root (ferenda is an editable install, no path surgery needed)."""

import argparse
import json
import time
from pathlib import Path

from ferenda.lib import aihierarki, catalog, concepts
from ferenda.lib.hierarki import _anchor_within
from ferenda.lib.util import normalize_fold

GOLDEN = Path(__file__).resolve().parents[2] / \
    "test/files/regleringshierarki/golden-ten.json"
RESULTS = Path(__file__).resolve().parent / "results"


class CallCache:
    """Per-request checkpoint: every validated reply appended to a JSONL as
    it lands, keyed by the prompt hash, so a crashed or restarted run never
    pays a finished LLM call twice. Result-dict keys are tuples; they ride
    as lists and come back as tuples (a plain-string key stays a string)."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        if path.exists():
            for line in path.read_text("utf-8").splitlines():
                rec = json.loads(line)
                self.data[rec["k"]] = {
                    (tuple(k) if isinstance(k, list) else k): v
                    for k, v in rec["v"]}

    def __contains__(self, key):
        return key in self.data

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"k": key,
                 "v": [[list(k) if isinstance(k, tuple) else k, v]
                       for k, v in value.items()]}, ensure_ascii=False) + "\n")


def _contains(a, b):
    """Word-bounded containment either way, folded."""
    fa, fb = normalize_fold(a), normalize_fold(b)
    return " %s " % fb in " %s " % fa or " %s " % fa in " %s " % fb


def _matches_concept(term, concept):
    """Inflection-aware: the row term "säkerhetsåtgärder" is the golden
    concept "säkerhetsåtgärd" (term_pattern covers the ending classes)."""
    return (_contains(term, concept)
            or bool(concepts.term_pattern(concept)
                    .search(normalize_fold(term)))
            or bool(concepts.term_pattern(term)
                    .search(normalize_fold(concept))))


def score_a(chain, spans):
    """(required_hits, required_total, precise_spans, produced_total)."""
    req_hit = req_total = ok = produced = 0
    for cl in chain["a_clauses"]:
        got = spans.get((cl["doc"], cl["anchor"]), [])
        produced += len(got)
        accepted = cl["subjects"] + cl.get("extra_ok", [])
        for subj in cl["subjects"]:
            req_total += 1
            if any(_contains(g, subj) for g in got):
                req_hit += 1
        for g in got:
            if any(_contains(g, a) for a in accepted):
                ok += 1
    return req_hit, req_total, ok, produced


def score_rows(chain, rows):
    """Golden-rung recall + role accuracy + concept-scoped false rows."""
    concept = chain["concept"]
    if not concept:
        return {"rungs_hit": 0, "rungs_total": 0, "role_hit": 0,
                "hand_hit": 0, "hand_total": 0,
                "false_rows": 0, "concept_rows": 0}
    mine = [r for r in rows if _matches_concept(r[2], concept)]
    hit = role_hit = hand_hit = 0
    matched = set()
    plain = [r for r in chain["rungs"] if r.get("note") != "hand"]
    hand = [r for r in chain["rungs"] if r.get("note") == "hand"]
    for rung in plain + hand:
        found = [r for r in mine if r[0] == rung["doc"]
                 and (_anchor_within(r[1], rung["anchor"])
                      or _anchor_within(rung["anchor"], r[1]))]
        if not found:
            continue
        matched.update(found)
        if rung in hand:
            hand_hit += 1
        else:
            hit += 1
            if any(r[3] in rung["roles"] for r in found):
                role_hit += 1
    return {"rungs_hit": hit, "rungs_total": len(plain),
            "role_hit": role_hit, "hand_hit": hand_hit,
            "hand_total": len(hand),
            "false_rows": len([r for r in mine if r not in matched]),
            "concept_rows": len(mine)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("single", "batch"), required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--chains", nargs="*", help="chain names; default all")
    ap.add_argument("--rescore", help="re-score a saved results json "
                    "(no LLM calls)")
    args = ap.parse_args()
    golden = json.loads(GOLDEN.read_text("utf-8"))
    chains = [c for c in golden["chains"]
              if not args.chains or c["name"] in args.chains]
    saved = json.loads(Path(args.rescore).read_text("utf-8")) \
        if args.rescore else None
    con = None if saved else catalog.connect("site/data/catalog.sqlite")
    RESULTS.mkdir(exist_ok=True)
    cache = CallCache(RESULTS / ("%s.calls.jsonl" % args.label))
    out = {"label": args.label, "mode": args.mode, "chains": {}}
    totals = aihierarki.new_stats()
    t0 = time.perf_counter()
    for chain in chains:
        ct0 = time.perf_counter()
        clauses = [(cl["doc"], cl["anchor"]) for cl in chain["a_clauses"]]
        if saved:
            prev = saved["chains"].get(chain["name"])
            if not prev:
                continue
            rows = [tuple(r) for r in prev["produced_rows"]]
            stats = prev["stats"]
        else:
            rows, stats = aihierarki.run_component(
                con, chain["docs"], clauses, batched=args.mode == "batch",
                cache=cache)
        spans = {}          # the A result, read back off the rows
        for doc, anchor, term, role, _label in rows:
            if role == "delegerar" and (doc, anchor) in clauses:
                spans.setdefault((doc, anchor), []).append(term)
        a = score_a(chain, spans)
        r = score_rows(chain, rows)
        subjects = chain.get("d_subjects", [])
        r["subject_found"] = bool(subjects) and any(
            _matches_concept(term, subj)
            for term in {x[2] for x in rows} for subj in subjects)
        out["chains"][chain["name"]] = {
            "a": {"required_hit": a[0], "required_total": a[1],
                  "ok_spans": a[2], "produced": a[3]},
            "rows": r, "stats": stats,
            "wall": (prev["wall"] if saved
                     else round(time.perf_counter() - ct0, 1)),
            "produced_rows": [list(x) for x in rows]}
        for k, v in stats.items():
            totals[k] += v
        # checkpoint after every chain: a crash on chain N must not discard
        # chains 1..N-1 (socialförsäkringsbalken cost this run 45 minutes)
        RESULTS.mkdir(exist_ok=True)
        (RESULTS / ("%s.partial.json" % args.label)).write_text(
            json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
        print("%-26s subj %s  A %d/%d req (%d/%d spans ok)  rungs %d/%d  "
              "roles %d  hand %d/%d  fp %d  %.0fs"
              % (chain["name"], "y" if r["subject_found"] else "N",
                 a[0], a[1], a[2], a[3],
                 r["rungs_hit"], r["rungs_total"], r["role_hit"],
                 r["hand_hit"], r["hand_total"], r["false_rows"],
                 out["chains"][chain["name"]]["wall"]), flush=True)
    out["totals"] = totals
    out["wall"] = saved["wall"] if saved \
        else round(time.perf_counter() - t0, 1)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / ("%s.json" % args.label)).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    ra = [c["a"] for c in out["chains"].values()]
    rr = [c["rows"] for c in out["chains"].values()]
    calls = sum(totals[t + "_calls"]
                for t in ("a", "b1", "b2", "c", "d"))
    print("== %s: subjects %d/%d | A recall %d/%d, A precision %d/%d | "
          "rungs %d/%d, roles right %d, hand %d/%d, false rows %d | "
          "%d calls, %.0fs, %d+%d tokens"
          % (args.label,
             sum(1 for x in rr if x.get("subject_found")), len(rr),
             sum(x["required_hit"] for x in ra),
             sum(x["required_total"] for x in ra),
             sum(x["ok_spans"] for x in ra),
             sum(x["produced"] for x in ra),
             sum(x["rungs_hit"] for x in rr),
             sum(x["rungs_total"] for x in rr),
             sum(x["role_hit"] for x in rr),
             sum(x["hand_hit"] for x in rr),
             sum(x["hand_total"] for x in rr),
             sum(x["false_rows"] for x in rr),
             calls, out["wall"],
             sum(totals[t + "_prompt_tokens"]
                 for t in ("a", "b1", "b2", "c", "d")),
             sum(totals[t + "_completion_tokens"]
                 for t in ("a", "b1", "b2", "c", "d"))))


if __name__ == "__main__":
    main()
