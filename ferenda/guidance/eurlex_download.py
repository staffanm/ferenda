"""Route A: guidance that its issuing body publishes in EUT rather than on its
own site.

The ESA:erna publish their riktlinjer themselves and are harvested off their own
pages (route ``site``). The ECB and the ESRB do not: their yttranden,
rekommendationer, varningar and beslut appear in Europeiska unionens officiella
tidning, and the EU:s publikationsbyrå is the only place they can be enumerated.
A SPARQL census of what CELLAR holds under each body's corporate-body URI is
what settles which route a body takes, and it settles it in opposite directions:
CELLAR holds 169 works for the EBA and every one is a vacancy notice, while it
holds 3 214 for the ECB.

**Stored here, not under `eurlex`.** `eurlex` carries sector 1, parts of 3 and
parts of 6; guidance is none of those, and a rekommendation filed under a CELEX
address would carry an identity nobody cites it by.

**Identity is the body's own number, and CELLAR states it.** This is the whole
reason route A is cheap where route B is expensive: the ESA harvesters read a
number off a PDF cover because their sites print it nowhere else, but CELLAR
carries the ECB's own number as three predicates of the work --

    resource_legal_internal_number_prefix             CON
    resource_legal_internal_number_year               2013
    resource_legal_internal_number_sequential_number  82

-- which is "CON/2013/82", the form 122 förarbeten cite that yttrande by, and
not one of them cites it as 52013AB0082.

**The ESRB states it in only a quarter of its works**, so it is read off the
title for the rest. Measured over its 113 English expressions: 26 carry the
number predicates, 83 more print ``ESRB/ÅÅÅÅ/N`` in the title, and the remaining
4 are vacancy notices and an announcement -- the same CELLAR noise the EBA
census turned up, and not guidance at all. Reading the title is a second witness
to the same number rather than a guess: the pattern is the body's own number
scheme, and a work matching neither is counted and left.

**The ESRB takes no series segment.** It numbers rekommendationer (62), beslut
(23), varningar (20) and råd (2) in one ESRB/ÅÅÅÅ/N sequence with no collisions,
so the number alone names the document and ``/guidance/esrb/2014-01`` reproduces
the citation exactly. The ECB does divide its output -- CON/… are the yttranden
and ECB/… its rättsakter -- so its yttranden take the ``con`` segment their
citation already carries.

Language and format are `lib.cellar`'s, unchanged: Swedish where the body
published one and English otherwise, and Formex before XHTML before HTML before
any PDF, with each promise verified against the bytes actually served.
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path

from ..lib.cellar import (
    LANGUAGES,
    PREFIXES,
    fetch_metadata,
    fetch_selection,
    sparql_select,
    store_document,
)
from ..lib.harvest import store_record
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session
from ..lib.util import Reporter, basefile_slug, record_path
from .issuers import BY_KOD

AGENT = "http://publications.europa.eu/resource/authority/corporate-body/%s"
LANG_URI = "http://publications.europa.eu/resource/authority/language/%s"
# where a reader is sent to see the document at its publisher
EURLEX_PAGE = "https://eur-lex.europa.eu/legal-content/SV/TXT/?uri=CELEX:%s"
# how many works one enumeration asks for at a time
PAGE = 500
# how many works one selection/metadata round trip covers
META_CHUNK = 500


@dataclass(frozen=True)
class Body:
    """One body whose guidance CELLAR holds, as the enumeration needs it."""
    kod: str                # our issuer kod, and the harvest scope
    agent: str              # its corporate-body code in CELLAR
    serie: str | None       # the guidance series its works belong to
    prefix: str             # the internal-number prefix that marks that series
    celex: re.Pattern       # the CELEX shape to accept, filtering out the
                            # body's other output (the ECB authors 3 214 works
                            # and only 1 633 of them are yttranden)


BODIES = {
    "ecb": Body("ecb", "ECB", "con", "CON",
                re.compile(r"^5\d{4}AB\d{4}$")),
    "esrb": Body("esrb", "ESRB", None, "ESRB",
                 re.compile(r"^3\d{4}Y\d+")),
}


def content_dir(root, basefile):
    """Where one document's manifestations are stored, beside its record:
    ``ecb/con/2013-82`` -> ``<root>/ecb/ecb-con-2013-82/``. One directory per
    document because CELLAR answers per language and `lib.cellar` stores each
    language it got, which is what lets a page show the Swedish text and say so
    when there is only English."""
    return Path(root) / basefile.split("/", 1)[0] / basefile_slug(basefile)


def enum_query(body, offset):
    """One page of the works `body` authored, with everything the record needs.

    Titles come in both languages in the same query rather than in a second
    pass: the Swedish one is the document's title where it exists, and the
    English one is both the fallback and -- for the ESRB -- where the number is
    read from."""
    return (PREFIXES
            + "SELECT ?celex ?pfx ?yr ?nr ?d ?tsv ?ten WHERE { "
              "?w cdm:work_created_by_agent <%s> ; "
              "cdm:resource_legal_id_celex ?celex . "
              "OPTIONAL { ?w cdm:resource_legal_internal_number_prefix ?pfx } "
              "OPTIONAL { ?w cdm:resource_legal_internal_number_year ?yr } "
              "OPTIONAL { ?w cdm:resource_legal_internal_number_sequential_number ?nr } "
              "OPTIONAL { ?w cdm:work_date_document ?d } "
              "OPTIONAL { ?esv cdm:expression_belongs_to_work ?w ; "
              "cdm:expression_uses_language <%s> ; cdm:expression_title ?tsv } "
              "OPTIONAL { ?een cdm:expression_belongs_to_work ?w ; "
              "cdm:expression_uses_language <%s> ; cdm:expression_title ?ten } "
              "} ORDER BY ?celex LIMIT %d OFFSET %d"
            % (AGENT % body.agent, LANG_URI % "SWE", LANG_URI % "ENG",
               PAGE, offset))


def _value(row, key):
    return row[key]["value"] if key in row else None


def series_number(body, row, titel):
    """The body's own number for one work, as ``ÅÅÅÅ/N``, or None.

    The predicates are asked first because they are the body's own structured
    statement of the number. The title is asked second, for the works that carry
    no such statement -- and only for the body's own prefix, so a rekommendation
    that names another document in its title cannot be filed under that other
    number.

    Where the title prints several, the *last* is the document's own: an
    amending act names the act it amends first and carries its own number in the
    trailing parenthesis ("...om ändring av beslut ESRB/2011/1 om
    arbetsordningen ... (ESRB/2020/3)"). Reading the first put nine ESRB
    documents under the number of the act they amend, each one overwriting the
    amended act's own text."""
    prefix, ar, lopnummer = (_value(row, "pfx"), _value(row, "yr"),
                             _value(row, "nr"))
    if prefix == body.prefix and ar and lopnummer:
        return "%s/%d" % (ar, int(lopnummer))
    printed = re.findall(r"\b%s/(\d{4})/(\d+)\b" % re.escape(body.prefix),
                         titel or "")
    return "%s/%d" % (printed[-1][0], int(printed[-1][1])) if printed else None


def enumerate_works(session, body, log=print):
    """Every work `body` authored that this source carries, newest last.

    Counts what it declines, because the two reasons are different things: a
    CELEX outside the body's guidance shape is the body's other output, and a
    work with no number is CELLAR noise -- a vacancy notice, an announcement --
    that carries no identity to file it under (rule:instrument-failures)."""
    seen, other_shape, unnumbered = {}, 0, 0
    offset = 0
    while True:
        rows = sparql_select(session, enum_query(body, offset))
        for row in rows:
            celex = _value(row, "celex")
            if not body.celex.match(celex):
                other_shape += 1
                continue
            svensk, engelsk = _value(row, "tsv"), _value(row, "ten")
            nummer = series_number(body, row, svensk or engelsk)
            if nummer is None:
                unnumbered += 1
                continue
            # one work per number: CELLAR lists a corrigendum under its own
            # CELEX and the same number, and the later CELEX is the corrected
            # text, so the last one wins
            seen[nummer] = (celex, nummer, _value(row, "d"), svensk, engelsk)
        if len(rows) < PAGE:
            break
        offset += PAGE
    log("%s: %d works -> %d numbered; declined %d outside the series' CELEX "
        "shape, %d carrying no %s number"
        % (body.kod, len(seen) + other_shape + unnumbered, len(seen),
           other_shape, unnumbered, body.prefix))
    return [seen[n] for n in sorted(seen)]


def _sync(body, root, full=False, only=None, limit=None, delay=0.5,
          languages=LANGUAGES, log=print):
    """Harvest one body's guidance out of CELLAR into this source's store."""
    issuer = BY_KOD[body.kod]
    serie = issuer.serie(body.serie)
    session = make_session(USER_AGENT)
    works = enumerate_works(session, body, log=log)
    if only:
        works = [w for w in works
                 if basefile_slug("%s/%s" % (body.kod, serie.slug(w[1])))
                 .endswith(basefile_slug(only))]
        assert works, "CELLAR carries no %s document %s" % (body.kod, only)
    report = Reporter()
    seen = new = 0
    utan_text = 0
    selection, dates, eurovoc, validity = {}, {}, {}, {}
    for start in range(0, len(works), META_CHUNK):
        # the selection and the metadata come per chunk rather than per work:
        # both queries take a VALUES list, so a thousand works cost one round
        # trip each instead of two thousand
        chunk = works[start:start + META_CHUNK]
        celexes = [w[0] for w in chunk]
        selection = fetch_selection(session, celexes, languages)
        dates, eurovoc, validity, _answered = fetch_metadata(
            session, celexes)
        for celex, nummer, wdate, svensk, engelsk in chunk:
            if limit is not None and new >= limit:
                break
            seen += 1
            basefile = "/".join(x for x in (body.kod, body.serie,
                                            serie.slug(nummer)) if x)
            target = content_dir(root, basefile)
            stored = store_document(session, target, celex,
                                    dates.get(celex, wdate),
                                    selection.get(celex, []),
                                    eurovoc.get(celex, []),
                                    validity.get(celex, (None, None)))
            time.sleep(delay)
            if not stored:
                # CELLAR holds the work but no text in any language we take.
                # Not a failure to record: the document is simply not readable
                # here yet, and a later run finds it if a translation lands.
                utan_text += 1
                report.update(seen, len(works), scope=body.kod, actual=new,
                              utan_text=utan_text)
                continue
            new += store_record(
                record_path(root, body.kod, basefile),
                {"basefile": basefile, "utgivare": body.kod,
                 "serie": body.serie, "nummer": nummer, "celex": celex,
                 "sprak": "sv" if "swe" in stored else "en",
                 "titel": (svensk if "swe" in stored else None)
                 or engelsk or svensk,
                 "antagen": dates.get(celex, wdate), "version": None,
                 "konsultation_url": None, "amnesord": eurovoc.get(celex, []),
                 "source_url": EURLEX_PAGE % celex,
                 "dokument_url": EURLEX_PAGE % celex,
                 "manifestationer": sorted(stored)},
                full=full)
            report.update(seen, len(works), scope=body.kod, actual=new,
                          utan_text=utan_text)
        if limit is not None and new >= limit:
            break
    report.done()
    log("%s: %d works, %d stored, %d holding no swe/eng text"
        % (body.kod, seen, new, utan_text))
    return seen, new


def ecb_sync(root, full=False, only=None, limit=None, delay=0.5):
    """The ECB's yttranden (CON/ÅÅÅÅ/N)."""
    return _sync(BODIES["ecb"], root, full=full, only=only, limit=limit,
                 delay=delay)


def esrb_sync(root, full=False, only=None, limit=None, delay=0.5):
    """The ESRB's rekommendationer, varningar, beslut och råd, one sequence."""
    return _sync(BODIES["esrb"], root, full=full, only=only, limit=limit,
                 delay=delay)


SYNC = {"ecb": ecb_sync, "esrb": esrb_sync}

# These two bodies publish in EUT rather than on their own sites, so a run
# reports the Publications Office as where the documents come from.
ORIGIN = "https://publications.europa.eu/"
