"""Write one briefing file per answer for an ärende, for an author to build the
answer key from.

Usage: make_briefs.py <ärende basefile> <n> <out dir>
       make_briefs.py sou/2026-20 50 /tmp/briefs

A brief carries the inquiry's section outline and the answer's full text, and
nothing else -- no generated layer, so a key authored from it cannot inherit the
machine's reading. The `n` longest answers are taken: length is a structural
proxy for engagement, and a random sample of this corpus is mostly boilerplate
"vi har inga synpunkter" replies, which measure nothing.
"""

import json
import sys
from pathlib import Path

from accommodanda.lib import compress, layout
from accommodanda.lib.util import basefile_slug
from accommodanda.remisser import ai_analyze

BRIEF = """REMISSVAR: %s
ORGANISATION: %s
ÄRENDE: %s

=== AVSNITTSÖVERSIKT (utredningens avsnitt; [id] rubrik) ===
%s

=== REMISSVARETS TEXT (stycken numrerade [pN]) ===
%s
"""


def main(arende, count, outdir):
    answers = sorted(
        (json.loads(compress.read_bytes(f))
         for f in (layout.ARTIFACT / "remisser" / arende).glob("*.json.br")),
        key=lambda a: -sum(len(p) for p in a["full_text"]))[:count]
    assert answers, "no parsed answers under %s" % arende

    ref = answers[0]["remitterat"][0]
    slug = layout.resolve_basefile(
        "forarbete", "%s/%s" % (ref["typ"], basefile_slug(ref["basefile"])),
        *(["%s/%s" % (ref["typ"], ref["slug"])] if ref.get("slug") else []))
    outline, ids = ai_analyze.section_outline(
        json.loads(compress.read_bytes(layout.artifact("forarbete", slug)))["structure"])
    print("host förarbete %s, %d sections" % (slug, len(ids)))

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for a in answers:
        body = "\n".join("[p%d] %s" % (i, p) for i, p in enumerate(a["full_text"]))
        (outdir / (a["basefile"].replace("/", "__").replace(":", "-") + ".txt")
         ).write_text(BRIEF % (a["basefile"], a["organisation"],
                               a["arende_titel"], outline, body))
    print("%d briefs in %s (shortest %d chars)"
          % (len(answers), outdir,
             sum(len(p) for p in answers[-1]["full_text"])))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), sys.argv[3])
