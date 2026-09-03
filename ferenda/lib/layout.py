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
from . import compress, datasets
from .catalog import BASE, local, strip_fragment
from .eu_structure import revision_base
from .util import basefile_slug, confine

DATA = config.DATA
GENERATED = DATA / "generated"
# may live off data_root (config.yml: catalog_root, a fast local disk)
CATALOG = config.CATALOG_ROOT / "catalog.sqlite"

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
# NB: the reference-projection parsed/distilled "golden" oracles are temporary
# scaffolding, deliberately NOT a data_root stage -- they live in the old
# checkout (see tools/corpus/golden_dv*.py, which take an oracle path arg).

# the on-disk source-dir name under each stage; "dv" -> "dom" (see above)
SOURCE_DIR = {"sfs": "sfs", "dv": "dom", "forarbete": "forarbete",
              "eurlex": "eurlex", "foreskrift": "foreskrift", "avg": "avg",
              "rs": "rs", "guidance": "guidance", "lawreview": "lawreview",
              "hudoc": "hudoc", "coe": "coe", "icrc": "icrc", "untc": "untc",
              "icc": "icc", "icj": "icj",
              "remisser": "remisser", "kommentar": "kommentar",
              "begrepp": "begrepp", "site": "site", "stats": "stats"}


def artifact_dir(source: str) -> Path:
    """The parsed-artifact directory of a source: ``artifact/<source>``."""
    return ARTIFACT / SOURCE_DIR[source]


# Which sources put documents in the catalog. The three left out parse plenty
# and catalogue nothing, each on purpose: `remisser` holds 80k consultation
# responses we do not publish, `site` and `stats` render editorial pages rather
# than corpus documents. Named here, next to the artifact layout both readers
# share, because two of them ask: each source's `artifacts` lister (the set
# `build.py` asserts against this tuple) builds relate/index/dump's
# work list from it, and the ops dashboard reads it to tell "parsed but never
# catalogued" (a real fault) from "parsed and never meant to be" (these three).
CATALOGUED_SOURCES = ("sfs", "dv", "forarbete", "kommentar", "begrepp",
                      "eurlex", "foreskrift", "avg", "rs", "guidance",
                      "lawreview", "hudoc", "coe", "icrc", "untc", "icc",
                      "icj")


# raw roots -- the download writers put their structure under these
SFS_DOWNLOADED = DOWNLOADED / "sfs"
SFS_ARTIFACT = ARTIFACT / "sfs"                     # sfs artifacts + sidecars + archive/
DOM_DOWNLOADED = DOWNLOADED / "dom"                 # dv api records
DV_LEGACY_DOWNLOADED = DOWNLOADED / "dv"            # dv legacy store
FA_DOWNLOADED = DOWNLOADED / "forarbete"
EURLEX_DOWNLOADED = DOWNLOADED / "eurlex"
FORESKRIFT_DOWNLOADED = DOWNLOADED / "foreskrift"   # <fs>/<slug>.{json,pdf}
AVG_DOWNLOADED = DOWNLOADED / "avg"                 # <org>/<slug>.{json,pdf,html}
RS_DOWNLOADED = DOWNLOADED / "rs"                   # <org>/<slug>.{json,pdf}
GUIDANCE_DOWNLOADED = DOWNLOADED / "guidance"       # <utgivare>/<slug>.{json,pdf}
LAWREVIEW_DOWNLOADED = DOWNLOADED / "lawreview"     # <journal>/<slug>.{json,html|pdf}
HUDOC_DOWNLOADED = DOWNLOADED / "hudoc"             # <itemid>.{json,html} + clin/<itemid>.json
COE_DOWNLOADED = DOWNLOADED / "coe"                 # <CETS>.{json,pdf|html}
ICRC_DOWNLOADED = DOWNLOADED / "icrc"               # <ICRC-number>.json (JSON:API envelope)
UNTC_DOWNLOADED = DOWNLOADED / "untc"               # <UNTS-no>.{html,text.html|pdf} (status page + authentic text)
ICC_DOWNLOADED = DOWNLOADED / "icc"                 # <doc-number>.{json,pdf} (Legal Tools record + PDF)
ICJ_DOWNLOADED = DOWNLOADED / "icj"                 # <decision-stem>.{json,pdf} (index row + Reports PDF)

# remisser's ärende records + answer PDFs share one download tree (see remisser_arende)
REMISSER_DOWNLOADED = DOWNLOADED / "remisser"

# index sidecars that live inside a source's artifact dir but are NOT corpus
# documents -- the case-law identity index and the AI-guidance discovery index.
# `artifacts()` filters them out so no consumer treats them as a document (they
# are JSON lists / index maps, not artifacts). Owned here because the artifact
# tree is layout's; guidance_discover imports GUIDANCE_INDEX rather than the
# reverse (lib must not import a vertical).
DOM_INDEX = ARTIFACT / "dom" / "identity-index.json"        # case-law identity index
# beside it: datasets.CASENUMBERS, the case-number index `dv casenumbers` writes
# (spelled there, not here -- see its comment for why)
GUIDANCE_INDEX = ARTIFACT / "kommentar" / "guidance-index.json"  # AI-guidance index


def _sfs_parts(basefile):
    year, nr = basefile.split(":", 1)
    nr = nr.replace(" ", "_")
    confine(Path(year) / nr, basefile, "sfs")     # both halves become segments
    return year, nr


def _alnum_slug(s):
    return "".join(c if c.isalnum() else "_" for c in s).strip("_")


def case_slug(case_id: str) -> str:
    """Filesystem-safe form of a DV case id ("AD 1993 nr 100" ->
    "AD_1993_nr_100"); runs of non-word characters collapse to one underscore.
    Not `_alnum_slug` (which underscores each character, "s." -> "s__"). Lives
    here, not in the dv vertical, because the path grammar is layout's."""
    return re.sub(r"[^\w]+", "_", case_id).strip("_")


def kommentar_host(basefile: str) -> str:
    """The host source a kommentar/begrepp basefile annotates. A kommentar borrows
    its host's identity (`annotates:` is an SFS number, a CELEX, an FS id or a
    förarbete id), so its artifact is filed *under that host source* -- mirroring
    the content repo's `commentary/<source>/…` layout and, crucially, reusing the
    host's own path transform so two sources can never collide on one flat name.
    The split is the same one `wiki.host_uri` makes: an FS id / förarbete id first
    (they carry a `/`), then a colon means SFS, a HUDOC item id means the
    Strasbourg case law, else a bare CELEX (eurlex)."""
    if _FORESKRIFT_LOC.match(basefile):
        return "foreskrift"
    if basefile.startswith(FORARBETE):
        return "forarbete"
    if HUDOC_ITEMID.match(basefile):
        return "hudoc"
    return "sfs" if ":" in basefile else "eurlex"


# --------------------------------------------------------------------------
# storage relpath -> artifact / downloaded
# --------------------------------------------------------------------------

def relpath(source: str, basefile: str) -> Path:
    """The filesystem-safe storage sub-path of a document, shared by its
    downloaded and artifact trees where both are rule-based. The result is
    confined to the source's own tree (`_confine`); half the sources below put
    the basefile into the path verbatim."""
    return confine(_relpath(source, basefile), basefile, source)


def _relpath_sfs(basefile):
    year, nr = _sfs_parts(basefile)
    return Path(year) / nr


def _relpath_dv(basefile):
    return Path(case_slug(basefile))


def _relpath_forarbete(basefile):
    typ, rest = basefile.split("/", 1)
    return Path(typ) / _fa_year(rest) / rest


def _relpath_eurlex(basefile):
    return Path(basefile[1:5]) / basefile.replace("/", "_")


def _relpath_foreskrift(basefile):
    fs, rest = basefile.split("/", 1)            # "fffs/2013:10"
    return Path(fs) / rest.replace(":", "-").replace(" ", "_")


def _relpath_org(basefile):
    """avg, rs and lawreview: "jo/2340-2025", "jk/2024/8082" -- and, for rs, the
    agency's own ställningstagande number: "fk/2025:01", "kfm/1-23-VER"; for
    lawreview, the journal's issue coordinates: "svjt/2026-104",
    "jp/2025-01-03"."""
    org, rest = basefile.split("/", 1)
    return Path(org) / rest.replace("/", "-").replace(":", "-")


def _relpath_verbatim(basefile):
    """The basefile is already a clean, filesystem-safe path, used as it stands:

    * guidance -- "<utgivare>/<serie>/<nummer>" (the series slug normalises the
      number), kept nested rather than flattened so the artifact tree reads like
      the URI: guidance/edpb/riktlinjer/05-2020, guidance/eba/gl/2021-05
    * the folkrätt sources -- one identifier per document
    * site and stats -- editorial pages under fixed basefiles (`frontpage`,
      `sitenews`, `om/<slug>`) and the single corpus-measurement artifact
      (`statistik`)
    """
    return Path(basefile)


def _relpath_remisser(basefile):
    """``<typ>/<referred document id>/<org-slug>`` -- the ärende is keyed on the
    document it sends out ("sou/2026:14", "pm/LI2026/01339"), so the id itself
    may contain a slash; the org is always the last segment."""
    typ, rest = basefile.split("/", 1)
    ident, _, org = rest.rpartition("/")
    return Path(typ) / basefile_slug(ident) / org


def _relpath_kommentar(basefile):
    """File the annotation under its host source, reusing that source's
    transform: sfs/2009/400, eurlex/2023/32023R2854 -- so a commentary on SFS
    2009:400 and one on a same-slug act in another source never collide."""
    host = kommentar_host(basefile)
    return Path(host) / _relpath(host, basefile)


def _relpath_begrepp(basefile):
    """Concept names are their own namespace (no host); keep the flat slug."""
    return Path(_alnum_slug(basefile))


# source -> its basefile→sub-path rule. Half the sources share `_relpath_verbatim`.
_RELPATH = {"sfs": _relpath_sfs, "dv": _relpath_dv,
            "forarbete": _relpath_forarbete, "eurlex": _relpath_eurlex,
            "foreskrift": _relpath_foreskrift, "avg": _relpath_org,
            "rs": _relpath_org, "lawreview": _relpath_org,
            "guidance": _relpath_verbatim, "hudoc": _relpath_verbatim,
            "coe": _relpath_verbatim, "icrc": _relpath_verbatim,
            "untc": _relpath_verbatim, "icc": _relpath_verbatim,
            "icj": _relpath_verbatim, "site": _relpath_verbatim,
            "stats": _relpath_verbatim, "remisser": _relpath_remisser,
            "kommentar": _relpath_kommentar, "begrepp": _relpath_begrepp}


def _relpath(source: str, basefile: str) -> Path:
    try:
        rule = _RELPATH[source]
    except KeyError:
        raise ValueError("unknown source %r" % source) from None
    return rule(basefile)


def artifact(source: str, basefile: str) -> Path:
    """The parsed-artifact path: ``artifact/<source>/<relpath>.json``."""
    rel = relpath(source, basefile)
    return artifact_dir(source) / rel.with_name(rel.name + ".json")


def stats_snapshot(generated: str) -> Path:
    """Where one day's corpus measurement is archived beside the current one:
    ``artifact/stats/archive/statistik-<YYYY-MM-DD>.json``.

    `compute` overwrites the live artifact every run, which answers "what does
    the corpus look like now" and destroys "what did it look like then" -- and
    the series is the interesting part of a measurement that only ever runs on
    the whole corpus. Keyed on the report's own `generated` date, so several
    computes in one day settle on that day's figure rather than accumulating.

    The `archive/` subdirectory is safe here only because nothing walks the
    stats artifact tree: `stats` registers no artifacts lister and its source lists
    the single basefile `statistik` verbatim, so relate/dump never glob it. Do
    not copy this layout into a source whose artifacts *are* walked."""
    return artifact_dir("stats") / "archive" / ("statistik-%s.json" % generated)


def resolve_basefile(source: str, basefile: str, *alternates: str) -> str:
    """`basefile` respelled the way the artifact tree actually spells it;
    `basefile` unchanged when nothing matches.

    `alternates` are further basefiles the same document may be filed under,
    tried in order after `basefile` itself. A source that mints identity from
    one page cannot always tell which spelling another source minted from a
    different page: regeringen.se keys a departementspromemoria on its
    diarienummer when its /rattsliga-dokument/ listing states one and on the
    landing-page slug when it doesn't, and a remiss page states neither -- it
    carries its *own* dnr, which usually but not always coincides. Passing both
    candidates lets the tree settle it, instead of either source guessing.

    A cross-source join keys on an identifier one source copied out of another's
    page, and a publisher need not render it the same way twice: regeringen.se
    prints a diarienummer's department prefix as "JU2026/01595" on the remiss
    that sends a promemoria out and "Ju2026/01595" on the promemoria's own
    listing -- one document, two spellings, and the remisser -> forarbete join
    lands on whichever the remiss page happened to use. Case is the *only*
    licence granted: a name matching nothing on disk, or matching two files at
    once, comes back untouched so the caller's own missing-artifact error still
    names exactly what it looked for (rule:fail-fast).

    Returns a basefile rather than a path because callers need both -- the
    artifact to read and the ``artifact:<source>/<basefile>`` key that records
    having read it (lib.annstore) -- and those must agree."""
    for candidate in (basefile, *alternates):
        resolved = _respell(source, candidate)
        if resolved is not None:
            return resolved
    return basefile


def _respell(source, basefile):
    """`basefile` as the artifact tree spells it, or None when the tree holds no
    such document. Case is the only licence granted (see `resolve_basefile`)."""
    path = artifact(source, basefile)
    if compress.exists(path):
        return basefile
    matches = [p for p in compress.glob(path.parent, "*.json")
               if p.name.lower() == path.name.lower()]
    if len(matches) != 1:
        return None
    head, _, tail = basefile.rpartition("/")
    stem = matches[0].name.removesuffix(".json")
    if stem.lower() != tail.lower():
        # the tree names this document by something other than the basefile's
        # own last segment (the per-source `relpath` rule rewrote it), so the
        # on-disk spelling can't be spliced back
        return None
    return "%s/%s" % (head, stem) if head else stem


# --------------------------------------------------------------------------
# patch files -- curated, version-controlled fixes to a document's raw/
# intermediate source, applied at parse time (see lib/patch.py). Unlike the
# downloaded/artifact trees these are hand-authored knowledge that must be
# reviewable, so they live in a *checkout*, not under DATA. Keyed by the same
# (source, basefile) -> relpath rule as artifact(), so a document's patch sits
# at a predictable location.
#
# They sit in the content repo (`config.WIKI_ROOT`) beside `commentary/`,
# `concept/` and `ann/`, not in this code repo: a patch is hand-authored
# editorial knowledge about one document, the same kind of thing as a
# commentary, and the /patch editor commits what it writes exactly as the
# commentary editor does. One repo for everything the running site writes also
# means one mount and one push -- the deployed image is built without `.git`,
# so an in-image tree could neither commit nor survive a redeploy.
# --------------------------------------------------------------------------
PATCHES = config.WIKI_ROOT / "patches"


def patch(source: str, basefile: str, suffix: str = ".patch") -> Path:
    """The patch-file path for a document: ``patches/<source>/<relpath><suffix>``.
    `suffix` selects the variant -- ``.patch`` (plain), ``.rot18.patch`` (an
    obfuscated redaction, so removed personal data is not itself plain-text
    googleable in the committed patch) or ``.desc`` (a multi-line description
    sidecar)."""
    # An absent patch tree is indistinguishable from "this document has no
    # patch": find_patch would return None for every document, and a redaction
    # would be dropped -- silently republishing the personal data it removes.
    # So a missing tree fails here rather than parsing on. (rule:fail-fast)
    assert PATCHES.is_dir(), (
        "patch tree %s missing -- WIKI_ROOT (%s) must point at a checkout of "
        "the content repo; an absent tree drops every redaction without a word"
        % (PATCHES, config.WIKI_ROOT))
    rel = relpath(source, basefile)
    return PATCHES / source / rel.with_name(rel.name + suffix)


# non-document json files that share a source's artifact dir: the index sidecars
# (by basename, so the filter is independent of where ARTIFACT is rooted), the
# sfs `.versions.json` historical-consolidation sidecars and the föreskrift
# `.grund.json` as-enacted sidecars -- extra *pages*, not corpus documents, so
# relate/dump must never see them.
_NON_ARTIFACT_NAMES = frozenset({DOM_INDEX.name, datasets.CASENUMBERS.name,
                                 GUIDANCE_INDEX.name})


def _is_document_artifact(path, root):
    """Whether one json file under a source's artifact dir is a corpus document:
    not an index sidecar, not a per-document `.versions`/`.grund` sidecar, and
    not part of the `archive/` consolidation subtree (sfs's superseded wordings
    -- tens of thousands of files that are extra *pages*, never documents; see
    `sfs_version_file`). Everything a source publishes lives at the depth
    `relpath` puts it at, so the archive is excluded by its subtree, not by
    guessing at a file's name."""
    return (path.name not in _NON_ARTIFACT_NAMES
            and not path.name.endswith(".versions.json")
            and not path.name.endswith(".grund.json")
            and "archive" not in path.relative_to(root).parts[:-1])


def artifacts(source: str) -> list[Path]:
    """Every parse artifact of `source` on disk, sorted -- the iteration
    companion to `artifact`, so the tree layout has one home and a consumer
    can't drift out of sync with it by hand-globbing. Non-document json that
    happens to live in the artifact dir is excluded -- the identity/guidance
    index sidecars, the sfs `.versions.json` / föreskrift `.grund.json` layers
    and the whole `archive/` subtree of superseded consolidations.

    Artifacts are stored precompressed (lib/compress), so a document may be on
    disk as `.json`, `.json.br` or `.json.gz`; each is mapped back to its logical
    `.json` path (deduplicated). The transparent read/stat helpers resolve that
    logical path to whatever variant is present, so every consumer keeps working
    on logical paths regardless of the on-disk storage format."""
    root = artifact_dir(source)
    logical = {compress.logical(p) for suffix in ("", *compress.SUFFIXES)
               for p in root.glob("**/*.json" + suffix)}
    return sorted(p for p in logical if _is_document_artifact(p, root))


# --------------------------------------------------------------------------
# downloaded (raw). SFS keeps three raw forms under downloaded/; eurlex bundles
# many files in one per-document directory. dv and the wiki sources resolve
# their raw path through an index (api record / wiki page), so only their
# downloaded roots are exposed (above), not per-document rules.
# --------------------------------------------------------------------------

def sfs_source(basefile: str) -> Path:               # new beta-API JSON (the primary form)
    year, nr = _sfs_parts(basefile)
    return SFS_DOWNLOADED / year / (nr + ".json")


def sfs_fetched() -> Path:                      # {basefile: "YYYY-MM-DD"} last-fetch map
    # the last date each act was fetched from the upstream ES passthrough, bumped
    # on every fetch (incl. an unchanged one, which the content-hashed source file
    # cannot record) -- the "Senast hämtad" the SFS page shows (C1)
    return SFS_DOWNLOADED / ".fetched.json"


def sfs_sfst(basefile: str) -> Path:                 # legacy consolidated-text HTML
    year, nr = _sfs_parts(basefile)
    return SFS_DOWNLOADED / "sfst" / year / (nr + ".html")


def sfs_sfsr(basefile: str) -> Path:                 # legacy register HTML
    year, nr = _sfs_parts(basefile)
    return SFS_DOWNLOADED / "sfsr" / year / (nr + ".html")


def sfs_pdf_dir() -> Path:                      # the facsimile mirror's root (+ its harvest state)
    return SFS_DOWNLOADED / "pdf"


def sfs_pdf(basefile: str) -> Path:                  # officially published SFS PDF (facsimile source)
    year, nr = _sfs_parts(basefile)
    return sfs_pdf_dir() / year / (nr + ".pdf")


# --------------------------------------------------------------------------
# sfs archive -- superseded consolidations. Each stage keeps its own archive/
# subtree (downloaded/sfs/archive for raw, artifact/sfs/archive for parsed), in
# a per-document .versions/ layout. A version id is the SFS number
# of the last amendment folded into that consolidation ("2003:466" ->
# 2003/466.<ext>), or a bare archival counter ("11") where the cutoff is absent.
# --------------------------------------------------------------------------

def _sfs_version_dir(stage_dir, basefile):
    """The .versions/ tree of one statute under a stage dir's archive/ subtree
    (`stage_dir` is SFS_DOWNLOADED for raw, SFS_ARTIFACT for parsed)."""
    year, nr = _sfs_parts(basefile)
    return stage_dir / "archive" / year / nr / ".versions"


def sfs_version_file(stage_dir: Path, basefile: str, version: str) -> Path:
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
        return root / confine(Path(vyear) / ("%s.json" % vnr.replace(" ", "_")),
                              version, "sfs archive")
    return root / confine(Path("%s.json" % version.replace(" ", "_")),
                          version, "sfs archive")


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


def sfs_version_artifact(basefile: str, version: str) -> Path:
    """A parsed archived consolidation: the artifact-tree mirror of its
    download, keyed by the (possibly recovered) version id."""
    return sfs_version_file(SFS_ARTIFACT, basefile, version)


def sfs_versions_sidecar(basefile: str) -> Path:
    """The per-statute version index -- the versions stage's output, a sidecar
    next to the main artifact: which historical consolidations exist, their
    recovered version ids and their parse status."""
    rel = relpath("sfs", basefile)
    return SFS_ARTIFACT / rel.with_name(rel.name + ".versions.json")


# --------------------------------------------------------------------------
# eurlex versions -- the consolidated wordings (CONSLEG) of an EU act, keyed by
# the ISO date each wording began to apply ("2024-10-18", CELLAR's own version
# key). The raw download of a version lives under its *base act's* document dir
# (downloaded/eurlex/{year}/{celex}/.versions/{date}/, the same notice.ttl +
# content-per-language layout as the act itself); the parsed version artifacts
# live in an archive/ subtree like sfs's, so `artifacts()` never reads one as a
# corpus document.
# --------------------------------------------------------------------------

RE_EURLEX_VERSION = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _eurlex_version_id(version: str) -> str:
    """A eurlex version id, checked: the consolidation date, ISO-dashed. It
    becomes a path segment and a uri segment, so anything else raises."""
    if not RE_EURLEX_VERSION.match(version):
        raise ValueError("not a eurlex consolidation date: %r" % version)
    return version


def eurlex_version_artifact(basefile: str, version: str) -> Path:
    """A parsed consolidated version: the archive-subtree mirror of the sfs
    shape, so `_is_document_artifact` excludes it from the corpus."""
    rel = relpath("eurlex", basefile)
    return (artifact_dir("eurlex") / "archive" / rel / ".versions"
            / ("%s.json" % _eurlex_version_id(version)))


def eurlex_versions_sidecar(basefile: str) -> Path:
    """The per-act version index -- the eurlex versions stage's output, a
    sidecar next to the main artifact (the eurlex counterpart of
    `sfs_versions_sidecar`)."""
    rel = relpath("eurlex", basefile)
    return artifact_dir("eurlex") / rel.with_name(rel.name + ".versions.json")


def eurlex_sidecar_basefile(path: Path) -> str:
    """Inverse of eurlex_versions_sidecar: the CELEX a sidecar file names."""
    return path.name[:-len(".versions.json")].replace("_", "/")


# --------------------------------------------------------------------------
# versions, per source -- the one dispatch the version-history consumers (the
# renderer's compare panel, /api/v1/document/versions, /document/diff) go
# through, so a second consolidating source is a row here rather than a copy
# of the sfs plumbing in each consumer.
# --------------------------------------------------------------------------

# source -> (versions sidecar, version artifact, version sort key)
_VERSIONED = {"sfs": (sfs_versions_sidecar, sfs_version_artifact,
                      sfs_version_key),
              "eurlex": (eurlex_versions_sidecar, eurlex_version_artifact,
                         lambda version: version)}

VERSIONED_SOURCES = tuple(_VERSIONED)


def _versioned(source: str, part: int):
    try:
        return _VERSIONED[source][part]
    except KeyError:
        raise ValueError("source %r keeps no version history" % source) from None


def versions_sidecar(source: str, basefile: str) -> Path:
    return _versioned(source, 0)(basefile)


def version_artifact(source: str, basefile: str, version: str) -> Path:
    return _versioned(source, 1)(basefile, version)


def version_key(source: str, version: str):
    """Chronological sort key for a version id. A eurlex id is the ISO
    consolidation date, which sorts as itself. Raises like its sibling
    dispatchers: a third versioned source must choose its ordering here,
    not inherit string sorting silently."""
    return _versioned(source, 2)(version)


def foreskrift_grund_artifact(basefile: str) -> Path:
    """The as-enacted sidecar beside a föreskrift's main artifact: the base
    structure re-projected as its own page artifact (uri ``…/grund``), written
    by parse only when the main artifact presents a consolidation *and* the
    base text is parsed. Not a corpus document (see `_is_document_artifact`) --
    generate appends it as an extra page, like the sfs lydelse artifacts."""
    rel = relpath("foreskrift", basefile)
    return artifact_dir("foreskrift") / rel.with_name(rel.name + ".grund.json")


def foreskrift_grund_pages():
    """Every föreskrift ``/grund`` sidecar on disk -> sorted (uri, source,
    path, title) page rows for generate's extra-page plan (the föreskrift
    counterpart of build.sfs_version_pages)."""
    rows = []
    for path in sorted(compress.glob(artifact_dir("foreskrift"),
                                     "*/*.grund.json")):
        fs = path.parent.name
        year, _, nr = path.name[:-len(".grund.json")].partition("-")
        basefile = "%s/%s:%s" % (fs, year, nr)
        if not _FORESKRIFT_LOC.match(basefile):
            raise ValueError("grund sidecar %s does not decode to a "
                             "föreskrift basefile (%r)" % (path, basefile))
        rows.append(("%s%s/grund" % (BASE, basefile), "foreskrift",
                     str(path),
                     "%s %s:%s i ursprunglig lydelse" % (fs.upper(), year, nr)))
    return rows


def sfs_sidecar_basefile(path: Path) -> str:
    """Inverse of sfs_versions_sidecar: the statute basefile a sidecar file
    describes (the {y}/{n} path segments, slug-decoded)."""
    return "%s:%s" % (path.parent.name,
                      path.name[:-len(".versions.json")].replace("_", " "))


def _fa_year(slug: str) -> str:
    """The year segment of a förarbete document's on-disk sub-path, from its
    filesystem slug. Every förarbete id but ``pm`` leads with a 4-digit year (a
    riksmöte's first year for prop/bet/rskr/skr, the utgivningsår otherwise), so
    that is the segment; ``pm`` is keyed by title-slug or diarienummer with no
    year and buckets under ``_``. Segmenting by year keeps each directory small
    -- prop/bet/rskr each hold tens of thousands of files flat otherwise, the same
    reason SFS files by ``<year>/<nr>``."""
    return slug[:4] if slug[:4].isdigit() else "_"


def fa_dir(root: Path | str, typ: str, ident: str) -> Path:
    """The directory a förarbete document's record and body files share:
    ``<root>/<typ>/<year>``. `root` is a download root (``FA_DOWNLOADED``, or a
    test/scratch root); `ident` the per-type basefile (``2021:82`` or its slug),
    off which the year is read. Record and files live together, so a bare `files`
    name still resolves beside its record after segmentation."""
    return Path(root) / confine(Path(typ) / _fa_year(basefile_slug(ident)),
                                "%s/%s" % (typ, ident), "forarbete")


def fa_record_file(root: Path | str, typ: str, ident: str) -> Path:
    """The record JSON path for a förarbete document under a download `root`:
    ``<root>/<typ>/<year>/<slug>.json``. The writer-side companion to
    `fa_record` (which resolves under the global ``FA_DOWNLOADED``)."""
    return fa_dir(root, typ, ident) / (basefile_slug(ident) + ".json")


def fa_record(basefile: str) -> Path:
    typ, rest = basefile.split("/", 1)
    return fa_record_file(FA_DOWNLOADED, typ, rest)


# on-demand page facsimiles (rendered PNGs of source-PDF pages), keyed like the
# downloaded tree. A pure cache: rebuildable from the PDF at any time, evicted
# by an external process (never by this codebase).
FACSIMILE = DATA / "cache" / "facsimile"

# poppler conversions of source PDFs (`pdftohtml -xml`, `pdftotext`), brotli-
# compressed. Same contract as FACSIMILE -- a pure cache, rebuildable from the
# PDF, safe to delete. It exists because those subprocesses are the dominant
# cost of parsing a PDF-bodied document and their input never changes: a
# downloaded PDF is immutable, so re-running the converters on every re-parse is
# pure waste. The output is far *smaller* than the PDF (a 120 MB scan yields
# 40 kB of XML, since a scan carries almost no text), so the whole cache costs
# about 1% of the downloaded bytes.
PDFCONV = DATA / "cache" / "pdfconv"


def pdf_conversion(pdf_path, kind):
    """The cache path for one conversion of one PDF, mirroring the file's own
    location under `DATA` so it is obvious which PDF an entry belongs to.
    `kind` names the conversion -- "xml" (`pdftohtml -xml`), "hidden.xml" (the
    same with the invisible OCR layer) or "txt" (`pdftotext`) -- so the
    conversions never share an entry."""
    path = Path(pdf_path).resolve()
    if not path.is_relative_to(DATA.resolve()):
        # a PDF outside the data root (a test fixture, an ad-hoc path) has no
        # stable place in the cache tree, so it is simply not cached
        return None
    rel = path.relative_to(DATA.resolve())
    return PDFCONV / rel.with_suffix(".%s.br" % kind)


def facsimile(source: str, basefile: str, page: int) -> Path:
    """The cached facsimile PNG of one source-PDF page:
    ``cache/facsimile/<source>/<relpath>/sid<N>.png``."""
    return FACSIMILE / source / relpath(source, basefile) / ("sid%d.png" % page)


def facsimile_crop(source, basefile, page, bbox, dpi):
    """The cached PNG of one cropped region of a source-PDF page:
    ``cache/facsimile/<source>/<relpath>/sid<N>-<x>_<y>_<w>_<h>@<dpi>.png``.
    Keyed by the bbox (rounded PDF points) so a crop never collides with the full
    page `facsimile` sibling and a re-verified bbox lands on a fresh file, and by
    the render resolution so raising it lands on fresh files too -- the cache is
    evicted by an external process, so a stale entry under a reused name would
    otherwise be served at the old resolution for as long as it survives."""
    x0, y0, x1, y1 = (round(v) for v in bbox)
    name = "sid%d-%d_%d_%d_%d@%d.png" % (page, x0, y0, x1 - x0, y1 - y0, dpi)
    return FACSIMILE / source / relpath(source, basefile) / name


def fa_ocr_pdf(typ: str, basefile: str) -> Path:
    """The re-OCR sidecar PDF for a förarbete document (§7g): ``ocr/forarbete/
    <type>/<slug>.pdf``, slugged exactly like the downloaded record. Dropping a
    modern-OCR'd PDF here (an ``ocrmypdf`` pass over a frozen scan whose embedded
    OCR layer is weak) upgrades that document's parse -- parse prefers it over the
    legacy-root scan -- without touching the one-time import. The path is a parse
    input, so a new sidecar re-stales that document's parse."""
    return fa_dir(OCR / "forarbete", typ, basefile) / (basefile_slug(basefile) + ".pdf")


def fa_facsimile_pdf(typ: str, basefile: str) -> Path:
    """The page-image PDF a förarbete document's facsimile renders from:
    ``downloaded/forarbete/<type>/<slug>.pdf``, beside the record and slugged like
    it. Raw fetched bytes, so it lives in the download tree (`.pdf` -> stored
    plain).

    Deliberately a *rule*, not a record field, and deliberately NOT a parse input
    -- the same bargain as `sfs_pdf` (the mirrored SFS PDFs `api._sfs_pdf`
    resolves by existence alone). A record's `files` says what parse reads; this
    says what a facsimile rasterizes, and the two are only sometimes the same
    file. That split is what lets the propkb scan fetch (`forarbete/propkb.py`)
    add 17k page-image PDFs without rewriting 17k records -- a record rewrite
    would change its content hash and re-stale 17k parses (`build.hash_files`
    over `fa_parse_inputs`) for bytes parse never reads.

    For a document whose body already *is* a PDF the rule resolves to that same
    file, so it is consistent rather than merely non-conflicting."""
    return fa_dir(FA_DOWNLOADED, typ, basefile) / (basefile_slug(basefile) + ".pdf")


def eurlex_dir(basefile: str) -> Path:
    """The per-CELEX directory holding eurlex's raw files (notice.ttl + the
    per-language manifestations)."""
    return EURLEX_DOWNLOADED / relpath("eurlex", basefile)


# --------------------------------------------------------------------------
# remisser -- ärende records and answer PDFs share one download tree (an ärende's
# open/closed state is downloader-only, so the record is plain download-stage
# data, not a stage of its own). The filename grammar lives here so both the
# harvester (writer) and build.py (reader) derive the same paths.
# --------------------------------------------------------------------------

def remisser_arende(basefile: str) -> Path:
    """One stored ärende record: ``downloaded/remisser/<typ>/<id-slug>.json`` --
    the Remiss source of truth, beside its answer PDFs (the sibling
    ``<id-slug>/`` dir). `basefile` is the referred document's own identity,
    ``"<typ>/<identifier>"`` (``"sou/2026:14"``, ``"pm/LI2026/01339"``)."""
    typ, ident = basefile.split("/", 1)
    return REMISSER_DOWNLOADED / confine(
        Path(typ) / (basefile_slug(ident) + ".json"), basefile, "remisser")


def remisser_answer(arende_basefile: str, org_slug: str) -> Path:
    """One downloaded answer PDF:
    ``downloaded/remisser/<typ>/<id-slug>/<org-slug>.pdf`` -- the same relpath
    rule the artifact tree uses (`relpath`), so record, PDF and artifact all
    agree on where a given answer lives."""
    typ, ident = arende_basefile.split("/", 1)
    return REMISSER_DOWNLOADED / confine(
        Path(typ) / basefile_slug(ident) / (org_slug + ".pdf"),
        arende_basefile, "remisser")


# the bookkeeping of which regeringen.se remiss *ärende pages* have been examined,
# keyed by their URL slug -> the basefile minted for them (null when the ärende was
# passed over, e.g. an externally authored document). The listing names ärenden by
# URL slug while the corpus keys them by referred document, so this is what lets
# an incremental walk stop without re-fetching every ärende page. Derived state:
# delete it to force a full re-examination of the archive.
REMISSER_SEEN = REMISSER_DOWNLOADED / ".seen.json"
# which ärenden `ai-analyze` has run over, so `--update` can come back to one
# whose answers arrived after its analysis. Bookkeeping, not authored output, so
# it lives beside the download state rather than in the curated store: a marker
# per ärende there would rewrite ~2,300 git-tracked files on every pass.
REMISSER_ANALYSED = REMISSER_DOWNLOADED / ".analysed.json"


# --------------------------------------------------------------------------
# public URL / generated page
# --------------------------------------------------------------------------

# förarbete uri prefixes (prop/2025/26:161, sou/2020:1, …) -- each routes to its
# own top-level segment (/prop/…, /sou/…), lagen.nu's grammar, not a shared /fa/
FORARBETE = ("prop/", "sou/", "ds/", "dir/", "fm/", "skr/", "so/", "lr/",
             "bet/", "rskr/")

# uri namespaces keyed by the number the publisher prints -- a CELEX, a CETS
# number, an ICRC/UNTC/ICC/ICJ number -- each its own top-level segment, which
# is also the served path: celex/32016R0679 is the file eurlex/32016R0679.html
# served at /celex/32016R0679. (Until 2026-08-29 these ids carried an extra
# ext/ segment in the *canonical uri* only; identifier and address are now one
# grammar.) One table drives the location rules (page_relpath, url_to_relpath)
# so a new namespace is a row, not three edits: namespace -> (generated-page
# dir, file-name slug rule). Only celex deviates -- its dir is the source name
# and a CELEX's own '/' (12016E/TXT) is the only character to fold, so it keeps
# the id's case.
_EXT_NS = {"celex": ("eurlex", lambda rest: rest.replace("/", "_")),
           "coe": ("coe", _alnum_slug),
           "icrc": ("icrc", _alnum_slug),
           "untc": ("untc", _alnum_slug),
           "icc": ("icc", _alnum_slug),
           "icj": ("icj", _alnum_slug)}
# ... the segment names alone, for the one consumer that asks whether a uri is
# in a namespace whose publisher has a page to link out to when the corpus does
# not hold the document (`page._is_external`)
EXT_NAMESPACES = frozenset(_EXT_NS)


# --------------------------------------------------------------------------
# authoritative source url -- a document's canonical location at the publisher,
# where one is derivable by rule from its identity. Sources whose source url is
# *not* rule-derivable (e.g. a regeringen.se landing page) record it at download
# time instead; source_url returns None for them and stage.write_artifact stamps
# the recorded url. Either way the artifact ends up with one uniform
# `source_url` key, which the renderer turns into the page's "Källa" link.
# --------------------------------------------------------------------------

EURLEX_ELI = "https://eur-lex.europa.eu/eli/%s/%s/%s/oj"
EURLEX_CELEX = "https://eur-lex.europa.eu/legal-content/SV/TXT/?uri=CELEX:%s"
_ELI_TYPE = {"R": "reg", "L": "dir", "D": "dec"}     # CELEX act descriptor -> ELI
SFS_ITEM = ("https://beta.rkrattsbaser.gov.se/sfs/item"
            "?bet=%s&tab=forfattningstext")
DV_PUBLICERING = "https://rattspraxis.etjanst.domstol.se/sok/publicering/%s"


def dv_source_url(gruppkorrelationsnummer: str) -> str:
    """A case's page in the courts' public publication search. Keyed by the API
    record's gruppKorrelationsnummer (the publication group, not the record id),
    so this lives off record data -- build.dv_parse_run passes it in."""
    return DV_PUBLICERING % gruppkorrelationsnummer


def _eurlex_source_url(celex: str) -> str:
    """An EU act's canonical EUR-Lex address from its CELEX. Sector-3
    regulations, directives and decisions have an ELI -- e.g. 32023R2854 ->
    https://eur-lex.europa.eu/eli/reg/2023/2854/oj (leading zeros stripped from
    the number). Everything else (judgments, treaties, other act descriptors,
    and a corrigendum, which is published under the corrected act's own ELI and
    has none of its own) has no ELI, so fall back to the stable CELEX
    legal-content url.

    The revision test is what keeps 32022L2555R(04) off
    ".../eli/dir/2022/2555R(04)/oj", which EUR-Lex answers 404: every one of the
    corpus's corrigenda was linked to a page that does not exist."""
    eli_type = _ELI_TYPE.get(celex[5]) if len(celex) > 5 else None
    if celex.startswith("3") and eli_type and not revision_base(celex):
        return EURLEX_ELI % (eli_type, celex[1:5], celex[6:].lstrip("0") or "0")
    return EURLEX_CELEX % celex


def source_url(source: str, basefile: str) -> str | None:
    """The authoritative publisher url for a document, derived by rule from its
    identity where possible, else None -- in which case the downloader-recorded
    url is used instead (see stage.write_artifact)."""
    if source == "eurlex":
        return _eurlex_source_url(basefile)
    if source == "sfs":
        return SFS_ITEM % quote(basefile, safe="")
    return None


def page_relpath(uri: str) -> str:
    """The generated HTML file for a document uri, by uri shape -- lagen.nu's URL
    grammar: dv at dom/, förarbeten under their type segment (prop/, sou/, …), EU
    acts under eurlex/ (the CELEX kept intact). A statute is a *top-level* page
    named by its bare SFS id with the colon kept (2018:585 -> 2018:585.html), so
    it is served at lagen.nu's /2018:585 address (see `page_url`)."""
    loc = local(strip_fragment(uri))
    ns, sep, rest = loc.partition("/")
    if sep and ns in _EXT_NS:
        directory, slug = _EXT_NS[ns]
        return "%s/%s.html" % (directory, slug(rest))
    if loc.startswith("dom/"):
        prefix = "dom"
    elif loc.startswith("kommentar/"):
        prefix = "kommentar"
    elif loc.startswith("begrepp/"):
        prefix = "begrepp"
    elif loc.startswith(FORARBETE):
        # keep the type as the top-level segment, slug only the rest:
        # prop/2024/25:1 -> prop/2024_25_1.html (served at /prop/…)
        typ, _, rest = loc.partition("/")
        return "%s/%s.html" % (typ, _alnum_slug(rest))
    elif _FORESKRIFT_PAGE.match(loc):
        # an agency regulation, lagen.nu's /{fs}/{år}:{nr} grammar -- the
        # författningssamling is the top segment: fffs/2013:10 ->
        # fffs/2013_10.html; the as-enacted view beside a presented
        # consolidation rides the same rule (…/grund -> fffs/2013_10_grund.html)
        fs, _, rest = loc.partition("/")
        return "%s/%s.html" % (fs, _alnum_slug(rest))
    elif loc.startswith("avg/"):
        # a JO/JK decision, lagen.nu's /avg/{org}/{dnr} grammar (the URI the
        # MYNDIGHETSBESLUT citations mint): avg/jo/2340-2025 -> avg/jo_2340-2025.html
        _, _, rest = loc.partition("/")
        return "avg/%s.html" % rest.replace("/", "_")
    elif loc.startswith("rs/"):
        # a rättsligt ställningstagande, the same /{source}/{org}/{nummer}
        # grammar as a decision: rs/fk/2025:01 -> rs/fk_2025:01.html
        _, _, rest = loc.partition("/")
        return "rs/%s.html" % rest.replace("/", "_")
    elif loc.startswith("om/"):
        # an editorial about page: /om/english -> om/english.html (the slug is
        # already filesystem-safe). Explicit rather than leaning on the SFS
        # else-branch's incidental passthrough.
        return "%s.html" % loc
    elif loc.startswith("subdomain/"):
        # a definite-form subdomain's own standalone page
        # (PRD-subdomains.md): subdomain/lagen.nu/jante ->
        # subdomain/lagen.nu/jante.html, so ferenda/subdomains.py's symlink
        # step finds it the same way it finds every other whole-act target.
        # Explicit for the same reason the om/ branch is.
        return "%s.html" % loc
    else:
        # SFS: a top-level page, the SFS id kept verbatim (colon and all). The id
        # is already filesystem-safe (digits, ':', '_', '.'): 1827:60_s.1007.
        return "%s.html" % loc
    return "%s/%s.html" % (prefix, _alnum_slug(loc))


def page_url(uri: str) -> str:
    """The public URL a link points at -- lagen.nu's URI grammar: the document's
    host-stripped local path, served bare (no .html). A statute is /2018:585, a
    proposition /prop/2020/21:22, a case /dom/ad/1993:100, an EU act
    /celex/<celexid>. The canonical uri's path IS the served path -- the old
    ext/ identifier prefix is gone. The static server maps these back to the
    flattened on-disk files (see url_to_relpath, api.app.SiteFiles)."""
    return "/" + local(strip_fragment(uri))


def page_uri(path: str) -> str:
    """Inverse of page_url: the document uri a public lagen.nu URL path
    addresses -- the path reattached to the host, since uri path and served
    path are one grammar."""
    return BASE + unquote(path).lstrip("/")


def url_to_relpath(path: str) -> str | None:
    """Inverse of page_url: the on-disk static file for a public lagen.nu URL path.
    The path is a document's URI local form, so reattach the host and reuse
    the page_relpath rule."""
    # the path is an attacker-controlled request: refuse traversal-shaped
    # segments here (no rewrite -> the miss stays a 404) rather than relying
    # on the static server's containment check alone
    if ".." in unquote(path).lstrip("/").split("/"):
        return None
    return page_relpath(page_uri(path))


# a föreskrift loc is "<fs>/<år>:<nr>"; every författningssamling code ends in FS
# (fffs, nfs, kifs, …), which sets it apart from an SFS loc ("2013:635")
# the författningssamling slug alphabet: every registered series slug ends in
# -fs except BFNAR (Bokföringsnämndens allmänna råd) and RA-MS ("rams") --
# test_layout_grammar_covers_every_registered_fs keeps this in lock-step with
# foreskrift.agencies.SAMLINGAR -- the registry keyed by *samling*, not by the
# publisher scope a CLI harvest names (layout cannot import the vertical itself)
_FS_SLUG = r"(?:[a-zåäö]+fs|bfnar|rams)"
_FORESKRIFT_LOC = re.compile(r"^%s/\d{4}:\d+$" % _FS_SLUG)
# a HUDOC item id ("001-159324"): the identity the Strasbourg case law is filed
# under, and so also the `annotates:` of a commentary on one
HUDOC_ITEMID = re.compile(r"^\d{3}-\d+$")
# a föreskrift *page* address: the document itself or its /grund view (the
# as-enacted base text beside a presented consolidation). Distinct from
# _FORESKRIFT_LOC, which stays the strict basefile/identity grammar.
_FORESKRIFT_PAGE = re.compile(r"^%s/\d{4}:\d+(/grund)?$" % _FS_SLUG)
