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
from . import compress
from .catalog import BASE, local, strip_fragment
from .util import basefile_slug

DATA = config.DATA
GENERATED = DATA / "generated"

# --------------------------------------------------------------------------
# Stage-first layout: <stage>/<source>/…  (e.g. downloaded/sfs, artifact/dom).
# Grouping by pipeline stage first, source second, keeps each stage a single
# directory -- the bulky downloaded/ can live on its own volume, be snapshotted
# or synced, without dragging the derived trees along. Two source-name
# exceptions match lagen.nu's grammar:
#  * case law (source key "dv") files its api raw *and* every parsed artifact
#    under the name "dom" (the /dom/ URL); "dv" names only its legacy raw feed.
#  * kommentar + begrepp are authored as markdown in a separate content repo
#    (WIKI_ROOT, a sibling checkout); only their derived artifacts live here.
# --------------------------------------------------------------------------
WIKI_ROOT = config.WIKI_ROOT        # git-backed markdown content repo (begrepp/ + kommentar/ + site/)

# stage roots
DOWNLOADED = DATA / "downloaded"    # raw fetched bytes -- the bulk; volume candidate
ARTIFACT = DATA / "artifact"        # parsed JSON -- the source of truth
OCR = DATA / "ocr"                  # re-OCR sidecar PDFs (forarbete parse input)
# NB: the old-pipeline parsed/distilled "golden" oracles are temporary
# scaffolding, deliberately NOT a data_root stage -- they live in the old
# checkout (see tools/golden_dv*.py, which take an oracle path arg).

# the on-disk source-dir name under each stage; "dv" -> "dom" (see above)
SOURCE_DIR = {"sfs": "sfs", "dv": "dom", "forarbete": "forarbete",
              "eurlex": "eurlex", "foreskrift": "foreskrift", "avg": "avg",
              "hudoc": "hudoc", "coe": "coe", "icrc": "icrc", "untc": "untc",
              "icc": "icc",
              "remisser": "remisser", "kommentar": "kommentar",
              "begrepp": "begrepp", "site": "site"}


def artifact_dir(source):
    """The parsed-artifact directory of a source: ``artifact/<source>``."""
    return ARTIFACT / SOURCE_DIR[source]


# raw roots -- the download writers put their structure under these
SFS_DOWNLOADED = DOWNLOADED / "sfs"
SFS_ARTIFACT = ARTIFACT / "sfs"                     # sfs artifacts + sidecars + archive/
DOM_DOWNLOADED = DOWNLOADED / "dom"                 # dv api records
DV_LEGACY_DOWNLOADED = DOWNLOADED / "dv"            # dv legacy store
FA_DOWNLOADED = DOWNLOADED / "forarbete"
EURLEX_DOWNLOADED = DOWNLOADED / "eurlex"
FORESKRIFT_DOWNLOADED = DOWNLOADED / "foreskrift"   # <fs>/<slug>.{json,pdf}
AVG_DOWNLOADED = DOWNLOADED / "avg"                 # <org>/<slug>.{json,pdf,html}
HUDOC_DOWNLOADED = DOWNLOADED / "hudoc"             # <itemid>.{json,html}
COE_DOWNLOADED = DOWNLOADED / "coe"                 # <CETS>.{json,pdf|html}
ICRC_DOWNLOADED = DOWNLOADED / "icrc"               # <ICRC-number>.json (JSON:API envelope)
UNTC_DOWNLOADED = DOWNLOADED / "untc"               # <mtdsg_no>.html (MTDSG status page)
ICC_DOWNLOADED = DOWNLOADED / "icc"                 # <doc-number>.{json,pdf} (Legal Tools record + PDF)

# remisser's case records + answer PDFs share one download tree (see remisser_case)
REMISSER_DOWNLOADED = DOWNLOADED / "remisser"

# index sidecars that live inside a source's artifact dir but are NOT corpus
# documents -- the case-law identity index and the AI-guidance discovery index.
# `artifacts()` filters them out so no consumer treats them as a document (they
# are JSON lists / index maps, not artifacts). Owned here because the artifact
# tree is layout's; guidance_discover imports GUIDANCE_INDEX rather than the
# reverse (lib must not import a vertical).
DOM_INDEX = ARTIFACT / "dom" / "identity-index.json"        # case-law identity index
GUIDANCE_INDEX = ARTIFACT / "kommentar" / "guidance-index.json"  # AI-guidance index


def _sfs_parts(basefile):
    year, nr = basefile.split(":", 1)
    return year, nr.replace(" ", "_")


def _alnum_slug(s):
    return "".join(c if c.isalnum() else "_" for c in s).strip("_")


def case_slug(case_id):
    """Filesystem-safe form of a DV case id ("AD 1993 nr 100" ->
    "AD_1993_nr_100"); runs of non-word characters collapse to one underscore.
    Not `_alnum_slug` (which underscores each character, "s." -> "s__"). Lives
    here, not in the dv vertical, because the path grammar is layout's."""
    return re.sub(r"[^\w]+", "_", case_id).strip("_")


def kommentar_host(basefile):
    """The host source a kommentar/begrepp basefile annotates. A kommentar borrows
    its host's identity (`annotates:` is an SFS number, a CELEX, an FS id or a
    förarbete id), so its artifact is filed *under that host source* -- mirroring
    the content repo's `commentary/<source>/…` layout and, crucially, reusing the
    host's own path transform so two sources can never collide on one flat name.
    The split is the same one `wiki.host_uri` makes: an FS id / förarbete id first
    (they carry a `/`), then a colon means SFS, else a bare CELEX (eurlex)."""
    if _FORESKRIFT_LOC.match(basefile):
        return "foreskrift"
    if basefile.startswith(FORARBETE):
        return "forarbete"
    return "sfs" if ":" in basefile else "eurlex"


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
        return Path(case_slug(basefile))
    if source == "forarbete":
        typ, rest = basefile.split("/", 1)
        return Path(typ) / rest
    if source == "eurlex":
        return Path(basefile[1:5]) / basefile.replace("/", "_")
    if source == "foreskrift":
        fs, rest = basefile.split("/", 1)        # "fffs/2013:10"
        return Path(fs) / rest.replace(":", "-").replace(" ", "_")
    if source == "avg":
        org, rest = basefile.split("/", 1)       # "jo/2340-2025", "jk/2024/8082"
        return Path(org) / rest.replace("/", "-")
    if source in ("hudoc", "coe", "icrc", "untc", "icc"):
        return Path(basefile)
    if source == "remisser":
        case, org = basefile.split("/", 1)        # "<case-slug>/<org-slug>"
        return Path(case) / org
    if source == "kommentar":
        # file the annotation under its host source, reusing that source's
        # transform: sfs/2009/400, eurlex/2023/32023R2854 -- so a commentary on
        # SFS 2009:400 and one on a same-slug act in another source never collide
        host = kommentar_host(basefile)
        return Path(host) / relpath(host, basefile)
    if source == "begrepp":
        # concept names are their own namespace (no host); keep the flat slug
        return Path(_alnum_slug(basefile))
    if source == "site":
        # editorial pages under fixed basefiles (`frontpage`, `sitenews`,
        # `om/<slug>`); the basefile is already filesystem-safe, used verbatim
        return Path(basefile)
    raise ValueError("unknown source %r" % source)


def artifact(source, basefile):
    """The parsed-artifact path: ``artifact/<source>/<relpath>.json``."""
    rel = relpath(source, basefile)
    return artifact_dir(source) / rel.with_name(rel.name + ".json")


# --------------------------------------------------------------------------
# patch files -- curated, version-controlled fixes to a document's raw/
# intermediate source, applied at parse time (see lib/patch.py). Unlike the
# downloaded/artifact trees these are hand-authored knowledge that must be
# reviewable and must ship with the pipeline, so they live *in the repo*, not
# under DATA -- anchored to the package tree like the other curated in-repo
# resources (sfs/data/*.json). Keyed by the same (source, basefile) -> relpath
# rule as artifact(), so a document's patch sits at a predictable location.
# --------------------------------------------------------------------------
PATCHES = Path(__file__).resolve().parent.parent / "patches"   # accommodanda/patches


def patch(source, basefile, suffix=".patch"):
    """The patch-file path for a document: ``patches/<source>/<relpath><suffix>``.
    `suffix` selects the variant -- ``.patch`` (plain), ``.rot13.patch`` (a
    rot13-obfuscated redaction, so removed personal data is not itself plain-text
    googleable in the committed patch) or ``.desc`` (a multi-line description
    sidecar)."""
    rel = relpath(source, basefile)
    return PATCHES / source / rel.with_name(rel.name + suffix)


# non-document json files that share a source's artifact dir: the index sidecars
# (by basename, so the filter is independent of where ARTIFACT is rooted) and the
# sfs `.versions.json` historical-consolidation sidecars.
_NON_ARTIFACT_NAMES = frozenset({DOM_INDEX.name, GUIDANCE_INDEX.name})


def _is_document_artifact(path):
    return (path.name not in _NON_ARTIFACT_NAMES
            and not path.name.endswith(".versions.json"))


def artifacts(source):
    """Every parse artifact of `source` on disk, sorted -- the iteration
    companion to `artifact`, so the tree layout has one home and a consumer
    can't drift out of sync with it by hand-globbing. Non-document json that
    happens to live in the artifact dir (the identity/guidance index sidecars,
    the sfs `.versions.json` layers) is excluded -- it is not a corpus document.

    Artifacts are stored precompressed (lib/compress), so a document may be on
    disk as `.json`, `.json.br` or `.json.gz`; each is mapped back to its logical
    `.json` path (deduplicated). The transparent read/stat helpers resolve that
    logical path to whatever variant is present, so every consumer keeps working
    on logical paths regardless of the on-disk storage format."""
    root = artifact_dir(source)
    logical = {compress.logical(p) for suffix in ("", *compress.SUFFIXES)
               for p in root.glob("**/*.json" + suffix)}
    return sorted(p for p in logical if _is_document_artifact(p))


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


def sfs_pdf(basefile):                  # officially published SFS PDF (facsimile source)
    year, nr = _sfs_parts(basefile)
    return SFS_DOWNLOADED / "pdf" / year / (nr + ".pdf")


# --------------------------------------------------------------------------
# sfs archive -- superseded consolidations. Each stage keeps its own archive/
# subtree (downloaded/sfs/archive for raw, artifact/sfs/archive for parsed), in
# the old site's per-document .versions/ layout; a version id is the SFS number
# of the last amendment folded into that consolidation ("2003:466" ->
# 2003/466.<ext>), or a bare legacy counter ("11") where the old archiver
# couldn't recover the cutoff.
# --------------------------------------------------------------------------

def _sfs_version_dir(stage_dir, basefile):
    """The .versions/ tree of one statute under a stage dir's archive/ subtree
    (`stage_dir` is SFS_DOWNLOADED for raw, SFS_ARTIFACT for parsed)."""
    year, nr = _sfs_parts(basefile)
    return stage_dir / "archive" / year / nr / ".versions"


def sfs_version_file(stage_dir, basefile, version):
    """Physical path of one archived consolidation under a stage dir's archive
    subtree: ``<stage_dir>/archive/{y}/{n}/.versions/{vy}/{vn}.json`` -- a flat
    ``.versions/<version>.json`` for an unrecovered legacy counter with no year
    to nest under. The single owner of the .versions grammar, shared by the raw
    writer (`sfs_archive_version_download`, stage_dir=SFS_DOWNLOADED) and the
    parsed reader (`sfs_version_artifact`, stage_dir=SFS_ARTIFACT) so the two
    archives can never drift (version ids are space-free -- `sfs.download.
    version_id` strips them -- but slug them for parity with `relpath`)."""
    root = _sfs_version_dir(stage_dir, basefile)
    if ":" in version:
        vyear, vnr = version.split(":", 1)
        return root / vyear / ("%s.json" % vnr.replace(" ", "_"))
    return root / ("%s.json" % version.replace(" ", "_"))


def sfs_archive_version_download(destdir, basefile, version):
    """Write path for a superseded consolidation's raw JSON: the ``archive/``
    subtree of the live download dir. `destdir` is the injected download dir
    (SFS_DOWNLOADED in prod, a tmp/CLI dir under test), so the harvester stays
    root-relative while the slug grammar lives here."""
    return sfs_version_file(destdir, basefile, version)


def sfs_version_downloads(basefile):
    """Every archived consolidation of a statute: sorted (version, path) pairs
    from the archive's .versions/ tree -- legacy HTML (the two rättsdatabaser
    generations) and the new downloader's JSON side by side. When one version id
    exists in both forms the JSON (the richer, register-carrying form) wins."""
    root = _sfs_version_dir(SFS_DOWNLOADED, basefile)
    found = {}
    for path in sorted(compress.glob(root, "*/*")) + sorted(compress.glob(root, "*")):
        if path.is_dir() or path.suffix not in (".html", ".json"):
            continue   # junk (editor backups) never becomes a version
        version = ("%s:%s" % (path.parent.name, path.stem.replace("_", " "))
                   if path.parent != root else path.stem.replace("_", " "))
        if version not in found or path.suffix == ".json":
            found[version] = path
    return sorted(found.items())


def sfs_version_key(version):
    """Chronological sort key for a consolidation version id: the cutoff SFS
    number ("2003:466"); an unrecovered legacy counter ("11", no year to order
    by) sorts first, by counter."""
    if ":" in version:
        year, nr = version.split(":", 1)
        return (int(year), int(re.sub(r"\D", "", nr) or 0))
    return (0, int(version))


def sfs_version_artifact(basefile, version):
    """A parsed archived consolidation: the artifact-tree mirror of its
    download, keyed by the (possibly recovered) version id."""
    return sfs_version_file(SFS_ARTIFACT, basefile, version)


def sfs_versions_sidecar(basefile):
    """The per-statute version index -- the versions stage's output, a sidecar
    next to the main artifact: which historical consolidations exist, their
    recovered version ids and their parse status."""
    rel = relpath("sfs", basefile)
    return SFS_ARTIFACT / rel.with_name(rel.name + ".versions.json")


def sfs_sidecar_basefile(path):
    """Inverse of sfs_versions_sidecar: the statute basefile a sidecar file
    describes (the {y}/{n} path segments, slug-decoded)."""
    return "%s:%s" % (path.parent.name,
                      path.name[:-len(".versions.json")].replace("_", " "))


def fa_record(basefile):
    typ, rest = basefile.split("/", 1)
    return FA_DOWNLOADED / typ / (rest + ".json")


# on-demand page facsimiles (rendered PNGs of source-PDF pages), keyed like the
# downloaded tree. A pure cache: rebuildable from the PDF at any time, evicted
# by an external process (never by this codebase).
FACSIMILE = DATA / "cache" / "facsimile"


def facsimile(source, basefile, page):
    """The cached facsimile PNG of one source-PDF page:
    ``cache/facsimile/<source>/<relpath>/sid<N>.png``."""
    return FACSIMILE / source / relpath(source, basefile) / ("sid%d.png" % page)


def facsimile_crop(source, basefile, page, bbox):
    """The cached PNG of one cropped region of a source-PDF page:
    ``cache/facsimile/<source>/<relpath>/sid<N>-<x>_<y>_<w>_<h>.png``. Keyed by
    the bbox (rounded PDF points) so a crop never collides with the full page
    `facsimile` sibling and a re-verified bbox lands on a fresh file."""
    x0, y0, x1, y1 = (round(v) for v in bbox)
    name = "sid%d-%d_%d_%d_%d.png" % (page, x0, y0, x1 - x0, y1 - y0)
    return FACSIMILE / source / relpath(source, basefile) / name


def fa_ocr_pdf(typ, basefile):
    """The re-OCR sidecar PDF for a förarbete document (§7g): ``ocr/forarbete/
    <type>/<slug>.pdf``, slugged exactly like the downloaded record. Dropping a
    modern-OCR'd PDF here (an ``ocrmypdf`` pass over a frozen scan whose embedded
    OCR layer is weak) upgrades that document's parse -- parse prefers it over the
    legacy-root scan -- without touching the one-time import. The path is a parse
    input, so a new sidecar re-stales that document's parse."""
    return OCR / "forarbete" / typ / (basefile_slug(basefile) + ".pdf")


def eurlex_dir(basefile):
    """The per-CELEX directory holding eurlex's raw files (notice.ttl + the
    per-language manifestations)."""
    return EURLEX_DOWNLOADED / relpath("eurlex", basefile)


# --------------------------------------------------------------------------
# remisser -- case records and answer PDFs share one download tree (a case's
# open/closed state is downloader-only, so the record is plain download-stage
# data, not a stage of its own). The filename grammar lives here so both the
# harvester (writer) and build.py (reader) derive the same paths.
# --------------------------------------------------------------------------

def remisser_case(basefile):
    """One stored case record: ``downloaded/remisser/<case-slug>.json`` -- the
    Remiss source of truth, beside its answer PDFs (the sibling <case-slug>/ dir)."""
    return REMISSER_DOWNLOADED / (basefile + ".json")


def remisser_answer(case_basefile, org_slug):
    """One downloaded answer PDF: ``downloaded/remisser/<case-slug>/<org-slug>.pdf``."""
    return REMISSER_DOWNLOADED / case_basefile / (org_slug + ".pdf")


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


def source_url(source, basefile):
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
    elif loc.startswith("ext/coe/"):
        return "coe/%s.html" % _alnum_slug(loc[len("ext/coe/"):])
    elif loc.startswith("ext/icrc/"):
        return "icrc/%s.html" % _alnum_slug(loc[len("ext/icrc/"):])
    elif loc.startswith("ext/untc/"):
        return "untc/%s.html" % _alnum_slug(loc[len("ext/untc/"):])
    elif loc.startswith("ext/icc/"):
        return "icc/%s.html" % _alnum_slug(loc[len("ext/icc/"):])
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
    elif loc.startswith("avg/"):
        # a JO/JK decision, lagen.nu's /avg/{org}/{dnr} grammar (the URI the
        # MYNDIGHETSBESLUT citations mint): avg/jo/2340-2025 -> avg/jo_2340-2025.html
        _, _, rest = loc.partition("/")
        return "avg/%s.html" % rest.replace("/", "_")
    elif loc.startswith("om/"):
        # an editorial about page: /om/english -> om/english.html (the slug is
        # already filesystem-safe). Explicit rather than leaning on the SFS
        # else-branch's incidental passthrough.
        return "%s.html" % loc
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
    if loc.startswith("ext/coe/"):
        return "/coe/" + loc[len("ext/coe/"):]
    if loc.startswith("ext/icrc/"):
        return "/icrc/" + loc[len("ext/icrc/"):]
    if loc.startswith("ext/untc/"):
        return "/untc/" + loc[len("ext/untc/"):]
    if loc.startswith("ext/icc/"):
        return "/icc/" + loc[len("ext/icc/"):]
    return "/" + loc


def url_to_relpath(path):
    """Inverse of page_url: the on-disk static file for a public lagen.nu URL path.
    The path is a document's URI local form, so reattach the host and reuse the
    page_relpath rule; /celex/<id> is the public address of ext/celex/<id>."""
    loc = unquote(path).lstrip("/")
    # the path is an attacker-controlled request: refuse traversal-shaped
    # segments here (no rewrite -> the miss stays a 404) rather than relying
    # on the static server's containment check alone
    if ".." in loc.split("/"):
        return None
    if loc.startswith("celex/"):
        loc = "ext/celex/" + loc[len("celex/"):]
    elif loc.startswith("coe/"):
        loc = "ext/coe/" + loc[len("coe/"):]
    elif loc.startswith("icrc/"):
        loc = "ext/icrc/" + loc[len("icrc/"):]
    elif loc.startswith("untc/"):
        loc = "ext/untc/" + loc[len("untc/"):]
    elif loc.startswith("icc/"):
        loc = "ext/icc/" + loc[len("icc/"):]
    return page_relpath(BASE + loc)


# a föreskrift loc is "<fs>/<år>:<nr>"; every författningssamling code ends in FS
# (fffs, nfs, kifs, …), which sets it apart from an SFS loc ("2013:635")
_FORESKRIFT_LOC = re.compile(r"^[a-zåäö]+fs/\d{4}:\d+$")
