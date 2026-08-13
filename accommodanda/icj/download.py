"""Harvester for International Court of Justice decisions.

One index, two transports:

  * ``icj-cij.org/decisions`` -- a Drupal view whose exposed filters are plain
    query parameters. It answers ordinary HTTP, and one request with
    ``from=1946`` returns every decision the Court has ever issued (877 rows).
    Its default is ``from=2023``, which shows 87 -- passing the year explicitly
    is what makes the harvest complete, not a paging loop.
  * the decision PDFs -- behind a Cloudflare challenge that no header or cookie
    from the index page satisfies. They are fetched through
    `lib.browser.DetachedChrome`, the same headful transport ``rs`` and
    ``foreskrift`` use, which clears the challenge and hands back the exact
    bytes Chrome cached.

A record is stored as one JSON (the index row: case, kind, procedure, date)
plus the English PDF.
"""

import collections
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup

from ..lib import browser, compress
from ..lib.harvest import HarvestWatermark, ItemKey, verify_pdf, walk, write_record
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.util import normalize_space
from .model import KINDS, RE_LANGUAGE, doc_basefile, parse_stem

ICJ = "https://www.icj-cij.org"
DECISIONS = ICJ + "/decisions"
# the view's own exposed filters. `type=1` is its "All decisions"; the harvest
# does its own scoping (see `in_scope`) rather than trust three separate views.
# `from` is the whole scoping filter. No `to` is sent: the exposed select only
# offers years up to the current one and answers an out-of-range year with an
# empty result page under a 200 -- so pinning an upper year here would harvest
# nothing at all the first January after it went stale.
FIRST_YEAR = 1946
# the Court files a decision under one profile directory per case number
CASE_FILES = "/sites/default/files/case-related/"
# a Chrome profile shared across a run, so one challenge clears the whole
# harvest rather than one per document
PROFILE = ".chrome-profile"

# The Court's own word on the law. Judgments and advisory opinions are taken
# whole; of the 688 orders only those indicating provisional measures are --
# the other ~620 fix and extend time-limits for the Memorial and the
# Counter-Memorial, which is docket bookkeeping and not a reader's document.
RE_PROVISIONAL = re.compile(r"provisional measures", re.I)


def in_scope(row):
    """Whether one index row belongs in the corpus."""
    if row["kind"] in ("judgment", "advisory opinion"):
        return True
    return bool(RE_PROVISIONAL.search(row["procedure"] or ""))


def record_path(root, basefile):
    return Path(root) / (basefile + ".json")


def body_path(root, basefile):
    return Path(root) / (basefile + ".pdf")


def list_basefiles(root):
    return compress.list_stems(root)


def _english(hrefs):
    """The English PDF among a row's language variants, or the bilingual one
    when the Court published no English-only copy.

    Every one of the 877 rows carries an English copy today, so this is not a
    fallback papering over a gap -- it is the Court's own two publication
    shapes, and the reader needs the text either way."""
    variants = {}
    for href in hrefs:
        stem = href.rsplit("/", 1)[-1].removesuffix(".pdf")
        language = stem.rsplit("-", 1)[-1] if "-" in stem else ""
        if RE_LANGUAGE.match(language):
            variants[language.upper().rstrip("C")] = href
    return variants.get("EN") or variants.get("BI")


def _row(element):
    """One ``.views-row`` -> its record, or None when the row carries no
    decision PDF (the view also lists the odd press item)."""
    fields = [normalize_space(field.get_text(" ", strip=True))
              for field in element.select(".views-field > .field-content")]
    hrefs = [a["href"] for a in element.find_all("a", href=True)
             if a["href"].startswith(CASE_FILES) and a["href"].endswith(".pdf")]
    href = _english(hrefs)
    if href is None or len(fields) < 4:
        return None
    stem = href.rsplit("/", 1)[-1].removesuffix(".pdf")
    parts = parse_stem(stem.rsplit("-", 1)[0])
    if parts is None:
        return None
    return {"basefile": doc_basefile(stem.rsplit("-", 1)[0]),
            "case": parts["case"], "date": parts["date"],
            "kind": KINDS[parts["kind"]], "title": fields[0],
            "case_name": fields[2], "procedure": fields[3] or None,
            "url": ICJ + href}


def enumerate_decisions(session):
    """Every in-scope decision as a record, newest first.

    One request. The whole index is 1.1 MB of HTML and the view does not
    paginate -- ``?page=1`` returns the same rows -- so a paging loop here would
    silently harvest the first page 80 times."""
    envelope = request(session, "GET", DECISIONS, timeout=180, params={
        "type": "1", "from": str(FIRST_YEAR), "sort_bef_combine": "order_DESC"})
    rows = BeautifulSoup(envelope.text, "html.parser").select(".views-row")
    # an empty index means the view's markup or its filter names drifted, not
    # that the Court has issued no decisions -- fail loudly rather than let the
    # next relate wipe the corpus (rule:fail-fast)
    if not rows:
        raise ValueError("icj: /decisions returned no rows -- the view's "
                         ".views-row markup or its exposed filters drifted")
    records = [record for record in map(_row, rows) if record]
    if len(records) < len(rows) // 2:
        raise ValueError("icj: only %d of %d index rows parsed -- the row "
                         "markup drifted" % (len(records), len(rows)))
    scoped = [record for record in records if in_scope(record)]
    _check_scope(scoped)
    return scoped


# What the index has looked like since the Court put it online, as floors rather
# than exact counts so an ordinary new decision does not trip them.
FLOORS = {"judgment": 150, "advisory opinion": 30}
MIN_PROVISIONAL = 60
MAX_ORDER_SHARE = 0.4       # of the scoped set


def _check_scope(scoped):
    """Refuse an index whose shape says the view drifted.

    `_row` reads the view's columns by position, and column 3 is `procedure` --
    which is the whole scope decision for orders. If the Drupal view gains or
    reorders a column, every row still parses and the guard above stays quiet,
    while the corpus silently gains the ~620 time-limit orders or loses the 66
    provisional-measures ones. Only the distribution can see that
    (rule:fail-fast)."""
    counts = collections.Counter(record["kind"] for record in scoped)
    for kind, floor in FLOORS.items():
        if counts[kind] < floor:
            raise ValueError(
                "icj: the index lists %d %ss, below the %d the Court has "
                "issued -- the /decisions view's columns drifted"
                % (counts[kind], kind, floor))
    if counts["order"] < MIN_PROVISIONAL:
        raise ValueError(
            "icj: only %d provisional-measures orders passed the scope test, "
            "below %d -- the view's `procedure` column drifted"
            % (counts["order"], MIN_PROVISIONAL))
    if counts["order"] > MAX_ORDER_SHARE * len(scoped):
        raise ValueError(
            "icj: orders are %d of %d scoped decisions, over %.0f%% -- the "
            "scope test is letting the Court's ~620 time-limit orders through"
            % (counts["order"], len(scoped), 100 * MAX_ORDER_SHARE))


def fetch_pdf(chrome, url):
    data = chrome.pdf(url)
    verify_pdf(data)
    return data


def resolve(chrome, root, record, full=False, delay=0.3):
    """Store one decision: its index row as JSON, its English PDF beside it."""
    path = record_path(root, record["basefile"])
    body = body_path(root, record["basefile"])
    if not full and compress.exists(path) and compress.exists(body):
        return False
    compress.write_download(body, fetch_pdf(chrome, record["url"]))
    write_record(path, record)
    time.sleep(delay)
    return True


def sync(root, full=False, only=None, limit=None, delay=0.3, log=print):
    root = Path(root)
    records = enumerate_decisions(make_session(USER_AGENT))
    if only:
        record = next((r for r in records if r["basefile"] == only), None)
        if record is None:
            raise ValueError("ICJ lists no in-scope decision %s" % only)
        records = [record]

    watermark = HarvestWatermark(root / ".watermark.json",
                                 lookahead_limit=30, safety_days=30)

    def item_key(record):
        return ItemKey(record["basefile"],
                       compress.exists(record_path(root, record["basefile"]))
                       and compress.exists(body_path(root, record["basefile"])),
                       record["date"])

    # one Chrome for the whole run: the Cloudflare challenge is cleared once and
    # its cookie then serves every fetch, and a browser launch per document
    # would cost more than the download
    with browser.DetachedChrome(root / PROFILE, settle=8.0) as chrome:
        result = walk(records,
                      resolve=lambda r: resolve(chrome, root, r, full=full,
                                                delay=delay),
                      item_key=item_key,
                      watermark=None if only else watermark,
                      full=full, limit=limit, scope="icj", count_label="stored",
                      total=len(records), log=log)
    return result.seen, result.new
