"""The per-agency registry -- where the ~100 författningssamlingar live as
configuration over the shared harvest engine (:mod:`harvest`). Each entry is an
:class:`~harvest.Agency`: a författningssamling code, the issuing org, its index
URL, and the architecture (an ``enumerate`` + a ``resolve``) that fits its site,
plus ``params``.

The point of the engine is that an agency is *config*, not a pipeline. The five
here span the real publishing architectures found in the wild:

  * **FFFS** (Finansinspektionen) -- a single HTML förteckning; bespoke
    ``fi_enumerate`` (its rows carry the year+lopnummer fused in the detail URL).
  * **SSMFS** (Strålsäkerhetsmyndigheten) -- ``paginated_enumerate`` (`?page=N`),
    landing pages, text-classified files (PDFs served without a `.pdf` suffix).
  * **NFS** (Naturvårdsverket) -- ``json_enumerate`` (a search API returns the
    whole corpus), landing pages, files classified by PDF filename.
  * **KIFS** (Kemikalieinspektionen) -- ``indexed_enumerate`` (one static page),
    landing pages whose files are grouped under ``<h2>`` section headings
    (``classify_section``), Sitevision ``/download/`` PDFs.

Adding the next agency is picking an enumerate + classify and filling params; a
genuinely new site shape is one new function in :mod:`harvest`, not a pipeline.
"""

import re
import time

from bs4 import BeautifulSoup

from ..lib.net import BROWSER_UA, request
from . import harvest
from .harvest import (
    Agency,
    DocRef,
    classify_default_regulation,
    classify_file,
    classify_href,
    classify_section,
    classify_single,
    indexed_enumerate,
    json_enumerate,
    paginated_enumerate,
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

# paginated + landing + filename-classify (MSB -> mcf.se; msbfs/mcffs co-prefix)
MSBFS = Agency(
    fs="msbfs", name="Myndigheten för civilt försvar (f.d. MSB)",
    publisher="Myndigheten för samhällsskydd och beredskap",
    base_url="https://www.mcf.se",
    index_url="https://www.mcf.se/sv/regler/gallande-regler/",
    enumerate=paginated_enumerate, resolve=resolve_landing,
    params={"page_url": "https://www.mcf.se/sv/regler/gallande-regler/?selectedpage={page}",
            "row_select": "a.constitution-list-card-link",
            # new texts live under /contentassets/, the old SÄI/SÄIFS ones under
            # /siteassets/ with UUID filenames -- match both asset roots.
            "pdf_select": 'a[href*="assets/"][href$=".pdf"]',
            "classify": classify_default_regulation},
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
# Frozen-only författningssamlingar (REWRITE.md §7g priority 6). SKVFS
# (Skatteverket, behind an F5 bot-defense) and Socialstyrelsen's SOSFS / HSLF-FS
# (a React SPA) are the plan's two known-hard, *deferred* harvests: no live
# enumerate/resolve is written for them (both need a bot-evading harvest posture
# nobody has built yet). Their only source is the frozen legacy tree, materialized
# once by :mod:`legacy` (`lagen foreskrift import-legacy skvfs|sosfs`). They are
# registered so the fs codes are first-class scopes / URIs (browse facets,
# render); a `download` over them is a logged no-op (`download.sync`), and the
# import stamps each record with a `source` marker so a future live harvester's
# record is never clobbered by a re-import.
#
# Each frozen corpus tree carries *two* fs series: SKVFS + its Riksskatteverket
# predecessor RSFS (cited "RSFS 1985:20", so its own code + URIs), and SOSFS +
# the joint HSLF-FS series. So one import writes records for two agencies, keyed
# by each entry's authoritative (post-sanitization) basefile. HSLF-FS is slugged
# `hslffs` (hyphen stripped) -- the entries' own basefile field, the ELSÄK-FS ->
# `elsakfs` precedent, and the `^[a-zåäö]+fs/…` layout locator all agree; the
# `designation` carries the printed "HSLF-FS" the identifier needs.
# --------------------------------------------------------------------------

def frozen_agency(fs, name, publisher, designation, site):
    """A författningssamling whose only source is the frozen legacy tree: a
    registry entry with no live enumerate/resolve (§7g deferred harvest). ``site``
    is the agency's home page, kept for provenance though no harvester reads it."""
    return Agency(fs=fs, name=name, publisher=publisher, base_url=site,
                  index_url=site, designation=designation)


SKVFS = frozen_agency("skvfs", "Skatteverket", "Skatteverket", "SKVFS",
                      "https://www.skatteverket.se")
RSFS = frozen_agency("rsfs", "Riksskatteverket", "Skatteverket", "RSFS",
                     "https://www.skatteverket.se")
SOSFS = frozen_agency("sosfs", "Socialstyrelsen", "Socialstyrelsen", "SOSFS",
                      "https://www.socialstyrelsen.se")
HSLFFS = frozen_agency(
    "hslffs", "Gemensamma författningssamlingen (hälso- och sjukvård m.m.)",
    "Socialstyrelsen", "HSLF-FS", "https://www.socialstyrelsen.se")

# corpus (a frozen tree name) -> the fs series it holds. Drives the import verb:
# the corpus is walked once and each entry routed to its own fs by basefile.
LEGACY_CORPORA = {"skvfs": ("skvfs", "rsfs"), "sosfs": ("sosfs", "hslffs")}


# fs code -> Agency. New agencies append here; a new *site shape* is a new
# enumerate/classify in harvest.py, not a new pipeline.
REGISTRY = {a.fs: a for a in (
    FFFS, SSMFS, NFS, KIFS, BFS,                       # first wave (5)
    ELSAKFS, RGKFS, LMFS, KOVFS, PTSFS, MSBFS,         # second wave (10):
    LIVSFS, STEMFS, TFS, SIFS,                         #   ELSÄK-FS … SIFS
    PMFS, RPSFS,                                       # third wave: police FS (2)
    SKVFS, RSFS, SOSFS, HSLFFS,                        # frozen-only (§7g): SKV/SOS
)}
