"""Shared regeringen.se harvest knowledge (rule:second-use-goes-to-lib).

Two verticals harvest regeringen.se -- forarbete (/rattsliga-dokument/) and
remisser (/remisser/) -- and both need the same two facts about the site: the
doctype table behind /rattsliga-dokument/ (`TYPES`, which remisser uses to
resolve a case's "Genvägar" link or title back to the referred förarbete's
canonical basefile) and the listing DOM (`ul.list--block > li` items, walked
by `listing_items`). Each vertical keeps its own pagination mechanism and
record semantics; only the site knowledge lives here.
"""

from bs4 import BeautifulSoup

BASE = "https://www.regeringen.se"

# regeringen.se URLs that must not be harvested: dual-published duplicates,
# mislabelled types, and wrong-number slugs the infomaster never corrected.
# Ported from the legacy source's `misleading_urls`; keyed on the current
# /rattsliga-dokument/ path (the leading scheme+host is stripped before the
# lookup so http/https and trailing-slash variants all match). Extend as new
# bad pages surface -- one curated line per document, with the reason.
MISLEADING_URLS = frozenset({
    # SÖ 1980:72 dual-published: this 1994 copy carries a wrong date and no PDF;
    # the 1979/06/so-198072 page is the one with the body. (rule:fail-fast would
    # otherwise collide the two on the same SÖ number.)
    "/rattsliga-dokument/sveriges-internationella-overenskommelser/1994/01/so-198072-",
})


def is_misleading(url):
    """Whether `url` is on the curated skip-list, ignoring scheme/host and a
    trailing slash so http/https and with/without-slash variants all match."""
    path = url.split("regeringen.se", 1)[-1].rstrip("/")
    return path in MISLEADING_URLS


def landing_vignette(html):
    """The document's own identifier as regeringen.se prints it above the H1 --
    ``<span class="h1-vignette">`` (e.g. ``SÖ 1980:72``, or the bare word
    ``Lagrådsremiss`` for a lagrådsremiss). The authoritative identifier source,
    since a listing link text and a URL slug are both unreliable. None when the
    page carries no vignette."""
    span = BeautifulSoup(html, "html.parser").find("span", class_="h1-vignette")
    return span.get_text(strip=True) if span else None


# type -> (url segment, taxonomy category id, identifier regex over the listing
# link text). A None regex marks a type regeringen.se publishes without a
# number; its basefile is derived from the landing page instead (see
# forarbete.download).
TYPES = {
    "prop": ("proposition", 1329, r"Prop\. (\d{4}/\d{2,4}:\d+)"),
    "sou": ("statens-offentliga-utredningar", 1331, r"SOU (\d{4}:\d+)"),
    "ds": ("departementsserien-och-promemorior", 1325, r"Ds (\d{4}:\d+)"),
    "pm": ("departementsserien-och-promemorior", 1325, None),
    "dir": ("kommittedirektiv", 1327, r"Dir\. (\d{4}:\d+)"),
    "fm": ("forordningsmotiv", 1326, r"Fm (\d{4}:\d+)"),
    "skr": ("skrivelse", 1330, r"Skr\. (\d{4}/\d{2,4}:\d+)"),
    "so": ("sveriges-internationella-overenskommelser", 1332, None),
    "lr": ("lagradsremiss", 2085, None),
}


def listing_items(html, hrefpat):
    """The regeringen.se listing DOM -- ``ul.list--block > li`` items whose
    anchor matches `hrefpat` -- as (li, href, absolute url with trailing slash,
    link text) tuples, in page order (newest first)."""
    soup = BeautifulSoup(html, "html.parser")
    for li in soup.select("ul.list--block > li"):
        a = li.find("a", href=hrefpat)
        if not a:
            continue
        href = a["href"]
        assert isinstance(href, str)
        url = (BASE + href) if href.startswith("/") else href
        yield li, href, (url if url.endswith("/") else url + "/"), a.get_text(
            " ", strip=True)
