"""Named EU cases: CELEX -> usual name / nickname ("Schrems II"), for the case
page heading and the inbound-citation label.

The Court of Justice assigns no official "usual name" as data. EUR-Lex/CELLAR
carry only the full parties string ("Data Protection Commissioner v Facebook
Ireland and Schrems"), not the short nicknames practitioners actually use
("Schrems II", "Cassis de Dijon", "Costa v ENEL"). Those live on Wikidata, where
an EU-case item carries its CELEX number (property P476) and its label is the
usual name. This module harvests that mapping into ``eurlex/data/casenames.json``
so `lib.eucasenaming` can label a case by its nickname; the name appears nowhere
in the judgment text, so this is the only place it is attached.

Coverage is the famous cases only (a few hundred -- Wikidata models the notable
ones); every other case falls back to its bare case number ("C-176/09"), so the
snapshot is enrichment, not a completeness contract. Wikidata is one curated
source behind a stable JSON: swapping it for another (a hand-curated list, a
different endpoint) is a change to this harvester alone, not to the eucasenaming
readers.

The committed JSON is the shipped snapshot; re-run ``lagen eurlex casenames`` to
refresh it as Wikidata grows.
"""

import json
import re

from ..lib import net, util
from ..lib.datasets import NAMEDEUCASES
from .download import USER_AGENT

WDQS = "https://query.wikidata.org/sparql"

# every Wikidata item that carries a case-law (sector 6) CELEX number, with the
# item's label resolved once via the label service under a language preference
# (nicknames are usually language-neutral; sv/en lead, mul/fr/de/it back them
# up). The service returns the Q-id itself when an item has no label in any of
# them -- those are dropped in parse (an item with no usable name).
QUERY = """
SELECT ?celex ?itemLabel WHERE {
  ?item wdt:P476 ?celex .
  FILTER(STRSTARTS(?celex, "6"))
  SERVICE wikibase:label { bd:serviceParam wikibase:language "sv,en,mul,fr,de,it". }
}
ORDER BY ?celex
"""

# the label service's fallback when an item has no label in the requested
# languages: the bare Q-id ("Q12345") -- not a name, so such rows are skipped
_QID = re.compile(r"^Q\d+$")


def parse_bindings(bindings):
    """The SPARQL result bindings -> case records: one ``{celex, name}`` per named
    case, deduplicated by CELEX (the first name wins on the rare item carrying two
    CELEX or two items sharing one), Q-id fallbacks dropped. Sorted by CELEX for a
    stable committed snapshot."""
    names = {}
    for row in bindings:
        celex, name = row["celex"]["value"], row["itemLabel"]["value"]
        if not _QID.match(name):
            names.setdefault(celex, name)
    return [{"celex": celex, "name": names[celex]} for celex in sorted(names)]


def harvest(out_path=NAMEDEUCASES, session=None):
    """Query Wikidata for EU cases with a CELEX number and write the parsed
    records to ``out_path`` (the committed snapshot). Returns the records. A
    network failure propagates (the existing snapshot stays in place)."""
    session = session or net.make_session(USER_AGENT)
    bindings = net.request(session, "GET", WDQS, parse_json=True, timeout=120,
                           params={"query": QUERY, "format": "json"}
                           )["results"]["bindings"]
    cases = parse_bindings(bindings)
    util.write_atomic(
        out_path,
        json.dumps({"_comment": "Named EU cases (CELEX -> usual name), harvested "
                    "from Wikidata (P476 CELEX number -> item label). Refresh "
                    "with `lagen eurlex casenames`. See eurlex/casenames.py.",
                    "_source_url": WDQS, "cases": cases},
                   ensure_ascii=False, indent=1) + "\n")
    return cases


if __name__ == "__main__":
    cases = harvest()
    print("downloaded %d named EU cases -> %s" % (len(cases), NAMEDEUCASES))
