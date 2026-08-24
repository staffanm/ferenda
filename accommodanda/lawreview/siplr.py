"""The siplr (Stockholm Intellectual Property Law Review) walk: the journal's
one issues page, then each issue page's article headings and the per-article
PDFs set beside them.

The journal's issues page is the whole inventory (the 2018 and later issues),
one link per issue. An issue page sets its number and year in its own H1
("Issue #2 2025"), its articles as a heading each -- the title, and the
author set beside it in the journal's three hands: after its "By" line, as a
bare name line at the heading's tail (its 2023 #1), or not at all (the 2024
#1 interview) -- and beside them the per-article PDFs.

The two lists are paired in the order the page sets them, and the file's
own name settles a pairing the order would get wrong: the journal's older
files carry the article's title slug ("being-equitable-about..."), its
newer ones the author's surname ("SIPLR2025_nr2_4_Lundstedt.pdf"), and a
file that names another heading than its place in the order is filed under
the heading it names -- the 2019 #1 page sets eight print-only headings
before its one PDF, and page order alone filed that PDF under the wrong
article. A heading no PDF pairs with (the 2024 #2 issue lists one) names a
print-only record; an article PDF beside no heading, a file whose name
names no heading where the counts already disagree, and a second PDF on
one heading are each a page shape the reader does not know, and the walk
says so. The files that are not articles -- the combined-issue file
(`Hela_`, `Online*`, `IP_nr-N_YYYY_A4` and `_www` names across the years),
the issue's own cover and contents files (`Issue*`), its `inlagaomslag` and
`titelsidor` files, and the one class-action pleading file its 2022 #2 issue
hosts -- and a file set on another issue's page (its name stamps the
issue's own year, and the journal's 2024 #2 page hosts one of 2022's) are
set aside by their names, and name no record.
"""

import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

from ..lib import harvest, net
from ..lib.harvest import select_pending
from ..lib.util import approximate_date, normalize_space, record_path
from .journals import SIPLR

__all__ = ["siplr_sync"]

RE_ISSUE_HREF = re.compile(r"https?://stockholmiplawreview\.com/issue-(\d+)-(\d{4})/$")
# The issue page's H1 ("Issue #2 2025"): the number and year the page states
# for every article it sets, nothing inferred from the link that got here.
RE_ISSUE_H1 = re.compile(r"Issue\s+#(\d+)\s+(\d{4})\b", re.I)
# The "By" line that splits a heading's title from its author. The journal
# has once set the line with no space before the name ("ByEashan"), so the
# space after "By" is optional; the capital after it keeps a title's own
# "Bylaw" from reading as a line. Where a title does set a "By" of its own,
# the journal's author line is the last of them, at the heading's tail.
RE_BY = re.compile(r"\s+By\s*(?=[A-ZÅÄÖ])(.*)$", re.S)
# The files an issue page sets that are not articles, the journal's hands
# across its years: the combined-issue file under its `Hela_` name in the
# 2018 issues, its `Online*` and bare `IP_nr-N_YYYY_A4` names in the 2019
# through 2022 ones and its `_www` name in the 2025 one; the issue's own
# cover-and-contents file under its `Issue*` name; the cover's `inlagaomslag`
# and title pages' `titelsidor` names; the 2023 #1 whole-issue `hela` hand;
# and the one class-action pleading file its 2022 #2 issue has hosted since.
RE_NOT_AN_ARTICLE = re.compile(
    r"(^Hela_|^Online|^IP_nr-\d+_\d{4}_A4\.pdf$|^Issue"
    r"|(?:^|_)www\.pdf$|hela\.pdf$|inlagaomslag|titelsidor"
    r"|github_complaint)", re.I)
# The journal's per-issue naming hand stamps the issue's number and year
# into an article file's name (`..._Tryck_IP_nr-2_2019_A4_...`), so a stamp
# that states another year than the page's is a file set on the wrong issue
# page: the journal's 2024 #2 page hosts one of 2022's.
RE_NAME_STAMP = re.compile(r"IP_nr-\d+_(\d{4})")


def _siplr_issues(session):
    """The journal's whole issue inventory off the one issues page, oldest
    first the way the page sets it."""
    html = net.request(session, "GET", SIPLR.listings[0]).text
    soup = BeautifulSoup(html, "html.parser")
    issues = {}
    for a in soup.find_all("a", href=RE_ISSUE_HREF):
        href = a.get("href")
        assert isinstance(href, str)
        match = RE_ISSUE_HREF.search(href)
        assert match is not None
        issues.setdefault(match.group(0), True)
    if not issues:
        raise ValueError("the siplr issues page names no issues -- "
                         "the page moved")
    return sorted(issues)


def _siplr_issue_code(url):
    """The issue's number and year off the address that states them.
    `RE_ISSUE_HREF` sets the number in its first group and the year in its
    second, the order its filter below relies on."""
    m = RE_ISSUE_HREF.search(url)
    assert m is not None
    return m.group(1), m.group(2)


def _siplr_heading_lines(h3):
    """The heading's lines in the page's own order, its `<br>` breaks the
    line ends, a marked run (the 2024 #1 interview's `<em>` subtitle) read
    into its line."""
    lines, buf = [], []
    for child in h3.children:
        if child.name == "br":
            lines.append("".join(buf))
            buf = []
        else:
            buf.append(child.get_text())
    lines.append("".join(buf))
    return [normalize_space(line) for line in lines
            if normalize_space(line)]


def _siplr_title_author(h3):
    """A heading's title and author, in the journal's three hands: after
    its "By" line, as a bare name line at the heading's tail, or nowhere
    (the heading is its title alone, and the record's author stays unset)."""
    text = normalize_space(h3.get_text(" ", strip=True))
    ms = [x for x in RE_BY.finditer(text)]
    if ms:
        m = ms[-1]
        return text[:m.start()], m.group(1).strip()
    lines = _siplr_heading_lines(h3)
    if len(lines) > 1:
        return " ".join(lines[:-1]), lines[-1]
    return text, None


def _fold_name(s):
    """A title or file name as a comparable slug: percent-escapes undone,
    diacritics folded to ASCII, everything that is not a letter or digit a
    single hyphen. The journal's own file names are this fold of the title
    (its older hand) or of the author's name (its newer one)."""
    s = unquote(unquote(s))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _pdf_headings(name, headings):
    """The headings a PDF's own file name names, as indexes into `headings`:
    the title slug the journal's older files carry (truncated names keep
    their first 18 letters), or an author-surname token its newer files
    carry. An empty set is a name in neither hand; two authors sharing a
    surname make the set ambiguous, and the page order settles it."""
    fn = _fold_name(name)
    named = set()
    for i, (title, author) in enumerate(headings):
        slug = _fold_name(title)
        if len(slug) >= 12 and slug[:18] in fn:
            named.add(i)
            continue
        for word in re.split(r"[,\s]+", author or ""):
            wf = _fold_name(word)
            if len(wf) >= 4 and re.search(r"(?:^|-)%s(?:-|$)" % re.escape(wf),
                                          fn):
                named.add(i)
                break
    return named


def _pair_pdfs(headings, pdfs, year, issue):
    """{heading index: pdf href}: each PDF filed under the heading its own
    file name names, the page order deciding only where the name is silent
    or ambiguous -- and blind page order refused where it could mispair (a
    nameless file while the counts already disagree). The 2019 #1 page is
    why the name outranks the order: eight print-only headings sit before
    its one PDF, and order alone filed that PDF under the wrong article."""
    taken = {}
    for href in pdfs:
        named = _pdf_headings(href.rsplit("/", 1)[1], headings)
        free = [i for i in range(len(headings)) if i not in taken]
        if not free:
            raise ValueError("siplr %s #%s: %d article PDFs beside %d "
                             "headings" % (year, issue, len(pdfs),
                                           len(headings)))
        name = href.rsplit("/", 1)[1]
        if len(named - set(taken)) == 1:
            target = (named - set(taken)).pop()
        else:
            target = free[0]
            if named and target not in named:
                raise ValueError(
                    "siplr %s #%s: %s names another article than its place"
                    % (year, issue, name))
            if not named and len(pdfs) != len(headings):
                raise ValueError(
                    "siplr %s #%s: cannot place %s -- its name names no "
                    "heading and the counts disagree" % (year, issue, name))
        if target in taken:
            raise ValueError("siplr %s #%s: two PDFs on one heading (%s)"
                             % (year, issue, name))
        taken[target] = href
    return taken


def _siplr_records_from_page(html, issue_url, year, issue):
    """One issue page's article headings and article PDFs as records, paired
    by `_pair_pdfs` (the file's own name, then the page order). A heading no
    PDF pairs with names a print-only record; an article PDF beside no
    heading is a page the journal did not set, and the walk says so."""
    soup = BeautifulSoup(html, "html.parser")
    headings = []
    for h3 in soup.find_all("h3"):
        if not h3.get_text(strip=True):
            continue
        title, author = _siplr_title_author(h3)
        if not title:
            raise ValueError("siplr %s #%s: a heading states no title"
                             % (year, issue))
        headings.append((title, author))
    pdfs = []
    for a in soup.find_all("a", href=re.compile(r"\.pdf$")):
        href = a.get("href")
        assert isinstance(href, str)
        name = href.split("/")[-1]
        if RE_NOT_AN_ARTICLE.search(name):
            continue                 # a file that is not an article
        m = RE_NAME_STAMP.search(name)
        if m is not None and m.group(1) != year:
            continue                 # a file set on another issue's page
        if href not in pdfs:         # the 2018 #2 page links one file twice
            pdfs.append(href)
    if len(pdfs) > len(headings):
        raise ValueError(
            "siplr %s #%s: %d article PDFs beside %d headings"
            % (year, issue, len(pdfs), len(headings)))
    paired = _pair_pdfs(headings, pdfs, year, issue)
    records = []
    for i, (title, author) in enumerate(headings):
        seq = "%02d" % (i + 1)
        records.append({
            "basefile": "siplr/%s-%s-%s" % (year, issue, seq),
            "journal": "siplr",
            "year": year,
            "issue": issue,
            "seq": seq,
            "titel": title,
            "fattare": author or None,
            "sammanfattning": None,
            "source_url": issue_url,
            # a heading no PDF pairs with names a print-only record: the
            # 2024 #2 issue lists one, and its archive state is permanent,
            # not a page it did not set
            "document_url": paired.get(i),
        })
    if not records:
        raise ValueError("siplr %s #%s: the issue page sets no articles"
                         % (year, issue))
    return records


def _siplr_page_code(html, url):
    """The issue the page's own heading states, read case-insensitively: the
    journal set one of its headings (2023 #1) in capitals, and a heading the
    reader would miss is a crash it does not need. A page that states no
    issue heading at all is a page the journal did not set, and the walk
    says so (as a Skip, the store stays dirty, the next run retries)."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = next((h for h in soup.find_all("h1")
               if h.get_text(strip=True).lower().startswith("issue")), None)
    if h1 is None:
        raise ValueError("siplr %s: no issue number on the page -- "
                         "the template moved" % url)
    m = RE_ISSUE_H1.match(h1.get_text(strip=True))
    if m is None:
        raise ValueError("siplr %s: an unreadable issue number %r"
                         % (url, h1.get_text(strip=True)))
    return m.group(2), m.group(1)


def siplr_sync(root, full=False, only=None, limit=None, delay=0.5):
    """The journal's whole archive: the one issues page, then the issue
    pages newest-first and every article PDF the page sets. A watermark on
    the issue's year stops a caught-up run once its newest issue is on disk
    in full -- one index read, one newest-issue-page read -- and never
    re-fetches an issue page whose articles are all stored. `--only
    siplr/2025-2-03` names its own issue, which is then the only issue page
    fetched and the walk stores that one document."""
    session = net.make_session(net.BROWSER_UA)
    issues = _siplr_issues(session)
    if only:
        year, issue, _seq = only.split("/", 1)[1].split("-")
        url = next((u for u in issues
                    if _siplr_issue_code(u) == (issue, year)), None)
        if url is None:
            raise ValueError("the siplr archive carries no issue %s %s"
                             % (year, issue))
        html = net.request(session, "GET", url).text
        time.sleep(delay)
        records = _siplr_records_from_page(html, url, year, issue)
        pending = []
        for r in records:
            u = r["document_url"]
            pending.append((r,
                            (lambda u=u:
                             net.request(session, "GET", u).content)
                            if u else None))
        return harvest.walk_records(
            root, select_pending(pending, only,
                                 "the siplr archive carries no article %s"),
            delay=delay, full=full, limit=limit, scope="siplr")
    watermark = harvest.HarvestWatermark(
        Path(root) / "siplr" / ".watermark.json",
        lookahead_limit=3, safety_days=30)
    # newest issue first: the journal publishes its articles in issues, so a
    # caught-up run proves its newest issue is complete and stops, and the
    # archive behind it is never re-read. The key is the issue code read off
    # the address, the year first: a string sort of the addresses would put
    # issue 2 of an older year ahead of issue 1 of the newest one
    issues.sort(key=lambda u: _siplr_issue_code(u)[::-1], reverse=True)

    def items():
        for url in issues:
            html = net.request(session, "GET", url).text
            time.sleep(delay)
            year, issue = _siplr_page_code(html, url)
            yield from _siplr_records_from_page(html, url, year, issue)

    def item_key(record):
        return harvest.document_item_key(
            record, record_path(root, "siplr", record["basefile"]),
            *([harvest.pdf_path(root, record["basefile"])]
              if record["document_url"] else []),
            # the journal states the year, not the day: the year's middle
            date=approximate_date(record["year"]))

    def resolve(record):
        return harvest.resolve_document(
            record, record_path(root, "siplr", record["basefile"]),
            harvest.pdf_path(root, record["basefile"]),
            ((lambda: net.request(session, "GET",
                                  record["document_url"]).content)
             if record["document_url"] else None),
            harvest.verify_pdf, full=full, delay=delay)

    result = harvest.walk(
        items(), resolve=resolve, item_key=item_key, watermark=watermark,
        full=full, limit=limit, only=only, scope="siplr")
    return result.seen, result.new