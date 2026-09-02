"""Entry point for the lawreview harvest: one run per journal scope.

The journals are separate upstreams sharing nothing but this entry point, so
they are as many scopes in `lib.harvest.dispatch_scopes`' sense and fan out
the way `guidance`/`rs`/`avg` do. Storage is
``site/data/downloaded/lawreview/{journal}/`` -- the journal is the directory,
and the slugged basefile the file stem (``svjt/svjt-2026-104.html``,
``jp/jp-2025-01-01.pdf``, ``urt/urt-2026-1-147.pdf``).

Each journal walks its own archive in its own module -- `svjt` and `jp` here,
the six newer ones in their own files, each one the shape of that journal's
listing: the svjt archive's year pages carry the article cards; the jp menu
page its issues; the ft platform's archive page its issue buttons, the
issue page its open-access cards; the nmt listing pages their issues' tables
of contents in full; the njel platform's one archive page its issues; the
siplr issues page its issues, the issue page its article headings beside
their PDFs; the urt listing its entries, the entry's own page its PDF; the
euar index page its issues, the issue page its item links, the item's own
page its document; the lod index page its years, the year pages their
issues, the issue page its table of contents, the article's own page its
document; and the lawpub platform's one paginated listing its per-article
PDFs (the tenth scope is a platform, not a journal -- see `lawpub.py`).

The deep listings walk newest-first and stop on the harvest watermark's
caught-up gate. svjt is the deepest: 1916 to now, ~17,000 article pages. A
year, once out, receives no new article after it -- the journal revises its
newest year in place and opens the next in January -- so a caught-up run
reads its newest year page and stops, and only a first run or a `--full` run
walks the whole depth. lod, njel, siplr, jp, ft, urt and euar walk their
shallower issue archives on the same gate: a caught-up run reads the index
and its newest issue's page and stops, and the archive behind it -- and the
issue pages in it -- are never re-read. nmt is the exception: its two
listing pages are the whole archive, so it re-reads them on every run and
fetches only the PDFs that moved.

The jp host answers a rate-limited client with a non-standard WAF status
(466), which `lib.net`'s retry table covers like any other throttle.
"""

import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..lib import harvest, net
from ..lib.util import normalize_space
from .euar import euar_sync
from .ft import ft_sync
from .journals import JOURNALS, JP, SCOPES, SVJT
from .lawpub import LISTING as LAWPUB_LISTING
from .lawpub import lawpub_sync
from .lod import lod_sync
from .njel import njel_sync
from .nmt import nmt_sync
from .siplr import siplr_sync
from .urt import urt_sync

__all__ = ["SCOPES", "SYNC", "sync"]

# the svjt archive's year filter, as a <select> of four-digit <option>s. The
# sibling filter (the häfte) offers one- and two-digit values, which is what
# keeps the two apart rather than a form-item id the site could rename.
RE_YEAR_OPTION = re.compile(r'<option value="(\d{4})"')

# a card's article link. The archive page's year is not a guarantee: the 1916
# page hosts one promoted card that names a 1941 article no other page lists,
# so a record reads its year and page off the card's own link, not off the
# page the card happens to sit on.
RE_SVJT_ARTICLE_HREF = re.compile(r"/svjt/(\d{4})/(\d+)$")


# --------------------------------------------------------------------------
# svjt (Svensk Juristtidning): the document is the article's web page
# --------------------------------------------------------------------------

def _svjt_years(session):
    """The archive's own year range, read off the archive page's year filter --
    a new issue year appears there the day it publishes, so nothing here has to
    be re-kept when the journal reaches a new year."""
    html = net.request(session, "GET", SVJT.listings[0]).text
    years = {m for m in RE_YEAR_OPTION.findall(html)
             if 1900 <= int(m) < 2100}
    return sorted(years)


def _title_case(word):
    """A name the cards set in capitals, title-cased: the word's first letter
    and the letter after each hyphen up, the rest down -- Swedish surnames
    keep the inner capital ("SMITH-OLOFSSON" -> "Smith-Olofsson")."""
    out, cap = [], True
    for ch in word:
        if ch == "-":
            out.append(ch)
            cap = True
        elif cap:
            out.append(ch.upper())
            cap = False
        else:
            out.append(ch.lower())
    return "".join(out)


def _svjt_records_from_page(html, year):
    """One year page's article cards as records, each naming the article's
    own page as its document. A card may name an article from another year
    (the 1916 page hosts one promoted 1941 card, and no other page lists
    that article), so the record takes its year and page off the card's own
    link rather than off the page it sits on. A card without an article link,
    or without a title, is a site change, not an entry to skip: raise, so the
    year is visible in the run's error log rather than silently missing from
    the archive."""
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for card in soup.select("div.article-grid-item > article"):
        link = card.find("a", href=RE_SVJT_ARTICLE_HREF)
        if link is None:
            raise ValueError("svjt %s: an article card names no page" % year)
        href = link.get("href")
        assert isinstance(href, str), \
            "svjt %s: an article card's page is not a link" % year
        # the `find` filter above already applied this rule, so the search is
        # guaranteed to land; the assert keeps ty's `Match | None` honest
        match = RE_SVJT_ARTICLE_HREF.search(href)
        assert match is not None
        # the card's own year, not the page's: the 1916 page hosts one
        # promoted 1941 card. Kept apart from the `year` parameter so a
        # later card's error still names the page being read.
        card_year, pagenum = match.group(1), match.group(2)
        title = card.select_one("h2.text-card-title")
        teaser = card.select_one("p.text-card-body")
        meta = card.select_one("div.article-meta")
        author = meta.select_one("span.author") if meta else None
        pdf = card.select_one("a.pdf-link") if meta else None
        pdf_href = None
        if pdf is not None:
            pdf_href = pdf.get("href")
            assert isinstance(pdf_href, str)
        # the cards set the author in capital letters; the rest of the corpus
        # states names in the case the publisher uses in prose
        fattare = None
        if author is not None:
            # a word the card set in capitals is title-cased, one it set in
            # its own case is kept as it stands
            fattare = " ".join(
                _title_case(w) if w.isupper() else w
                for w in normalize_space(
                    author.get_text(" ", strip=True)).split())
        record = {
            "basefile": "svjt/%s-%s" % (card_year, pagenum),
            "journal": "svjt",
            "year": card_year,
            "issue": pagenum,
            "titel": normalize_space(
                title.get_text(" ", strip=True)) if title else None,
            "fattare": fattare,
            "sammanfattning": normalize_space(
                teaser.get_text(" ", strip=True)) if teaser else None,
            "source_url": urljoin(SVJT.base, href),
            # a PDF of newer issues, where the journal publishes one; older
            # years are pages only
            "document_url": urljoin(SVJT.base, pdf_href) if pdf_href else None,
        }
        if not record["titel"]:
            raise ValueError("svjt %s: an article card names no title" % year)
        records.append(record)
    return records


# A WAF challenge page serves no article at all, but so does a listing the
# mirror serves in an article's place -- and a listing carries the
# article-node marker as often as an article does (its cards each link one),
# so that marker alone cannot tell them apart. An article page is the only
# kind that sets its running text in `div.body`, which is what
# `parse._svjt_body` reads, so the check asks for that.
verify_page = harvest.page_verifier('class="body"')


def svjt_sync(root, full=False, only=None, limit=None, delay=0.5):
    """The whole svjt archive, every year page, every article page behind it.
    The walk runs newest-first and stops on the harvest watermark's
    caught-up gate: the newest year's article pages all downloaded already
    say the archive is caught up, and the walk stops there, well short of
    the 1916 depth. `--only svjt/2026-104` names its own year instead, so
    that one page is the only listing fetched and the walk stores that one
    document, the watermark untouched."""
    session = net.make_session(net.BROWSER_UA)
    # newest year first; the basefile names its own year, so an --only run
    # reads that one year page instead of the archive
    years = [only.split("/", 1)[1][:4]] if only \
        else list(reversed(_svjt_years(session)))

    def records(year):
        # the listing fetch sleeps `delay` like every other fetch in the
        # walk (the edpb rule): a full run reads ~110 year pages back to
        # back without this
        html = net.request(session, "GET",
                           "%s/arkiv/%s" % (SVJT.base, year)).text
        time.sleep(delay)
        # within a year, the cards' own page numbers newest first -- the
        # year pages set their cards in no order at all
        return sorted(_svjt_records_from_page(html, year),
                      key=lambda r: (int(r["year"]), int(r["issue"])),
                      reverse=True)

    return harvest.issue_walk(
        root, "svjt", years, records,
        # the article's own page is its document: the journal published no
        # PDF for the years this walk reaches back over
        body=lambda record: (lambda: net.request(
            session, "GET", record["source_url"]).text),
        missing="the svjt archive carries no article %s",
        document=harvest.page_path, verify=verify_page,
        delay=delay, full=full, only=only, limit=limit)


# --------------------------------------------------------------------------
# jp (Juridisk Publikation): the document is the issue's PDF
# --------------------------------------------------------------------------

RE_JP_ISSUE_HREF = re.compile(r"juridiskpublikation\.se/tidskriften/([a-z0-9-]+)/?$")
RE_ISSUE_SLUG = re.compile(r"^nummer-(\d{2})(\d{4})")
RE_JUBILEE_SLUG = re.compile(r"^jubileumsnummer-(\d{4})")
RE_YEAR = re.compile(r"(\d{4})")
RE_PDF_HREF = re.compile(r"\.pdf$")


def _jp_issues(session):
    """The journal's whole issue inventory: the menu the one listing page
    states, newest first. The menu is set twice on the page, so an issue is
    kept once, on its first link."""
    html = net.request(session, "GET", JP.listings[0]).text
    soup = BeautifulSoup(html, "html.parser")
    issues = {}
    for a in soup.find_all("a", href=RE_JP_ISSUE_HREF):
        href = a.get("href")
        assert isinstance(href, str)
        # the `find_all` filter above already applied this rule, so the match
        # is guaranteed; the assert keeps ty's `Match | None` honest
        match = RE_JP_ISSUE_HREF.search(href)
        assert match is not None
        issues.setdefault(match.group(1),
                          normalize_space(a.get_text(" ", strip=True)))
    if not issues:
        raise ValueError("the jp listing names no issues -- the menu moved")
    return list(issues.items())


def _jp_issue_code(slug, label):
    """The issue's code and year, read off the slug where it carries them.
    WordPress renamed slugs as collisions appeared ("nummer-012013-2"), and two
    of the jubileumsnummer slugs carry no number at all ("nummer-jubileum",
    "jubileumsnummer-2019"); those take the year off the label that states it."""
    m = RE_ISSUE_SLUG.match(slug)
    if m:
        return m.group(1), m.group(2)
    m = RE_JUBILEE_SLUG.match(slug)
    if m:
        return "J", m.group(1)
    if slug.startswith("nummer-"):
        m = RE_YEAR.search(label)
        if m:
            return "J", m.group(1)
    raise ValueError("no jp issue code for slug %r (%r)" % (slug, label))


def _jp_block_record(section, heading):
    """One issue page's text block as a record, or None where the block names
    no article. The journal has issued these pages in two templates: the newer
    sets the title in its own paragraph and the author in a separate italic
    one, and the older (through ~2016) sets the title, the abstract and the
    italic author as one paragraph of line-broken runs. Both are read the same
    way: the block's PDF link is the title, the first italic mark that is not
    the title is the author, and what remains of the block's text is the
    abstract."""
    link = None
    for a in section.find_all("a", href=RE_PDF_HREF):
        link = a
        break
    if link is None:
        return None                  # a block that names no article
    title = normalize_space(link.get_text(" ", strip=True))
    marks = [normalize_space(m.get_text(" ", strip=True))
             for m in section.find_all(["em", "i"])]
    fattare = next((m for m in marks if m and m != title), None)
    abstract = []
    for p in section.find_all("p"):
        text = normalize_space(p.get_text(" ", strip=True))
        for mark in (title, fattare):
            if mark and mark in text:
                text = normalize_space(text.replace(mark, "", 1))
        if text:
            abstract.append(text)
    return {
        "journal": "jp",
        "kind": "inledning" if heading.lower().startswith("inledning")
        else None,
        "titel": title,
        "fattare": fattare,
        "sammanfattning": " ".join(abstract) or None,
        "document_url": link["href"],
    }


def _jp_records_from_page(html, slug, label):
    """One issue page's article blocks as records, in the issue's own order.
    The blocks are walked off the entry wrapper in document order, so both of
    the templates the journal has issued the page in read off the same walk --
    the newer one sets a block as a direct child and the older one nests it in
    a column wrapper. The heading above a block says what the block is -- the
    editors' "Inledning" is an article like the rest, and it is the one the
    kind field names. A block that names no PDF states no article (the page's
    table of contents is set one) and takes no sequence number; an issue that
    names no articles at all is not an issue (the host served no page), and it
    says so."""
    code, year = _jp_issue_code(slug, label)
    url = "%s/tidskriften/%s/" % (JP.base, slug)
    soup = BeautifulSoup(html, "html.parser")
    wrapper = soup.select_one("div.entry-content-wrapper")
    if wrapper is None:
        raise ValueError("jp %s: no entry content -- the template moved" % slug)
    records = []
    heading = ""
    for el in wrapper.find_all(True):
        classes = el.get("class") or []
        if "av-special-heading" in classes:
            h = el.find("h3")
            heading = normalize_space(h.get_text(strip=True)) if h else ""
        elif "av_textblock_section" not in classes:
            continue
        block = _jp_block_record(el, heading)
        if block is None:
            continue                  # a block that names no article
        seq = "%02d" % (len(records) + 1)
        records.append({
            "basefile": "jp/%s-%s-%s" % (year, code, seq),
            "year": year,
            "issue": code,
            "seq": seq,
            "source_url": url,
            **block,
        })
    if not records:
        raise ValueError("jp %s: no article names a PDF" % slug)
    return records


def jp_sync(root, full=False, only=None, limit=None, delay=0.5):
    """The journal's whole issue inventory: the one listing page, then the
    issue pages newest-first and every PDF the issue names. A watermark on
    the issue's year stops a caught-up run once its newest issues are on
    disk in full, and never re-fetches an issue page whose records are all
    stored. `--only jp/2025-01-01` names its own issue, which is then the
    only issue page fetched and the walk stores that one document."""
    session = net.make_session(net.BROWSER_UA)
    # the menu sets the issues newest first: a caught-up run proves its
    # newest issues are complete and stops, and the backlist is never
    # re-read. The basefile names its own issue, so an --only run reads that
    # one issue page instead of the inventory
    issues = _jp_issues(session)
    if only:
        year, code = only.split("/", 1)[1].split("-")[:2]
        issues = [(s, l) for s, l in issues
                  if _jp_issue_code(s, l) == (code, year)]
    # the slug rule runs before the walk: a slug shape the registry does not
    # hold is a code gap and fails the run loud, never a skip
    for slug, label in issues:
        _jp_issue_code(slug, label)

    def records(issue):
        slug, label = issue
        try:
            html = net.request(session, "GET",
                               "%s/tidskriften/%s/" % (JP.base, slug)).text
            time.sleep(delay)
            return _jp_records_from_page(html, slug, label)
        except ValueError as err:
            # a challenged or template-less page can read as an articleless
            # issue: one issue that serves no page must not stop the sweep
            # over the others. The Skip is the record of the miss: `walk`
            # logs it, the store stays dirty, and the next run re-meets the
            # page. An --only run of such an issue ends red on its own: the
            # walk meets the named document nowhere
            return [harvest.Skip("jp %s served no article list: %s"
                                 % (slug, err))]

    return harvest.issue_walk(
        root, "jp", issues, records,
        body=lambda record: (lambda: net.request(
            session, "GET", record["document_url"]).content),
        missing="the jp listing carries no document %s",
        delay=delay, full=full, only=only, limit=limit)


# --------------------------------------------------------------------------
# the shared entry point
# --------------------------------------------------------------------------

SYNC = {"svjt": svjt_sync, "jp": jp_sync, "ft": ft_sync, "nmt": nmt_sync,
        "njel": njel_sync, "siplr": siplr_sync, "urt": urt_sync,
        "euar": euar_sync, "lod": lod_sync, "lawpub": lawpub_sync}
# `journals.SCOPES` is the one list of what this source harvests (re-exported
# here, where the CLI reads it); this table is what runs them, so a scope
# added to one and not the other is a fault, said here rather than as a
# missing key mid-run
assert set(SYNC) == set(SCOPES), \
    "scope registry and SYNC disagree: %s" % (set(SYNC) ^ set(SCOPES))

# each scope's listing origin(s), for the per-scope harvest banner: the
# journals state theirs in the registry, the lawpub platform its one listing
SCOPE_ORIGINS = {**{j.kod: j.listings for j in JOURNALS},
                 "lawpub": (LAWPUB_LISTING,)}


def sync(root, scopes=None, full=False, only=None, limit=None, delay=0.5,
         jobs=None):
    """Download the named scopes (default all ten: the nine journals and
    the lawpub platform). The journals
    are separate hosts, so they fan out the way `guidance` does: concurrency
    is across scopes only, and each runner paces its own host. With no
    `jobs` the run fans out one worker per scope, so the wall time is the
    slowest journal alone. A scope that fails is reported and the run carries
    on with the rest, ending with an error that names it: one broken host
    does not take the others down, and the failed one is re-run on its
    own afterwards (`walk_records` has already stored everything it got)."""
    run = list(scopes or SCOPES)
    if jobs is None:
        jobs = len(run)
    return harvest.dispatch_scopes(root, scopes, SYNC, SCOPES, full=full,
                                   only=only, limit=limit, delay=delay,
                                   jobs=jobs, label="lawreview download",
                                   strict=False)