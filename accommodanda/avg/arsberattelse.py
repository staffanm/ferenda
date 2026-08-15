"""JO ämbetsberättelse pages: "2005/06 s. 171" -> diarienummer, for the
citation engine.

A JO decision is cited two ways: by diarienummer ("dnr 2042-2004") and by its
page in the printed ämbetsberättelse ("JO 2005/06 s. 171"). The dnr *is* the
document URI tail, so that form links by construction; the ämbetsberättelse
form names the same decision through a mapping only the corpus knows -- each
artifact records its own page as ``metadata.officialReport``. This module
sweeps the JO artifacts and writes that mapping to
``avg/data/arsberattelse.json``, the committed snapshot lib.lagrum's
``jo_arsb_ref`` production resolves through (the same shape as
``dv/data/namedcases.json``: the source owns the minting, the engine reads a
pure JSON snapshot).

Values are lists: nothing forbids two decisions from starting on one page, and
the engine only links a page that names exactly one decision. Re-run
``lagen avg arsberattelse`` after a JO harvest to refresh the snapshot.
"""

import json
import re

from ..lib import compress, layout
from ..lib.datasets import JO_ARSBERATTELSE

# "JO 2005/06 s. 335" and its legacy spellings ("Jo 2004/05 s. 423",
# "JO 2011/12 s.151", "91/92 s48") -- 1 607 of 1 608 artifacts at census
# 2026-08-15. The one leftover has a dnr misfiled in the field
# ("JO 5286-2018 s. 148") and is reported, not mapped.
RE_REPORT = re.compile(r"(?:JO|Jo|jo)?\s*(\d{2,4})/(\d{2})\s+s\.?\s*(\d+)$")


def harvest():
    """Rewrite the snapshot from the JO artifacts on disk. Returns the
    mapping ({"2005/06 s. 171": ["2042-2004"], ...}) plus the list of
    officialReport values that match no known spelling."""
    pages: dict[str, list[str]] = {}
    unparseable = []
    for path in layout.artifacts("avg"):
        if path.parent.name != "jo":
            continue
        art = json.loads(compress.read_text(str(path)))
        report = art["metadata"].get("officialReport")
        if not report:
            continue
        m = RE_REPORT.match(report.strip())
        if not m:
            unparseable.append(report)
            continue
        y1, y2, page = m.groups()
        if len(y1) == 2:            # legacy import spelling "91/92 s48"
            y1 = "19" + y1
        dnr = art["uri"].rsplit("/", 1)[1]
        pages.setdefault("%s/%s s. %s" % (y1, y2, page), []).append(dnr)
    snapshot = {k: sorted(v) for k, v in sorted(pages.items())}
    JO_ARSBERATTELSE.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    return snapshot, unparseable
