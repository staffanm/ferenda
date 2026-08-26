"""EU case identity: the case number a CELEX case is cited by, and the display
name a reader sees.

The EU mirror of `lib.casenaming` (which does the same for Swedish court
decisions): a cross-layer contract keyed on the CELEX, read identically by the
source that stamps it at parse time, the catalog row that labels every listing
and inbound citation, and the renderer's page heading. It depends only on the
shipped named-EU-cases snapshot (`eurlex/data/casenames.json`), never on a
source -- so it lives here in lib.

Two domain facts drive the naming:

  * a case's *identity* is its case number in the court's own form -- "C-311/18"
    (Court of Justice), "T-201/04" (General Court), "F-1/05" (the former Civil
    Service Tribunal) -- derived from the CELEX, which lays the number out as
    ``6·YYYY·<court>·<kind>·<serial>`` (the inverse of the citation engine's
    `fmt_ecj_ref`).
  * a handful of cases carry a usual name / nickname ("Schrems II", "Cassis de
    Dijon"). The Court publishes no such name as data (EUR-Lex/CELLAR carry only
    the full parties string, "Data Protection Commissioner v Facebook Ireland
    and Schrems"), so it is curated on Wikidata and harvested into
    `casenames.json` (`eurlex.casenames`). The nickname appears nowhere in the
    judgment text, so this is the only place it is attached; when present it
    leads the citation, "C-311/18 (Schrems II)".

`case_name` (the page heading) and `case_citation` (the inbound-citation label)
are the two display entry points; both fall back to the bare case number, which
every caselaw CELEX has.
"""

import functools
import json
import re

from .datasets import NAMEDEUCASES

# a caselaw CELEX: sector 6, four-digit year, court letter (C/T/F), document-kind
# letter (J judgment / C opinion / O order), four-digit serial. The two-digit
# year tail and the serial are what the case number is built from.
_CELEX_CASE = re.compile(r"6\d{2}(\d{2})([CTF])[A-Z](\d{4})$")


def case_number(celex):
    """The court's case number for a caselaw CELEX -- "62018CJ0311" -> "C-311/18",
    "62004TJ0201" -> "T-201/04", "62009CC0176" -> "C-176/09" (an AG opinion shares
    its judgment's case number). The bare CELEX for any sector-6 id that does not
    match the modern shape (a joined case, a pre-1954 id), so a listing never
    shows an empty name."""
    m = _CELEX_CASE.match(celex or "")
    if not m:
        return celex
    yy, court, serial = m.groups()
    return "%s-%d/%s" % (court, int(serial), yy)


@functools.cache
def _names():
    """CELEX -> usual name, from the shipped Wikidata snapshot. The file is
    committed (a checkout without it is broken, not merely un-harvested), so a
    missing file is a hard error rather than a silent empty map."""
    assert NAMEDEUCASES.exists(), (
        "%s missing -- a broken checkout, not an unharvested snapshot; run "
        "`lagen eurlex casenames` or restore the committed file" % NAMEDEUCASES)
    data = json.loads(NAMEDEUCASES.read_text(encoding="utf-8"))
    return {c["celex"]: c["name"] for c in data["cases"]}


def given_name(celex):
    """The case's curated usual name / nickname, or None -- "Schrems II" for
    "62018CJ0311"."""
    return _names().get(celex)


def case_name(celex):
    """The display name for a case page heading: the usual name when the case has
    one, else the bare case number -- "Schrems II", "C-176/09"."""
    return given_name(celex) or case_number(celex)


def case_citation(celex):
    """The case's citation label for an inbound reference: "Number (Name)" when
    named, else the bare number -- "C-311/18 (Schrems II)", "C-176/09"."""
    name = given_name(celex)
    number = case_number(celex)
    return "%s (%s)" % (number, name) if name else number
