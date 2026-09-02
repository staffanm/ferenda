"""The urt (Upphandlingsrättslig Tidskrift) walk: the journal's one open-access
listing, then each article's own page and the PDF that page sets.

The listing is a flat run of entries, year heading over its articles: the
author's name, a break, and a link whose own text is the article's title and
citation ("… UrT 2026 no 1 p. 1"). The journal has set these entries in one
hand for twelve years, and an entry that sets no citation is an entry the
journal did not set, and the walk says so.

The listing's link goes to the article's own page, not to the PDF: the page
states the article's issue, opening page and year in its "Volym/Sida/År"
line and sets the PDF. The record takes each of these off the page itself,
the listing's citation beside it -- and a listing that states a year or page
the page states otherwise is a listing the journal has broken, and the walk
says so too. A page that sets its meta line beside no PDF link is an
article the journal published in the print issue only, and it names a
print-only record.
"""

import re
import time

from bs4 import BeautifulSoup

from ..lib import harvest, net
from ..lib.util import normalize_space
from .journals import URT

__all__ = ["urt_sync"]

# The listing's citation, the hand the journal has kept for twelve years.
# The "UrT" token, the "no" and the page's marker ("p." on the newer
# entries, "s." on the older) are each set on some entries and dropped on
# the others; the year is set on every entry. A combined issue's two numbers
# are a required digit run with an optional dash extension, not a nested
# optional group: 3.14's `re` mis-compiles the nested form when `\s*` sits
# between its literals, and this hand sets a space on both sides of the run.
RE_URT_SLUG = re.compile(r"/reportage/([^/]+)/?$")

RE_URT_CITE = re.compile(
    r"(?:UrT\s*)?(\d{4})\s*(?:(\d+)\s*)?(?:no\s*(\d+)(?:-(\d+))?\s*)?"
    r",?\s*[ps]\.\s*(\d+)")

# The article page's own statement of its article, three labels in its PDF
# line. `Volym` states the issue, `Sida` the article's opening page, `År`
# the year -- and the value sits in the text after the label, beside it
# or across its break. The journal has set the labels in three hands: the
# newer pages write the colon into the label's mark
# (`<strong>Volym:</strong> no 1`), the older ones set the colon after it
# (`<strong>Volym</strong> : no 1`), and the oldest ones set the labels
# plain or bolded with no mark at all (`Volym: no 1`, `<b>Volym</b>: no 1`).

RE_URT_META = re.compile(
    r"(?<!\w)(Volym|Sida|År)\b\s*:?\s*(?:</(?:strong|b)>\s*)?:?\s*([^<\"&]+)",
    re.I)
# The issue's number: a single number, or a combined issue's two halves
# ("3-4") -- the same hand the listing's own citation states.
RE_URT_ISSUE_NO = re.compile(r"\d+(-\d+)?")


def _urt_meta_value(raw):
    """The value off a meta line, the journal's two hands on it: the newer
    pages write it bare ("Volym: 1", "Sida: 1"), the older ones write it
    with its marker ("Volym: no 1", "Sida: s. 1"). The marker is not part
    of the value, and neither is the colon its older hands set after the
    label. A page's head restates the line in its description tag, and the
    reader keeps the last match of each label, which is the body's -- so a
    value the body states is never the head's joined one."""
    value = raw.strip().lstrip(":").strip()
    lowered = value.lower()
    for marker in ("no ", "s. ", "p. ", "nr "):
        if lowered.startswith(marker):
            return value[len(marker):].strip()
    return value


def _urt_entries(session):
    """The listing's whole entry inventory, in the listing's own order.
    An entry is the year heading over it (the nearest year heading in the
    listing's markup), its author, the first reportage link it sets, and the
    citation that link's own text states. The journal has sometimes split an
    entry's citation across its link into three, and the entry is kept whole
    on the link they all share."""
    html = net.request(session, "GET", URT.listings[0]).text
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    # walk the page in order so each entry takes the year heading over it
    year = None
    for node in soup.find_all(["h2", "p"]):
        if node.name == "h2":
            y = node.get_text(strip=True)
            if y.isdigit() and len(y) == 4:
                year = y
            continue
        links = node.find_all("a", href=re.compile(r"/reportage/"))
        if not links or year is None:
            continue
        # one entry per article: the links share the article's slug, and the
        # journal has once set a stray link into a dead entry beside them.
        # a link's slug is the match, not a split of the link: the journal
        # sets some links with a trailing slash and some without
        href = links[0].get("href")
        assert isinstance(href, str), "an urt entry link is not a link"
        slug = RE_URT_SLUG.search(href)
        if slug is None:
            continue
        article_links = []
        for a in links:
            h = a.get("href")
            if not isinstance(h, str):
                continue
            sm = RE_URT_SLUG.search(h)
            if sm is not None and sm.group(1) == slug.group(1):
                article_links.append(a)
        full = normalize_space(node.get_text(" ", strip=True))
        anchor = article_links[0]
        author = full.split(anchor.get_text(strip=True) or "\x00")[0] \
            .strip() or None
        ms = [x for x in RE_URT_CITE.finditer(full)]
        # the citation is the line's tail; a year the title itself names
        # sits to its left, so the rightmost match is the citation
        m = ms[-1] if ms else None
        if m is None:
            raise ValueError("urt: an entry sets no citation (%r)"
                             % full[:60])
        if m.group(3) and m.group(4):
            issue = "%s-%s" % (m.group(3), m.group(4))
        else:
            issue = m.group(3) or m.group(2)
        entries.append({
            "author": author,
            "year": m.group(1),
            "issue": issue,
            "sida": m.group(5),
            "url": anchor["href"],
        })
    if not entries:
        raise ValueError("the urt listing names no entries -- the page moved")
    return entries


def _urt_article_page(session, url):
    """One article page's own statement of its article: the title its H1
    sets, the opening page its "Volym" line states, the year its "År" line
    states, the PDF its PDF link sets, and the author its bold line states.
    The page states no title, or no meta line, is a page the journal did not
    set, and the walk says so. A page that sets its meta line beside no PDF
    link is an article the journal printed in the print issue only, and it
    names a print-only record."""
    html = net.request(session, "GET", url).text
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if h1 is None or not h1.get_text(strip=True):
        raise ValueError("urt %s: no title on the article page" % url)
    title = normalize_space(h1.get_text(" ", strip=True))
    pdf = None
    for a in soup.find_all("a", href=re.compile(r"\.pdf$")):
        pdf = a.get("href")
        break
    meta = {k.lower(): _urt_meta_value(v)
            for k, v in RE_URT_META.findall(html)}
    volym = meta.get("volym")
    sida = meta.get("sida")
    ar = meta.get("år")
    if volym is None or sida is None or ar is None:
        raise ValueError(
            "urt %s: the article page states no issue, page or year" % url)
    if not (RE_URT_ISSUE_NO.fullmatch(volym)
            and sida.isdigit() and ar.isdigit()):
        raise ValueError(
            "urt %s: the article page states no issue, page or year" % url)
    fattare = None
    for p in soup.find_all("p"):
        strong = p.find(["strong", "b"])
        if strong is None:
            continue
        text = normalize_space(strong.get_text(" ", strip=True)).strip()
        if not text:
            continue
        if text.endswith(":"):
            continue        # a label line, its "PDF:" line and the newer
            # hand's meta labels alike, not the author's line
        # the older hand sets its meta labels' colon off the mark, and a
        # bare label is the meta line, not the author's line
        if text.lower() in ("volym", "sida", "år"):
            continue
        fattare = text
        break
    return {"title": title, "pdf": pdf, "issue": volym,
            "sida": sida, "year": ar,
            "fattare": fattare}


def _urt_record(entry, page, year, issue):
    """The entry's article as a record: the page states the title, the
    author's line and the PDF (or names a print-only record where it sets
    none), and the entry beside it states the article's place in the
    issue. `--only` and the walk build the record the same way."""
    return {
        "basefile": "urt/%s-%s-%s" % (year, issue, entry["sida"]),
        "journal": "urt",
        "year": year,
        "issue": issue,
        "seq": None,
        "titel": page["title"],
        "fattare": page["fattare"] or entry["author"],
        "sammanfattning": None,
        "sida": entry["sida"],
        "source_url": entry["url"],
        # a page that sets its meta line beside no PDF link is an article
        # in the print issue only, and names a print-only record
        "document_url": page["pdf"],
    }


def urt_sync(root, full=False, only=None, limit=None, delay=0.5):
    """The journal's whole open access: the one listing, then the article
    pages newest-first and every PDF the page sets. A watermark on the
    article's year stops a caught-up run once its newest articles are on
    disk in full, and never re-fetches an article page whose record is
    stored. `--only urt/2026-1-147` names its own article, which is then
    the only article page fetched and the walk stores that one document.
    The record's page is the listing's own, the article's place in the
    issue: the page's `Sida` line states the article PDF's own first page,
    and the two are different measures on the journal's newer digital
    issues, so the walk checks no more than the year against it."""
    session = net.make_session(net.BROWSER_UA)
    entries = _urt_entries(session)
    if only:
        # the basefile names its own issue, so an --only run reads that
        # issue's article pages instead of the whole listing's
        parts = only.split("/", 1)[1].split("-")
        year, issue, _sida = parts[0], "-".join(parts[1:-1]) or "1", \
            parts[-1]
        entries = [e for e in entries if e["year"] == year
                   and (e["issue"] is None or e["issue"] == issue)]
    # newest article first, by the year the listing states and the article's
    # own number in the issue: a caught-up run proves its newest articles
    # are complete and stops, and the archive behind it is never re-read
    entries.sort(key=lambda e: (int(e["year"]),
                                int((e["issue"] or "0").split("-")[0]),
                                int(e["sida"])), reverse=True)

    def records(entry):
        # the unit the listing enumerates is one article's own page
        page = _urt_article_page(session, entry["url"])
        time.sleep(delay)
        if entry["year"] != page["year"]:
            raise ValueError(
                "urt: the listing states %s, the page states %s for %r"
                % (entry["year"], page["year"], page["title"][:50]))
        return [_urt_record(entry, page, page["year"],
                            entry["issue"] or page["issue"])]

    return harvest.issue_walk(
        root, "urt", entries, records,
        # the journal sets an article it published no PDF for: the record
        # alone is that entry
        body=lambda record: ((lambda: net.request(
            session, "GET", record["document_url"]).content)
            if record["document_url"] else None),
        missing="the urt listing carries no article %s",
        delay=delay, full=full, only=only, limit=limit)