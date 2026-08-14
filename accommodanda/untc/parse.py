""":class:`Treaty` artifacts, joined from a status page and an authentic text.

The status half scrapes the MTDSG page's stable ASP.NET control ids for the
treaty's conclusion, entry into force and UNTS registration, and its
participation table for each state's signature and
ratification/accession/succession.  The curated entry supplies the
authoritative title/Swedish-name/group the page itself does not.

The text half reads the articles from the depositary's own publication
(`text`), because the MTDSG carries no treaty text at all -- which is why these
artifacts used to ship six metadata rows and an empty structure, with nothing
for a citation to land on. Both halves are offline; neither touches the network.
"""

import re

from bs4 import BeautifulSoup

from ..lib import compress, patch
from ..lib.util import english_date, normalize_space
from . import text as treaty_text
from .download import page_path, text_path
from .model import Party, Provision, Treaty, load_treaties

# the consent-to-be-bound column closes each date with a case-sensitive action
# marker (a plain date is a ratification); the markers are documented in the
# column header itself ("Accession(a), Succession(d), Ratification")
RE_MARKER = re.compile(r"\d{4}\s*([A-Za-z]{1,2})\b")
ACTIONS = {"a": "accession", "d": "succession", "c": "formal confirmation",
           "A": "acceptance", "AA": "approval"}


def _text(soup, id_suffix):
    element = soup.find(id=re.compile(id_suffix + "$"))
    return normalize_space(element.get_text(" ", strip=True)) if element else None


def _date(value):
    # the MTDSG mixes full ("27 January 1980") and abbreviated ("27 Jun 2001")
    # month names; english_date's prefix matching covers both
    return english_date(value or "")


def _conclusion(value):
    """'Vienna, 23 May 1969' -> ('Vienna', '1969-05-23')."""
    if not value:
        return None, None
    place = value.split(",", 1)[0].strip() or None
    return place, _date(value)


def _clean(cell):
    """A table cell's text with its footnote superscripts dropped -- the
    reference numbers the MTDSG appends to a name ('Participant 3', 'Bosnia and
    Herzegovina 3'). The `<a class="noteIndex">` that wraps a declaring state's
    name is kept: the country name is its text, not a stray link."""
    for note in cell.find_all("sup"):
        note.decompose()
    return normalize_space(cell.get_text(" ", strip=True))


def _action(text):
    """The consent form a ratification-column cell records, from its trailing
    marker (case-sensitive: 'a' accession vs 'A' acceptance); a bare date is a
    ratification."""
    marker = RE_MARKER.search(text)
    return ACTIONS.get(marker.group(1), "ratification") if marker else "ratification"


def _participants(soup):
    """The participation table -> Party rows. Anchored on the grid's stable
    control id (`tblgrid`), not a header cell -- some treaties precede it with a
    territorial-notification table that also opens with 'Participant'. The
    columns vary (a signature column is absent where a treaty allows only
    accession), so the header names which column is the signature and which the
    consent-to-be-bound date."""
    table = soup.find(id=re.compile(r"tblgrid$"))
    if table is None:
        raise ValueError("MTDSG page carries no participation grid (tblgrid)")
    rows = table.find_all("tr")
    header = [_clean(cell) for cell in rows[0].find_all(["td", "th"])]
    action_col = len(header) - 1            # the last column is always the consent date
    signature_col = next((i for i, name in enumerate(header)
                          if i not in (0, action_col) and "Signature" in name), None)
    parties = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) <= action_col:
            continue
        country = _clean(cells[0])
        if not country:
            continue
        signature = (_date(_clean(cells[signature_col]))
                     if signature_col is not None else None)
        consent = _clean(cells[action_col])
        action_date = _date(consent)
        parties.append(Party(country=country, signature=signature,
                             action=_action(consent) if action_date else None,
                             action_date=action_date))
    return parties


def parse_page(entry, html, provisions=()):
    soup = BeautifulSoup(html, "html.parser")
    place, date = _conclusion(_text(soup, "rptTreaty_ctl00_tcText"))
    # the conclusion date is load-bearing (catalog date, year facet, folkrätt
    # sort); an in-force MTDSG instrument always states one, so its absence means
    # the control id drifted -- reject rather than ship a dateless artifact
    if date is None:
        raise ValueError("MTDSG %s: no conclusion date (control drift?)"
                         % entry["mtdsg_no"])
    return Treaty(
        mtdsg_no=entry["mtdsg_no"], unts=entry["unts"], chapter=entry["chapter"],
        title=entry["title"],
        conclusion_place=place, conclusion_date=date,
        entry_into_force=_text(soup, "rptEIF_ctl00_tcText"),
        registration=_text(soup, "rptRegistration_ctl00_tcText"),
        parties=_participants(soup),
        provisions=list(provisions),
    )


def read_provisions(entry, root, basefile):
    """The treaty's articles, read from whichever shape its depositary
    publishes. The reader is named in the curated entry, so nothing here sniffs
    the file (rule:configured-by-data)."""
    path = text_path(root, entry)
    # the downloader writes the text before the record is considered complete,
    # so a status page without one means a half-finished store rather than a
    # treaty the depositary does not publish (rule:fail-fast)
    if not compress.exists(path):
        raise ValueError("%s: the status page is stored without its text -- "
                         "re-run `lagen untc download --only %s`"
                         % (basefile, basefile))
    lines = (treaty_text.pdf_lines(path) if entry["text"]["reader"] == "pdf"
             else treaty_text.html_lines(
                 patch.apply("untc", basefile + ".text", compress.read_text(path))))
    return [Provision(fragment, heading, paragraphs)
            for fragment, heading, paragraphs in treaty_text.provisions(lines)]


def parse(basefile, root):
    entry = load_treaties()[basefile]
    html = patch.apply("untc", basefile,
                       compress.read_text(page_path(root, basefile)))
    return parse_page(entry, html,
                      read_provisions(entry, root, basefile)).to_artifact()
