"""Import authored answer-key JSON into the curated store as `.ann.key` layers.

Usage: import_keys.py <dir of authored json>

The key belongs beside the layers it judges, in the git-backed store, so it is
versioned, reviewable and hand-correctable -- the same place
`tools/aigenomforande-bench` puts its `.ann.golden`. Each key records the hash of
the answer artifact it was authored against: an `on_point` sentence is verbatim
from the text as extracted *then*, so a later reparse can invalidate it, and
`evaluate.py` reports which keys have drifted rather than silently scoring
against a sentence that no longer exists.
"""

import json
import sys
from pathlib import Path

from accommodanda.lib import annstore

REQUIRED = ("basefile", "overall", "sections", "has_criticism")


def main(src):
    written = skipped = 0
    for f in sorted(Path(src).glob("*.json")):
        key = json.loads(f.read_text())
        missing = [k for k in REQUIRED if k not in key]
        if missing:
            print("SKIP %s -- missing %s" % (f.name, ", ".join(missing)))
            skipped += 1
            continue
        out = annstore.path("remisser", key["basefile"], suffix=".ann.key")
        annstore.write(out, {k: key[k] for k in key if k != "basefile"},
                       annstore.artifact_input("remisser", key["basefile"]),
                       force=True)
        written += 1
    print("%d keys written, %d skipped" % (written, skipped))


if __name__ == "__main__":
    main(sys.argv[1])
