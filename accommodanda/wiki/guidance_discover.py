"""`lagen kommentar propose-guidance <dg-policy-url> [<CELEX>]` -- the Track-B
guidance proposer (a hand/machine aid, no LLM).

Given a European Commission DG *policy page* (e.g.
https://digital-strategy.ec.europa.eu/en/policies/data-act), scrape it for

  (a) the EUR-Lex reference to the act it is about -- a cross-check that the page
      matches the CELEX you expect, and
  (b) the guidance/library items it links, each resolved to its current PDF,

and emit a draft `guidance:` frontmatter block to paste -- after review -- into
the act's kommentar markdown (`commentary/eurlex/<year>/<CELEX>.md`), where
`lagen kommentar ai-annotate` then turns each PDF into fine-grained links.

Why a human still decides: a Commission microsite carries NO machine-readable
link from a guidance document to the legislation it explains -- verified against
Cellar / EUR-Lex / data.europa.eu, the relation lives only in prose, and the
policy page mixes genuine guidance ON the act with general policy, factsheets and
impact assessments. So this does the drudge the machine is good at -- finding the
candidates, resolving the version-specific `document/NNNNN` PDF URLs that drift on
every FAQ revision -- and leaves the one un-derivable judgement to a person:
which candidates are guidance on *this* act, and worth annotating.

Guidance *published in the OJ* is a different animal -- it gets its own sector-5
`XC`/`DC` CELEX and is machine-linked to the parent act in Cellar
(`work_cites_work` / `resource_legal_based_on_resource_legal`), so it belongs in
the corpus as an ordinary eurlex document, not as an external `.ann` link.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import lxml.html
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from ..lib import layout
from ..lib.net import BROWSER_UA as UA
from ..lib.util import write_atomic

# the op.europa.eu WAF and the DG sites 403 non-browser clients; a plain browser
# UA is enough (no cookies needed for the public policy/library pages) -- see
# lib/net.BROWSER_UA for the shared identity.
# the Commission newsroom redirector serves the raw PDF bytes at this path; the
# numeric id is per-file/per-version (it changes when the FAQ is revised), which
# is exactly why this has to be re-resolved rather than authored once by hand
RE_DOC = re.compile(r"https?://[^\"'<> ]*newsroom/dae/redirection/document/\d+")
# ELI reg/dir/dec -> the sector-3 CELEX letter, so a policy page's ELI link can be
# checked against the CELEX you passed even when it is not spelled out as CELEX:
ELI_LETTER = {"reg": "R", "dir": "L", "dec": "D"}

# The Commission sites that publish a per-act *guidance page* (a hub that links
# both the act's EUR-Lex entry and the guidance documents about it) in a shape we
# can enumerate: each is `(sitemap_url, hub_url_pattern)`. Only DG CONNECT's
# digital-strategy site follows this today -- its `/en/policies/<slug>` pages are
# the hubs the Data Act / CRA / AI Act guidance hangs off. Sibling DG sites
# structure content differently (no uniform per-act hub), so they are not
# crawlable this way and stay manual (`propose-guidance <their-page-url>`); when
# one does gain a usable hub pattern it is one entry here, not new code.
GUIDANCE_SITES = [
    ("https://digital-strategy.ec.europa.eu/sitemap.xml",
     re.compile(r"^https?://[^/]+/[a-z]{2}/policies/[a-z0-9\-]+$")),
]
# the built CELEX -> [guidance-page url] index; refreshed by `discover-guidance`,
# read by `propose-guidance <CELEX>` to auto-find the page(s) for an act. The path
# is owned by layout (which excludes it from `artifacts()` as a non-document).
INDEX_PATH = layout.GUIDANCE_INDEX
# a full crawl is a few hundred page fetches; fan out modestly. Concurrency does
# NOT change the DG site's 429 rate (measured: same ~half-fail at 1/3/6 workers),
# so parallelism only finishes the pages that *do* succeed sooner
CRAWL_WORKERS = 8


def _session():
    """A requests session that retries a couple of times *immediately* on the
    transient statuses the Commission WAF throws -- it 429s a large random fraction
    of requests with a `Retry-After: 0.000` (i.e. retry now) and enforces a rate
    budget that no client-side backoff defeats. So we retry fast (a genuine
    transient clears) and rely on the index *merging across runs* for the pages a
    given run's budget can't reach -- not on grinding one run to completion (that
    turns a 2-minute crawl into hours). One session per thread (not shareable
    across concurrent GETs)."""
    s = requests.Session()
    s.headers["User-Agent"] = UA
    retry = Retry(total=2, backoff_factor=0, respect_retry_after_header=False,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET"]))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def fetch(session, url):
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    return lxml.html.fromstring(resp.content)


def celex_from_href(href):
    """The CELEX a EUR-Lex href points at, or None. Handles both the explicit
    `?uri=CELEX:32023R2854` form and the ELI `/eli/reg/2023/2854` form (mapped to
    its sector-3 CELEX). A consolidated-text ELI/CELEX (leading 0, e.g.
    `01996L0009-...`) is not a base act, so it is ignored."""
    m = re.search(r"CELEX(?::|%3A)([0-9][0-9A-Z]+)", href, re.I)
    if m:
        celex = m.group(1).upper()
        return None if celex.startswith("0") else celex
    m = re.search(r"/eli/(reg|dir|dec)/(\d{4})/(\d+)", href)
    if m:
        kind, year, num = m.groups()
        return "3%s%s%s" % (year, ELI_LETTER[kind], num.zfill(4))
    return None


def library_items(tree, base):
    """Absolute `/en/library/<slug>` URLs linked from the policy page, in first-seen
    order -- these are the guidance/publication cards to resolve one hop deeper."""
    seen = {}
    for href in tree.xpath("//a/@href"):
        path = urlparse(urljoin(base, href)).path
        if re.fullmatch(r"/[a-z]{2}/library/[a-z0-9\-]+", path):
            seen.setdefault(urljoin(base, href).split("?")[0].split("#")[0], None)
    return list(seen)


def describe(session, url):
    """A library item resolved to `(title, pdf_url_or_None)`: its og:title (clean,
    already de-HTML-escaped) and the current newsroom PDF link it offers. Multiple
    document links can appear (PDF + a Markdown variant); we take the first, which
    on the Commission's pages is the PDF."""
    tree = fetch(session, url)
    title = (tree.xpath("//meta[@property='og:title']/@content")
             or tree.xpath("//h1//text()") or [url])[0].strip()
    docs = []
    for href in tree.xpath("//a/@href"):
        absu = urljoin(url, href)
        if RE_DOC.fullmatch(absu.split("?")[0]) and absu not in docs:
            docs.append(absu.split("?")[0])
    return title, (docs[0] if docs else None)


def direct_docs(tree, policy_url):
    """Guidance PDFs linked *straight from the policy page*, bypassing a library
    item (the CRA page links its FAQ this way). `(anchor-text, policy_url, pdf)` --
    the policy page is the stable human landing here since there is no library
    slug. First-seen order, one entry per distinct PDF."""
    out, seen = [], set()
    for a in tree.xpath("//a[@href]"):
        pdf = urljoin(policy_url, a.get("href")).split("?")[0]
        if RE_DOC.fullmatch(pdf) and pdf not in seen:
            seen.add(pdf)
            title = " ".join("".join(a.itertext()).split()) or "(untitled PDF)"
            out.append((title, policy_url, pdf))
    return out


def yaml_scalar(s):
    """A YAML-safe rendering of a title: quote when it carries a `:` or other
    indicator that would otherwise break the `key: value` parse (en-dashes and
    the like are fine bare, matching the hand-authored files)."""
    if re.search(r"[:#]|^[\s\-?&*!|>%@`\"']|\s$", s):
        return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')
    return s


def propose(policy_url):
    """Scrape a DG policy page. Returns `(celexes, resolved, skipped)`:
    `celexes` the set of act CELEX its EUR-Lex links point at (for cross-check);
    `resolved` the `[(title, url, pdf)]` guidance candidates (library items with a
    PDF, plus PDFs linked directly on the page, deduped by PDF); `skipped` the
    `[(title, url, None)]` library items where no PDF could be resolved (surfaced,
    never silently dropped)."""
    sess = _session()
    tree = fetch(sess, policy_url)

    celexes = {c for href in tree.xpath("//a/@href")
               if "eur-lex" in href if (c := celex_from_href(href))}
    resolved, skipped, have = [], [], set()
    for url in library_items(tree, policy_url):
        title, pdf = describe(sess, url)
        if pdf:
            resolved.append((title, url, pdf))
            have.add(pdf)
        else:
            skipped.append((title, url, None))
    # PDFs the policy page links directly (no library item), minus any already
    # reached via a library item above
    for title, url, pdf in direct_docs(tree, policy_url):
        if pdf not in have:
            resolved.append((title, url, pdf))
    return celexes, resolved, skipped


def frontmatter_block(resolved):
    """The draft `guidance:` YAML for the resolved candidates, as a string."""
    lines = ["guidance:"]
    for title, url, pdf in resolved:
        lines += ["  - title: %s" % yaml_scalar(title),
                  "    url: %s" % url,
                  "    pdf: %s" % pdf]
    return "\n".join(lines)


# -- the crawler: build a CELEX -> guidance-page index so `propose-guidance` can
#    take an act id instead of a hand-known URL -------------------------------

def sitemap_locs(session, sitemap_url):
    """Every `<loc>` in a site's sitemap.xml (a flat urlset on the DG sites -- no
    sitemap-index to recurse), as absolute URL strings."""
    resp = session.get(sitemap_url, timeout=60)
    resp.raise_for_status()
    root = lxml.html.fromstring(resp.content)
    return [loc.text.strip() for loc in root.xpath("//*[local-name()='loc']")
            if loc.text and loc.text.strip()]


def hub_urls(locs, pattern):
    """The guidance-hub URLs among a sitemap's locs -- the per-act pages matching
    the site's hub pattern (a single-segment `/policies/<slug>`, not its deeper
    sub-pages). Deduped, normalised to https, order-preserving."""
    seen = {}
    for loc in locs:
        url = loc.replace("http://", "https://", 1)
        if pattern.match(url):
            seen.setdefault(url, None)
    return list(seen)


def page_celexes(url):
    """The act CELEX(es) a guidance page points at via its EUR-Lex links -- the
    join that keys the index. A fresh retrying session per call (not shared) so the
    crawl can fan out across threads safely and each page rides out a 429."""
    resp = _session().get(url, timeout=60)
    resp.raise_for_status()
    tree = lxml.html.fromstring(resp.content)
    return {c for href in tree.xpath("//a/@href")
            if "eur-lex" in href if (c := celex_from_href(href))}


def build_index(sites=GUIDANCE_SITES, progress=None, limit=None, force=False):
    """Crawl the configured guidance sites and return `(index, stats)`. `index`
    maps each act CELEX to the sorted guidance-page URLs that reference it; unless
    `force`, it is **merged onto the existing on-disk index** -- the site 429s a
    random slice of every run, so successive runs fill each other's gaps and the
    index converges (a `force` run starts clean, authoritative when the rate budget
    is fresh). `stats` is `{fetched, total, failed:[(url, err)]}`. `progress(done,
    total, url)` is called as pages resolve; `limit` caps pages (a quick check)."""
    sess = _session()
    pages = []
    for sitemap_url, pattern in sites:
        found = hub_urls(sitemap_locs(sess, sitemap_url), pattern)
        pages += found[:limit] if limit else found

    index = {} if force else {c: set(v) for c, v in load_index().items()}
    failed, done, fetched = [], 0, 0
    with ThreadPoolExecutor(max_workers=CRAWL_WORKERS) as pool:
        futures = {pool.submit(page_celexes, url): url for url in pages}
        for fut in as_completed(futures):
            url = futures[fut]
            done += 1
            if progress:
                progress(done, len(pages), url)
            try:
                celexes = fut.result()
            except requests.RequestException as exc:
                failed.append((url, str(exc)))
                continue
            fetched += 1
            for celex in celexes:
                index.setdefault(celex, set()).add(url)
    stats = {"fetched": fetched, "total": len(pages), "failed": failed}
    return {c: sorted(urls) for c, urls in sorted(index.items())}, stats


def load_index():
    """The on-disk `CELEX -> [page url]` index, or `{}` if none built yet."""
    return json.loads(INDEX_PATH.read_text()) if INDEX_PATH.exists() else {}


def write_index(index):
    write_atomic(INDEX_PATH, json.dumps(index, ensure_ascii=False, indent=2))
    return INDEX_PATH


def pages_for(celex):
    """The guidance-page URLs indexed for an act CELEX (empty list if the index is
    missing or has no entry)."""
    return load_index().get(celex, [])
