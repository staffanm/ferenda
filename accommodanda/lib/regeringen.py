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

# type -> (url segment, taxonomy category id, identifier regex over the listing
# link text). A None regex marks a type regeringen.se publishes without a
# number; its basefile falls back to the landing-page slug.
TYPES = {
    "prop": ("proposition", 1329, r"Prop\. (\d{4}/\d{2,4}:\d+)"),
    "sou": ("statens-offentliga-utredningar", 1331, r"SOU (\d{4}:\d+)"),
    "ds": ("departementsserien-och-promemorior", 1325, r"Ds (\d{4}:\d+)"),
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
