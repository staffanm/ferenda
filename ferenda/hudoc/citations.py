"""Case-law cross-references inside an ECHR text, as inline links.

A judgment cites its precedent by name and application number -- "Keenan
v. the United Kingdom, no. 27229/95, § 111" -- 14.8 distinct cases per
judgment on average, and 88% of the cited numbers name documents this corpus
already holds in its own metadata (``applicationNumber``). None of them
linked: roughly 175,000 internal case-law links latent across 13,567
judgments.

Two matchers, in the two-stage shape the citation engine uses (a cheap
trigger proposes, a structured match disposes):

  * **application number** -- "no. 27229/95", "nos. 3455/05 and 28901/95".
    The number is the identity HUDOC itself keys on, so this is the precise
    path.
  * **case name without a number** -- "the court held in Keenan v. the
    United Kingdom that …". The " v. " connector triggers; both sides then
    have to match the *held corpus's* own case titles, respondent first
    (a closed set of states), then the longest applicant ending at the
    connector. A name no held title carries matches nothing.

Both refuse ambiguity the same way: an application number (or name) borne by
several held documents links only where exactly one is a judgment, or where
a date printed beside the citation picks one -- a citation pins the decision
cited, and guessing between a chamber and a Grand Chamber judgment would
mislink it (rule:fail-fast). Measured on the corpus: 17,241 appnos map to
exactly one held judgment, 925 to more than one, and dates tell all but 3 of
those apart.
"""

import functools
import re
from pathlib import Path

from ..lib import compress
from ..lib.emdref import fold_party_name
from ..lib.lagrum import ECHR_BASE, Ref, yield_overlaps
from ..lib.util import normalize_space
from .model import document_kind, record_date

PREDICATE = "dcterms:references"

RE_APPNO_LIST = re.compile(
    r"\b[Nn]os?\.\s*(\d{3,5}/\d{2}(?:(?:\s*(?:,|and)\s*)\d{3,5}/\d{2})*)")
RE_APPNO = re.compile(r"\d{3,5}/\d{2}")
# a date printed within the citation's own apparatus ("no. 59548/00, § 80,
# 17 January 2008"): reaches past pinpoints and reporter cites, but never
# a sentence boundary
RE_NEAR_DATE = re.compile(
    r"[^.;()]{0,60}?\b(\d{1,2})\s+(January|February|March|April|May|June|July"
    r"|August|September|October|November|December)\s+(\d{4})")
MONTHS = {m: i + 1 for i, m in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"))}
RE_VS = re.compile(r"\sv\.\s")
# how one held case title decomposes: an optional "CASE OF", the two parties,
# an optional serial suffix the Court itself numbers repeat cases with
RE_TITLE = re.compile(
    r"^(?:CASE OF |AFFAIRE )?(?P<applicant>.+?) (?:v\.|c\.) "
    r"(?P<respondent>.+?)(?:\s*\(No\.\s*(?P<serial>\d+)\))?$", re.I)
RE_SERIAL_AFTER = re.compile(r"\s*\(No\.\s*(\d+)\)", re.I)
# how far back an applicant's name may start before " v. ", and how far
# forward a respondent may reach; past these the words are the sentence's,
# not the citation's
APPLICANT_WINDOW = 60
RESPONDENT_WINDOW = 45


@functools.lru_cache(maxsize=1)
def index(root):
    """The held corpus, keyed for both matchers: ``by_no[appno]`` and
    ``by_name[(applicant, respondent, serial)]`` -> [(kind, date, itemid)],
    plus the normalized respondent names (the closed set the name matcher
    anchors on). Built once from the stored records -- the corpus indexing
    itself, not a derived store."""
    by_no, by_name, respondents, identity = {}, {}, set(), {}
    for path in sorted(Path(root).glob("001-*.json*")):
        if ".json" not in path.name:
            continue
        record = compress.read_json(path)
        entry = (document_kind(record.get("documentcollectionid2")),
                 record_date(record), record["itemid"])
        appnos = [no.strip() for no in (record.get("appno") or "").split(";")
                  if no.strip()]
        for no in appnos:
            by_no.setdefault(no, []).append(entry)
        m = RE_TITLE.match(normalize_space(record.get("docname") or ""))
        key = None
        if m:
            key = (fold_party_name(m.group("applicant")),
                   fold_party_name(m.group("respondent")),
                   m.group("serial") or "")
            by_name.setdefault(key, []).append(entry)
            respondents.add(key[1])
        identity[record["itemid"]] = (frozenset(appnos), key)
    return by_no, by_name, respondents, identity


def _pick(candidates, own, near_date):
    """The one document a citation means, or None. An exact nearby date wins;
    else the sole judgment; else the sole document. Several candidates and no
    date is a chamber/Grand-Chamber guess, and stays unlinked."""
    candidates = [c for c in candidates if c[2] != own]
    if near_date:
        dated = [c for c in candidates if c[1] == near_date]
        if len(dated) == 1:
            return dated[0]
    judgments = [c for c in candidates if c[0] == "judgment"]
    if len(judgments) == 1:
        return judgments[0]
    if not judgments and len(candidates) == 1:
        return candidates[0]
    return None


def _near_date(text, pos):
    m = RE_NEAR_DATE.match(text, pos)
    if not m:
        return None
    return "%04d-%02d-%02d" % (int(m.group(3)), MONTHS[m.group(2)],
                               int(m.group(1)))


def _appno_refs(text, own, by_no, own_appnos):
    out = []
    for m in RE_APPNO_LIST.finditer(text):
        near = _near_date(text, m.end())
        for no in RE_APPNO.finditer(m.group(1)):
            # the document's own application number names *this* case -- in
            # the cover ("Application no. 78103/14") and in the referral
            # banner -- and linking it reached whichever sibling judgment
            # survived the self-exclusion
            if no.group(0) in own_appnos:
                continue
            picked = _pick(by_no.get(no.group(0), ()), own, near)
            if picked:
                start = m.start(1) + no.start()
                out.append(Ref(start, start + len(no.group(0)), no.group(0),
                               PREDICATE, ECHR_BASE + picked[2]))
    return out


def _name_refs(text, own, by_name, respondents, own_key):
    out, consumed = [], 0
    for m in RE_VS.finditer(text):
        if m.start() < consumed:
            continue
        # respondent: the longest known state name the following text spells,
        # its determiner included in the span but not the key
        after = text[m.end():m.end() + RESPONDENT_WINDOW]
        resp = next((cut for cut in range(len(after), 0, -1)
                     if fold_party_name(after[:cut]) in respondents
                     and (cut == len(after) or not after[cut].isalnum())), None)
        if resp is None:
            continue
        serial = RE_SERIAL_AFTER.match(text, m.end() + resp)
        # applicant: the longest word-boundary suffix before " v. " that,
        # with this respondent, is a held case
        before = text[max(0, m.start() - APPLICANT_WINDOW):m.start()]
        found = None
        for cut in [0] + [w.end() for w in re.finditer(r"[\s(]", before)]:
            key = (fold_party_name(before[cut:]), fold_party_name(after[:resp]),
                   serial.group(1) if serial else "")
            if before[cut:].strip() and key in by_name:
                found = (cut, key)
                break
        if not found:
            continue
        cut, key = found
        # the document's own case name is self-description, not a citation
        if key == own_key:
            continue
        if serial:
            end = serial.end()
        else:
            # the respondent cut is punctuation-insensitive, so it may have
            # swallowed the ", " leading into the citation's apparatus
            end = m.end() + resp
            while not text[end - 1].isalnum():
                end -= 1
        picked = _pick(by_name[key], own, _near_date(text, end))
        if picked is None:
            continue
        start = max(0, m.start() - APPLICANT_WINDOW) + cut
        # the applicant cut is punctuation-insensitive too, so it may sit on
        # the ", " before the name ("see, mutatis mutandis, Keenan v. …")
        while not text[start].isalnum():
            start += 1
        out.append(Ref(start, end, text[start:end], PREDICATE,
                       ECHR_BASE + picked[2]))
        consumed = end
    return out


def refs(text, own, root):
    """Every case-law citation in one block of text, as `lagrum.Ref` spans:
    the application numbers, plus the named cases the number-less prose
    citations resolve to. A name overlapping a number's span yields to it --
    the number is the stronger identity."""
    by_no, by_name, respondents, identity = index(root)
    own_appnos, own_key = identity.get(own) or (frozenset(), None)
    numbered = _appno_refs(text, own, by_no, own_appnos)
    named = yield_overlaps(
        _name_refs(text, own, by_name, respondents, own_key), numbered)
    return sorted(numbered + named, key=lambda ref: ref.start)
