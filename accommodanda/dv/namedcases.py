"""Named HD cases: nickname -> NJA referat -> case URI, for the ⌘K resolver.

Högsta domstolen publishes an official, regularly-updated list of *named*
precedents ("Namngivna rättsfall") -- a one-line nickname per case
("Instagrambilden", "Girjasdomen") beside its NJA referat ("NJA 2020 s. 273").
The nicknames are how practitioners actually refer to these cases, yet they
appear nowhere in the case text, so full-text search can never find them. This
module harvests that list into ``dv/data/namedcases.json`` so the resolver can
turn the nickname straight into the published case URI.

The list is a clean two/three-column PDF table (A: NJA ref, B: namn, C: mål nr),
which ``pdftotext -layout`` lays out faithfully -- so the parse is a per-line
regex, not the font-aware reflow the document verticals need. Each determinate
referat is run through the *same* ``lib.casenaming.case_uri`` the corpus mints
with, so the harvested URI is byte-identical to the published document.

The committed JSON is the shipped snapshot (resolve reads it directly); re-run
``lagen dv namedcases`` to refresh it as HD updates the list. The newest entries
carry "s. xxx" (the NJA page isn't assigned yet) and so get no URI -- recorded
for completeness, but they only start resolving once their page is set.
"""

import json
import re
import subprocess
import tempfile

from ..lib import net
from ..lib.casenaming import case_uri
from ..lib.datasets import NAMEDCASES
from .download import USER_AGENT

URL = ("https://www.domstol.se/globalassets/filer/domstol/hogstadomstolen/"
       "namngivna-rattsfall/officiell-lista-over-namngivna-rattsfall.pdf")

# a table row: leading row number, then column A (the NJA reference, single-spaced
# internally) up to the first 2+-space column gap, then the name and an optional
# mål-nr column (again 2+-space separated). Column A always opens "<year> s. ".
_ROW = re.compile(r"^\s*\d+\s+(\d{4} s\. .+?)\s{2,}(.+?)\s*$")
_NJA_REF = re.compile(r"^\d{4} s\. ")
_COLGAP = re.compile(r"\s{2,}")


def _pdf_text(pdf_bytes):
    """The PDF laid out as text (``pdftotext -layout``), preserving the column
    alignment the row parse keys on."""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf_bytes)
        f.flush()
        return subprocess.run(["pdftotext", "-layout", f.name, "-"],
                              capture_output=True, check=True).stdout.decode("utf-8")


def parse_rows(text):
    """Parse the laid-out table text into case records: one dict per row with
    ``namn`` (the nickname), ``referat`` ("NJA <year> s. <page>"), ``malnr`` (or
    None), and ``uri`` -- the case URI for a determinate referat, None while the
    referat is still "s. xxx" (page unassigned). Non-row lines (the title, the
    column header, page numbers) don't match and are skipped."""
    out = []
    for line in text.splitlines():
        m = _ROW.match(line)
        if not m or not _NJA_REF.match(m.group(1)):
            continue
        cola, rest = m.group(1).strip(), m.group(2)
        parts = _COLGAP.split(rest, maxsplit=1)
        namn = parts[0].strip()
        malnr = parts[1].strip() if len(parts) > 1 else None
        referat = "NJA " + cola
        # "s. xxx" => the NJA page isn't assigned yet, so no canonical URI exists
        uri = None if "xxx" in cola.lower() else case_uri(referat)
        out.append({"namn": namn, "referat": referat,
                    "malnr": malnr, "uri": uri})
    return out


def parse(pdf_bytes):
    """The case records in the named-rättsfall PDF (parse_rows over its laid-out
    text)."""
    return parse_rows(_pdf_text(pdf_bytes))


def harvest(out_path=NAMEDCASES, session=None):
    """Download HD's named-rättsfall list and write the parsed records to
    ``out_path`` (the committed snapshot). Returns the records. A network failure
    propagates (the existing snapshot stays in place as the fallback)."""
    session = session or net.make_session(USER_AGENT)
    pdf_bytes = net.request(session, "GET", URL).content
    cases = parse(pdf_bytes)
    out_path.write_text(
        json.dumps({"_comment": "Named HD cases (nickname -> NJA referat -> "
                    "case URI), harvested from HD's official list. Refresh with "
                    "`lagen dv namedcases`. See dv/namedcases.py.",
                    "_source_url": URL, "cases": cases},
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    return cases


if __name__ == "__main__":
    cases = harvest()
    resolvable = sum(1 for c in cases if c["uri"])
    print("harvested %d named cases (%d resolvable) -> %s"
          % (len(cases), resolvable, NAMEDCASES))
