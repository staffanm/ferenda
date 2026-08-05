"""Shared regeringen.se harvest knowledge (rule:second-use-goes-to-lib).

Two verticals harvest regeringen.se -- forarbete (/rattsliga-dokument/) and
remisser (/remisser/) -- and both need the same facts about the site: the
doctype table behind /rattsliga-dokument/ (`TYPES`, which remisser uses to
resolve a case's remitted-document link back to the referred förarbete's
canonical basefile), the listing DOM (`ul.list--block > li` items, walked by
`listing_items`), and the **identity rules** for the two doctypes regeringen.se
publishes without a series number (`pm_identity`, `lr_identity`). Each vertical
keeps its own pagination mechanism and record semantics; only the site knowledge
lives here.

The identity rules are shared because both verticals must land on the *same*
basefile for the same document, from different pages: forarbete names it off the
/rattsliga-dokument/ listing, remisser off the link a remiss case page makes to
it. Any divergence would silently split one document into two
(rule:second-use-goes-to-lib).
"""

import re

from bs4 import BeautifulSoup

from .util import text_slug

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


def regeringen_path(url):
    """A regeringen.se url as the path a curated table keys on: scheme and host
    dropped, trailing slash off, so http/https and with/without-slash variants all
    match the one entry."""
    return url.split("regeringen.se", 1)[-1].rstrip("/")


def is_misleading(url):
    """Whether `url` is on the curated skip-list, ignoring scheme/host and a
    trailing slash so http/https and with/without-slash variants all match."""
    return regeringen_path(url) in MISLEADING_URLS


# The landing slug of a plainly-numbered series is regeringen's own machine-made
# form of the identifier -- `.../2023/06/sou-202327/` is SOU 2023:27 -- so it
# recovers the number where the *printed* one is malformed. It regularly is: the
# SOU 2023:27 remiss says "SOU 2023 27" in its link text, its title and its H1,
# colon and all missing, which no identifier regex can match.
#
# Only the `<series> <year>:<no>` types, whose slug is that shape: a prop/skr
# number is riksmöte-based ("2015/16:51") and a slug like `skr.-20151651` would
# read as 2015:1651. And only when the slug's *own* prefix names the type -- the
# path segment it was reached under is not enough, since regeringen files a
# document under another type's segment now and then (a Ds at
# `/rattsliga-dokument/skrivelse/…`), and reading `ds-201551` as this segment's
# SOU 2015:51 would mint a real but entirely unrelated document.
SLUG_NUMBERED = ("sou", "ds", "dir", "fm")
_SLUG_NUMBER = {t: re.compile(r"^%s\.?-(\d{4})(\d+)$" % t) for t in SLUG_NUMBERED}


def slug_number(typ, slug):
    """``YYYY:N`` read off a numbered series' landing slug, or None when `typ` is
    not one of those series or the slug is not its number. The year is the leading
    four digits, so `sou-20172` is 2017:2 and `sou-202327` 2023:27.

    Lives here, beside the other identity rules, because a basefile rule is site
    knowledge both verticals must agree on -- but only remisser applies this one
    today. forarbete simply *skips* a listing item whose text carries no
    identifier, so a document regeringen mis-numbered on its listing is
    unharvestable from that side and the cross-ref remisser mints for it can
    dangle until forarbete learns the same rule (the slug is already in hand
    there)."""
    hit = _SLUG_NUMBER[typ].match(slug) if typ in _SLUG_NUMBER else None
    return "%s:%s" % hit.groups() if hit else None


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


# the trailing ", Lagrådsremiss" a lagrådsremiss title carries is stripped
# before slugging
_LR_SUFFIX = re.compile(r",?\s*Lagrådsremiss\s*$", re.IGNORECASE)

LR_SLUG_LEN = 60           # 30 collapsed distinct docs sharing a title prefix
                           # ("Behandling av personuppgifter …"); 60 leaves only
                           # genuine duplicates, which the caller dedups.


def lr_identity(date, title):
    """A lagrådsremiss's (basefile, identifier). Lagrådsremisser carry no unique
    number (the landing vignette is the bare word "Lagrådsremiss"), only a title
    that may recur across years -- so the basefile is ``<year>/<title-slug>`` and
    the identifier is the cleaned title. Raises when either is missing
    (rule:fail-fast) rather than minting a colliding stub.

    `title` is the text of the link that named the document, which both callers
    read off a listing/case page verbatim -- so an "Utkast till lagrådsremiss:
    …" prefix is part of the identity, exactly as regeringen.se prints it, and
    the draft keeps a basefile of its own separate from the final remiss."""
    year = (date or "")[:4]
    clean = _LR_SUFFIX.sub("", title).strip()
    slug = text_slug(clean, maxlen=LR_SLUG_LEN)
    if not (year.isdigit() and slug):
        raise ValueError("lagrådsremiss without a year+title: date=%r title=%r"
                         % (date, title))
    return "%s/%s" % (year, slug), clean


def pm_identity(dnr, slug):
    """A departementspromemoria's basefile: its diarienummer when regeringen.se
    names one, else the landing page's own URL slug.

    Promemorior outside the Ds series carry no series number; the dnr
    (``Ju2026/01691``) is the closest thing to an identifier, and a handful of
    pages print none at all -- for those the landing slug is all there is. Both
    verticals must agree on the fallback, or a dnr-less promemoria would be one
    document to forarbete and another to remisser."""
    return dnr or slug


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
