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
    """SFS id ("2018:585") -> its established short name ("säkerhetsskyddslagen")."""
    data = json.loads(datasets.NAMEDLAWS.read_text(encoding="utf-8"))
    return {lawid.replace("_", " "): entry["label"]
            for lawid, entry in data.items()
            if isinstance(entry, dict) and entry.get("label")}


def _first(value):
    """A dataset `label`/`abbr` may be a str or a list of variants; take the
    primary (first) one."""
    return value[0] if isinstance(value, list) else value


def _named(label, abbr):
    """Compose a treaty's display name from its curated Swedish label and acronym:
    "Europakonventionen (EKMR)", or just the label when there is no acronym."""
    label, abbr = _first(label), _first(abbr)
    if label and abbr:
        return "%s (%s)" % (label, abbr)
    return label or abbr or ""


@functools.lru_cache(maxsize=1)
def _coe_names():
    """ETS/CETS number ("005") -> {label, abbr} (europakonventalen / EKMR)."""
    data = json.loads(datasets.COE_NAMES.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict)}


@functools.lru_cache(maxsize=1)
def _icrc_names():
    """ICRC number ("375") -> {label, abbr} (tredje Genèvekonventionen / GK III)."""
    data = json.loads(datasets.ICRC_NAMES.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict)}


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
    title = art.get("title") or _local(art["uri"])
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
        return Labels(short_id, short_title, title, label or short_id)
    if doctype == "treaty":
        # a founding/consolidated treaty carries no extractable short title -- the
        # raw CELEX is all the artifact holds -- so a curated Swedish name stands in
        # as both the short title and the official title (E1); short_id is the CELEX
        name = _treaty_names().get(_EU_TREATY_SUFFIX.sub("", celex))
        return Labels(celex, name or "", name or title, name or title)
    m = _EU_DESIGNATION.search(label) or _EU_DESIGNATION.search(title)
    short_id = m.group(0) if m else (art.get("celex") or "")
    # short_title: the curated/extracted short name, else the descriptive tail of
    # the stamped short label (label minus its leading designation)
    short_title = named or (label[m.end():].strip() if m and label else "")
    return Labels(short_id, short_title, title, label or short_title or short_id)


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
    entry = _coe_names().get(art.get("number"), {})
    name = _named(entry.get("label"), entry.get("abbr"))
    short_id = art.get("identifier") or ("CETS " + (art.get("number") or ""))
    return Labels(short_id, name, art.get("title") or short_id, name or short_id)


def _icrc(art):
    entry = _icrc_names().get(art.get("number"), {})
    abbr, name = _first(entry.get("abbr")), _named(entry.get("label"), entry.get("abbr"))
    short_id = abbr or art.get("identifier") or ("ICRC " + (art.get("number") or ""))
    return Labels(short_id, name, art.get("title") or short_id, name or short_id)


def _untc(art):
    entry = _untc_names().get(art.get("number"), {})
    abbr, name = _first(entry.get("abbr")), _named(entry.get("sv"), entry.get("abbr"))
    short_id = abbr or art.get("identifier") or ("MTDSG " + (art.get("number") or ""))
    return Labels(short_id, name, art.get("title") or short_id, name or short_id)


# --------------------------------------------------------------------------
# avg (JO / JK / ARN decisions)
# --------------------------------------------------------------------------

def _avg(art):
    # short_id is the citation id ("JO dnr 4849-2006"); the inbound/descriptive
    # form prefers the ämbetsberättelse reference ("JO 2024 s. 246") when there is
    # one, per I1. The long decision title is the official/heading form.
    md = art.get("metadata", {})
    ident = art.get("identifier") or _local(art["uri"])
    return Labels(ident, "", md.get("title") or ident,
                  md.get("officialReport") or ident)


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
             "avg": _avg, "hudoc": _hudoc, "coe": _coe, "icrc": _icrc,
             "untc": _untc, "icc": _icc}


def document_labels(source, art):
    return _DISPATCH.get(source, _generic)(art)
