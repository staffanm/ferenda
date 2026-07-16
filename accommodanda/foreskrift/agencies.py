"""The per-agency registry -- the government författningssamlingar of the
lagrummet.se list (the per-county samlingar excluded) as configuration over the
shared harvest engine (:mod:`harvest`). Each entry is an
:class:`~harvest.Agency`: a författningssamling code, the issuing org, its index
URL, and the architecture (an ``enumerate`` + a ``resolve``) that fits its site,
plus ``params``. 71 fs codes are registered -- 66 live-harvested, the rest
closed series with no live harvester (RSFS, SOSFS/HSLF-FS, SJVFS, SVKFS): their
documents live in the corpus. SKVFS and MTFS select a detached headful-Chrome
transport in config; ordinary agencies stay on HTTP.

An agency is *config*, not a pipeline. Many sites are covered by the four
generic enumerate shapes (``indexed``/``paginated``/``json``/``sitemap``) plus a
``resolve`` (``resolve_landing`` / ``resolve_direct``) and a classify; the
representative spread:

  * **FFFS** (Finansinspektionen) -- a single HTML förteckning; bespoke
    ``fi_enumerate`` (its rows carry the year+lopnummer fused in the detail URL).
  * **SSMFS** (Strålsäkerhetsmyndigheten) -- ``paginated_enumerate`` (`?page=N`),
    landing pages, text-classified files (PDFs served without a `.pdf` suffix).
  * **NFS** (Naturvårdsverket) -- ``json_enumerate`` (a search API returns the
    whole corpus), landing pages, files classified by PDF filename.
  * **KIFS** (Kemikalieinspektionen) -- ``indexed_enumerate`` (one static page),
    landing pages whose files are grouped under ``<h2>`` section headings
    (``classify_section``), Sitevision ``/download/`` PDFs.

The rest of the file is the long tail of sites that need a bespoke ``enumerate``
(a POST search API, an inline family listing, a filename-slug number the generic
:func:`ref` can't read, predecessor-series routing under ``fs_from_designation``
or ``DocRef.fs``). Those still ride the engine: they mint DocRefs through
:func:`ref` / :func:`direct_docref` and, when a listing is oldest-first,
re-order via :func:`newest_first` so the incremental watermark's date-boundary
stop stays valid. Adding an agency is picking an enumerate + classify and
filling params; a genuinely new site *shape* is one new function here or in
:mod:`harvest`, not a pipeline.
"""

import html
import json
import re
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote, unquote

import requests
from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.net import BROWSER_UA, is_not_found, request
from ..lib.util import basefile_slug as slug
from ..lib.util import document_extension, record_path
from . import harvest, mtfs, skvfs
from .harvest import (
    Agency,
    DocRef,
    classify_default_regulation,
    classify_file,
    classify_href,
    classify_section,
    classify_single,
    direct_docref,
    indexed_enumerate,
    json_enumerate,
    newest_first,
    paginated_enumerate,
    ref,
    resolve_direct,
    resolve_landing,
)

# --------------------------------------------------------------------------
# FFFS (Finansinspektionen) -- bespoke enumerate: the förteckning row links the
# base by a detail URL /sok-fffs/{year}/{base-digits}/ (year+lopnummer fused),
# not by a clean "FFFS YYYY:N" string, so the generic indexed_enumerate can't
# read it. The base detail page hangs the whole family (original + konsoliderad
# + every amendment), resolved by the shared resolve_landing (text-classified).
# --------------------------------------------------------------------------

RE_FI_BASE = re.compile(r"/sok-fffs/(\d{4})/(\d{4})(\d+)/?$")


def fi_enumerate(session, agency):
    """One DocRef per distinct *base* regulation from FI's single förteckning."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        assert isinstance(href, str)
        m = RE_FI_BASE.search(href)
        if not m:
            continue
        arsutgava, lopnummer = m.group(1), str(int(m.group(3)))
        basefile = "%s/%s:%s" % (agency.fs, arsutgava, lopnummer)
        if basefile in seen:
            continue
        seen.add(basefile)
        yield DocRef(basefile=basefile,
                     identifier="%s %s:%s" % (agency.fs.upper(), arsutgava, lopnummer),
                     url=harvest.absolute(agency.base_url, a["href"]))


FFFS = Agency(
    fs="fffs", name="Finansinspektionen", publisher="Finansinspektionen",
    base_url="https://www.fi.se",
    index_url="https://www.fi.se/sv/vara-register/fffs/forteckning-fffs/",
    enumerate=fi_enumerate, resolve=resolve_landing,
)

SSMFS = Agency(
    fs="ssmfs", name="Strålsäkerhetsmyndigheten", publisher="Strålsäkerhetsmyndigheten",
    base_url="https://www.stralsakerhetsmyndigheten.se",
    index_url="https://www.stralsakerhetsmyndigheten.se/publikationer/foreskrifter/",
    enumerate=paginated_enumerate, resolve=resolve_landing,
    params={
        "page_url": "https://www.stralsakerhetsmyndigheten.se/publikationer/foreskrifter/?page={page}",
        "row_select": "ul.search-result li.search-item div.search-content h3 a",
        "pdf_select": "div.meta li.file-link a[href]",
        "classify": classify_file,
    },
)

NFS = Agency(
    fs="nfs", name="Naturvårdsverket", publisher="Naturvårdsverket",
    base_url="https://www.naturvardsverket.se",
    index_url="https://www.naturvardsverket.se/lagar-och-regler/foreskrifter-och-allmanna-rad/",
    enumerate=json_enumerate, resolve=resolve_landing,
    params={
        "api_url": "https://www.naturvardsverket.se/api/naturvardsverket/regulation/search/?s=500&id=7925&lang=sv",
        "unwrap": "searchModel",
        "id_field": "nfsText", "url_field": "url", "title_field": "heading",
        # filenames come both hyphenated (nfs-2014-29.pdf) and underscored /
        # zero-padded (nfs_2007_09.pdf); match either, classify_href sorts them.
        "pdf_select": 'a[href*="nfs" i][href$=".pdf"]',
        "classify": classify_href,
    },
)

KIFS = Agency(
    fs="kifs", name="Kemikalieinspektionen", publisher="Kemikalieinspektionen",
    base_url="https://www.kemi.se",
    index_url="https://www.kemi.se/lagar-och-regler/lagstiftningar-inom-kemikalieomradet/kemikalieinspektionens-foreskrifter-kifs",
    enumerate=indexed_enumerate, resolve=resolve_landing,
    params={
        "link_select": 'a[href*="kemikalieinspektionens-foreskrifter-kifs/kifs-"]',
        "pdf_select": 'a[href*="/download/"]',
        "classify": classify_section,
    },
)


# --------------------------------------------------------------------------
# BFS (Boverket) -- the API-direct architecture: an open REST API returns the
# whole register, each item carrying its PDF URL (dokumentlank) and amendment
# back-link (grundforfattning), so there is *no landing page* -- resolve_direct
# downloads straight from the listing. Consolidations live in ovrigaDokument.
# --------------------------------------------------------------------------

def bfs_enumerate(session, agency):
    """One DocRef per grundförfattning from Boverket's REST API, with its
    amendments + consolidations attached (the whole register in one call; the
    API requires an explicit Accept header)."""
    items = request(session, "GET", agency.params["api_url"], parse_json=True,
                    headers={"Accept": "application/json"})
    family = {}
    for it in items:
        family.setdefault(it.get("grundforfattning") or it["forfattning"], []).append(it)
    for it in items:
        if it.get("typ") != "grundforfattning":
            continue
        m = re.search(r"(\d{4}):(\d+)", it["forfattning"])
        if not m:
            continue
        arsutgava, lopnummer = m.group(1), str(int(m.group(2)))
        members = family.get(it["forfattning"], [])
        amendments = [{"identifier": x["forfattning"], "url": x.get("dokumentlank")}
                      for x in members if x.get("typ") == "andringsforfattning"]
        consolidations = [{"url": d.get("lank") or d.get("dokumentlank")}
                          for x in members for d in (x.get("ovrigaDokument") or [])
                          if d.get("typ") == "Konsolidering"]
        yield DocRef(
            basefile="%s/%s:%s" % (agency.fs, arsutgava, lopnummer),
            identifier="%s %s:%s" % (agency.fs.upper(), arsutgava, lopnummer),
            url=it.get("dokumentlank") or "", title=it.get("titel"),
            extra={"regulation_url": it.get("dokumentlank"),
                   "consolidations": consolidations, "amendments": amendments,
                   "title": it.get("titel"),
                   "source_url": "https://forfattningssamling.boverket.se/"})


BFS = Agency(
    fs="bfs", name="Boverket", publisher="Boverket",
    base_url="https://rinfo.boverket.se",
    index_url="https://api.boverket.se/forfattningssamling/v1/forfattningar",
    enumerate=bfs_enumerate, resolve=resolve_direct,
    params={"api_url": "https://api.boverket.se/forfattningssamling/v1/forfattningar"},
)


# --------------------------------------------------------------------------
# Second wave (10 agencies). Each is config over the engine; the comment notes
# the architecture combination it exercises.
# --------------------------------------------------------------------------

# indexed + landing + filename-classify (PDFs under /globalassets/foreskrifter/)
ELSAKFS = Agency(
    fs="elsakfs", name="Elsäkerhetsverket", publisher="Elsäkerhetsverket",
    base_url="https://www.elsakerhetsverket.se",
    index_url="https://www.elsakerhetsverket.se/om-oss/lag-och-ratt/foreskrifter-i-nummerordning/",
    enumerate=indexed_enumerate, resolve=resolve_landing,
    params={"link_select": 'div.list-item.regulation-item a[href*="/foreskrifter/elsak-fs-"]',
            "pdf_select": 'a[href*="/globalassets/foreskrifter/elsak-fs"][href$=".pdf"]',
            "classify": classify_href},
)

# indexed + DIRECT (the listing anchor is the PDF); skip companion memo PDFs
RGKFS = Agency(
    fs="rgkfs", name="Riksgälden", publisher="Riksgäldskontoret",
    base_url="https://www.riksgalden.se",
    index_url="https://www.riksgalden.se/sv/press-och-publicerat/foreskrifter/",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href$=".pdf"]', "direct": True,
            "skip_re": r"Beslutspromemoria|[Ff]örteckning"},
)

# indexed + DIRECT (anchor is the PDF, no consolidations published)
LMFS = Agency(
    fs="lmfs", name="Lantmäteriet", publisher="Lantmäteriet",
    base_url="https://www.lantmateriet.se",
    index_url="https://www.lantmateriet.se/sv/om-lantmateriet/Rattsinformation/Foreskrifter/",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href*="/rattsinformation/foreskrifter/"][href$=".pdf"]',
            "direct": True},
)

# json REST API + DIRECT (each record carries downloadUrl)
KOVFS = Agency(
    fs="kovfs", name="Konsumentverket", publisher="Konsumentverket",
    base_url="https://publikationer.konsumentverket.se",
    index_url="https://publikationer.konsumentverket.se/sok?q=KOVFS",
    enumerate=json_enumerate, resolve=resolve_direct,
    params={"api_url": "https://publikationer-api.konsumentverket.se/api/products?search=KOVFS&pageIndex=1&pageSize=50&sort=title",
            "results_key": "data", "id_field": "title", "url_field": "downloadUrl",
            "title_field": "title", "direct": True},
)

# indexed + landing + filename-classify; Radware gate -> browser UA + sv locale
PTSFS = Agency(
    fs="ptsfs", name="Post- och telestyrelsen", publisher="Post- och telestyrelsen",
    base_url="https://pts.se",
    index_url="https://pts.se/regelbibliotek/gallande-foreskrifter-och-allmanna-rad/",
    enumerate=indexed_enumerate, resolve=resolve_landing,
    params={"link_select": '.secondlevel__content__box a[href^="/regelbibliotek/"]',
            "pdf_select": '.documentpage__content a[href$=".pdf"]',
            "classify": classify_href},
    user_agent=BROWSER_UA, headers={"Accept-Language": "sv-SE,sv;q=0.9"},
)

# paginated + landing + filename-classify. MCF (Myndigheten för civilt försvar)
# took over MSB's författningssamling; its "gällande regler" listing mixes the
# new MCFFS series with still-in-force MSBFS and older SÄIFS/IFS regulations, so
# `fs_from_designation` keeps each document under its own samling code rather
# than collapsing them onto one -- an MSBFS föreskrift keeps its MSBFS identity.
# The FS designation is read off each row's own text.
MCFFS = Agency(
    fs="mcffs", name="Myndigheten för civilt försvar (f.d. MSB)",
    publisher="Myndigheten för civilt försvar",
    base_url="https://www.mcf.se",
    index_url="https://www.mcf.se/sv/regler/gallande-regler/?sortOrder=DescendingYear",
    enumerate=paginated_enumerate, resolve=resolve_landing,
    params={"page_url": "https://www.mcf.se/sv/regler/gallande-regler/"
                        "?sortOrder=DescendingYear&selectedpage={page}",
            "row_select": "a.constitution-list-card-link",
            # new texts live under /contentassets/, the old SÄI/SÄIFS ones under
            # /siteassets/ with UUID filenames -- match both asset roots.
            "pdf_select": 'a[href*="assets/"][href$=".pdf"]',
            "classify": classify_default_regulation,
            "fs_from_designation": True},
)

# indexed over per-year pages + DIRECT (col-1 PDF link per row)
LIVSFS = Agency(
    fs="livsfs", name="Livsmedelsverket", publisher="Livsmedelsverket",
    base_url="https://www.livsmedelsverket.se",
    index_url="https://www.livsmedelsverket.se/om-oss/lagstiftning1/foreskrifter-i-nummerordning/",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"index_urls": ["https://www.livsmedelsverket.se/om-oss/lagstiftning1/"
                           "foreskrifter-i-nummerordning/foreskrifter-i-nummerordning-%d/" % y
                           for y in range(2026, 1995, -1)],
            "link_select": "td p.related-info > a[href]", "direct": True,
            "optional_pages": True},
)

# indexed + landing; type axis lives on the index, landing hangs one PDF
STEMFS = Agency(
    fs="stemfs", name="Energimyndigheten", publisher="Statens energimyndighet",
    base_url="https://www.energimyndigheten.se",
    index_url="https://www.energimyndigheten.se/om-oss/foreskrifter/",
    enumerate=indexed_enumerate, resolve=resolve_landing,
    params={"link_select": 'div.fake-td[data-headline="Nummer"] a',
            "pdf_select": 'a.link-download, a[href*="GetTemplateResource"]',
            "classify": classify_single},
)


# indexed via POST ("show all" button) + DIRECT (anchor is the PDF)
TFS = Agency(
    fs="tfs", name="Tullverket", publisher="Tullverket",
    base_url="https://www.tullverket.se",
    index_url="https://www.tullverket.se/omoss/dethargortullverket/verksamhetochorganisation/"
              "lagarochforeskrifter/sokitullverketsstyrdokument.4.7df61c5915510cfe9e7f3c1.html",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"post_data": {"searchString": "( metadata.dokumenttyp:TFS )",
                          "sortval": "NrU", "step": "0.0", "next": "0.0",
                          "submit": "« Allt »"},
            "link_select": 'a[rel="external"][href*="/download/"]', "direct": True},
)


# json REST API + landing + section-classify (h3 over each document list)
SIFS = Agency(
    fs="sifs", name="Spelinspektionen", publisher="Spelinspektionen",
    base_url="https://www.spelinspektionen.se",
    index_url="https://www.spelinspektionen.se/lagar-regler/foreskrifter/",
    enumerate=json_enumerate, resolve=resolve_landing,
    params={"api_url": "https://www.spelinspektionen.se/api/regulationapi?query=&showAll=true&top=100&skip=0",
            "results_key": "items", "id_field": "heading", "url_field": "url",
            "title_field": "heading",
            "pdf_select": 'ul.sp-documentlist a.sp-documentlist__item-link[href$=".pdf"]',
            "classify": classify_href},
)


# --------------------------------------------------------------------------
# PMFS / RPSFS (Polismyndigheten + its predecessor Rikspolisstyrelsen) -- the
# inline-family architecture: bespoke enumerate, but resolve_direct. The in-force
# listing is paginated HTML (.../N/, walk until a page has no rows) where each
# <li> is a whole *family*: a Grundföreskrift plus, nested inline under <h4>
# subtitles, its Ändringsföreskrifter and a Sammanställd (consolidated) version --
# the PDFs hang on the index row itself, there is no per-regulation landing page.
# So the family is read straight off the <li> into resolve_direct's extra payload
# (regulation_url + consolidations + amendment graph), like Boverket's API-direct
# source, but scraped. The single listing carries both series; a family routes by
# its *base* designation's prefix (params['keep_prefix']) so PMFS and RPSFS keep
# their own namespaces -- two Agency instances over one shared enumerate. An
# RPSFS base amended by a later PMFS act is normal: each amendment's identifier
# is read from its own link text, so a mixed-prefix amendment graph is fine.
# --------------------------------------------------------------------------

RE_PMFS_DESIG = re.compile(r"\b((?:PM|RPS)FS)\s+(\d{4}):(\d+)")


def pmfs_enumerate(session, agency):
    """One DocRef per base regulation of the series ``params['keep_prefix']``
    (``PMFS``/``RPSFS``), with its consolidation + amendments attached, read
    inline from each family <li> across the paginated in-force listing."""
    keep = agency.params["keep_prefix"]
    seen = set()
    page = 1
    while True:
        soup = BeautifulSoup(request(session, "GET", "%s%d/" % (agency.index_url, page)).text,
                             "html.parser")
        items = [li for li in soup.select("li.c-list__item")
                 if li.select_one('a.icon-document[href*="forfattningssamling"]')]
        if not items:
            return                                  # walked past the last page
        for li in items:
            base = next((m for lab in li.select(".c-regulation__label")
                         if (m := RE_PMFS_DESIG.search(lab.get_text(" ", strip=True)))), None)
            if base is None or base.group(1) != keep:
                continue
            year, lop = base.group(2), str(int(base.group(3)))
            basefile = "%s/%s:%s" % (agency.fs, year, lop)
            if basefile in seen:
                continue
            seen.add(basefile)
            title_el = li.select_one(".c-regulation__title")
            title = title_el.get_text(" ", strip=True) if title_el else None
            regulation_url, consolidations, amendments, role = None, [], [], ""
            for el in li.find_all(["h4", "a"]):
                cls = el.get("class") or []
                if el.name == "h4" and "c-regulation__subtitle" in cls:
                    role = el.get_text(" ", strip=True).lower()
                    continue
                if "icon-document" not in cls or "forfattningssamling" not in (el.get("href") or ""):
                    continue
                url = harvest.absolute(agency.base_url, el["href"])
                if "grund" in role:
                    regulation_url = regulation_url or url
                elif "sammanst" in role:
                    consolidations.append({"url": url})
                elif "ndring" in role:
                    am = RE_PMFS_DESIG.search(el.get_text(" ", strip=True))
                    amendments.append({"url": url,
                                       "identifier": "%s %s:%s" % (am.group(1), am.group(2),
                                                                   str(int(am.group(3)))) if am else None})
            yield DocRef(
                basefile=basefile, identifier="%s %s:%s" % (keep, year, lop),
                url=regulation_url or agency.index_url, title=title,
                extra={"regulation_url": regulation_url, "consolidations": consolidations,
                       "amendments": amendments, "title": title,
                       "source_url": agency.index_url})
        page += 1
        time.sleep(0.5)


PMFS = Agency(
    fs="pmfs", name="Polismyndigheten", publisher="Polismyndigheten",
    base_url="https://polisen.se",
    index_url="https://polisen.se/lagar-och-regler/polismyndighetens-forfattningssamling/",
    enumerate=pmfs_enumerate, resolve=resolve_direct,
    params={"keep_prefix": "PMFS"},
)

# RPSFS -- Rikspolisstyrelsens (pre-2015) series, still partly in force; shares
# the PMFS listing, kept apart by the base-prefix routing in pmfs_enumerate.
RPSFS = Agency(
    fs="rpsfs", name="Rikspolisstyrelsen", publisher="Polismyndigheten",
    base_url="https://polisen.se",
    index_url="https://polisen.se/lagar-och-regler/polismyndighetens-forfattningssamling/",
    enumerate=pmfs_enumerate, resolve=resolve_direct,
    params={"keep_prefix": "RPSFS"},
)


# --------------------------------------------------------------------------
# Fourth wave: the remaining lagrummet.se-listed government författnings-
# samlingar (county \d+FS excluded). Each is config over the engine; the
# comment notes the architecture combination it exercises.
# --------------------------------------------------------------------------

# indexed over three Sitevision category pages (el / fjärrvärme / naturgas) +
# landing + filename-classify. The base text's landing link carries only the
# title (no designation), so files are classified by their PDF filename
# (EIFS-YYYY-N-…, …-konsoliderad, …-om-ändring-…), not the link text.
EIFS = Agency(
    fs="eifs", name="Energimarknadsinspektionen", publisher="Energimarknadsinspektionen",
    base_url="https://ei.se",
    index_url="https://ei.se/om-oss/lagar-och-regler/foreskrifter/foreskrifter---el",
    enumerate=indexed_enumerate, resolve=resolve_landing,
    params={"index_urls": [
                "https://ei.se/om-oss/lagar-och-regler/foreskrifter/foreskrifter---el",
                "https://ei.se/om-oss/lagar-och-regler/foreskrifter/foreskrifter---fjarrvarme-och-fjarrkyla",
                "https://ei.se/om-oss/lagar-och-regler/foreskrifter/foreskrifter---naturgas"],
            "link_select": 'a[href*="/publikationer/foreskrifter-"]',
            "pdf_select": 'a[href*="/download/"][href$=".pdf"]',
            "classify": classify_href},
)

# indexed + landing + text-classify ("Konsoliderad utgåva", "HVMFS 2021:17" on
# the landing's /download/ links). HaV took over Fiskeriverkets samling, so the
# same förteckning also lists still-in-force FIFS regulations --
# `fs_from_designation` keeps each under its own fs. One repeal row names only
# the foreign NFS act it revokes (no own HVMFS number in the listing text),
# which would misroute to fs=nfs; skip_re drops it.
HVMFS = Agency(
    fs="hvmfs", name="Havs- och vattenmyndigheten", publisher="Havs- och vattenmyndigheten",
    base_url="https://www.havochvatten.se",
    index_url="https://www.havochvatten.se/vagledning-foreskrifter-och-lagar/foreskrifter.html",
    enumerate=indexed_enumerate, resolve=resolve_landing,
    params={"link_select": 'a[href*="/foreskrifter/register-"]',
            "pdf_select": 'a[href*="/download/"]',
            "classify": classify_file,
            "skip_re": r"Naturvårdsverkets föreskrifter \(NFS",
            "fs_from_designation": True},
)


# --------------------------------------------------------------------------
# SKSFS (Skogsstyrelsen) -- direct PDFs on one static page (grouped by subject in
# `div.regulation-table-wrapper` tables), no landing pages, no consolidations.
# indexed_enumerate can't be reused: the anchor text of an ändringsföreskrift
# leads with a *colon-formatted reference to the base it amends* ("… (SKSFS
# 2014:12) …") while its own number appears only space-formatted ("SKSFS 2015 4")
# -- so the shared text-first number extraction would misfile every amendment
# onto its base. This bespoke enumerate reads the number from the PDF filename
# slug only (sksfs-YYYY-N-…), which is unambiguous, and skips bilaga attachments.
# --------------------------------------------------------------------------

RE_SKSFS_FILE = re.compile(r"sksfs?-(\d{4})-(\d{1,3})\b")


def skogs_enumerate(session, agency):
    """One DocRef per PDF from Skogsstyrelsen's static register, keyed off the
    filename slug (the link text's colon-reference names the amended base, not the
    document's own number)."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for a in soup.select('a[href$=".pdf"][href*="/foreskrifter-efter-amne/"]'):
        href = a["href"]
        assert isinstance(href, str)
        name = href.rsplit("/", 1)[-1].lower()
        if "-bilaga" in name:                     # a separately-published annex
            continue
        m = RE_SKSFS_FILE.search(name)
        if not m:
            continue
        arsutgava, lopnummer = m.group(1), str(int(m.group(2)))
        text = a.get_text(" ", strip=True)
        docref = direct_docref(agency, agency.fs, arsutgava, lopnummer,
                               harvest.absolute(agency.base_url, href), seen, title=text)
        if docref:
            yield docref


SKSFS = Agency(
    fs="sksfs", name="Skogsstyrelsen", publisher="Skogsstyrelsen",
    base_url="https://www.skogsstyrelsen.se",
    index_url="https://www.skogsstyrelsen.se/lag-och-tillsyn/forfattningar/",
    enumerate=skogs_enumerate, resolve=resolve_direct,
)


# --------------------------------------------------------------------------
# SGU-FS (Sveriges geologiska undersökning) -- direct PDFs on one static page, but
# each base regulation is a <p> family: the grundföreskrift anchor plus, inline,
# its ändringsföreskrift anchor(s) and a "Konsoliderad version …" anchor. So the
# family is read straight off each <p> into resolve_direct's extra payload
# (regulation_url + consolidations + amendment graph), like a scraped API source.
# The link text carries a clean "SGU-FS YYYY:N" designation on every anchor.
# --------------------------------------------------------------------------

RE_SGU_DESIG = re.compile(r"SGU[- ]FS\s+(\d{4}):(\d+)", re.IGNORECASE)


def sgu_enumerate(session, agency):
    """One DocRef per base SGU-FS regulation, its consolidation + amendments read
    inline from the <p> family that groups them on the single register page."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for p in soup.select("div.main-content p"):
        anchors = p.select('a[href$=".pdf"]')
        if not anchors:
            continue
        base = None
        consolidations, amendments = [], []
        for a in anchors:
            text = a.get_text(" ", strip=True)
            url = harvest.absolute(agency.base_url, a["href"])
            m = RE_SGU_DESIG.search(text)
            if re.search(r"konsolider", text, re.IGNORECASE):
                consolidations.append({"url": url})
            elif "ändring" in text.lower():
                amendments.append({"url": url,
                                   "identifier": "SGU-FS %s:%s" % (m.group(1), str(int(m.group(2)))) if m else None})
            elif m:
                base = (m.group(1), str(int(m.group(2))), text, url)
        if base is None:
            continue
        arsutgava, lopnummer, title, url = base
        basefile = "sgufs/%s:%s" % (arsutgava, lopnummer)
        if basefile in seen:
            continue
        seen.add(basefile)
        yield DocRef(basefile=basefile, fs="sgufs",
                     identifier="SGU-FS %s:%s" % (arsutgava, lopnummer),
                     url=url, title=title,
                     extra={"regulation_url": url, "consolidations": consolidations,
                            "amendments": amendments, "title": title,
                            "source_url": agency.index_url})


SGUFS = Agency(
    fs="sgufs", name="Sveriges geologiska undersökning",
    publisher="Sveriges geologiska undersökning",
    base_url="https://www.sgu.se",
    index_url="https://www.sgu.se/om-sgu/verksamhet/foreskrifter/",
    enumerate=sgu_enumerate, resolve=resolve_direct,
    designation="SGU-FS",
)


# --------------------------------------------------------------------------
# DVFS (Domstolsverket) -- bespoke enumerate over a POST JSON search API. The
# register is an Optimizely/Find React SPA whose ordinance search posts to
# /api/search/{pageRootId} (an empty facet array = the whole register), paging
# by skip (100/page). Each hit already carries its landing-page URL (link.url)
# and title (description); the landing hangs one PDF under /globalassets/.../dvfs/,
# resolved by resolve_landing (filename-classified). The register lists every
# DVFS number as its own document -- grundföreskrifter, ändringsföreskrifter and
# allmänna råd alike -- with no consolidated family page, so each is harvested as
# its own base regulation (no amendment graph is published to attach).
# --------------------------------------------------------------------------

def dvfs_enumerate(session, agency):
    """One DocRef per DVFS document, paging Domstolsverket's ordinance search
    API (POST, empty facet body, 100 hits/page, newest-first by ordinanceId)."""
    seen = set()
    skip = 0
    while True:
        data = request(session, "POST", agency.params["api_url"] % skip,
                       parse_json=True, json=[],
                       headers={"Accept": "application/json"})
        items = data["searchResultItems"]
        for r in items:
            docref = harvest.ref(agency, r["link"]["title"], r["link"]["url"],
                                 seen, title=r.get("description"))
            if docref:
                yield docref
        skip += len(items)
        if not items or skip >= data["totalMatching"]:
            return
        time.sleep(0.3)


DVFS = Agency(
    fs="dvfs", name="Domstolsverket", publisher="Domstolsverket",
    base_url="https://www.domstol.se",
    index_url="https://www.domstol.se/om-sveriges-domstolar/for-dig-som-aktor-i-domstol/"
              "stod-for-aktorer-i-domstol/dvfs/",
    enumerate=dvfs_enumerate, resolve=resolve_landing,
    params={"api_url": "https://www.domstol.se/api/search/90578?searchPageId=90578"
                       "&scope=ordinance&skip=%d&take=100&sortMode=ordinanceId&isZip=false",
            "pdf_select": 'a[href*="/dvfs/"][href$=".pdf"]',
            "classify": classify_href},
)

# indexed + DIRECT. The förteckning links each in-force FARK straight to its PDF
# under /globalassets/dokument/foreskrifter[-konsoliderade]/; the anchor text
# carries the "(KVFS YYYY:N, FARK …)" designation. Most links are the
# konsoliderad version (the in-force text) -- the register publishes no separate
# family page, so resolve_direct stores each linked PDF as the base text. The
# selector excludes the regelförteckning under /regelverk/.
KVFS = Agency(
    fs="kvfs", name="Kriminalvården", publisher="Kriminalvården",
    base_url="https://www.kriminalvarden.se",
    index_url="https://www.kriminalvarden.se/om-oss/styrning-och-uppfoljning/"
              "kriminalvardens-foreskrifter/",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href*="/globalassets/dokument/foreskrifter"][href$=".pdf"]',
            "direct": True},
)

# indexed + DIRECT. A Sitevision document listing whose rows link each
# föreskrift straight to its /download/ PDF; the anchor text ends with the
# "… KFMFS YYYY:N | pdf | NN kB" designation. Rows for allmänna råd /
# information without a KFMFS number carry no designation and are dropped by
# ref (no number to key on).
KFMFS = Agency(
    fs="kfmfs", name="Kronofogdemyndigheten", publisher="Kronofogdemyndigheten",
    base_url="https://www.kronofogden.se",
    index_url="https://www.kronofogden.se/om-kronofogden/dina-rattigheter-lagar-och-regler/"
              "foreskrifter-allmanna-rad-och-meddelanden",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"link_select": 'div.documentList a[href*="/download/"]', "direct": True},
)


# --------------------------------------------------------------------------
# KBVFS (Kustbevakningen) -- bespoke enumerate: the filterable "gällande"
# register is a JS table whose rows are non-anchor elements carrying the PDF URL
# in a data-href attribute (and "KBVFS YYYY:N …" in the row text), so the generic
# indexed_enumerate (which reads href) can't see them. The data-href is the PDF
# itself (no landing), so each row builds a direct DocRef for resolve_direct.
# --------------------------------------------------------------------------

def kbvfs_enumerate(session, agency):
    """One direct DocRef per KBVFS row, reading the PDF URL from data-href and
    the designation from the row text."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for row in soup.select("[data-href]"):
        text = row.get_text(" ", strip=True)
        docref = harvest.ref(agency, text, row["data-href"], seen,
                             title=text, direct=True)
        if docref:
            yield docref


KBVFS = Agency(
    fs="kbvfs", name="Kustbevakningen", publisher="Kustbevakningen",
    base_url="https://www.kustbevakningen.se",
    index_url="https://www.kustbevakningen.se/om-oss/kustbevakningens-forfattningssamling/",
    enumerate=kbvfs_enumerate, resolve=resolve_direct,
)


# --------------------------------------------------------------------------
# FFS (Försvarsmakten) -- API-direct over Episerver/Optimizely Content Delivery.
# The four "Gällande FFS <period>" listing pages are JS (documentCategoryList),
# each fetching /api/episerver/v3.0/content/<pageId>?expand=PDFFilesArea; the
# JSON's `documentInfo` carries one entry per FFS text (name, preamble title,
# direct PDF url), so there is no landing page -- resolve_direct downloads
# straight from the listing. The samling also hangs FIB (Försvarets interna
# bestämmelser) documents; only FFS is in scope, so FIB-named rows are dropped.
# --------------------------------------------------------------------------

FFS_API = "https://www.forsvarsmakten.se/api/episerver/v3.0/content/%s?expand=PDFFilesArea"
FFS_PAGE_IDS = ["2562", "2563", "2564", "2565"]   # 1978-94, 1995-2011, 2012-13, 2014-


def ffs_enumerate(session, agency):
    """One DocRef per FFS text across the four Episerver listing pages; `ref`
    reads the number from the name ("FFS 2017:5") or, failing that, the PDF
    filename slug (bare-numbered rows like "2013:3" -> ffs-2013-03.pdf)."""
    seen = set()
    for pid in agency.params["page_ids"]:
        data = request(session, "GET", agency.params["api_url"] % pid,
                       parse_json=True, headers={"Accept": "application/json"})
        for doc in data.get("documentInfo", []):
            name = (doc.get("name") or "").strip()
            if name.upper().startswith("FIB"):     # interna bestämmelser, out of scope
                continue
            docref = harvest.ref(agency, name, doc.get("url", ""), seen,
                                 title=doc.get("preamble"), direct=True)
            if docref:
                yield docref


FFS = Agency(
    fs="ffs", name="Försvarsmakten", publisher="Försvarsmakten",
    base_url="https://www.forsvarsmakten.se",
    index_url="https://www.forsvarsmakten.se/om-forsvarsmakten/myndighetsinformation/dokument/",
    enumerate=ffs_enumerate, resolve=resolve_direct,
    params={"api_url": FFS_API, "page_ids": FFS_PAGE_IDS},
)

# indexed + DIRECT. A single static page lists the few in-force föreskrifter as
# direct /globalassets/ PDF links; the base number comes from the filename slug
# (forfattningssamling-kfs-2017-1.pdf) or a bare "(2020:1)" in the link text
# (kfs120.pdf). No consolidations published.
KFS = Agency(
    fs="kfs", name="Kommerskollegium", publisher="Kommerskollegium",
    base_url="https://www.kommerskollegium.se",
    index_url="https://www.kommerskollegium.se/uppdrag/forfattningssamling/",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href$=".pdf"][href*="kfs"]', "direct": True},
)

# indexed + DIRECT. A static "föreskrifter" page lists the in-force MIGRFS texts
# as direct /download/<node>/MIGRFS_YYYY_N.pdf links; the number comes from the
# "MIGRFS YYYY:N" link text. One legacy row uses the old N/YYYY numbering
# ("MIGRFS 5/2011" -> migrfs052011.pdf) that the generic parser can't read as a
# year:lopnummer, so it is skipped (skip_re).
MIGRFS = Agency(
    fs="migrfs", name="Migrationsverket", publisher="Migrationsverket",
    base_url="https://www.migrationsverket.se",
    index_url="https://www.migrationsverket.se/om-migrationsverket/styrning-och-uppfoljning/foreskrifter.html",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href*="migrfs" i][href$=".pdf"]', "direct": True,
            "skip_re": r"\d+/\d{4}"},
)


# --------------------------------------------------------------------------
# ÅFS (Åklagarmyndigheten) -- bespoke enumerate + DIRECT. A single Sitevision
# page server-renders every numbered författning as a direct /globalassets/ PDF
# anchor (an <h2> designation + a title <p>). The generic path can't be used:
# the fs slug must transliterate å ("ÅFS" -> "aafs", NOT "afs" which collides
# with Arbetsmiljöverkets AFS; predecessor "RÅFS" -> "raafs"), and the printed
# identifier must stay "ÅFS"/"RÅFS", neither of which agency.fs.upper() yields.
# Consolidated in-force texts live on a separate page and are not harvested.
# --------------------------------------------------------------------------

RE_AAFS_DESIG = re.compile(r"\b(RÅFS|ÅFS)\s*(\d{4}):(\d+)")
# printed designation -> (fs slug transliterating å->aa, identifier prefix)
AAFS_SERIES = {"ÅFS": ("aafs", "ÅFS"), "RÅFS": ("raafs", "RÅFS")}


def aafs_enumerate(session, agency):
    """One DocRef per numbered författning off Åklagarmyndighetens single listing
    page; routes ÅFS vs its RÅFS predecessor to their own (transliterated) fs."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for a in soup.find_all("a", href=True):
        head = a.find(["h2", "h3"])
        m = RE_AAFS_DESIG.match(head.get_text(" ", strip=True)) if head else None
        if not m:
            continue
        fs, prefix = AAFS_SERIES[m.group(1)]
        year, lop = m.group(2), str(int(m.group(3)))
        title_p = next((p.get_text(" ", strip=True) for p in a.find_all("p")
                        if not re.match(r"Storlek|Publicerad", p.get_text(strip=True))), None)
        docref = direct_docref(agency, fs, year, lop,
                               harvest.absolute(agency.base_url, a["href"]), seen,
                               identifier="%s %s:%s" % (prefix, year, lop), title=title_p)
        if docref:
            yield docref


AAFS = Agency(
    fs="aafs", name="Åklagarmyndigheten", publisher="Åklagarmyndigheten",
    base_url="https://www.aklagare.se",
    index_url="https://www.aklagare.se/om-oss/dokument/forfattningssamling",
    enumerate=aafs_enumerate, resolve=resolve_direct,
    designation="ÅFS",
)


# --------------------------------------------------------------------------
# PRVFS (Patent- och registreringsverket) -- bespoke enumerate + DIRECT. The
# "Avdelning A1 - gällande grundförfattningar" page hangs the base PDFs straight
# (no landing). Each entry's designation lives in the anchor text as a bare
# "YYYY:N" ("2026:2, P:144"); two anchors are a stray second PDF in another
# entry's paragraph with empty text, whose number is only in the 2-digit-year
# filename ("25prvfs-4_p142.pdf" -> 2025:4) -- so a filename fallback is needed,
# which the generic indexed_enumerate can't do (it wants a 4-digit-year slug).
# --------------------------------------------------------------------------

RE_PRV_TEXT = re.compile(r"(\d{4}):(\d+)")
RE_PRV_FILE = re.compile(r"/(\d{2})prvfs-?(\d+)_", re.IGNORECASE)


def prvfs_enumerate(session, agency):
    """One DocRef per PRVFS grundförfattning from Avdelning A1. Number from the
    anchor's bare "YYYY:N", else the 2-digit-year filename slug (pivot at 50:
    77->1977, 25->2025) for the stray empty-text second links."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for a in soup.select('a[href*="/prvfs/"][href$=".pdf"]'):
        href = a["href"]
        assert isinstance(href, str)
        m = RE_PRV_TEXT.search(a.get_text(" ", strip=True))
        if m:
            year, lop = m.group(1), str(int(m.group(2)))
        else:
            fm = RE_PRV_FILE.search(href)
            if not fm:
                continue
            yy = int(fm.group(1))
            year, lop = str(1900 + yy if yy > 50 else 2000 + yy), str(int(fm.group(2)))
        title = a.get_text(" ", strip=True) or None
        docref = direct_docref(agency, agency.fs, year, lop,
                               harvest.absolute(agency.base_url, href), seen, title=title)
        if docref:
            yield docref


PRVFS = Agency(
    fs="prvfs", name="Patent- och registreringsverket",
    publisher="Patent- och registreringsverket",
    base_url="https://www.prv.se",
    index_url="https://www.prv.se/sv/om-oss/var-verksamhet/styrdokument/"
              "prvs-forfattningssamling/avdelning-a1---gallande-grundforfattningar/",
    enumerate=prvfs_enumerate, resolve=resolve_direct,
)


# --------------------------------------------------------------------------
# RA-FS / RA-MS (Riksarkivet) -- API-direct over the föreskrifter subdomain's
# Angular backend: GET api/{series}/sok?...&sokBlandGiltiga=true returns the whole
# valid corpus as `traffLista`; each PDF is fetched by api/{series}/pdf/{path}.
# One shared enumerate drives both series (two Agency instances, cf. PMFS/RPSFS).
# RA-MS (myndighetsspecifika arkivföreskrifter/gallringsbeslut) is a distinct
# numbered series agencies cite ("RA-MS 2020:x") with its own PDFs, so it is its
# own fs "rams" rather than being folded into RA-FS. Each list entry is a numbered
# act; entries whose nummer == grundforfattning are base regulations, the rest are
# amendments grouped under their base (huvuddokument typId 2 = text, 1 = consolidated).
# --------------------------------------------------------------------------

RA_API = "https://foreskrifter.riksarkivet.se/api/"
RA_SOK = ("%s%s/sok?nummer=&rubrik=&fulltext=&myndighet=&arkivbildare="
          "&sokBlandGiltiga=true")


def _ra_pdf_url(series, huvuddokument, typ_id):
    """The api/{series}/pdf/{encoded-path} URL for the first huvuddokument of a
    given typId (2 = the författning text, 1 = the konsoliderad version)."""
    for hd in huvuddokument:
        if hd.get("typId") == typ_id and hd.get("publicerad") and hd.get("path"):
            return RA_API + series + "/pdf/" + quote(hd["path"], safe="")
    return None


def ra_enumerate(session, agency):
    """One DocRef per Riksarkivet grundförfattning of ``params['series']``
    (rafs/rams), consolidation + amendments attached from the shared corpus."""
    series = agency.params["series"]
    items = request(session, "GET", RA_SOK % (RA_API, series), parse_json=True)["traffLista"]
    family = {}
    for it in items:
        family.setdefault(it.get("grundforfattning"), []).append(it)
    for it in items:
        if it.get("nummer") != it.get("grundforfattning"):
            continue                              # an amendment, listed under its base
        m = re.match(r"(\d{4}):(\d+)$", it["nummer"])
        if not m:
            continue
        year, lop = m.group(1), str(int(m.group(2)))
        hd = it.get("huvuddokument", [])
        amendments = [{"identifier": "%s %s" % (agency.designation, x["nummer"]),
                       "url": _ra_pdf_url(series, x.get("huvuddokument", []), 2)}
                      for x in family.get(it["nummer"], []) if x.get("nummer") != it["nummer"]]
        cons = _ra_pdf_url(series, hd, 1)
        yield DocRef(
            basefile="%s/%s:%s" % (agency.fs, year, lop),
            identifier="%s %s:%s" % (agency.designation, year, lop),
            url=agency.index_url, title=it.get("rubrik"),
            extra={"regulation_url": _ra_pdf_url(series, hd, 2),
                   "consolidations": [{"url": cons}] if cons else [],
                   "amendments": amendments, "title": it.get("rubrik"),
                   "source_url": agency.index_url})


RAFS = Agency(
    fs="rafs", name="Riksarkivet", publisher="Riksarkivet",
    base_url="https://foreskrifter.riksarkivet.se",
    index_url="https://foreskrifter.riksarkivet.se/rafs",
    enumerate=ra_enumerate, resolve=resolve_direct,
    params={"series": "rafs"}, designation="RA-FS",
)

RAMS = Agency(
    fs="rams", name="Riksarkivet (myndighetsspecifika föreskrifter)",
    publisher="Riksarkivet",
    base_url="https://foreskrifter.riksarkivet.se",
    index_url="https://foreskrifter.riksarkivet.se/rams",
    enumerate=ra_enumerate, resolve=resolve_direct,
    params={"series": "rams"}, designation="RA-MS",
)


# --------------------------------------------------------------------------
# RFS (Riksdagsförvaltningen) -- API-direct over riksdagen.se's open dokument-
# lista JSON API (doktyp=rfs, paginated newest-first). Each hit is a numbered RFS
# act carrying its PDF in `filbilaga.fil`; the series is published flat (base regs
# and ändringsföreskrifter each get their own RFS number and PDF, with no family
# link in the API), so -- like LMFS -- each number is its own DocRef. Non-numbered
# entries (the "RFS-register" index, rm='zz') are skipped.
# --------------------------------------------------------------------------

RFS_API = ("https://data.riksdagen.se/dokumentlista/?doktyp=rfs&utformat=json"
           "&sz=200&sort=datum&sortorder=desc&p=%d")


def rfs_enumerate(session, agency):
    """One DocRef per numbered RFS act, walking the paginated dokumentlista API."""
    seen = set()
    page = 1
    while True:
        dl = request(session, "GET", RFS_API % page, parse_json=True)["dokumentlista"]
        docs = dl.get("dokument", [])
        if not docs:
            return
        for d in docs:
            year, lop = str(d.get("rm", "")), str(d.get("beteckning", ""))
            if not (re.fullmatch(r"\d{4}", year) and lop.isdigit()):
                continue                          # register/other non-numbered entry
            lop = str(int(lop))
            basefile = "%s/%s:%s" % (agency.fs, year, lop)
            if basefile in seen:
                continue
            seen.add(basefile)
            fil = (d.get("filbilaga") or {}).get("fil") or []
            pdf = next((f["url"] for f in fil if f.get("typ") == "pdf"), None)
            yield DocRef(
                basefile=basefile, identifier="RFS %s:%s" % (year, lop),
                url=pdf or "", title=d.get("titel"),
                extra={"regulation_url": pdf, "title": d.get("titel"),
                       "source_url": "https://data.riksdagen.se/dokument/%s" % d.get("dok_id")})
        if int(dl.get("@sida", 1)) >= int(dl.get("@sidor", 1)):
            return
        page += 1


RFS = Agency(
    fs="rfs", name="Riksdagsförvaltningen", publisher="Riksdagsförvaltningen",
    base_url="https://data.riksdagen.se",
    index_url="https://data.riksdagen.se/dokumentlista/?doktyp=rfs",
    enumerate=rfs_enumerate, resolve=resolve_direct,
)

# indexed + DIRECT. A single static page lists the whole samling as direct PDF
# links under .../forfattningssamling/{agency}/*.pdf; the KRFS samling is shared
# by several culture-sector agencies (Kulturrådet, Konstnärsnämnden, MTM,
# Musikverket, Riksantikvarieämbetet), all KRFS-numbered under one fs. Every
# anchor text starts "KRFS YYYY:N", so the generic direct enumerate reads the
# number straight off it (MTM files like "2025_3-1.pdf" carry no designation in
# the filename, but the text always does).
KRFS = Agency(
    fs="krfs", name="Statens kulturråd", publisher="Statens kulturråd",
    base_url="https://www.kulturradet.se",
    index_url="https://www.kulturradet.se/om-oss/forfattningssamling/",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href*="forfattningssamling-dokument"][href$=".pdf"]',
            "direct": True},
)


# --------------------------------------------------------------------------
# BFNAR (Bokföringsnämnden) -- a flat WordPress list of direct-linked PDFs, one
# per numbered allmänt råd (grund *and* ändring alike carry their own BFNAR
# number, so every PDF is a document in its own right). The designation is
# "BFNAR", not "*FS", so the generic RE_FS_NUMBER helpers don't apply and the
# link text names the *amended* råd, not the file's own number -- the only
# reliable own-number signal is the filename slug, in three shapes
# (bfnar-2026-1 / bfnar2025-4 / bfnar17-7, the last with a 2-digit year). A
# konsoliderad ("kons") file attaches to its grund's base; there are no landing
# pages, so resolve_direct downloads straight off the listing.
# --------------------------------------------------------------------------

RE_BFNAR_NUM = re.compile(r"bfnar[-_]?(\d{2,4})[-_]?(\d{1,3})", re.IGNORECASE)


def _bfnar_number(name):
    """(year, lopnummer) a BFNAR filename names as its *own* number. Clean
    filenames start with the own number (``bfnar17-7``, ``bfnar16-5-andring-i-…``);
    the two descriptive slugs (``…-bfnar-13-4``) carry the amended råd first and
    the own number last, so take the first match when the name starts ``bfnar``,
    else the last. 2-digit years are 20YY (the series began in 2000)."""
    matches = RE_BFNAR_NUM.findall(name)
    if not matches:
        return None
    yy, lop = matches[0] if name.lower().startswith("bfnar") else matches[-1]
    year = yy if len(yy) == 4 else "20" + yy
    return year, str(int(lop))


def bfnar_enumerate(session, agency):
    """One DocRef per distinct BFNAR number, grund + konsoliderad grouped."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    families: dict = {}
    order = []
    for a in soup.select('a[href*="bfnar" i][href$=".pdf" i]'):
        href = a["href"]
        assert isinstance(href, str)
        name = href.rsplit("/", 1)[-1].lower()
        number = _bfnar_number(name)
        if number is None:
            continue
        fam = families.setdefault(number, {"regulation": None, "consolidation": [],
                                           "title": a.get_text(" ", strip=True)})
        if number not in order:
            order.append(number)
        url = harvest.absolute(agency.base_url, href)
        if "kons" in name:
            fam["consolidation"].append({"url": url})
        elif fam["regulation"] is None:
            fam["regulation"] = url
    for year, lop in order:
        fam = families[(year, lop)]
        yield DocRef(
            basefile="bfnar/%s:%s" % (year, lop),
            identifier="BFNAR %s:%s" % (year, lop),
            url=fam["regulation"] or (fam["consolidation"][0]["url"] if fam["consolidation"] else ""),
            title=fam["title"],
            extra={"regulation_url": fam["regulation"] or
                   (fam["consolidation"][0]["url"] if fam["consolidation"] else None),
                   "consolidations": fam["consolidation"], "title": fam["title"],
                   "source_url": agency.index_url})


BFNAR = Agency(
    fs="bfnar", name="Bokföringsnämnden", publisher="Bokföringsnämnden",
    base_url="https://www.bfn.se",
    index_url="https://www.bfn.se/redovisningsregler/allmanna-rad/",
    enumerate=bfnar_enumerate, resolve=resolve_direct,
)


# --------------------------------------------------------------------------
# BOLFS (Bolagsverket) -- one Sitevision page, but structured as inline
# landing content: each base regulation is introduced by a heading
# ("Grundföreskriften BOLFS 2004:4"), its amendments and konsoliderad version
# under their own following headings ("Ändringsföreskrifter till BOLFS 2004:4
# …", "Konsoliderad version av BOLFS 2004:4"). The direct-link PDF filenames
# are unreliable (some carry no number: konsoliderad-version-av-…pdf), so the
# number and role come from the nearest heading. A bespoke enumerate walks
# headings + links in document order into resolve_direct's family payload. The
# site gates default requests -> browser UA + sv Accept-Language.
# --------------------------------------------------------------------------

RE_BOLFS = re.compile(r"BOLFS\s+(\d{4}):(\d+)")


def _bolfs_amend_id(href):
    """A BOLFS amendment's own number from its filename's *last* YYYY_N pair
    (``bolfs_2004_4_2006_3`` -> 2006:3), or None when unparseable."""
    nums = re.findall(r"(\d{4})[_-](\d{1,3})", href.rsplit("/", 1)[-1])
    return "BOLFS %s:%s" % (nums[-1][0], str(int(nums[-1][1]))) if len(nums) >= 2 else None


def bolfs_enumerate(session, agency):
    """One DocRef per base BOLFS regulation, grund + konsoliderad + amendment
    refs read off the heading-delimited sections of the single förteckning."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    families: dict = {}
    order = []
    cur = None
    role = "regulation"
    for el in soup.find_all(["h2", "h3", "h4", "a"]):
        if el.name != "a":
            low = el.get_text(" ", strip=True).lower()
            m = RE_BOLFS.search(el.get_text(" ", strip=True))
            if "ändring" in low:
                role = "amendment"
            elif "konsolider" in low:
                role = "consolidation"
            elif m and "grundför" in low or (m and role == "regulation"):
                role = "regulation"
            else:
                continue
            if m:
                cur = (m.group(1), str(int(m.group(2))))
                if cur not in families:
                    families[cur] = {"regulation": None, "consolidation": [], "amendment": []}
                    order.append(cur)
            continue
        href = el.get("href") or ""
        assert isinstance(href, str)
        if cur is None or "/download/" not in href or ".pdf" not in href.lower():
            continue
        url = harvest.absolute(agency.base_url, href)
        fam = families[cur]
        if role == "regulation":
            if fam["regulation"] is None:
                fam["regulation"] = url
        elif role == "consolidation":
            fam["consolidation"].append({"url": url})
        else:
            fam["amendment"].append({"url": url, "identifier": _bolfs_amend_id(href)})
    for year, lop in order:
        fam = families[(year, lop)]
        yield DocRef(
            basefile="bolfs/%s:%s" % (year, lop),
            identifier="BOLFS %s:%s" % (year, lop),
            url=fam["regulation"] or (fam["consolidation"][0]["url"] if fam["consolidation"] else ""),
            extra={"regulation_url": fam["regulation"] or
                   (fam["consolidation"][0]["url"] if fam["consolidation"] else None),
                   "consolidations": fam["consolidation"], "amendments": fam["amendment"],
                   "source_url": agency.index_url})


BOLFS = Agency(
    fs="bolfs", name="Bolagsverket", publisher="Bolagsverket",
    base_url="https://bolagsverket.se",
    index_url="https://bolagsverket.se/omoss/varverksamhet/styrochplaneringsdokument/"
              "bolagsverketsforeskrifterbolfs.2161.html",
    enumerate=bolfs_enumerate, resolve=resolve_direct,
    user_agent=BROWSER_UA, headers={"Accept-Language": "sv-SE,sv;q=0.9"},
)


# --------------------------------------------------------------------------
# CSNFS (Centrala studiestödsnämnden) -- a Sitevision page whose "Fulltext -
# <ämne>" sections list exactly the in-force base regulations, each as a single
# konsoliderad/fulltext PDF (the "Tryckt version" sections below hold the
# separate grund + ändrings-PDFs, folded into the fulltext already). Enumerate
# the Fulltext-section links as the base set; the base number leads each link's
# text ("2001:1 Centrala studiestödsnämndens föreskrifter…"). Direct PDFs, so
# resolve_direct downloads the fulltext as the regulation.
# --------------------------------------------------------------------------

def csnfs_enumerate(session, agency):
    """One DocRef per in-force CSNFS base regulation (its Fulltext PDF)."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for a in soup.select('a[href*="/download/"]'):
        href = a["href"]
        assert isinstance(href, str)
        head = a.find_previous(["h2", "h3", "h4"])
        if ".pdf" not in href.lower() or head is None \
                or not head.get_text(" ", strip=True).lower().startswith("fulltext"):
            continue
        text = a.get_text(" ", strip=True)
        m = re.search(r"(\d{4}):(\d+)", text)
        if not m:
            continue
        docref = direct_docref(agency, agency.fs, m.group(1), str(int(m.group(2))),
                               harvest.absolute(agency.base_url, href), seen, title=text)
        if docref:
            yield docref


CSNFS = Agency(
    fs="csnfs", name="Centrala studiestödsnämnden", publisher="Centrala studiestödsnämnden",
    base_url="https://www.csn.se",
    index_url="https://www.csn.se/om-csn/lag-och-ratt/forfattningssamling",
    enumerate=csnfs_enumerate, resolve=resolve_direct,
)

# indexed + DIRECT + fs_from_designation. An Optimizely site listing both the
# RIFS series and its predecessor RNFS (Revisorsnämnden) on one page as direct
# /globalassets/ PDFs with clean "RIFS 2018:1" / "RNFS 1996:1" link text; each
# series keeps its own fs. RNFS base regs are listed konsoliderad-first, so
# dedup keeps the in-force text. skip_re drops the companion
# "Förarbeten"/"Hemställan" PDFs.
RIFS = Agency(
    fs="rifs", name="Revisorsinspektionen", publisher="Revisorsinspektionen",
    base_url="https://www.revisorsinspektionen.se",
    index_url="https://www.revisorsinspektionen.se/regelverk/samtliga-foreskrifter/",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href*="/regelverk/"][href$=".pdf"]', "direct": True,
            "fs_from_designation": True, "skip_re": r"Förarbeten|Hemställan"},
)


# --------------------------------------------------------------------------
# IAFFS (Inspektionen för arbetslöshetsförsäkringen) -- bespoke enumerate: the
# index rows link a landing page /lag-ratt/foreskrifter/iaffs-{year}{lop}/ with
# year+lopnummer fused into the slug (like FFFS), so the generic indexed_enumerate
# can't read the number. Each landing hangs the whole family (grund + konsoliderad
# + ändringar) as /globalassets/dokument/foreskrifter/ links -- some without a
# .pdf suffix and numbered with a hyphen in the link text ("IAFFS 2025-6"), so the
# role + number are read off the filename slug (classify_href), not the link text.
# --------------------------------------------------------------------------

RE_IAF_BASE = re.compile(r"/foreskrifter/iaffs-(\d{4})(\d+)/?$")


def iaf_enumerate(session, agency):
    """One DocRef per base regulation from IAF's single förteckning."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        assert isinstance(href, str)
        m = RE_IAF_BASE.search(href)
        if not m:
            continue
        arsutgava, lopnummer = m.group(1), str(int(m.group(2)))
        basefile = "%s/%s:%s" % (agency.fs, arsutgava, lopnummer)
        if basefile in seen:
            continue
        seen.add(basefile)
        yield DocRef(basefile=basefile,
                     identifier="%s %s:%s" % (agency.fs.upper(), arsutgava, lopnummer),
                     url=harvest.absolute(agency.base_url, href),
                     title=a.get_text(" ", strip=True))


IAFFS = Agency(
    fs="iaffs", name="Inspektionen för arbetslöshetsförsäkringen",
    publisher="Inspektionen för arbetslöshetsförsäkringen",
    base_url="https://www.iaf.se",
    index_url="https://www.iaf.se/lag-ratt/foreskrifter/",
    enumerate=iaf_enumerate, resolve=resolve_landing,
    params={"pdf_select": 'a[href*="/globalassets/dokument/foreskrifter/"]',
            "classify": classify_href},
)

# indexed + DIRECT: the listing anchors are /link/{uuid}.aspx redirects straight
# to the PDF, and each anchor's own text carries the designation ("IMYFS 2024:1",
# "DIFS 2018:1"). No landing, no consolidations. The page also lists the
# predecessor Datainspektionen series DIFS, so fs_from_designation keeps DIFS
# documents under their own samling rather than collapsing them onto imyfs.
# Non-regulation links (Vägledning) carry no FS number and are dropped by ref.
IMYFS = Agency(
    fs="imyfs", name="Integritetsskyddsmyndigheten",
    publisher="Integritetsskyddsmyndigheten",
    base_url="https://www.imy.se",
    index_url="https://www.imy.se/om-oss/beslut-publikationer-och-remisser/"
              "foreskrifter-och-allmanna-rad/",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href*="/link/"][href$=".aspx"]', "direct": True,
            "fs_from_designation": True},
)


# --------------------------------------------------------------------------
# KAMFS (Kammarkollegiet) -- bespoke enumerate over one static index of direct
# PDF links. Kammarkollegiet hosts several rekvirerande myndigheters föreskrifter
# (Fastighetsmäklarinspektionen, Trafikanalys, Myndigheten för stöd till
# trossamfund, …); they all share the single KAMFS samling, so every document is
# keyed by its own KAMFS number. The row text names that number in a parenthesis
# ("(KAMFS 2024:1)") -- EXCEPT ändrings-/upphävande-rows, whose parenthesis cites
# the *amended* regulation; for those the own number is read from the PDF filename
# slug (kamfs_YYYY-N.pdf) instead. No landing pages, no consolidations published.
# --------------------------------------------------------------------------

RE_KAM_HREF = re.compile(r"kamfs[ _-]?(\d{4})[ _-]?(\d{1,3})", re.IGNORECASE)
RE_KAM_TEXT = re.compile(r"KAMFS\s+(\d{4}):(\d+)")
RE_KAM_AMEND = re.compile(r"ändring|andring|upphäv|upphav", re.IGNORECASE)
RE_KAM_SKIP = re.compile(r"f[öo]rteckning|skriv ut", re.IGNORECASE)


def kam_enumerate(session, agency):
    """One DocRef per KAMFS regulation from Kammarkollegiet's single index."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        assert isinstance(href, str)
        if not href.split("?")[0].lower().endswith(".pdf"):
            continue
        text = a.get_text(" ", strip=True)
        if RE_KAM_SKIP.search(text):
            continue
        href_num = RE_KAM_HREF.findall(unquote(href).rsplit("/", 1)[-1])
        text_pair = (m.group(1), m.group(2)) if (m := RE_KAM_TEXT.search(text)) else None
        # an ändrings-/upphävande-row's parenthesis names the amended regulation,
        # not its own number -- take the own number from the filename slug there.
        if RE_KAM_AMEND.search(text):
            pair = href_num[-1] if href_num else text_pair
        else:
            pair = text_pair or (href_num[-1] if href_num else None)
        if pair is None:                        # a numberless companion PDF
            continue
        arsutgava, lopnummer = pair
        lopnummer = str(int(lopnummer))
        docref = direct_docref(agency, agency.fs, arsutgava, lopnummer,
                               harvest.absolute(agency.base_url, href), seen, title=text)
        if docref:
            yield docref


KAMFS = Agency(
    fs="kamfs", name="Kammarkollegiet", publisher="Kammarkollegiet",
    base_url="https://www.kammarkollegiet.se",
    index_url="https://www.kammarkollegiet.se/om-oss/kammarkollegiets-forfattningssamling-kamfs",
    enumerate=kam_enumerate, resolve=resolve_direct,
)

# indexed + DIRECT: one static page of direct PDF links under
# /forfattningssamling/kkvfs_YYYY-N.pdf. Base rows name "KKVFS YYYY:N" in the
# text; upphävande-rows carry only a description, so ref falls back to the
# filename slug for the number. konkurrensverket.se sits behind a Cloudflare
# front that 403s HTTP/1.1 and only serves HTTP/2, which requests/urllib3 cannot
# speak, so this agency sets ``http2=True``: harvest() builds the session with
# lib.net.make_http2_session (the httpx2 HTTP/2 client) instead of a requests
# Session, and the shared engine runs unchanged over it.
KKVFS = Agency(
    fs="kkvfs", name="Konkurrensverket", publisher="Konkurrensverket",
    base_url="https://www.konkurrensverket.se",
    index_url="https://www.konkurrensverket.se/om-oss/forfattningssamling/",
    enumerate=indexed_enumerate, resolve=resolve_direct, http2=True,
    params={"link_select": 'a[href*="/forfattningssamling/kkvfs"][href$=".pdf"]',
            "direct": True},
)


# --------------------------------------------------------------------------
# STFS (Sametinget) -- the whole samling (grund + ändring, flat, no landing
# pages) is served by a Sitevision document-bank servlet returning the result
# list as an HTML fragment inside JSON, paged by ``idx`` (10/page). Each row's
# <h3> opens with the document's own designation ("STFS 2025:1 …") and its
# linkCol anchor is the PDF itself, so it's read straight into resolve_direct.
# --------------------------------------------------------------------------

def stfs_enumerate(session, agency):
    """One DocRef per STFS document from the DocBankServlet, walking ``idx``
    pages until one returns no rows."""
    seen = set()
    idx = 1
    while True:
        data = request(session, "GET", "%s?q=&path=/dokumentbank&cat=%s&subCat=&idx=%d"
                       % (agency.params["servlet_url"], agency.params["cat"], idx),
                       parse_json=True)
        items = BeautifulSoup(data["html"], "html.parser").select("div.item")
        if not items:
            return
        for it in items:
            head, link = it.find("h3"), it.select_one("div.links a[href]")
            if not head or not link:
                continue
            text = head.get_text(" ", strip=True)
            docref = harvest.ref(agency, text, link["href"], seen, title=text, direct=True)
            if docref:
                yield docref
        idx += 1
        time.sleep(0.3)


STFS = Agency(
    fs="stfs", name="Sametinget", publisher="Sametinget",
    base_url="https://sametinget.se",
    index_url="https://sametinget.se/dokumentbank?cat=72",
    enumerate=stfs_enumerate, resolve=resolve_direct,
    params={"servlet_url": "https://sametinget.se/servlet/DocBankServlet", "cat": "72"},
)


# --------------------------------------------------------------------------
# SJÖFS (Sjöfartsverket) -- a two-level static site: the systematisk förteckning
# links ~21 subject-category subpages, each listing its in-force SJÖFS as bare
# "YYYY:N" anchors straight to the PDF under /globalassets/om-oss/lagrum/. (Most
# maritime rules moved to Transportstyrelsens TSFS in 2009; this harvests the
# SJÖFS Sjöfartsverket still publishes.) The two-level crawl is not a generic
# shape, so a thin bespoke enumerate discovers the categories then reuses ref;
# resolve_direct downloads. Pre-1970 texts with a letter lopnummer ("1952:A9")
# carry no plain YYYY:N and are skipped.
# --------------------------------------------------------------------------

def sjofs_enumerate(session, agency):
    """One direct DocRef per SJÖFS, crawling each subject-category subpage found
    on the systematisk förteckning."""
    p = agency.params
    idx = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    cats, seen_cat = [], set()
    for a in idx.select(p["category_select"]):
        raw = a.get("href") or ""
        assert isinstance(raw, str)
        href = raw.split("#")[0].rstrip("/")
        if href and not href.endswith(p["category_root"]) and href not in seen_cat:
            seen_cat.add(href)
            cats.append(href)
    seen = set()
    for cat in cats:
        soup = BeautifulSoup(request(session, "GET", harvest.absolute(agency.base_url, cat)).text,
                             "html.parser")
        for a in soup.select(p["link_select"]):
            text = a.get_text(" ", strip=True)
            docref = harvest.ref(agency, text, a.get("href", ""), seen, title=text, direct=True)
            if docref:
                yield docref
        time.sleep(0.3)


SJOFS = Agency(
    fs="sjofs", name="Sjöfartsverket", publisher="Sjöfartsverket",
    base_url="https://www.sjofartsverket.se",
    index_url="https://www.sjofartsverket.se/sv/om-oss/lagrum/systematisk-forteckning-n/",
    enumerate=sjofs_enumerate, resolve=resolve_direct, designation="SJÖFS",
    params={"category_select": 'a[href*="/systematisk-forteckning-n/"]',
            "category_root": "systematisk-forteckning-n",
            "link_select": 'a[href*="/globalassets/om-oss/lagrum/"][href$=".pdf"]'},
)


# --------------------------------------------------------------------------
# Shared enumerate for TPPVFS + VRFS: a Sitevision /download/ index whose row
# text names the *base* regulation an ändringsföreskrift amends before its own
# number ("…ändring i … (VRFS 2013:1) … (VRFS 2024:1)"), so the document's own
# designation is the LAST "XXFS YYYY:N" in the text -- not the first ref would
# otherwise take. We hand ref just that last designation; direct + resolve_direct.
# --------------------------------------------------------------------------

def last_designation_enumerate(session, agency):
    """One direct DocRef per row of a /download/ index, keyed on the LAST FS
    designation in the row text (the document's own number)."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for a in soup.select(agency.params["link_select"]):
        text = a.get_text(" ", strip=True)
        matches = harvest.RE_FS_NUMBER.findall(text)
        if not matches:
            continue
        prefix, year, lop = matches[-1]
        docref = harvest.ref(agency, "%s %s:%s" % (prefix, year, lop),
                             a.get("href", ""), seen, title=text, direct=True)
        if docref:
            yield docref


# TPPVFS (Totalförsvarets plikt- och prövningsverk) -- tiny samling on one page;
# its rows mix the current TPPVFS with the predecessor TRMFS (Totalförsvarets
# rekryteringsmyndighet), so fs_from_designation keeps each under its own fs.
TPPVFS = Agency(
    fs="tppvfs", name="Totalförsvarets plikt- och prövningsverk",
    publisher="Totalförsvarets plikt- och prövningsverk",
    base_url="https://www.pliktverket.se",
    index_url="https://www.pliktverket.se/om-myndigheten/vart-uppdrag/lag-och-ratt/foreskrifter",
    enumerate=last_designation_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href*="/download/"][href$=".pdf"]',
            "fs_from_designation": True},
)


# VRFS (Vetenskapsrådet) -- one page, /download/ PDFs; the samling also carries
# Etikprövningsmyndighetens föreskrifter, but those are *numbered VRFS* too, so
# it is a single vrfs samling (no fs_from_designation).
VRFS = Agency(
    fs="vrfs", name="Vetenskapsrådet", publisher="Vetenskapsrådet",
    base_url="https://www.vr.se",
    index_url="https://www.vr.se/om-vetenskapsradet/styrande-dokument/vetenskapsradets-forfattningssamling.html",
    enumerate=last_designation_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href*="/download/"][href$=".pdf"]'},
)

# VALFS (Valmyndigheten) -- one page listing the samling as /download/ PDFs whose
# text carries "VALFS YYYY:N" (own number always first, no base-ref prefix), so
# the generic indexed_enumerate + direct fits; the valfs filename filter drops
# the page's external SFS/RA-MS links.
VALFS = Agency(
    fs="valfs", name="Valmyndigheten", publisher="Valmyndigheten",
    base_url="https://www.val.se",
    index_url="https://www.val.se/det-svenska-valsystemet/grunderna-i-det-svenska-valsystemet/lagar-och-regler",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href*="/download/"][href*="valfs" i]', "direct": True},
)


# --------------------------------------------------------------------------
# MEMYFS (Mediemyndigheten) -- a single static page of DIRECT PDF links, no
# landing pages and no API. One flat "Lagar, förordningar och föreskrifter" page
# hangs every föreskrift PDF (grund, ändring and upphävande alike, each its own
# record like Riksgälden's RGKFS). The page also carries two predecessor series:
# MPRTFS (Myndigheten för press, radio och tv) and MRTVFS (Myndigheten för radio
# och tv), so a bespoke enumerate reads each PDF's designation -- accepting the
# ':'/'-'/'_' separators the site mixes in link text *and* filenames -- and keeps
# each document under its own fs (like MCFFS's fs_from_designation) rather than
# collapsing the predecessors onto memyfs. Law/förordning links (riksdagen.se,
# SFS PDFs) carry no such designation and are skipped for free.
# --------------------------------------------------------------------------

RE_MEMY_DESIG = re.compile(r"(MEMYFS|MPRTFS|MRTVFS)[\s_-]*(\d{4})[:._\- ]*(\d{1,3})",
                           re.IGNORECASE)


def memy_enumerate(session, agency):
    """One DocRef per föreskrift PDF on Mediemyndigheten's flat listing, each
    filed under its own series (MEMYFS / predecessor MPRTFS / MRTVFS)."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for a in soup.select('a[href$=".pdf"], a[href$=".PDF"]'):
        href = a["href"]
        assert isinstance(href, str)
        if "/Recycle-Bin/" in href:              # trashed duplicates, often 404
            continue
        # the designation lives in the visible text ("(MEMYFS 2025:3)"); the
        # filename ("...-memyfs-2025_3.pdf") is the fallback when the text omits it
        m = RE_MEMY_DESIG.search(a.get_text(" ", strip=True)) or RE_MEMY_DESIG.search(href)
        if not m:
            continue
        fs = m.group(1).lower()
        arsutgava, lopnummer = m.group(2), str(int(m.group(3)))
        title = a.get_text(" ", strip=True)
        docref = direct_docref(agency, fs, arsutgava, lopnummer,
                               harvest.absolute(agency.base_url, href), seen,
                               identifier="%s %s:%s" % (m.group(1).upper(), arsutgava, lopnummer),
                               title=title)
        if docref:
            yield docref


MEMYFS = Agency(
    fs="memyfs", name="Mediemyndigheten", publisher="Mediemyndigheten",
    base_url="https://mediemyndigheten.se",
    index_url="https://mediemyndigheten.se/om-oss/lagar-forordningar-och-foreskrifter/",
    enumerate=memy_enumerate, resolve=resolve_direct,
)


# --------------------------------------------------------------------------
# MDFFS (Myndigheten för digital förvaltning, DIGG) -- a Sitevision register
# where each föreskrift's index block links three *sub-pages* (Grundförfattning,
# Konsoliderad version, Ändringsförfattning); there is no aggregate landing page
# (the föreskrift's own slug 404s). So a bespoke enumerate groups the index links
# by their shared parent path segment, reads the base number off the
# "Grundförfattning (MDFFS YYYY:N)" link, then fetches the grund sub-page for its
# /download/ PDF into resolve_direct's payload. DIGG mixes publishing formats:
# the older föreskrifter hang a /download/ PDF, the newer ones (and *every*
# konsoliderad version) are full text inline as HTML with no PDF -- so an HTML-
# only grundföreskrift yields regulation_url=None (its text stays reachable via
# the recorded source_url), and consolidations aren't downloaded (no PDF exists).
# Ändringsförfattningar are recorded as reference links (their own sub-page URL).
# --------------------------------------------------------------------------

RE_MDFFS_DESIG = re.compile(r"MDFFS\s*(\d{4}):(\d+)")
RE_MDFFS_SEG = re.compile(r"/om-oss/forfattningssamling/([^/]+)/")


def _mdffs_download_url(session, agency, href):
    """The /download/ PDF URL a DIGG föreskrift sub-page hangs, or None when the
    föreskrift is published inline as HTML instead."""
    soup = BeautifulSoup(request(session, "GET", harvest.absolute(agency.base_url, href)).text,
                         "html.parser")
    a = soup.select_one('a[href*="/download/"]')
    return harvest.absolute(agency.base_url, a["href"]) if a else None


def mdffs_enumerate(session, agency):
    """One DocRef per DIGG grundförfattning, its amendment graph read from the
    sub-pages the index groups under a shared parent slug."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    groups = defaultdict(list)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        assert isinstance(href, str)
        m = RE_MDFFS_SEG.match(href)
        if m and a.get_text(strip=True):
            groups[m.group(1)].append(a)
    seen = set()
    for anchors in groups.values():
        grund = next((a for a in anchors if a.get_text(strip=True).startswith("Grundför")), None)
        if grund is None:
            continue
        base = RE_MDFFS_DESIG.search(grund.get_text(" ", strip=True))
        if not base:
            continue
        arsutgava, lopnummer = base.group(1), str(int(base.group(2)))
        basefile = "%s/%s:%s" % (agency.fs, arsutgava, lopnummer)
        if basefile in seen:
            continue
        seen.add(basefile)
        amendments = []
        for a in anchors:
            if a.get_text(" ", strip=True).startswith("Ändringsför"):
                am = RE_MDFFS_DESIG.search(a.get_text(" ", strip=True))
                amendments.append({"identifier": "MDFFS %s:%s" % (am.group(1), str(int(am.group(2))))
                                   if am else None,
                                   "url": harvest.absolute(agency.base_url, a["href"])})
        grund_url = harvest.absolute(agency.base_url, grund["href"])
        yield DocRef(
            basefile=basefile,
            identifier="%s %s:%s" % (agency.fs.upper(), arsutgava, lopnummer),
            url=grund_url, title=grund.get_text(" ", strip=True),
            extra={"regulation_url": _mdffs_download_url(session, agency, grund["href"]),
                   "consolidations": [], "amendments": amendments,
                   "source_url": grund_url})
        time.sleep(0.3)


MDFFS = Agency(
    fs="mdffs", name="Myndigheten för digital förvaltning",
    publisher="Myndigheten för digital förvaltning",
    base_url="https://www.digg.se",
    index_url="https://www.digg.se/om-oss/forfattningssamling",
    enumerate=mdffs_enumerate, resolve=resolve_direct,
)


# --------------------------------------------------------------------------
# MYHFS (Myndigheten för yrkeshögskolan) -- a Vue/Vuetify page listing DIRECT
# PDF links (assets.myh.se) under per-year headings, no landing pages. The link
# text carries no number, so the (year, lopnummer) come from the filename -- but
# the site mixes ``myhfs-YEAR-LOP`` and ``myhfs-LOP-YEAR`` orders, so a bespoke
# enumerate reads the two integers and takes the 4-digit one as the year. A few
# older föreskrifter are stored under a title-only filename with no number at
# all; those cannot be keyed to a basefile from the listing and are skipped.
# --------------------------------------------------------------------------

RE_MYHFS_FILE = re.compile(r"myhfs-(\d{1,4})-(\d{1,4})", re.IGNORECASE)


def myh_enumerate(session, agency):
    """One DocRef per MYHFS PDF; number parsed from the filename (either order)."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for a in soup.select('a[href*="assets.myh.se"][href$=".pdf"]'):
        href = a["href"]
        assert isinstance(href, str)
        m = RE_MYHFS_FILE.search(href.rsplit("/", 1)[-1])
        if not m:                                # title-only filename, no number
            continue
        first, second = int(m.group(1)), int(m.group(2))
        arsutgava, lopnummer = (first, second) if first > 999 else (second, first)
        title = a.get_text(" ", strip=True)
        docref = direct_docref(agency, agency.fs, arsutgava, lopnummer, href, seen, title=title)
        if docref:
            yield docref


MYHFS = Agency(
    fs="myhfs", name="Myndigheten för yrkeshögskolan",
    publisher="Myndigheten för yrkeshögskolan",
    base_url="https://www.myh.se",
    index_url="https://www.myh.se/lag-och-ratt/foreskrifter-och-allmanna-rad/"
              "gallande-foreskrifter-och-allmanna-rad",
    enumerate=myh_enumerate, resolve=resolve_direct,
)


# --------------------------------------------------------------------------
# SKOLFS (Skolverket) -- API-direct via an Angular register at
# skolfs.skolverket.se/api. /statute is capped, so enumerate year by year
# (/statute/years lists them); each year's hits carry every grund-, ändrings-
# and senaste-lydelse-post, linked by baseSkolfsNumber. A post's PDF is
# /document/{documentType}/{skolfsNumber}/pdf; a base's consolidation is its
# SENASTE_LYDELSE post (present only once the base has been amended). The single
# samling holds Skolverket, Skolinspektionen, SPSM *and* regeringen texts alike,
# so every grundförfattning/allmänna-råd post is a base regardless of issuer.
# --------------------------------------------------------------------------

def skolfs_enumerate(session, agency):
    """One DocRef per base SKOLFS regulation, its consolidation + amendments
    attached, from Skolverket's register API (the whole corpus, walked per year)."""
    api = agency.params["api_url"]
    hits = []
    for year in request(session, "GET", api + "/statute/years", parse_json=True):
        data = request(session, "GET", "%s/statute?year=%s" % (api, year), parse_json=True)
        hits += [h for g in data["searchGroups"] for h in g["searchHits"]]
        time.sleep(0.3)
    amendments: dict[str, list] = {}
    consolidated = set()
    for h in hits:
        if h["documentType"] == "ANDRINGSFORFATTNING":
            amendments.setdefault(h["baseSkolfsNumber"], []).append(h["skolfsNumber"])
        elif h["documentType"] == "SENASTE_LYDELSE":
            consolidated.add(h["baseSkolfsNumber"])
    seen = set()
    for h in hits:
        if h["documentType"] not in ("GRUNDFORFATTNING", "ALLMANNA_RAD_OVRIGT"):
            continue
        num = h["skolfsNumber"]
        year, lop = num.split(":")
        basefile = "%s/%s:%s" % (agency.fs, year, str(int(lop)))
        if basefile in seen:
            continue
        seen.add(basefile)
        pdf = "%s/document/%s/%s/pdf" % (api, h["documentType"], num)
        cons = [{"url": "%s/document/SENASTE_LYDELSE/%s/pdf" % (api, num)}] \
            if num in consolidated else []
        yield DocRef(
            basefile=basefile,
            identifier="%s %s:%s" % (agency.fs.upper(), year, str(int(lop))),
            url=pdf, title=h["statuteTitle"],
            extra={"regulation_url": pdf, "consolidations": cons,
                   "amendments": [{"identifier": "%s %s" % (agency.fs.upper(), a),
                                   "url": "%s/document/ANDRINGSFORFATTNING/%s/pdf" % (api, a)}
                                  for a in amendments.get(num, [])],
                   "title": h["statuteTitle"], "source_url": agency.index_url})


SKOLFS = Agency(
    fs="skolfs", name="Statens skolverk", publisher="Statens skolverk",
    base_url="https://skolfs.skolverket.se",
    index_url="https://www.skolverket.se/styrning-och-ansvar/regler-och-ansvar/"
              "sok-forordningar-och-foreskrifter-skolfs",
    enumerate=skolfs_enumerate, resolve=resolve_direct,
    params={"api_url": "https://skolfs.skolverket.se/api"},
)

# indexed + DIRECT. One static HTML page of direct PDF links; number+designation
# come from the *link text* ("SiSFS 2025:1 ..."), the filenames being unreliable
# (a "SiSFS 2021:1" row links a 2020-1 file). The page mixes two samlingar --
# SiSFS and the SiSUVFS (ungdomsvård) series -- so fs_from_designation keeps
# each document under its own fs. Append PDFs without a designation
# (förteckning, "Övriga vårdavgifter" tables) carry no number and are dropped
# by ref; konsoliderad rows dedup against the base listed above them.
SISFS = Agency(
    fs="sisfs", name="Statens institutionsstyrelse",
    publisher="Statens institutionsstyrelse",
    base_url="https://www.stat-inst.se",
    index_url="https://www.stat-inst.se/om-sis/lagar-forordningar-forfattningar/"
              "sis-forfattningssamling/",
    enumerate=indexed_enumerate, resolve=resolve_direct, designation="SiSFS",
    params={"link_select": 'a[href$=".pdf"]', "direct": True,
            # the förteckning PDF's filename date (…_250901.pdf) otherwise
            # misparses to a bogus "2509:1"; it carries no designation anyway.
            "skip_re": r"Förteckning", "fs_from_designation": True},
)


# --------------------------------------------------------------------------
# UHRFS (Universitets- och högskolerådet) -- the "gällande föreskrifter i
# löpnummerordning" page hangs the PDFs directly. A bespoke enumerate is needed:
# the link text names the *amended base* ("Föreskrifter om ändring i … (UHRFS
# 2024:2)"), so the generic direct ref would key an amendment by the wrong
# number -- the amendment's own number lives only in the filename (uhrfs-2026-4-…
# .pdf). Companion PDFs (konsekvensutredning/promemoria/rättelse/förteckning)
# carry a uhrfs number in their filename too, so they are dropped by link text.
# No HSVFS predecessor appears on the in-force page. Each PDF is its own document
# (flat, like LMFS -- amendments list as their own bases, no consolidation).
# --------------------------------------------------------------------------

RE_UHRFS_FILE = re.compile(r"uhrfs[-_](\d{4})[-_](\d{1,3})", re.IGNORECASE)
RE_UHRFS_SKIP = re.compile(r"Konsekvensutredning|Promemoria|Rättelse|Förteckning|Remiss")


def uhrfs_enumerate(session, agency):
    """One DocRef per in-force UHRFS PDF, keyed by the number in its filename
    slug (the link text names the amended base, not the file's own number)."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for a in soup.select('a[href*="uhrfs"][href$=".pdf"]'):
        href = a["href"]
        assert isinstance(href, str)
        text = a.get_text(" ", strip=True)
        if RE_UHRFS_SKIP.search(text):
            continue
        m = RE_UHRFS_FILE.search(href.rsplit("/", 1)[-1])
        if not m:
            continue
        year, lop = m.group(1), str(int(m.group(2)))
        docref = direct_docref(agency, agency.fs, year, lop,
                               harvest.absolute(agency.base_url, href), seen, title=text)
        if docref:
            yield docref


UHRFS = Agency(
    fs="uhrfs", name="Universitets- och högskolerådet",
    publisher="Universitets- och högskolerådet",
    base_url="https://www.uhr.se",
    index_url="https://www.uhr.se/publikationer/lagar-och-regler-for-hogre-utbildning/"
              "Universitets--och-hogskoleradets-forfattningssamling/"
              "gallande-foreskrifter-i-lopnummerordning/",
    enumerate=uhrfs_enumerate, resolve=resolve_direct,
)

# indexed + DIRECT. A small static page of direct PDF links with clean
# "UFS YYYY:N" link text. Its own PDFs sit under .../ufs_YYYY_N.pdf; the
# link_select's "ufs_" underscore excludes the "Tillämpningsstöd-ufs-..." helper
# PDFs (hyphen) and the cross-referenced MDFFS/DIGG documents. Five föreskrifter,
# no amendments/consolidations published.
UFS = Agency(
    fs="ufs", name="Upphandlingsmyndigheten", publisher="Upphandlingsmyndigheten",
    base_url="https://www.upphandlingsmyndigheten.se",
    index_url="https://www.upphandlingsmyndigheten.se/om-oss/"
              "upphandlingsmyndighetens-forfattningssamling/",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href$=".pdf"][href*="ufs_"]', "direct": True},
)


# --------------------------------------------------------------------------
# SCBFS (Statistiska centralbyrån) -- bespoke enumerate: the register is two
# tab tables (uppgiftslämnande föreskrifter + the monthly KPI tillkännagivanden,
# both SCB-FS), each row a <th>designation</th> + <td><a>title</a></td> whose
# anchor points at a /link/{uuid}.aspx landing. The designation lives in the row,
# not the anchor text, so indexed_enumerate can't read it -- read it off the row.
# The landing hangs exactly one /contentassets/ PDF (classify_single). SCB-FS
# also carries Medlingsinstitutets föreskrifter, all under the one scbfs samling.
# --------------------------------------------------------------------------

def scb_enumerate(session, agency):
    """One DocRef per row across SCB's UL + KPI förteckning tables, yielded
    newest-first (the tables print oldest-first) so the incremental watermark's
    date-boundary stop is valid."""
    seen = set()
    refs = []
    for url in agency.params["index_urls"]:
        soup = BeautifulSoup(request(session, "GET", url).text, "html.parser")
        for row in soup.select("table tr"):
            a = row.find("a", href=True)
            if not a:
                continue
            docref = ref(agency, row.get_text(" ", strip=True), a["href"], seen,
                         title=a.get_text(" ", strip=True))
            if docref:
                refs.append(docref)
    yield from newest_first(refs)


SCBFS = Agency(
    fs="scbfs", name="Statistiska centralbyrån", publisher="Statistiska centralbyrån",
    base_url="https://www.scb.se",
    index_url="https://www.scb.se/om-scb/scbs-verksamhet/regelverk-och-policyer/foreskrifter/",
    enumerate=scb_enumerate, resolve=resolve_landing,
    designation="SCB-FS",
    params={"index_urls": [
                "https://www.scb.se/om-scb/scbs-verksamhet/regelverk-och-policyer/foreskrifter/?currentpageId=98388&type=UL",
                "https://www.scb.se/om-scb/scbs-verksamhet/regelverk-och-policyer/foreskrifter/?currentpageId=98388&type=KPI"],
            "pdf_select": 'a[href*="/contentassets/"][href$=".pdf"]',
            "classify": classify_single},
)

# indexed + landing + filename-classify. The register moved to
# regelverk.swedac.se, a static site with one landing HTML per föreskrift
# (stafs-YYYY-N[-konsol].html). The in-force förteckning is a static,
# newest-first anchor list (link text carries the "STAFS YYYY:N" designation),
# so indexed_enumerate fits and stays newest-first for the incremental
# watermark. Each landing hangs its own relative stafs-*.pdf (skip the external
# github .md and cross-referenced agency PDFs); classify_href sorts a konsol
# landing's consolidation from a base regulation.
STAFS = Agency(
    fs="stafs", name="Styrelsen för ackreditering och teknisk kontroll",
    publisher="Styrelsen för ackreditering och teknisk kontroll (Swedac)",
    base_url="https://regelverk.swedac.se",
    index_url="https://regelverk.swedac.se/foreskrifter/",
    enumerate=indexed_enumerate, resolve=resolve_landing,
    params={"link_select": 'a[href*="/foreskrifter/swedac/stafs-"]',
            "pdf_select": 'a[href$=".pdf"]:not([href^="http"])',
            "classify": classify_href},
)


# --------------------------------------------------------------------------
# TVFS (Tillväxtverket) -- Sitevision /download/ links that ARE the PDFs (direct,
# no landing), one flat föreskrift list. The listing mixes the current TVFS with
# its Nutek predecessor NUTFS, so fs_from_designation keeps each under its own
# samling. Every anchor carries its "XXFS YYYY:N" designation in the link text;
# skip the "Årlig förteckning" list PDF.
# --------------------------------------------------------------------------

def tvv_enumerate(session, agency):
    """Tillväxtverket's flat föreskrift list re-sorted newest-first (the page
    lists them unordered) so the incremental watermark's date-boundary stop is
    valid; the list mixes TVFS with its Nutek predecessor NUTFS."""
    yield from newest_first(indexed_enumerate(session, agency))


TVFS = Agency(
    fs="tvfs", name="Tillväxtverket", publisher="Tillväxtverket",
    base_url="https://tillvaxtverket.se",
    index_url="https://tillvaxtverket.se/tillvaxtverket/omtillvaxtverket/varverksamhet/foreskrifter.595.html",
    enumerate=tvv_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href*="/download/"]', "direct": True,
            "skip_re": r"[Ff]örteckning", "fs_from_designation": True},
)


_TRANSLIT = str.maketrans("åäöÅÄÖ", "aaoAAO")


# --------------------------------------------------------------------------
# AFS (Arbetsmiljöverket) -- indexed + landing + filename-classify, but the
# PDFs hang on a per-regulation *författningshistorik* subpage, not the base
# landing (which renders the text as HTML). AV renumbered its whole samling on
# 2025-01-01: the 67 old häften were replaced by 15 new grundföreskrifter
# AFS 2023:1..15 (all dated 2023, in force 2025), so the in-force register is
# exactly those 15 base links (afs-YYYYN, year+lopnummer fused in the slug).
# The hist subpage hangs the base PDF, its konsoliderad version and every
# ändringsföreskrift (afsYYYY-N filenames), classified by afs_classify -- the
# amendment's own number is the afsYYYY-N that is *not* the base's.
# --------------------------------------------------------------------------

RE_AFS_BASE = re.compile(r"/foreskrifter/afs-(\d{4})(\d+)/$")
RE_AFS_NUM = re.compile(r"afs\s*(\d{4})[-_](\d+)", re.IGNORECASE)


def afs_classify(a, fs, base_ars, base_lop):
    """Role + number from the AFS PDF filename. Konsoliderad is flagged by a
    'konsoliderad' path segment; an ändring names two afsYYYY-N numbers (base +
    its own, in either order) so the own number is the one that isn't the base;
    a plain afsYYYY-N is the base regulation."""
    name = a.get("href", "").rsplit("/", 1)[-1].lower()
    if "konsekvensutred" in name or "beslutsprom" in name:
        return None
    if "konsolider" in name:
        return ("consolidation", base_ars, base_lop)
    nums = [(y, str(int(l))) for y, l in RE_AFS_NUM.findall(name)]
    if not nums:
        return None
    if re.search(r"andring|ändring", name):
        own = next((n for n in nums if n != (base_ars, base_lop)), nums[-1])
        return ("amendment", own[0], own[1])
    ars, lop = nums[-1]
    return (("regulation" if (ars, lop) == (base_ars, base_lop) else "amendment"), ars, lop)


def afs_enumerate(session, agency):
    """One DocRef per in-force AFS grundföreskrift, its url pointed at the
    författningshistorik subpage (where the PDFs live)."""
    soup = BeautifulSoup(request(session, "GET", agency.index_url).text, "html.parser")
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        assert isinstance(href, str)
        m = RE_AFS_BASE.search(href)
        if not m:
            continue
        arsutgava, lopnummer = m.group(1), str(int(m.group(2)))
        basefile = "%s/%s:%s" % (agency.fs, arsutgava, lopnummer)
        if basefile in seen:
            continue
        seen.add(basefile)
        fused = m.group(1) + m.group(2)      # the slug fuses year+lopnummer
        hist = harvest.absolute(agency.base_url,
                        href.rstrip("/") + "/forfattningshistorik-afs-%s/" % fused)
        yield DocRef(basefile=basefile,
                     identifier="%s %s:%s" % (agency.fs.upper(), arsutgava, lopnummer),
                     url=hist, title=a.get_text(" ", strip=True))


AFS = Agency(
    fs="afs", name="Arbetsmiljöverket", publisher="Arbetsmiljöverket",
    base_url="https://www.av.se",
    index_url="https://www.av.se/arbetsmiljoarbete-och-inspektioner/publikationer/foreskrifter/",
    enumerate=afs_enumerate, resolve=resolve_landing,
    params={"pdf_select": 'a[href*="/globalassets/"][href$=".pdf"]',
            "classify": afs_classify},
)


# --------------------------------------------------------------------------
# TSFS (Transportstyrelsen) -- bespoke enumerate over per-year listing pages
# (ts-foreskrifter-i-nummerordning/YYYY/), landing (detail) pages, bespoke
# filename-classify. Each year page lists every föreskrift as an
# li.tsfs-item whose link carries the authoritative RuleNumber + ruleprefix in
# its href (the title text names *other* FS numbers, so the query params, not
# the text, are the number source). Grundföreskrifter (title not "om ändring")
# are enumerated; ändringar are captured as references on their base's detail
# page, which hangs the whole family: the grund PDF, its konsoliderad (a 'k'
# suffix) and every amendment (TSFS YYYY_N.pdf). Predecessor samlingar appear on
# the same register -- SJÖFS, LFS (Luftfartsstyrelsen), VVFS (Vägverket), JvSFS
# (Järnvägsstyrelsen) -- routed to their own fs by the ruleprefix.
# --------------------------------------------------------------------------

RE_TS_ROW = re.compile(r"RuleNumber=(\d{4}):(\d+)&(?:amp;)?ruleprefix=([A-Za-zÅÄÖåäö]+)",
                       re.IGNORECASE)
RE_TS_PDF = re.compile(r"([a-zåäö]+)[ _](\d{4})[_ ](\d+)(k)?\.pdf$", re.IGNORECASE)


def ts_classify(a, fs, base_ars, base_lop):
    """Role + number from the TSFS PDF filename ('TSFS 2012_113.pdf', the
    konsoliderad 'TSFS 2012_113k.pdf', predecessor 'jvsfs_2008_3.pdf')."""
    m = RE_TS_PDF.search(a.get("href", "").rsplit("/", 1)[-1])
    if not m:
        return None
    ars, lop, kons = m.group(2), str(int(m.group(3))), m.group(4)
    if kons:
        return ("consolidation", base_ars, base_lop)
    return (("regulation" if (ars, lop) == (base_ars, base_lop) else "amendment"), ars, lop)


def ts_enumerate(session, agency):
    """One DocRef per grundföreskrift, newest year first, across the per-year
    nummerordning pages; the fs is routed off each row's ruleprefix so a
    predecessor samling (VVFS/LFS/JvSFS/SJÖFS) keeps its own identity."""
    seen = set()
    for year in range(date.today().year, 1977, -1):
        try:
            response = request(session, "GET", "%s%d/" % (agency.index_url, year))
        except requests.exceptions.HTTPError as exc:
            if is_not_found(exc):
                continue                       # a year with no register page
            raise
        soup = BeautifulSoup(response.text, "html.parser")
        for li in soup.select("li.tsfs-item"):
            a = li.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            assert isinstance(href, str)
            m = RE_TS_ROW.search(href)
            if not m or "om ändring" in a.get_text(" ", strip=True).lower():
                continue
            arsutgava, lopnummer, prefix = m.group(1), str(int(m.group(2))), m.group(3)
            doc_fs = re.sub(r"[^0-9a-z]", "", prefix.translate(_TRANSLIT).lower())
            basefile = "%s/%s:%s" % (doc_fs, arsutgava, lopnummer)
            if basefile in seen:
                continue
            seen.add(basefile)
            yield DocRef(basefile=basefile, fs=doc_fs,
                         identifier="%s %s:%s" % (prefix.upper(), arsutgava, lopnummer),
                         url=harvest.absolute(agency.base_url, href),
                         title=a.get_text(" ", strip=True))
        time.sleep(0.3)


TSFS = Agency(
    fs="tsfs", name="Transportstyrelsen", publisher="Transportstyrelsen",
    base_url="https://www.transportstyrelsen.se",
    index_url="https://www.transportstyrelsen.se/sv/om-oss/dina-rattigheter-lagar-och-regler/"
              "forfattningssamling/ts-foreskrifter-i-nummerordning/",
    enumerate=ts_enumerate, resolve=resolve_landing,
    params={"pdf_select": 'a[href*="/TSFS/"][href$=".pdf"]', "classify": ts_classify},
)


# --------------------------------------------------------------------------
# TRVFS (Trafikverket) -- the register is a standalone ASP.NET app
# (trvfs.ea.trafikverket.se/TRVFS) reached by POSTing the "Sök författnings-
# samling" form (Ar=Alla) for the whole collection (~670 rows, grund +
# ändring). Each row links a DocumentHistory family page; enumerate keeps the
# Grundföreskrift rows, bespoke trv_resolve fetches the family page and
# downloads its single grundföreskrift PDF (no konsoliderad versions are
# published) while recording the Ändringsförfattningar as references. The
# register hosts Trafikverkets föreskrifter and its predecessors Vägverket
# (VVFS) and Banverket -- the fs is routed off the DocumentHistory id's prefix
# (bare number = TRVFS; VVFS-prefixed = vvfs). Entries whose id carries a
# TSFS/SFS prefix are Transportstyrelsen's own samling (harvested separately)
# or statutes, and are skipped.
# --------------------------------------------------------------------------

TRV_POST = {"Dokumentbeteckning": "", "Celexnummer": "", "Ikrafttradandefrom": "",
            "Ikrafttradandetom": "", "Titel": "", "Hastforeskrift": "false",
            "Upphavda": "false", "Ar": "Alla"}
RE_TRV_ID = re.compile(r"/DocumentHistory/([A-Za-zÅÄÖ]*)\s*(\d{4})-(\d+)$", re.IGNORECASE)
# fs codes on the register that are not Trafikverket's to store (own samlingar)
TRV_SKIP_PREFIX = {"TSFS", "SFS"}


def trv_enumerate(session, agency):
    """One DocRef per Grundföreskrift in Trafikverkets register, fs routed off
    the DocumentHistory id prefix (bare = TRVFS, else the predecessor samling)."""
    soup = BeautifulSoup(
        request(session, "POST", agency.index_url, data=TRV_POST).text, "html.parser")
    seen = set()
    for a in soup.select('a[href*="/DocumentHistory/"]'):
        tr = a.find_parent("tr")
        if not tr or "Grundföreskrift" not in tr.get_text():
            continue
        href = a["href"]
        assert isinstance(href, str)
        m = RE_TRV_ID.search(href)
        if not m:
            continue
        prefix = (m.group(1) or "TRVFS").upper()
        if prefix in TRV_SKIP_PREFIX:
            continue
        arsutgava, lopnummer = m.group(2), str(int(m.group(3)))
        doc_fs = prefix.translate(_TRANSLIT).lower()
        basefile = "%s/%s:%s" % (doc_fs, arsutgava, lopnummer)
        if basefile in seen:
            continue
        seen.add(basefile)
        yield DocRef(basefile=basefile, fs=doc_fs,
                     identifier="%s %s:%s" % (prefix, arsutgava, lopnummer),
                     url=harvest.absolute(agency.base_url, href))


def trv_resolve(session, agency, ref, root, delay=0.5, *, log=print, rejects=None):
    """Fetch a DocumentHistory family page: download the grundföreskrift PDF and
    record its Ändringsförfattningar as amendment references (Trafikverket
    publishes no konsoliderad versions)."""
    fs = ref.fs or agency.fs
    landing = request(session, "GET", ref.url).text
    soup = BeautifulSoup(landing, "html.parser")
    # the family page is a table of label/value <td> pairs; the title sits in the
    # <td> after the one reading "Rubrik"
    label = next((td for td in soup.find_all("td")
                  if td.get_text(strip=True) == "Rubrik"), None)
    cell = label.find_next_sibling("td") if label else None
    title = cell.get_text(" ", strip=True) if cell else None
    amendments = [{"identifier": "%s %s" % (fs.upper(), am.get_text(strip=True)),
                   "url": harvest.absolute(agency.base_url, am["href"])}
                  for am in soup.select('a[href*="/DocumentHistory/"]')]
    files = {"regulation": None, "consolidation": [], "amendment": amendments,
             "memo": [], "attachment": []}
    pdf = soup.select_one('a[href*="/TRVFS/pdf/"][href$=".pdf"]')
    if pdf:
        url = harvest.absolute(agency.base_url, pdf["href"])
        data = request(session, "GET", url).content
        if document_extension(data) == ".pdf":
            name = "%s-regulation.pdf" % slug(ref.basefile)
            compress.write_download(Path(root) / fs / name, data)
            files["regulation"] = {"name": name, "url": url, "identifier": ref.identifier}
            time.sleep(delay)
        else:
            msg = "%s %s: regulation link served a non-PDF body (%s)" % (fs, ref.basefile, url)
            log("  " + msg)
            if rejects is not None:
                rejects.append(msg)
    compress.write_download(Path(root) / fs / (slug(ref.basefile) + ".html"), landing)
    record = {"fs": fs, "basefile": ref.basefile, "identifier": ref.identifier,
              "title": ref.title or title, "publisher": agency.publisher,
              "url": ref.url, "files": files}
    compress.write_download(record_path(root, fs, ref.basefile),
                            json.dumps(record, ensure_ascii=False, indent=2))
    return record


TRVFS = Agency(
    fs="trvfs", name="Trafikverket", publisher="Trafikverket",
    base_url="https://trvfs.ea.trafikverket.se",
    index_url="https://trvfs.ea.trafikverket.se/TRVFS/Home/SearchInDocCollection",
    enumerate=trv_enumerate, resolve=trv_resolve,
)


# --------------------------------------------------------------------------
# AFFS (Arbetsförmedlingen) -- the register is a Sitevision app-state JSON
# ("list" of gällande författningar) rendered client-side, so there are no
# anchors in the static HTML: a bespoke enumerate reads each entry's displayName
# ("AFFS 2025:1", predecessor "AMSFS 1996:7" from Arbetsmarknadsstyrelsen) and
# landing URI out of the embedded JSON. fs_from_designation keeps AMSFS under its
# own samling. Each landing hangs the one regulation PDF (classify_single).
# --------------------------------------------------------------------------

RE_AFFS_ENTRY = re.compile(
    r'"displayName":"([^"]+)"[^{}]*?"URI":"([^"]+)"[^{}]*?"description":"([^"]*)"')


def affs_enumerate(session, agency):
    """One DocRef per författning from AFFS's embedded register JSON."""
    page = request(session, "GET", agency.index_url).text
    seen = set()
    for name, uri, description in RE_AFFS_ENTRY.findall(page):
        # the displayName occasionally carries a stray space ("AFFS 2025: 2")
        docref = harvest.ref(agency, name.replace(": ", ":"), uri, seen)
        if docref:
            docref.title = description or None
            yield docref


AFFS = Agency(
    fs="affs", name="Arbetsförmedlingen", publisher="Arbetsförmedlingen",
    base_url="https://arbetsformedlingen.se",
    index_url="https://arbetsformedlingen.se/om-oss/var-verksamhet/styrning-och-resultat/forfattningssamling-affs",
    enumerate=affs_enumerate, resolve=resolve_landing,
    params={"classify": classify_single, "fs_from_designation": True},
)


# --------------------------------------------------------------------------
# AGVFS (Arbetsgivarverket) -- indexed + landing + single-PDF. The listing
# anchors carry the designation in their text ("AGVFS 2026:3 B3 …") and link to a
# landing that hangs the one regulation PDF.
#
# CAVEAT: Arbetsgivarverket numbers its samling "YYYY:N Xn" (a subseries letter +
# counter, e.g. "AgVFS 2010:2 A2" and "AgVFS 2010:2 A3" -- two *distinct*
# documents sharing 2010:2). The framework's <fs>/<year>:<lop> basefile has no
# room for the Xn axis, so the two 2010:2 documents collapse to one basefile
# (only one is kept). All other AGVFS numbers are unique, so 9 of the 10 current
# documents harvest cleanly. See report.
# --------------------------------------------------------------------------

AGVFS = Agency(
    fs="agvfs", name="Arbetsgivarverket", publisher="Arbetsgivarverket",
    base_url="https://www.arbetsgivarverket.se",
    index_url="https://www.arbetsgivarverket.se/avtal-och-skrifter?category=32",
    enumerate=indexed_enumerate, resolve=resolve_landing,
    designation="AgVFS",
    params={"link_select": 'a[href*="/avtal-och-skrifter/agvfs/agvfs-"]',
            "pdf_select": 'a[href$=".pdf"]', "classify": classify_single},
)


# --------------------------------------------------------------------------
# FKFS (Försäkringskassan, lagrummet.forsakringskassan.se) -- the whole register
# is one embedded JSON corpus on /foreskrifter (each doc carries its own
# forfattningssamling -- FKFS or the predecessor RFFS/Riksförsäkringsverket --
# arsutgava, lopnummer and a Sitevision node id). A bespoke enumerate yields one
# DocRef per *base* regulation, routed to its own fs. The per-document detail page
# /foreskrifter/dokument?id=<id> hangs the family (grundförfattning + sammanställd
# consolidation + the change documents) as a <script data-props> JSON with real
# /download/ PDF URLs -- fkfs_resolve reads that and hands the download +
# record-writing to resolve_direct. RFFS records land under fs="rffs".
# --------------------------------------------------------------------------

RE_FKFS_DOC = re.compile(r'\{"nummer":"[^"]*".*?"samling":[^{}]*\}')


def fkfs_enumerate(session, agency):
    """One DocRef per base regulation from FKFS's embedded register corpus."""
    corpus = html.unescape(request(session, "GET", agency.index_url).text)
    seen = set()
    for obj in RE_FKFS_DOC.findall(corpus):
        d = json.loads(obj)
        if d["isChangeDocument"]:
            continue
        fs = d["forfattningssamling"].lower()          # "fkfs" or "rffs"
        lop = str(int(d["lopnummer"]))
        basefile = "%s/%s:%s" % (fs, d["arsutgava"], lop)
        if basefile in seen:
            continue
        seen.add(basefile)
        yield DocRef(
            basefile=basefile, fs=fs if fs != agency.fs else None,
            identifier="%s %s:%s" % (d["forfattningssamling"], d["arsutgava"], lop),
            url="%s/dokument?id=%s" % (agency.index_url, d["id"]),
            title=d.get("titel"))


def fkfs_resolve(session, agency, ref, root, delay=0.5, *, log=print, rejects=None):
    """Read a base regulation's family JSON off its detail page (grundförfattning +
    sammanställd + change documents, each with a real /download/ PDF URL) into
    ``ref.extra`` and delegate download + record-writing to resolve_direct."""
    page = request(session, "GET", ref.url).text
    props = next(p for p in re.findall(r'data-props="([^"]*)"', page)
                 if "baseDocument" in p)
    fam = json.loads(html.unescape(props))
    base = fam["baseDocument"]
    current = fam.get("currentDocument")     # None when there is no sammanställd version
    amendments = []
    for c in fam.get("changeDocuments", []):
        meta = c.get("metadata", {})
        nummer = meta.get("dokument_nr", {}).get("value")
        prefix = meta.get("forfattningssamling", {}).get("value") or ref.identifier.split()[0]
        amendments.append({"identifier": "%s %s" % (prefix, nummer) if nummer else None,
                           "url": harvest.absolute(agency.base_url, c["url"])})
    ref.extra = {
        "regulation_url": harvest.absolute(agency.base_url, base["url"]),
        "consolidations": [{"url": harvest.absolute(agency.base_url, current["url"])}]
                          if current and current.get("url") and current["url"] != base["url"] else [],
        "amendments": amendments,
        "title": ref.title, "source_url": ref.url,
    }
    return resolve_direct(session, agency, ref, root, delay, log=log, rejects=rejects)


FKFS = Agency(
    fs="fkfs", name="Försäkringskassan", publisher="Försäkringskassan",
    base_url="https://lagrummet.forsakringskassan.se",
    index_url="https://lagrummet.forsakringskassan.se/foreskrifter",
    enumerate=fkfs_enumerate, resolve=fkfs_resolve,
)


# --------------------------------------------------------------------------
# PFS (Pensionsmyndigheten) -- indexed + DIRECT (the listing anchor is the PDF, an
# AEM /content/dam/ file). The page mixes four series as flat PDFs: PFS (its own),
# the predecessor RFFS + FKFS föreskrifter it administers, and PRS (rättsliga
# ställningstaganden -- not författningar). PRS live under a different DAM folder
# (…/rattsliga-stallningstaganden/) so the /foreskrifter/ href filter drops them;
# RFFS/FKFS -- authoritatively harvested from Försäkringskassan's own register
# (the FKFS agency) -- are dropped by skip_re on their "Riksförsäkringsverkets/
# Försäkringskassans föreskrifter" link text, leaving PFS only.
# --------------------------------------------------------------------------

PFS = Agency(
    fs="pfs", name="Pensionsmyndigheten", publisher="Pensionsmyndigheten",
    base_url="https://www.pensionsmyndigheten.se",
    index_url="https://www.pensionsmyndigheten.se/om-pensionsmyndigheten/allmanna-handlingar/lagar-och-regler",
    enumerate=indexed_enumerate, resolve=resolve_direct,
    params={"link_select": 'a[href*="reskrifter/"][href$=".pdf"]', "direct": True,
            "skip_re": r"Riksförsäkringsverket|Försäkringskassan|\bRFFS\b|\bFKFS\b|\bPRS\b"},
)


# --------------------------------------------------------------------------
# Closed and browser-gated författningssamlingar. Socialstyrelsen's SOSFS /
# HSLF-FS are closed series (no live harvester; their documents live in the
# corpus). SKVFS is live but must use detached headful Chrome because F5 rejects
# both HTTP clients and Playwright-instrumented navigation; its Agency.browser
# flag selects that transport without affecting any other agency. The one SKVFS
# register also enumerates the closed RSFS predecessor (cited "RSFS 1985:20", so
# its own code + URIs) into its own namespace, so RSFS needs no second sweep.
# HSLF-FS is slugged `hslffs` (hyphen stripped) -- the ELSÄK-FS -> `elsakfs`
# precedent and the `^[a-zåäö]+fs/…` layout locator agree; the `designation`
# carries the printed "HSLF-FS" the identifier needs.
# --------------------------------------------------------------------------

def frozen_agency(fs, name, publisher, designation, site):
    """A closed/static författningssamling: a registry entry with no live
    enumerate/resolve, so nothing new is harvested -- its historical documents
    live in the corpus like any other source's. ``site`` is the agency's home
    page, kept for provenance though no harvester reads it."""
    return Agency(fs=fs, name=name, publisher=publisher, base_url=site,
                  index_url=site, designation=designation)


# SVKFS (Affärsverket svenska kraftnät) is not live-harvestable for a different
# reason: SvK has effectively delegated its regulatory output to Energimarknads-
# inspektionen (EIFS); its current SvKFS page lists a single upphävd föreskrift
# (SvKFS 2005:2, replaced by EIFS 2025:2) with no PDF and no register to scrape.
# SJVFS (Statens jordbruksverk): the register itself is public (a Sitevision
# search portlet, predecessor LSFS included) but every document link redirects
# to an authenticated Microsoft 365 / SharePoint tenant (login.microsoftonline
# .com) -- no anonymous PDF access. Frozen until Jordbruksverket restores
# public documents or a SharePoint-authenticated harvest posture exists (§7g).
SJVFS = frozen_agency("sjvfs", "Statens jordbruksverk", "Statens jordbruksverk",
                      "SJVFS", "https://jordbruksverket.se")

SVKFS = frozen_agency("svkfs", "Affärsverket svenska kraftnät",
                      "Affärsverket svenska kraftnät", "SvKFS",
                      "https://www.svk.se")

# MTFS (Tillväxtanalys) sits behind the same F5/Shape JavaScript bot-defense as
# SKVFS. Its one-page Sitevision register and direct PDFs work through the same
# detached headful-Chrome transport, selected only by these two Agency configs.
MTFS = Agency(
    fs="mtfs", name="Tillväxtanalys",
    publisher="Myndigheten för tillväxtpolitiska utvärderingar och analyser",
    base_url="https://www.tillvaxtanalys.se",
    index_url=mtfs.INDEX_URL,
    enumerate=mtfs.enumerate_register,
    resolve=mtfs.resolve,
    designation="MTFS",
    browser=True,
    browser_settle=20.0,
)

SKVFS = Agency(
    fs="skvfs", name="Skatteverket", publisher="Skatteverket",
    base_url="https://www4.skatteverket.se",
    index_url=skvfs.INDEX_URL,
    enumerate=skvfs.enumerate_register,
    resolve=skvfs.resolve,
    designation="SKVFS",
    browser=True,
    browser_settle=20.0,
)
RSFS = frozen_agency("rsfs", "Riksskatteverket", "Skatteverket", "RSFS",
                     "https://www.skatteverket.se")
SOSFS = frozen_agency("sosfs", "Socialstyrelsen", "Socialstyrelsen", "SOSFS",
                      "https://www.socialstyrelsen.se")
HSLFFS = frozen_agency(
    "hslffs", "Gemensamma författningssamlingen (hälso- och sjukvård m.m.)",
    "Socialstyrelsen", "HSLF-FS", "https://www.socialstyrelsen.se")


# fs code -> Agency. New agencies append here; a new *site shape* is a new
# enumerate/classify in harvest.py, not a new pipeline.
REGISTRY = {a.fs: a for a in (
    FFFS, SSMFS, NFS, KIFS, BFS,                       # first wave (5)
    ELSAKFS, RGKFS, LMFS, KOVFS, PTSFS, MCFFS,         # second wave (10):
    LIVSFS, STEMFS, TFS, SIFS,                         #   ELSÄK-FS … SIFS
    PMFS, RPSFS,                                       # third wave: police FS (2)
    EIFS, HVMFS, SKSFS, SGUFS,                         # fourth wave: the rest of
    DVFS, KVFS, KFMFS, KBVFS,                          #   the lagrummet.se list
    FFS, KFS, MIGRFS, AAFS,
    PRVFS, RAFS, RAMS, RFS, KRFS,
    BFNAR, BOLFS, CSNFS, RIFS,
    IAFFS, IMYFS, KAMFS, KKVFS,
    STFS, SJOFS, TPPVFS, VRFS, VALFS,
    MEMYFS, MDFFS, MYHFS,
    SKOLFS, SISFS, UHRFS, UFS,
    SCBFS, STAFS, TVFS,
    AFS, TSFS, TRVFS,
    AFFS, AGVFS, FKFS, PFS,
    SJVFS, SVKFS,                                      # closed: no public documents/register
    MTFS, SKVFS,                                       # live: detached Chrome for the F5 wall
    RSFS, SOSFS, HSLFFS,                               # closed series; RSFS also emitted by SKVFS
)}
