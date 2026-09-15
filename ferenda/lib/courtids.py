"""International court document identities, shared by producers and lookup.

Case numbers alone do not identify decisions. ICC document numbers and ICJ
decision filename stems do, so these functions never select a case's judgment.
"""

import re

from .util import BASE

ICC_DOC_BASE = re.compile(r"ICC-\d+/\d+-\d+/\d+-\d+", re.I)
ICC_CASE = re.compile(r"ICC-\d+/\d+-\d+/\d+", re.I)
ICJ_STEM = re.compile(r"^(\d{3})[-_](\d{8})[-_]([A-Za-z]{3})[-_](\d{2})[-_](\d{2})$")
ICJ_REPORT = re.compile(
    r"I\.?\s?C\.?\s?J\.?\s+Reports\s+(?P<year>\d{4})"
    r"\s*(?:\((?P<volume>[IVX]+)\))?,?\s*(?:at\s+)?pp?\.\s*(?P<page>\d+)")


def citation_key(value):
    """An exact alias key. ICJ Reports keep their volume and start page."""
    value = " ".join(value.split())
    if match := re.fullmatch(ICJ_REPORT.pattern, value, re.I):
        return "icj reports %s %s %d" % (
            match["year"], (match["volume"] or "").lower(), int(match["page"]))
    return value.casefold()


def icc_basefile(doc_number):
    """The ICC document number with slashes replaced by underscores."""
    return doc_number.replace("/", "_")


def icc_uri(doc_number):
    return "%sicc/%s" % (BASE, icc_basefile(doc_number))


def icj_parts(stem):
    match = ICJ_STEM.match(stem)
    if not match:
        return None
    case, date, kind, part, sub = match.groups()
    return {"case": case, "date": "%s-%s-%s" % (date[:4], date[4:6], date[6:]),
            "kind": kind.upper(), "part": part, "sub": sub}


def icj_basefile(stem):
    parts = icj_parts(stem)
    if parts is None:
        raise ValueError("icj: %r is not a decision filename stem" % stem)
    return "%s-%s-%s-%s-%s" % (parts["case"], parts["date"].replace("-", ""),
                               parts["kind"], parts["part"], parts["sub"])


def icj_uri(basefile):
    return "%sicj/%s" % (BASE, basefile)


def resolve(q):
    """One ICC document number or ICJ decision filename, else None."""
    if re.fullmatch(ICC_DOC_BASE.pattern + r"(?:-[A-Za-z0-9]+)*", q, re.I):
        return icc_uri("ICC-" + q[4:])
    stem = re.sub(r"^ICJ\s+", "", q, flags=re.I)
    stem = re.sub(r"(?:[-_](?:EN|FR|BI)C?)?(?:\.pdf)?$", "", stem, flags=re.I)
    if ICJ_STEM.fullmatch(stem):
        return icj_uri(icj_basefile(stem))
    return None
