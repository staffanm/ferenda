"""The euar (EU och arbetsrätt) walk: the journal's one index page, then each
issue page's item cards, the item's own page being its document.

The journal has published its newsletter since 1998, and the one index page
states every issue since: a link per issue, the issue's number and year in
the link's own address (the combined issues in it, the journal's "nr 3-4",
set in their address as well). An issue page states its number and year in
its own H1 ("Nordiskt nyhetsbrev nr 3-4 2020" -- the journal dropped the
"nr" in its newest issues), and it sets its items as its article cards, the
featured cards and the remaining-articles rows alike: each card states the
item's headline in its heading and links the item's own page, so a card's
heading is the item and its link is the item's address. The featured cards
set the issue's number and year in their own tag as well, which the walk
cross-checks against the page's H1: the tag may still name the combined
range the journal used before it split the issue, and the page's own
number and the tag's must overlap, the year agreeing.

The item's document is the item's own page: the page stores it, and
`parse._euar_body` reads its running text, its author and its publication day
off the stored page, so the walk fetches the page lazily as each record's
body and states the page's address as the record's document.

The journal has taken its oldest issue pages offline (its pre-2005 issues
404 at their addresses), and a dead issue page must not stop the sweep over
the rest: it is recorded as a skip in the run's output, and the index still
lists it, so the next run re-meets it. The journal has also set four of its
own cards to item addresses that 404, and it has not mended them (see
`KNOWN-GAPS.md`): a card to one of these pages contributes no document and
no record, and the run stays clean around it.
"""

import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..lib import harvest, net
from ..lib.util import normalize_space
from .journals import EUAR

__all__ = ["euar_sync"]

RE_ISSUE_HREF = re.compile(
    r"euocharbetsratt\.se/nyhetsbrev/nordiskt-nyhetsbrev(?:-nr)?"
    r"-(\d+(?:-\d+)?)-(\d{4})/$")
# The issue page's H1 ("Nordiskt nyhetsbrev nr 3-4 2020", and the journal's
# newest issues with the "nr" out of it): the page's own statement of the
# issue, the record's year and issue both.
RE_ISSUE_H1 = re.compile(
    r"Nordiskt nyhetsbrev\s+(?:nr\s+)?(\d+(?:-\d+)?)\s+(\d{4})")
# The link of an item's card: the journal's article namespace, nothing else
# on the issue page links there. A card that links elsewhere is page
# material, not an item.
RE_ITEM_HREF = re.compile(r"/artiklar/[^/]+/?$")
# The featured cards' tag ("Nr 2 2026", "Nr 3-4 2020"): the card's own
# statement of the issue, cross-checked against the page's H1. The number's
# second half stays its own group: the journal sets the combined issues in
# the tag as well, and the record's issue is the number joined back.
RE_ITEM_TAG = re.compile(r"Nr\s+(\d+)(?:-(\d+))?\s+(\d{4})")

# Four of the journal's own cards link to item pages the journal has broken
# on its side: the four addresses answer 404, and the journal has not mended
# the links (see `KNOWN-GAPS.md`). The item's page is its document, so a
# card to one of these pages contributes nothing the walk can store: the
# record is not written, and the run stays clean around it. A card that
# keeps its live address is recorded like the rest.
DEAD_ITEM_URLS = frozenset((
    "https://euocharbetsratt.se/artiklar/"
    "eu-domstolen-svarar-islands-landsretturregler-om-kollektiva-uppsagningar-galler-ocksanar-arbetsgivaren-vill-andra-arbetsvillkoren/",
    "https://euocharbetsratt.se/artiklar/"
    "nationella-domstolar-ska-avgora-ominhyrning-rimligen-kan-ses-som-temporar/",
    "https://euocharbetsratt.se/artiklar/"
    "tco-anmaler-sverige-for-indirekt-konsdiskriminering/",
    "https://euocharbetsratt.se/artiklar/"
    "uppforandekoder-och-social-markning-bor-bli-mer-enhetliga/",
))
# The journal split its 2017 combined newsletter into a "nr 3" page and a
# "nr 4" page, but the 3 page still tags its featured cards "Nr 3-4 2017":
# a card tag may name the combined range the journal used before the split,
# and the check below takes the page's H1 as the record's issue, asking only
# that the tag's numbers overlap the page's and its year agree.


# An item page is the only kind that sets its running text in
# `div.post-single-content`, which is what `parse._euar_body` reads -- a WAF
# challenge and a listing served in an item's place both lack it (a listing
# carries the item-node marker as often as an item does, so that marker
# alone cannot tell them apart).
verify_page = harvest.page_verifier("post-single-content", what="item")


def _euar_issues(session):
    """The journal's whole issue inventory off the one index page: every
    issue address the index states, newest first as the index sets them."""
    html = net.request(session, "GET", EUAR.listings[0]).text
    soup = BeautifulSoup(html, "html.parser")
    issues = {}
    for a in soup.find_all("a", href=RE_ISSUE_HREF):
        href = a.get("href")
        assert isinstance(href, str)
        match = RE_ISSUE_HREF.search(href)
        assert match is not None
        issues.setdefault(href, True)
    if not issues:
        raise ValueError("the euar index names no issues -- the page moved")
    return list(issues)


def _euar_issue_code(url):
    """The issue's number and year off the address that states them."""
    m = RE_ISSUE_HREF.search(url)
    assert m is not None
    return m.group(1), m.group(2)


def _euar_sort_key(url):
    """The walk's newest-first order, off the address: the year, then the
    issue's own first number -- the combined issues state both numbers, and
    a string sort of them would put "3-4" after "9"."""
    issue, year = _euar_issue_code(url)
    return int(year), int(issue.split("-")[0])


def _euar_records_from_page(html, issue_url):
    """One issue page's item cards as records, in the page's own order: its
    featured cards and its remaining-articles rows, which the page sets in
    one kind of article card. The item's headline is the card's heading; the
    year and issue are the page's own H1; and the document is the item's own
    page, the page the card links. A card without an article link is page
    material, not an item, and an issue page that sets no item card is a
    page the journal did not set, and the walk says so."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if h1 is None:
        raise ValueError("euar %s: no issue number on the page -- "
                         "the template moved" % issue_url)
    m = RE_ISSUE_H1.search(h1.get_text(" ", strip=True))
    if m is None:
        raise ValueError("euar %s: an unreadable issue number %r"
                         % (issue_url, h1.get_text(strip=True)))
    issue, year = m.group(1), m.group(2)
    records = []
    for card in soup.find_all("article"):
        a = card.find("a", href=RE_ITEM_HREF)
        if a is None:
            continue             # page material that links no article
        h2 = card.find("h2")
        if h2 is None:
            raise ValueError("euar %s %s: a card sets no headline"
                             % (year, issue))
        title = normalize_space(h2.get_text(" ", strip=True))
        if not title:
            raise ValueError("euar %s %s: a card sets an empty headline"
                             % (year, issue))
        href = a.get("href")
        assert isinstance(href, str)
        url = href if href.startswith("http") else urljoin(EUAR.base, href)
        if url in DEAD_ITEM_URLS:
            continue    # the journal's own dead link: see KNOWN-GAPS.md
        tag = card.find("p", class_="tag")
        if tag is not None:
            tm = RE_ITEM_TAG.search(tag.get_text(" ", strip=True))
            if tm is None:
                raise ValueError("euar %s %s: an unreadable card tag %r"
                                 % (year, issue,
                                    tag.get_text(strip=True)))
            card_issue = tm.group(1) + ("-" + tm.group(2) if tm.group(2)
                                        else "")
            tag_numbers = {part for part in (tm.group(1), tm.group(2))
                           if part}
            if tm.group(3) != year or not (set(issue.split("-"))
                                           & tag_numbers):
                raise ValueError("euar %s %s: a card tag states %s %s"
                                 % (year, issue, card_issue, tm.group(3)))
        seq = "%02d" % (len(records) + 1)
        records.append({
            "basefile": "euar/%s-%s-%s" % (year, issue, seq),
            "journal": "euar",
            "year": year,
            "issue": issue,
            "seq": seq,
            "titel": title,
            "fattare": None,          # the item's page states it
            "sammanfattning": None,
            "source_url": url,
            "document_url": url,
        })
    if not records:
        raise ValueError("euar %s %s: the issue page sets no items"
                         % (year, issue))
    return records


def euar_sync(root, full=False, only=None, limit=None, delay=0.5):
    """The newsletter's whole archive: the one index page, then the issue
    pages newest-first and every item page the issue names. A watermark on
    the issue's year stops a caught-up run once its newest issues are on
    disk in full, and never re-fetches an issue page whose items are all
    stored. `--only euar/2020-3-4-01` names its own issue, which is then
    the only issue page fetched and the walk stores that one document. The
    journal has taken its oldest issue pages offline (its pre-2005 issues
    404 at their addresses): a dead page is a skip in the run's output, and
    it keeps the store dirty until a run walks clean to the end."""
    session = net.make_session(net.BROWSER_UA)
    issues = _euar_issues(session)
    if only:
        # the basefile names its own issue, so an --only run reads that one
        # issue page instead of the archive
        parts = only.split("/", 1)[1].split("-")
        year, issue = parts[0], "-".join(parts[1:-1])
        issues = [u for u in issues
                  if _euar_issue_code(u) == (issue, year)]
    # newest issue first: a caught-up run proves its newest issues are
    # complete and stops, and the archive behind it is never re-read
    issues.sort(key=_euar_sort_key, reverse=True)

    def records(url):
        try:
            html = net.request(session, "GET", url).text
        except requests.exceptions.HTTPError as exc:
            # the journal has taken an issue page offline: one dead page
            # must not stop the sweep over the rest, and the skip is the
            # record of the miss (the index still lists the issue, so the
            # store stays dirty until the walk runs clean). An --only run
            # of a dead issue ends red on its own: the walk meets the named
            # article nowhere
            if exc.response is None or exc.response.status_code != 404:
                raise
            return [harvest.Skip("euar %s is gone (HTTP 404)" % url)]
        time.sleep(delay)
        return _euar_records_from_page(html, url)

    return harvest.issue_walk(
        root, "euar", issues, records,
        body=lambda record: (lambda: net.request(
            session, "GET", record["document_url"]).text),
        missing="the euar index carries no article %s",
        document=harvest.page_path, verify=verify_page,
        delay=delay, full=full, only=only, limit=limit)