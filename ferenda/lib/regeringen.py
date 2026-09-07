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


# Landing pages regeringen.se lists but cannot serve: path -> the evidence.
# Not an inference from a status code -- a 500 is an outage until proven
# otherwise, and reading one as "this document does not exist" is how a bad
# afternoon becomes a permanent hole in the corpus. An entry here says the
# stronger, curated thing: this page has answered the same way long enough to
# stop paying for it. Same posture, and the same one-line-of-evidence rule, as
# `remisser.download.BROKEN_ANSWERS` for a content file.
#
# The cost of NOT curating one is not just the fetch: `net.request` climbs its
# full retry ladder on a 5xx (6 attempts, ~62 s of backoff), the walk records a
# per-document error, and that leaves the watermark store dirty -- so every
# later run distrusts its consecutive-hit stop and walks deeper, for a page
# that will fail again.
#
# Drop an entry to re-test: the item comes straight back into the walk.
BROKEN_LANDINGS = {
    # Skr. 2015/16:115 "Verksamheten i Europeiska unionen under 2015". The
    # listing carries exactly one row for it and that row's page answers HTTP
    # 500 with EPiServer's own "Något gick fel" error body -- 6 attempts in a
    # harvest run on 2026-09-07, 3 more directly, and the two slug variants
    # regeringen.se also accepts 404 rather than 500. So the corpus does not
    # hold this skrivelse; riksdagen publishes the same document if it is ever
    # wanted.
    "/rattsliga-dokument/skrivelse/2016/03/skr.-201516115",
}


def broken_landing(url):
    """Whether `url` is a landing page regeringen.se lists but cannot serve
    (`BROKEN_LANDINGS`), normalized like `is_misleading`."""
    return regeringen_path(url) in BROKEN_LANDINGS


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


# regeringen.se prints a document's own number however the editor typed it. The
# house style is "Prop. 2025/26:294", but the same listings carry "prop.
# 2025/26:50", "Prop 2025/26:169", "Skr.2025/26:115", "Prop.  2025/26:7" and
# "Fm. 2016:1". Measured over the newest 400 items of each numbered type on
# 2026-09-07, the strict "Prop. <number>" shape missed 17 propositioner, 38
# skrivelser, 39 kommittedirektiv and 1 forordningsmotiv -- and forarbete held
# none of them. So the series word is matched without regard to case, its period
# is optional, and the space between word and number is any run of whitespace or
# none.
def _numbered(word, number):
    return r"(?i:\b%s)\s*\.?\s*(%s)" % (word, number)


# a riksmote number ("2025/26:223") and a year number ("2026:34"). The second
# half of a riksmote takes 2 to 4 digits because regeringen.se writes the long
# form too -- prop. 2025/26:223 is published as "Prop. 2025/2026:223", which
# `riksmote` folds back to the printed short form.
RIKSMOTE = r"\d{4}/\d{2,4}:\d+"
ARSNUMMER = r"\d{4}:\d+"


# type -> (url segment, taxonomy category id, identifier regex over the listing
# link text). A None regex marks a type regeringen.se publishes without a
# number; its basefile is derived from the landing page instead (see
# forarbete.download).
TYPES = {
    "prop": ("proposition", 1329, _numbered("prop", RIKSMOTE)),
    "sou": ("statens-offentliga-utredningar", 1331, _numbered("sou", ARSNUMMER)),
    "ds": ("departementsserien-och-promemorior", 1325, _numbered("ds", ARSNUMMER)),
    "pm": ("departementsserien-och-promemorior", 1325, None),
    "dir": ("kommittedirektiv", 1327, _numbered("dir", ARSNUMMER)),
    "fm": ("forordningsmotiv", 1326, _numbered("fm", ARSNUMMER)),
    "skr": ("skrivelse", 1330, _numbered("skr", RIKSMOTE)),
    "so": ("sveriges-internationella-overenskommelser", 1332, None),
    "lr": ("lagradsremiss", 2085, None),
}

# the types that share their taxonomy category with a sibling, and so partition
# it between them: ds takes the items numbered "Ds YYYY:N", pm takes the rest.
# An item of such a type that carries no number is the sibling's document, not
# a document whose number has to be recovered from somewhere else.
SHARED_CATEGORY = frozenset(
    t for t, (_s, category, _i) in TYPES.items()
    if sum(1 for _s2, c2, _i2 in TYPES.values() if c2 == category) > 1)

# how the corpus prints each numbered series, whatever the page did. The
# identifier is a document's display form ("Prop. 2025/26:223"), so it is minted
# from the series and the normalized number rather than kept as regeringen.se
# typed it -- prop. 2025/26:223's own page calls it "Prop. 2025/2026:223".
SERIES_LABEL = {"prop": "Prop.", "sou": "SOU", "ds": "Ds", "dir": "Dir.",
                "fm": "Fm", "skr": "Skr."}
assert SERIES_LABEL.keys() == {t for t, (_s, _c, i) in TYPES.items() if i}, \
    "every numbered type needs a printed series label"

_RE_RIKSMOTE = re.compile(r"^(\d{4})/(\d{2,4}):(\d+)$")


def riksmote(number):
    """A riksmote number in the form the corpus keys on: "2025/2026:223" ->
    "2025/26:223", the printed short form. Any other shape passes through."""
    m = _RE_RIKSMOTE.match(number)
    return ("%s/%s:%s" % (m.group(1), m.group(2)[-2:], m.group(3))
            if m else number)


def find_number(typ, text, anchored=False):
    """The document's own printed number in `text`, as
    ``(basefile, identifier, match)``, or None when `typ` is numberless or the
    text carries no number of its series.

    `basefile` is normalized (`riksmote`); `identifier` is the document's
    display form, minted from `SERIES_LABEL` and that basefile rather than kept
    as the page typed it; `match` locates the number in `text`, so a caller can
    cut it out of a link text and keep the title.

    `anchored` requires the number to open the text -- what remisser reads a
    link's own label with, where a series named later is a reference rather than
    the document's identity."""
    pattern = TYPES[typ][2]
    if pattern is None:
        return None
    hit = (re.match(pattern, text) if anchored else re.search(pattern, text))
    if hit is None:
        return None
    basefile = riksmote(hit.group(1))
    return basefile, "%s %s" % (SERIES_LABEL[typ], basefile), hit


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
