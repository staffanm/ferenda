"""The reader-facing name forms every document has, in one place.

For a given ``(source, artifact)`` this module answers four questions, and it is
*the* place to change how a source names itself:

===================  =========================================  ================
 field                what it is                                 shown as
===================  =========================================  ================
 ``short_id``         the bare identifier, no prose              ``div.eyebrow``
 ``short_title``      a short human name (may be '')             ``h1``
 ``official_title``   the full formal title                     dl.meta "Titel"
 ``descriptive_label`` the compact citing form (I1)              inbound/listings
===================  =========================================  ================

Each source has its own ``_<source>`` block below; to change how ICC cases are
labelled, edit ``_icc`` -- to change an SFS short title, edit ``_sfs``. Per
``rule:lib-never-imports-vertical`` this module imports no source code: it reads
the artifact dict (whose source-specific derivations were stamped at parse) plus
the shared curated datasets in ``lib/datasets.py``.

`document_labels(source, art)` returns a `Labels`; a source with no bespoke rule
falls back to `_generic` (identifier as short_id, title as everything else).
"""
import functools
import json
import re
from typing import NamedTuple

from . import datasets
from .text import sentences

# the document-uri prefix, mirrored from catalog.BASE. labels sits *below* catalog
# (catalog imports labels to stamp the `descriptive` column), so it cannot import
# it back; the local-id strip is one line, duplicated here to keep the layering acyclic.
_BASE = "https://lagen.nu/"


def _local(uri):
    return uri[len(_BASE):] if uri.startswith(_BASE) else uri


class Labels(NamedTuple):
    short_id: str          # bare identifier -> eyebrow
    short_title: str       # short human name (may be '') -> h1
    official_title: str    # full formal title -> dl.meta "Titel"
    descriptive_label: str # compact citing form -> inbound/listings


# --------------------------------------------------------------------------
# curated dataset accessors (loaded once)
# --------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _namedlaws():
    """SFS id ("2018:585") -> its established short name ("säkerhetsskyddslagen").

    Only the act that carries the name *now*. A row with an `until` records that
    an act once bore the name, which is what dates a citation written back then
    -- it is not what the act is called today, and reading it as one gave all
    three socialtjänstlagar the same short title and the same descriptive label.
    `descriptive_label` drives listings and inbound links, so the context rail
    this dating was built for would have shown three indistinguishable entries."""
    data = json.loads(datasets.NAMEDLAWS.read_text(encoding="utf-8"))
    return {lawid.replace("_", " "): entry["label"]
            for lawid, entry in data.items()
            if isinstance(entry, dict) and entry.get("label")
            and "until" not in entry}


def primary(value):
    """A dataset `label`/`abbr` may be a str or a list of variants; take the
    primary (first) one. Public because the folkrätt listing in `lib/render`
    reads the same fields off the same files (rule:second-use-goes-to-lib)."""
    return value[0] if isinstance(value, list) else value


def _named(label, abbr):
    """Compose a treaty's display name from its curated Swedish label and acronym:
    "Europakonventionen (EKMR)", or just the label when there is no acronym."""
    label, abbr = primary(label), primary(abbr)
    if label and abbr:
        return "%s (%s)" % (label, abbr)
    return label or abbr or ""


@functools.lru_cache(maxsize=None)
def treaty_names(path):
    """A hand-edited treaty names.json (coe or icrc) as {number: entry}, each
    entry carrying the informal Swedish name(s) (`label`) and acronym (`abbr`),
    either a string or a list: COE "005" -> europakonventionen / EKMR, ICRC
    "375" -> tredje Genèvekonventionen / GK III.

    One loader for both files, keyed on the path -- the two sources' name files
    have one shape, and the folkrätt listing in `lib/render` (which surfaces the
    curated instruments first) reads them the same way. Cached per file: that
    page rebuilds often."""
    return {number: entry
            for number, entry in json.loads(path.read_text("utf-8")).items()
            if isinstance(entry, dict)}


@functools.lru_cache(maxsize=1)
def _untc_names():
    """MTDSG id ("IV-9") -> {sv, abbr} (tortyrkonventionen / CAT)."""
    data = json.loads(datasets.UNTC_TREATIES.read_text(encoding="utf-8"))
    return {e["mtdsg_no"]: e for e in data["treaties"] if e.get("mtdsg_no")}


# a CELEX revision '(NN)' or corrigendum 'R(NN)' suffix -- stripped to the stem
# the curated treaty-name dataset is keyed by
_EU_TREATY_SUFFIX = re.compile(r"R?\(\d+\)$")


@functools.lru_cache(maxsize=1)
def _treaty_names():
    """CELEX stem ("12016M/TXT") -> curated Swedish name for EU primary law."""
    data = json.loads(datasets.EU_TREATIES.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


# --------------------------------------------------------------------------
# SFS
# --------------------------------------------------------------------------

_SFS_ID = re.compile(r"\s*\(\d{4}:[^)]+\)")


def _sfs(art):
    local = _local(art["uri"])
    props = art.get("metadata", {}).get("properties", {})
    official = props.get("dcterms:title") or ("SFS " + local)
    named = _namedlaws().get(local)
    if named:
        short_title = named[:1].upper() + named[1:]
    else:
        # the official title minus its "(YYYY:NN)" designation, wherever it sits:
        # "Säkerhetsskyddslag (2018:585)" -> "Säkerhetsskyddslag",
        # "Lag (2016:1145) om offentlig upphandling" -> "Lag om offentlig upphandling"
        short_title = re.sub(r"\s{2,}", " ", _SFS_ID.sub("", official)).strip()
    descriptive = named or short_title
    return Labels("SFS " + local, short_title, official, descriptive)


# --------------------------------------------------------------------------
# eurlex (acts + judgments + treaties)
# --------------------------------------------------------------------------

# a leading act designation as it appears in a stamped short label / title:
# "(EU) 2016/679", "(EG) nr 851/2004", "(EEG) nr 1408/71", "(EU) 2022/2523"
_EU_DESIGNATION = re.compile(
    r"^\((?:EU|EG|EEG|Euratom|EKSG)\)\s*(?:nr\s*)?\d[\d/]*", re.IGNORECASE)


def _eurlex(art):
    doctype = art.get("doctype")
    # the document's own title, when it has one. Much of the older case law
    # carries none -- the legacy court pages open straight into the parties,
    # with no line naming the document -- and the fallback must then be the
    # document's identity, never the URI tail: `_local(uri)` stood in here, so
    # 3 373 judgments were headed "ext/celex/61979CJ0155" instead of "C-155/79".
    title = art.get("title") or ""
    label = art.get("label") or ""
    shortname, abbr = art.get("shortname"), art.get("abbr")
    named = "%s (%s)" % (shortname, abbr) if shortname and abbr else shortname
    celex = art.get("celex") or _local(art["uri"])
    if doctype == "judgment":
        # label is the case citation ("C-311/18" or "C-311/18 (Schrems II)");
        # short_id is the bare case number, short_title the usual name
        short_id = re.sub(r"\s*\(.*\)$", "", label) or celex
        # an unnamed judgment stamps shortname == case number; no name to show
        short_title = named if named and named != short_id else ""
        return Labels(short_id, short_title, title or short_id, label or short_id)
    if doctype == "treaty":
        # a founding/consolidated treaty carries no extractable short title -- the
        # raw CELEX is all the artifact holds -- so a curated Swedish name stands in
        # as both the short title and the official title (E1); short_id is the CELEX
        name = _treaty_names().get(_EU_TREATY_SUFFIX.sub("", celex))
        return Labels(celex, name or "", name or title or celex,
                      name or title or celex)
    in_label = _EU_DESIGNATION.search(label)
    m = in_label or _EU_DESIGNATION.search(title)
    short_id = m.group(0) if m else (art.get("celex") or "")
    # short_title: the curated/extracted short name, else the descriptive tail of
    # the stamped short label (label minus its leading designation)
    short_title = named or (label[m.end():].strip() if m and label else "")
    # A short name is what the act's own page calls it, so the compact citing
    # form uses it too -- "(EU) 2016/679 Dataskyddsförordningen (GDPR)", not the
    # label's extracted tail "… Allmän dataskyddsförordning". Otherwise a rail, a
    # listing and the page name one act three ways. Only when the *label* carries
    # a real designation: `short_id` falls back to raw CELEX when it does not,
    # which would print "32003L0097" for "2003/97/EG".
    #
    # `shortname` has two producers -- the 29-entry curated table and
    # `eurlex/parse.official_short_title`, which reads a naming parenthesis off
    # any act's title -- so this is not bounded to the curated set. Where the
    # extractor is the source both strings normally come from the same
    # parenthesis and nothing moves: measured over 2,500 sampled acts, 0 changed;
    # over the curated table, 15 of 28 did.
    descriptive = ("%s %s" % (in_label.group(0), named)
                   if named and in_label else
                   (label or short_title or short_id))
    return Labels(short_id, short_title, title or short_title or short_id,
                  descriptive)


# --------------------------------------------------------------------------
# dv (Swedish case law)
# --------------------------------------------------------------------------

_DV_NAMED = re.compile(r"^(.*\S)\s+\((.+)\)$")


def dv_fallback_label(art):
    """The canonical case identity: the name-prefixed label stamped at parse time
    ("Meteoriten (NJA 2025 s. 897)"), or -- for an artifact parsed before that
    field -- the referat, else "{court} {målnummer}", else the court, else the uri
    tail. The single source of this fallback chain for both the label derivation
    here and catalog.dv_document (which is a pure consumer of it)."""
    referat, malnr = art.get("referat") or [], art.get("malnummer") or []
    return art.get("label") or (
        referat[0] if referat
        else ("%s %s" % (art.get("court", ""), malnr[0])).strip() if malnr
        else art.get("court") or _local(art["uri"]))


def _dv(art):
    # the stamped label is the canonical identity: name-prefixed for a named case
    # -- "Meteoriten (NJA 2025 s. 897)", or a pre-referat "Underhåll och lagval
    # (Högsta domstolen, mål Ö 4337-25)" -- and bare otherwise ("HFD 2011 ref. 4").
    # A named case splits into name (short_title) + id (short_id); an unnamed one
    # has no name, so the whole label is the id.
    label = dv_fallback_label(art)
    m = _DV_NAMED.match(label)
    if m:
        return Labels(m.group(2), m.group(1), label, label)
    return Labels(label, "", label, label)


# --------------------------------------------------------------------------
# generic fallback (identifier as short_id, title as the rest)
# --------------------------------------------------------------------------

def _generic(art):
    short_id = art.get("identifier") or _local(art["uri"])
    title = (art.get("title") or art.get("metadata", {}).get("title")
             or short_id)
    return Labels(short_id, "", title, short_id)


# --------------------------------------------------------------------------
# begrepp (concepts)
# --------------------------------------------------------------------------

def _kommentar(art):
    # deliberately inert: a kommentar is never a page of its own (absent from
    # build.SOURCE_RENDERERS by design) and no rail prints its name -- the rails
    # embed its *content* on the commented document's page, and the inbound
    # panel excludes the source (page.INBOUND_ORDER). Fixed forms here keep the
    # catalog columns stable ("Kommentar" + the author line, what the inbound
    # sidecar records) and stop the generic fallback's uri tail ("kommentar/
    # 1810:0926") from landing in the descriptive column.
    return Labels("Kommentar", "", art.get("author") or "Kommentar", "Kommentar")


def _begrepp(art):
    # a concept has no identifier separate from its name, so the term is all
    # four forms. Without this it fell to `_generic`, whose id-of-last-resort is
    # the uri tail -- and the *descriptive* form is what an inbound rail prints,
    # so a statute's Begrepp section read "begrepp/Misshandel" (D3).
    term = art.get("title") or _local(art["uri"]).rpartition("/")[2]
    return Labels(term, term, term, term)


# --------------------------------------------------------------------------
# forarbete (prop/sou/ds/dir/…)
# --------------------------------------------------------------------------

def _forarbete(art):
    # the identifier ("Prop. 2019/20:1") is the eyebrow id; the descriptive title
    # ("Budgetpropositionen för 2020") is the heading
    ident = art.get("identifier") or _local(art["uri"])
    title = art.get("title") or ident
    return Labels(ident, title, title, ident)


# --------------------------------------------------------------------------
# foreskrift (agency regulations)
# --------------------------------------------------------------------------

def _foreskrift(art):
    # short_id is the FS number ("FFFS 2013:1"); the subject title is the heading
    # and, with the FS number spliced in, the official title. NOTE: many records
    # carry no title yet (a harvest/parse gap) -- both then fall back to the id.
    ident = art.get("identifier") or _local(art["uri"])
    title = art.get("metadata", {}).get("title")
    if not title:
        return Labels(ident, "", ident, ident)
    # "Finansinspektionens föreskrifter och allmänna råd om säkerställda
    # obligationer" -> official inserts the number before the "om …" subject
    official = re.sub(r"\s+om\s", " (%s) om " % ident, title, count=1)
    return Labels(ident, title, official if official != title else
                  "%s (%s)" % (title, ident), ident)


# --------------------------------------------------------------------------
# hudoc (European Court of Human Rights)
# --------------------------------------------------------------------------

def _hudoc(art):
    # eyebrow is the application number ("no. 48786/09"); the case caption is the
    # heading. The stamped applicationNumber is authoritative; fall back to the
    # itemid only if it is somehow absent.
    appno = (art.get("metadata", {}).get("applicationNumber") or [None])[0]
    short_id = "no. %s" % appno if appno else (art.get("itemid") or "")
    title = art.get("title") or short_id
    return Labels(short_id, title, title, title)


# --------------------------------------------------------------------------
# coe / icrc / untc (treaties -- names from curated datasets)
# --------------------------------------------------------------------------

def _coe(art):
    entry = treaty_names(datasets.COE_NAMES).get(art.get("number"), {})
    name = _named(entry.get("label"), entry.get("abbr"))
    short_id = art.get("identifier") or ("CETS " + (art.get("number") or ""))
    return Labels(short_id, name, art.get("title") or short_id, name or short_id)


def _icrc(art):
    entry = treaty_names(datasets.ICRC_NAMES).get(art.get("number"), {})
    abbr, name = primary(entry.get("abbr")), _named(entry.get("label"), entry.get("abbr"))
    short_id = abbr or art.get("identifier") or ("ICRC " + (art.get("number") or ""))
    return Labels(short_id, name, art.get("title") or short_id, name or short_id)


def _untc(art):
    entry = _untc_names().get(art.get("number"), {})
    abbr, name = primary(entry.get("abbr")), _named(entry.get("sv"), entry.get("abbr"))
    short_id = abbr or art.get("identifier") or ("MTDSG " + (art.get("number") or ""))
    return Labels(short_id, name, art.get("title") or short_id, name or short_id)


# --------------------------------------------------------------------------
# avg (JO / JK / ARN decisions)
# --------------------------------------------------------------------------

def _first_sentence(text):
    """The first sentence of a prose passage, Swedish-abbreviation-aware: a
    full stop after 's.k.', an initial ('J.A.'), 'kap.' or a bare number does
    not end the sentence. The whole text when no boundary is found. Used
    where a preamble must stand in for a title (an ARN referat, A4). The
    boundary rule itself lives in `lib.text.sentences`, which a second caller
    (remisser ai-analyze) needed whole rather than just its first result."""
    found = sentences(text)
    return found[0] if found else text


def _avg(art):
    # short_id is the citation id ("JO dnr 4849-2006"); the inbound/descriptive
    # form prefers the ämbetsberättelse reference ("JO 2024 s. 246") when there is
    # one, per I1. The long decision title is the official/heading form. An ARN
    # "title" is really the referat's preamble paragraph, so its first sentence
    # stands in as the short/heading form (A4) -- listings included (A2).
    md = art.get("metadata", {})
    ident = art.get("identifier") or _local(art["uri"])
    title = md.get("title") or ""
    if art.get("org") == "arn" and title:
        title = _first_sentence(title)
    return Labels(ident, title, md.get("title") or ident,
                  md.get("officialReport") or ident)


# --------------------------------------------------------------------------
# rs (myndigheternas rättsliga ställningstaganden)
# --------------------------------------------------------------------------

def _rs(art):
    # short_id is the citation form the agency itself uses ("FKRS 2025:01",
    # "Konkurrensverkets ställningstagande 2025:1"); the title is the subject.
    # A withdrawn ställningstagande says so wherever it is named in prose --
    # it still governed what the agency did while it stood, but a reader must
    # not take it for the agency's current reading.
    md = art.get("metadata", {})
    ident = art.get("identifier") or _local(art["uri"])
    title = md.get("title") or ident
    described = ident if md.get("status") != "upphävt" else "%s (upphävt)" % ident
    return Labels(ident, title, title, described)


# --------------------------------------------------------------------------
# edpb (Europeiska dataskyddsstyrelsen)
# --------------------------------------------------------------------------

def _edpb(art):
    # short_id is the citation form the issuer's own number gives it
    # ("Riktlinjer 05/2020", "WP 248"); the title is the subject. A document the
    # EDPB has published in no Swedish version says so wherever it is named:
    # the page a reader is being sent to is in English, and that is worth
    # knowing before following the link.
    md = art.get("metadata", {})
    ident = art.get("identifier") or _local(art["uri"])
    title = md.get("title") or ident
    described = ident if md.get("sprak") != "en" else "%s (engelsk version)" % ident
    return Labels(ident, title, title, described)


# --------------------------------------------------------------------------
# icc (International Criminal Court)
# --------------------------------------------------------------------------

def _icc(art):
    # the eyebrow is the *case* number ("ICC-01/14-01/18"), not the document number
    # ("…-403"): the page is the verdict, cited by its case. The caption is the h1.
    md = art.get("metadata", {})
    short_id = md.get("caseNumber") or art.get("docnumber") or _local(art["uri"])
    title = art.get("title") or short_id
    return Labels(short_id, title, title, md.get("documentNumber") or short_id)


_DISPATCH = {"sfs": _sfs, "eurlex": _eurlex, "dv": _dv,
             "forarbete": _forarbete, "foreskrift": _foreskrift,
             "avg": _avg, "rs": _rs, "edpb": _edpb,
             "hudoc": _hudoc, "coe": _coe, "icrc": _icrc,
             "untc": _untc, "icc": _icc, "begrepp": _begrepp,
             "kommentar": _kommentar}


def document_labels(source, art):
    return _DISPATCH.get(source, _generic)(art)

# --------------------------------------------------------------------------
# what kind of instrument an SFS is
# --------------------------------------------------------------------------

# Editorial interpolations in an SFS title ("/Rubriken upphör att gälla …/").
# Public: `facets` strips the same thing off the same titles for its sort key.
SFS_EDITORIAL = re.compile(r"/[^/]*/")
# The grundlagar open with their own designation, not "Lag"/"Balk", so they are
# pinned by SFS id rather than recognised from the title.
_GRUNDLAGAR = {"1974:152", "1949:105", "1991:1469", "1810:0926", "2014:801"}
_SFS_STATUTE_END = ("lag", "lagen", "balk", "balken")


def sfs_is_statute(title, local):
    """Whether an SFS is parliamentary primary law -- a lag, a balk, or one of
    the grundlagar -- as opposed to a förordning/kungörelse/etc. The designation
    is the phrase before the SFS number; a lag/balk ends in just that, however
    compound ('Lag', 'Förvaltningslag', 'Radio- och tv-lag', 'Plan- och
    bygglag', 'Brottsbalk').

    The title is the only signal with full coverage, and it is a good one: of
    the 654 SFS carrying a "meddelad med stöd av" clause -- an independent
    statement that the instrument is delegated, and so not a lag -- this rule
    calls 653 a förordning. (The one dissenter, Miljöbalk (1998:808), contains
    the phrase in its body rather than as its own ingress.) The clause itself
    cannot carry the distinction: it appears in under a tenth of förordningar.

    Drives the browse listing's visual hierarchy, the legacy feed ``rdf_type``
    filter, the catalog ``kind``, and through it which rung of the norm
    hierarchy a document occupies (`catalog.norm_level`)."""
    head = re.sub(r"\s+", " ", SFS_EDITORIAL.sub("", title)).strip()
    designation = head.split("(", 1)[0].strip().lower()
    return local in _GRUNDLAGAR or designation.endswith(_SFS_STATUTE_END)
