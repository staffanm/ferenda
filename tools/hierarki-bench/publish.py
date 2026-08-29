"""Publish regleringshierarki rows as curated `.ann` layers, which relate's
cross block merges into the table (`hierarki.hierarki_layers`).

Two modes:

    publish.py --results results/gemma4-batch-d.json
        publish a bench run's produced rows

    publish.py --seed https://lagen.nu/2018:585
        expand the seed's chain component from the catalog, run the full
        A/D/B/C pipeline against the serving model, publish the rows

Layers land as <annstore tree>/hierarki/<slug>.ann with payload key
"regleringshierarki"; the join key is the recorded uri, never the path."""

import argparse
import json
from pathlib import Path

from ferenda.lib import aihierarki, catalog
from ferenda.lib.aihierarki import (
    component,
    write_layers,
)


def publish_rows(con, rows):
    return write_layers(con, rows, force=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results")
    ap.add_argument("--seed")
    ap.add_argument("--chains", nargs="*",
                    help="with --results: chain names to publish; default all")
    args = ap.parse_args()
    con = catalog.connect("site/data/catalog.sqlite")
    if args.results:
        d = json.loads(Path(args.results).read_text("utf-8"))
        rows = [tuple(r) for name, c in d["chains"].items()
                if not args.chains or name in args.chains
                for r in c["produced_rows"]]
        print("published %d layers from %d rows"
              % (publish_rows(con, rows), len(rows)))
        return
    docs, clauses = component(con, args.seed)
    print("component: %d documents, %d pinned clauses" %
          (len(docs), len(clauses)))
    rows, stats = aihierarki.run_component(con, docs, clauses, batched=True)
    calls = sum(stats[t + "_calls"] for t in ("a", "b1", "b2", "c", "d"))
    print("rows %d over %d calls" % (len(rows), calls))
    print("published %d layers" % publish_rows(con, rows))


if __name__ == "__main__":
    main()
