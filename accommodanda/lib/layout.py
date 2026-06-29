"""Single source of truth for where every document lives -- on disk and on the
web. A document's identity is ``(source, basefile)``; three rule-based, pure
mappings derive from it:

  * ``downloaded`` -- the raw fetched bytes
  * ``artifact``   -- the parsed JSON
  * ``page_relpath`` -- the generated HTML file on disk
  * ``page_url`` -- the public lagen.nu address a link points at

The last two are deliberately *not* identical: a filesystem-safe, flattened file
name versus lagen.nu's URI grammar. A statute's page is the file ``2018:585.html``
but its public URL is the bare ``/2018:585``; a case lives at
``dom/dom_nja_2011s357.html`` but is served at ``/dom/nja/2011s357``; an EU act is
``eurlex/32016R0679.html`` but addressed ``/celex/32016R0679``. ``url_to_relpath``
is the inverse the static server applies (``api.app.SiteFiles``) to resolve a
public URL back to its file -- nginx's ``try_files`` rewrites, in Starlette.
Centralising these rules here -- instead of the ~10 scattered helpers in build.py
and render.py -- keeps the layout conventions in one reviewable place.
"""

import re
from pathlib import Path
from urllib.parse import quote, unquote

from .. import config
from ..dv.parse import slug as _dv_slug
from .catalog import BASE, local, strip_fragment

DATA = config.DATA
GENERATED = DATA / "generated"

# One dir per source, each with the uniform downloaded/ (raw) + artifact/
# (parsed) trees. Two deliberate exceptions, matching lagen.nu's grammar:
#  * case law's canonical dir is dom/ (the /dom/ URL); the api records and ALL
#    parsed case-law artifacts live there. dv/ keeps only the legacy raw feed.
#  * kommentar + begrepp share one mediawiki/ dump (one raw source, two derived).
SFS_ROOT = DATA / "sfs"
DOM_ROOT = DATA / "dom"          # case law (source key "dv"): api raw + artifacts
DV_ROOT = DATA / "dv"            # legacy case-law raw feed only
FA_ROOT = DATA / "forarbete"
EURLEX_ROOT = DATA / "eurlex"
FORESKRIFT_ROOT = DATA / "foreskrift"     # agency regulations (per-fs subtrees)
KOMMENTAR_ROOT = DATA / "kommentar"
BEGREPP_ROOT = DATA / "begrepp"
WIKI_ROOT = DATA / "mediawiki" / "downloaded"       # shared by kommentar+begrepp

ARTIFACT_ROOT = {"sfs": SFS_ROOT, "dv": DOM_ROOT, "forarbete": FA_ROOT,
                 "eurlex": EURLEX_ROOT, "foreskrift": FORESKRIFT_ROOT,
                 "kommentar": KOMMENTAR_ROOT, "begrepp": BEGREPP_ROOT}

# raw roots -- the download writers put their structure under these
SFS_DOWNLOADED = SFS_ROOT / "downloaded"
DOM_DOWNLOADED = DOM_ROOT / "downloaded"            # dv api records
DV_LEGACY_DOWNLOADED = DV_ROOT / "downloaded"       # dv legacy store
FA_DOWNLOADED = FA_ROOT / "downloaded"
EURLEX_DOWNLOADED = EURLEX_ROOT / "downloaded"
FORESKRIFT_DOWNLOADED = FORESKRIFT_ROOT / "downloaded"   # <fs>/<slug>.{json,pdf}

DOM_INDEX = DOM_ROOT / "identity-index.json"        # case-law identity index


def _sfs_parts(basefile):
    year, nr = basefile.split(":", 1)
    return year, nr.replace(" ", "_")


def _alnum_slug(s):
    return "".join(c if c.isalnum() else "_" for c in s).strip("_")


# --------------------------------------------------------------------------
# storage relpath -> artifact / downloaded
# --------------------------------------------------------------------------

def relpath(source, basefile):
    """The filesystem-safe storage sub-path of a document, shared by its
    downloaded and artifact trees where both are rule-based."""
    if source == "sfs":
        year, nr = _sfs_parts(basefile)
        return Path(year) / nr
    if source == "dv":
        return Path(_dv_slug(basefile))
    if source == "forarbete":
        typ, rest = basefile.split("/", 1)
        return Path(typ) / rest
    if source == "eurlex":
        return Path(basefile[1:5]) / basefile.replace("/", "_")
    if source == "foreskrift":
        fs, rest = basefile.split("/", 1)        # "fffs/2013:10"
        return Path(fs) / rest.replace(":", "-").replace(" ", "_")
    if source in ("kommentar", "begrepp"):
        return Path(_alnum_slug(basefile))
    raise ValueError("unknown source %r" % source)


def artifact(source, basefile):
    """The parsed-artifact path: ``<dir>/artifact/<relpath>.json``."""
    rel = relpath(source, basefile)
    return ARTIFACT_ROOT[source] / "artifact" / rel.with_name(rel.name + ".json")


# --------------------------------------------------------------------------
# downloaded (raw). SFS keeps three raw forms under downloaded/; eurlex bundles
# many files in one per-document directory. dv and the wiki sources resolve
# their raw path through an index (api record / wiki page), so only their
# downloaded roots are exposed (above), not per-document rules.
# --------------------------------------------------------------------------

def sfs_source(basefile):               # new beta-API JSON (the primary form)
    year, nr = _sfs_parts(basefile)
    return SFS_DOWNLOADED / year / (nr + ".json")


def sfs_sfst(basefile):                 # legacy consolidated-text HTML
    year, nr = _sfs_parts(basefile)
    return SFS_DOWNLOADED / "sfst" / year / (nr + ".html")


def sfs_sfsr(basefile):                 # legacy register HTML
    year, nr = _sfs_parts(basefile)
    return SFS_DOWNLOADED / "sfsr" / year / (nr + ".html")


def fa_record(basefile):
    typ, rest = basefile.split("/", 1)
    return FA_DOWNLOADED / typ / (rest + ".json")


def eurlex_dir(basefile):
    """The per-CELEX directory holding eurlex's raw files (notice.ttl + the
    per-language manifestations)."""
    return EURLEX_DOWNLOADED / relpath("eurlex", basefile)


# --------------------------------------------------------------------------
# public URL / generated page
# --------------------------------------------------------------------------

# förarbete uri prefixes (prop/2025/26:161, sou/2020:1, …) -- each routes to its
# own top-level segment (/prop/…, /sou/…), lagen.nu's grammar, not a shared /fa/
FORARBETE = ("prop/", "sou/", "ds/", "dir/", "fm/", "skr/", "so/", "lr/",
             "bet/", "rskr/")


# --------------------------------------------------------------------------
# authoritative source url -- a document's canonical location at the publisher,
# where one is derivable by rule from its identity. Sources whose source url is
# *not* rule-derivable (e.g. a regeringen.se landing page) record it at download
# time instead; source_url returns None for them and build.write_artifact stamps
# the recorded url. Either way the artifact ends up with one uniform
# `source_url` key, which the renderer turns into the page's "Källa" link.
# --------------------------------------------------------------------------

EURLEX_ELI = "https://eur-lex.europa.eu/eli/%s/%s/%s/oj"
EURLEX_CELEX = "https://eur-lex.europa.eu/legal-content/SV/TXT/?uri=CELEX:%s"
_ELI_TYPE = {"R": "reg", "L": "dir", "D": "dec"}     # CELEX act descriptor -> ELI
SFS_ITEM = ("https://beta.rkrattsbaser.gov.se/sfs/item"
            "?bet=%s&tab=forfattningstext")
DV_PUBLICERING = "https://rattspraxis.etjanst.domstol.se/sok/publicering/%s"


def dv_source_url(gruppkorrelationsnummer):
    """A case's page in the courts' public publication search. Keyed by the API
    record's gruppKorrelationsnummer (the publication group, not the record id),
    so this lives off record data -- build.dv_parse_run passes it in."""
    return DV_PUBLICERING % gruppkorrelationsnummer


def eurlex_source_url(celex):
    """An EU act's canonical EUR-Lex address from its CELEX. Sector-3
    regulations, directives and decisions have an ELI -- e.g. 32023R2854 ->
    https://eur-lex.europa.eu/eli/reg/2023/2854/oj (leading zeros stripped from
    the number). Everything else (judgments, treaties, other act descriptors)
    has no ELI, so fall back to the stable CELEX legal-content url."""
    eli_type = _ELI_TYPE.get(celex[5]) if len(celex) > 5 else None
    if celex.startswith("3") and eli_type:
        return EURLEX_ELI % (eli_type, celex[1:5], celex[6:].lstrip("0") or "0")
    return EURLEX_CELEX % celex


def source_url(source, basefile, metadata=None):
    """The authoritative publisher url for a document, derived by rule from its
    identity where possible, else None -- in which case the downloader-recorded
    url is used instead (see build.write_artifact)."""
    if source == "eurlex":
        return eurlex_source_url(basefile)
    if source == "sfs":
        return SFS_ITEM % quote(basefile, safe="")
    return None


def page_relpath(uri):
    """The generated HTML file for a document uri, by uri shape -- lagen.nu's URL
    grammar: dv at dom/, förarbeten under their type segment (prop/, sou/, …), EU
    acts under eurlex/ (the CELEX kept intact). A statute is a *top-level* page
    named by its bare SFS id with the colon kept (2018:585 -> 2018:585.html), so
    it is served at lagen.nu's /2018:585 address (see `page_url`)."""
    loc = local(strip_fragment(uri))
    if loc.startswith("dom/"):
        prefix = "dom"
    elif loc.startswith("kommentar/"):
        prefix = "kommentar"
    elif loc.startswith("begrepp/"):
        prefix = "begrepp"
    elif loc.startswith("ext/celex/"):
        return "eurlex/%s.html" % loc[len("ext/celex/"):].replace("/", "_")
    elif loc.startswith(FORARBETE):
        # keep the type as the top-level segment, slug only the rest:
        # prop/2024/25:1 -> prop/2024_25_1.html (served at /prop/…)
        typ, _, rest = loc.partition("/")
        return "%s/%s.html" % (typ, _alnum_slug(rest))
    elif _FORESKRIFT_LOC.match(loc):
        # an agency regulation, lagen.nu's /{fs}/{år}:{nr} grammar -- the
        # författningssamling is the top segment: fffs/2013:10 -> fffs/2013-10.html
        fs, _, rest = loc.partition("/")
        return "%s/%s.html" % (fs, _alnum_slug(rest))
    else:
        # SFS: a top-level page, the SFS id kept verbatim (colon and all). The id
        # is already filesystem-safe (digits, ':', '_', '.'): 1827:60_s.1007.
        return "%s.html" % loc
    return "%s/%s.html" % (prefix, _alnum_slug(loc))


def page_url(uri):
    """The public URL a link points at -- lagen.nu's URI grammar: the document's
    host-stripped local path, served bare (no .html). A statute is /2018:585, a
    proposition /prop/2020/21:22, a case /dom/ad/1993:100. An EU act lives under
    /celex/<celexid> (its ext/celex/ namespace collapsed). The static server maps
    these back to the flattened on-disk files (see url_to_relpath, api.app.SiteFiles)."""
    loc = local(strip_fragment(uri))
    if loc.startswith("ext/celex/"):
        return "/celex/" + loc[len("ext/celex/"):]
    return "/" + loc


def url_to_relpath(path):
    """Inverse of page_url: the on-disk static file for a public lagen.nu URL path.
    The path is a document's URI local form, so reattach the host and reuse the
    page_relpath rule; /celex/<id> is the public address of ext/celex/<id>."""
    loc = unquote(path).lstrip("/")
    if loc.startswith("celex/"):
        loc = "ext/celex/" + loc[len("celex/"):]
    return page_relpath(BASE + loc)


# a föreskrift loc is "<fs>/<år>:<nr>"; every författningssamling code ends in FS
# (fffs, nfs, kifs, …), which sets it apart from an SFS loc ("2013:635")
_FORESKRIFT_LOC = re.compile(r"^[a-zåäö]+fs/\d{4}:\d+$")
