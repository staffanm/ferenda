"""The nmt (Nordisk miljörätt) walk: the journal's two listing pages, the
issue tables of contents set in them, and the per-article PDFs the lines name.

The journal sets no issue pages: each of the two listing pages prints the
issues' tables of contents in full, and the walk reads every article off the
line its issue's contents set it in. A line is the author, the title, a
leader and the article's page -- and a link to the article's PDF where the
journal has published one.

The journal has set these lines in three hands across the 2009-2026 archive,
and the one reader here takes all three off the same line:

  * the newer issues put the title in a bold span and the page in the link's
    own text ("David Langlet; *Introduction* … <a>NMT…pdf</a>5");
  * the older ones set the title in the line's text and the author's short
    name in the link ("… 5 <a>Michanek.pdf</a>Michanek");
  * a print-only article names its page and no link at all
    ("Charlotta Zetterberg; Introduction … 5") -- the record keeps it and
    states no document, so the parse mines nothing behind it.

An issue is the line that names it, a bold line with no author before it
("NMT 2025:2", "NMT2024:2", the special issues' "NMT 2024:Special issue:").
A line that is a link on its own is a document link the journal set for the
article above it; a line that is neither states nothing the walk keeps.
"""

import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString

from ..lib import harvest, net
from ..lib.harvest import select_pending
from ..lib.util import normalize_space
from .journals import NMT

__all__ = ["nmt_sync"]

# An issue's label, every form the archive has set it in.
RE_NMT_LABEL = re.compile(
    r"^(?:NMT)?\s*(\d{4})(?::\s*(?:(\d+)|special\s+issue.*)"
    r"|-(\d+))?\s*(?:\.pdf)?$", re.I)

# A line's page: a leader (… or a run of dots), then the number -- on its
# own in the line's text in the older hands. The newer hands set the leader
# before the document link and the number after the link's own text, where
# it ends the line: the line's trailing number is then the page.
RE_NMT_PAGE = re.compile(r"(?:…|\.{2,})\s*(\d{1,3})\b")
RE_NMT_PAGE_END = re.compile(r"(?<!\d)(\d{1,3})\s*$")

# What splits a line's author from its title: the semicolon the newer hands
# set and the colon the older ones.
RE_NMT_SEP = re.compile(r"[;:]")


def _nmt_issue_code(label):
    """The issue's year and number, off its label. A label that carries only
    the year ("NMT 2009.pdf") is the year's one issue; the special issues
    are "s", the rest their own number."""
    m = RE_NMT_LABEL.match(normalize_space(label).strip())
    if m is None:
        raise ValueError("no nmt issue code for label %r" % label)
    year = m.group(1)
    if m.group(2) is not None:
        return year, m.group(2)
    if m.group(3) is not None:
        return year, m.group(3)
    if re.search(r"special\s+issue", label, re.I):
        return year, "s"
    return year, "1"


def _nmt_prefix(row):
    """The line's text before its first bold span -- the author, where the
    line has one. A line whose bold span is its first content has none: that
    is how an issue's label line is told off from an article's."""
    bolds = [s for s in row.find_all("span")
             if "bold" in (s.get("style") or "")]
    if not bolds:
        return None
    out = []
    for node in row.children:
        if node is bolds[0]:
            break
        if isinstance(node, NavigableString):
            out.append(str(node))
        elif node.name:
            out.append(node.get_text(" "))
    return normalize_space("".join(out))


def _nmt_bare_link(row):
    """The link when the row's only content is one of the journal's links.
    The journal also sets rows that are a text run and one link, the link
    being the author's name at the line's tail, and those are not bare."""
    link = row.find("a", class_="link1", recursive=False)
    if link is None:
        return None
    for c in row.children:
        if isinstance(c, NavigableString) and c.strip():
            return None
    return link


def _nmt_row(row, urljoin_, open_article):
    """One line of an issue's table of contents: what it is, and the payload
    the kind carries. `("header", label)`, `("article", dict)`,
    `("cont", dict)` and `("link", href)`; a line that is none of the four
    is noise and comes back as `None`.

    A new article is a line that sets an author: the newer hands set the
    author in plain text before a bold title run, and the older hands set
    the whole line as plain text that starts with a dash. A plain line with
    a separator that sets no dash is either a new article (the middle hands'
    hand) or the title-continuation line of the article above it (the
    journal's older hands set a long title across two such lines, the
    author's line first): where the line above it is an article the journal
    has not yet set a page for, it is the continuation.
    """
    links = row.find_all("a", class_="link1")
    full = normalize_space(row.get_text(" ", strip=True))
    if not full:
        return None
    bare = _nmt_bare_link(row)
    if bare is not None:
        # a row that is a link on its own: the issue's label, or the
        # document link the journal set for the article above it
        text = normalize_space(bare.get_text(" ", strip=True)).strip()
        if RE_NMT_LABEL.match(text):
            return ("header", text)
        return ("link", urljoin_(NMT.base, bare["href"]))
    bolds = [s for s in row.find_all("span")
             if "bold" in (s.get("style") or "")]
    if bolds:
        prefix = _nmt_prefix(row) or ""
        if not prefix.strip():
            # no author before the bold run: the issue's label line, a title
            # line continuing the article above it, or a stray bold line
            if links and RE_NMT_LABEL.match(
                    normalize_space(links[0].get_text(" ", strip=True))):
                return ("header",
                        normalize_space(links[0].get_text(" ", strip=True)))
            if RE_NMT_LABEL.match(full):
                return ("header", full)
            return ("cont", _nmt_part(row, links, full))
        if RE_NMT_SEP.search(prefix) is None:
            return None   # a pointer line ("Conference Speakers … 213")
        title = " ".join(normalize_space(s.get_text(" ", strip=True))
                         for s in bolds).strip()
        return ("article", _nmt_article(row, prefix, title, links, full))
    # a plain line: the older hands start the line with a dash, with or
    # without the space after it, and every other plain line is either a
    # new article (the middle hands' hand) or a continuation of the
    # article above it, by the rule the docstring sets out
    if full.startswith("-"):
        m = RE_NMT_SEP.search(full)
        if m is None:
            return None
        prefix = full[1:]
        m = RE_NMT_SEP.search(prefix)
        if m is None:
            return None
        prefix, rest = prefix[:m.start()], prefix[m.end():]
        title = rest
        page = None
        pm = [x for x in RE_NMT_PAGE.finditer(rest)]
        if pm:
            last = pm[-1]
            page = last.group(1)
            title = rest[:last.start()]
        href = None
        if links:
            href = urljoin_(NMT.base, links[0]["href"])
        return ("article", _nmt_article(row, prefix.lstrip("- "),
                                        title, links, full,
                                        page=page, doc=href))
    m = RE_NMT_SEP.search(full)
    if m is not None:
        prefix = full[:m.start()]
        # a caption line introducing the journal's special issues; the
        # line's own PDF is the line below it, and the walk keeps none of it
        if prefix.strip().lower().startswith("special issue"):
            return None
        if open_article is not None and open_article["sida"] is None:
            return ("cont", _nmt_part(row, links, full))
        rest = full[m.end():]
        title = rest
        page = None
        pm = [x for x in RE_NMT_PAGE.finditer(rest)]
        if pm:
            last = pm[-1]
            page = last.group(1)
            title = rest[:last.start()]
        href = None
        if links:
            href = urljoin_(NMT.base, links[0]["href"])
        return ("article", _nmt_article(row, prefix, title, links, full,
                                        page=page, doc=href))
    if links or RE_NMT_PAGE.search(full):
        return ("cont", _nmt_part(row, links, full))
    return None       # a caption line, a line the table of contents sets


def _nmt_part(row, links, full):
    """The payload a continuation line carries: the line's own text as the
    title's part (a bold line's bold run, a plain line's whole text), and the
    page and document link where the line sets them. The line's link text is
    the journal's name for its own PDF, and it keeps none of it in the
    title."""
    bolds = [s for s in row.find_all("span")
             if "bold" in (s.get("style") or "")]
    if bolds:
        title = " ".join(normalize_space(s.get_text(" ", strip=True))
                         for s in bolds).strip()
    else:
        title = full
    page = None
    pm = [x for x in RE_NMT_PAGE.finditer(full)]
    if pm:
        page = pm[-1].group(1)
        if not bolds:
            title = full[:pm[-1].start()]
    if page is None:
        tm = RE_NMT_PAGE_END.search(full)
        if tm:
            page = tm.group(1)
            if not bolds:
                title = full[:tm.start()]
    for a in links:
        text = normalize_space(a.get_text(" ", strip=True))
        title = title.replace(text, " ")
    title = normalize_space(title).strip(" -–")
    doc = None
    if links:
        doc = urljoin(NMT.base, links[0].get("href"))
    return {"title": title, "page": page, "doc": doc}


def _nmt_article(row, prefix, title, links, full, page=None, doc=None):
    """One article line as a record, minus the fields only the issue knows
    (its number, the line's place in the issue). The page is the line's own
    statement, and the document is the line's first link -- the newer hands'
    page link and the older hands' author link are the one and the same
    element. A title the journal sets across two lines states its page on
    the second of them, so the page may come from a later line: the page's
    reader completes the record, and the issue's check says so when the
    journal states a title and no page for it."""
    if page is None:
        pm = [x for x in RE_NMT_PAGE.finditer(full)]
        if pm:
            page = pm[-1].group(1)
    if page is None:
        tm = RE_NMT_PAGE_END.search(full)
        if tm:
            page = tm.group(1)
    # the newer hands leave the notation's own separator at the tail of the
    # author's run: it splits the author from the title, so it is not part
    # of the name
    author = normalize_space(
        prefix.lstrip("-– ").strip().rstrip(";:").strip())
    if doc is None and links:
        doc = urljoin(NMT.base, links[0].get("href"))
    return {
        "journal": "nmt",
        "year": None,          # the issue's, filled in by the caller
        "issue": None,
        "seq": None,
        "titel": normalize_space(title).strip(),
        "fattare": author or None,
        "sammanfattning": None,
        "sida": page,
        "source_url": None,    # the listing page, filled in by the caller
        "document_url": doc,
    }


def _nmt_issues_from_page(html, listing_url):
    """One listing page's issues, in the order the page sets them: each with
    the article records its table of contents names. A line that precedes
    every issue label is a page the journal did not set, and it says so. An
    article's page may sit on a title-continuation line a few lines below
    its author's line, so the walk completes the records line by line, and
    the check below it is the one that says a record the journal states no
    page for is missing."""
    soup = BeautifulSoup(html, "html.parser")
    issues = []
    current = None
    open_article = None
    for row in soup.find_all("p", class_="mobile-undersized-upper"):
        kind = _nmt_row(row, urljoin, open_article)
        if kind is None:
            continue
        name, payload = kind
        if name == "header":
            year, issue = _nmt_issue_code(payload)
            current = {"year": year, "issue": issue, "articles": []}
            issues.append(current)
            open_article = None
        elif name == "link":
            if open_article is not None and open_article["document_url"] is None:
                open_article["document_url"] = payload
            # a link under a line that named its own PDF is extra material
            # the journal sets in the contents, and the walk keeps none of it
        elif name == "cont":
            # a continuation line: the title's own line, and where the
            # journal has set them there, the article's page and document
            if open_article is None or open_article["sida"] is not None:
                continue
            if payload["title"]:
                open_article["titel"] = normalize_space(
                    (open_article["titel"] + " " +
                     payload["title"])).strip()
            if payload["page"] is not None:
                open_article["sida"] = payload["page"]
            if payload["doc"] is not None:
                open_article["document_url"] = payload["doc"]
        else:
            if current is None:
                raise ValueError(
                    "nmt: an article line precedes any issue label on %s"
                    % listing_url)
            article = payload
            article["year"] = current["year"]
            article["issue"] = current["issue"]
            article["source_url"] = listing_url
            current["articles"].append(article)
            open_article = article
    if not issues:
        raise ValueError("the nmt listing names no issues -- the page moved")
    for issue in issues:
        if not issue["articles"]:
            raise ValueError("nmt %s:%s states no articles"
                             % (issue["year"], issue["issue"]))
        for article in issue["articles"]:
            if not article["titel"]:
                raise ValueError("nmt %s:%s states no title"
                                 % (issue["year"], issue["issue"]))
            # the journal's oldest hands set no page on some lines of a
            # table of contents (2017:1 sets no page on three of its five
            # articles); the record keeps no page, and the identifier takes
            # the article's place in the issue instead
    return issues


def _nmt_text(session, url):
    """One listing page's text, decoded by the page's own head. The journal's
    server sends `text/html` with no charset, and the library then reads the
    page's bytes as ISO-8859-1, which turns the contents' leader … into two
    characters and the page's rules go blind: the page declares utf-8 in its
    head, and that is the decoding the page's text is set in."""
    raw = net.request(session, "GET", url).content
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:2048], re.I)
    if m is not None:
        return raw.decode(m.group(1).decode("ascii"))
    # no declared charset has not happened on this host; if it does, a strict
    # decode fails loud rather than mangling the leaders the line rules read
    return raw.decode("utf-8")


def nmt_sync(root, full=False, only=None, limit=None, delay=0.5):
    """The journal's whole archive: the two listing pages, then the per-article
    PDFs the tables of contents name. `--only nmt/2025-2-01` names its own
    issue's line, and the walk stores that one document."""
    session = net.make_session(net.BROWSER_UA)
    pending = []
    seen_basefiles = set()
    for listing in NMT.listings:
        html = _nmt_text(session, listing)
        time.sleep(delay)
        for issue in _nmt_issues_from_page(html, listing):
            for seq, article in enumerate(issue["articles"], start=1):
                basefile = "nmt/%s-%s-%02d" % (issue["year"], issue["issue"],
                                               seq)
                if basefile in seen_basefiles:
                    continue        # the same issue on both listing pages
                seen_basefiles.add(basefile)
                record = dict(article, seq="%02d" % seq,
                              basefile=basefile)
                body = None
                if record["document_url"] is not None:
                    u = record["document_url"]
                    body = (lambda u=u: net.request(session, "GET", u).content)
                pending.append((record, body))
    return harvest.walk_records(
        root, select_pending(pending, only,
                             "the nmt listings carry no article %s"),
        delay=delay, full=full, limit=limit, scope="nmt")