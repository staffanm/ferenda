"""Harvester for EUIPO:s riktlinjer för granskning -- the practice the
immaterialrättsmyndigheten states in advance for how it reads varumärkes-
förordningen (EU) 2017/1001, formgivningsförordningen (EG) nr 6/2002 and
förordning (EU) 2023/2411 om geografiska beteckningar för hantverks- och
industriprodukter.

**The riktlinjerna are not on www.euipo.europa.eu.** That site's
``/en/guidelines`` page is three links out to ``guidelines.euipo.europa.eu``, a
separate RWS Tridion Docs delivery app whose whole corpus is behind a public,
unauthenticated JSON API. Three endpoints are enough and no HTML is parsed:

  * ``/api/publications`` -- every publication: 356 rows, one per
    ``(produktfamilj, utgåva, språk)``.
  * ``/api/toc/{pub}/{node}`` -- one level of that publication's innehålls-
    förteckning; ``t1`` is the root and returns the delarna.
  * ``/api/page/{pub}/{pageid}`` -- one topic, whose ``Meta`` carries the three
    fields this harvest is built on: ``part.scope.generated.value`` (``PARTB``),
    ``section.scope.generated.value`` (``SECTION4``) and
    ``pdf.reference.generated.value``, the key of the PDF that topic sits in.
  * ``/binary/{pub}/{ref}`` -- that PDF. It answers with no
    ``Content-Disposition``, so the file name carries no identity at all.

**Swedish exists, and it is the point.** The delivery app publishes in all 24
official languages, Swedish among them, and both the API's ``Title`` and the
PDF cover are in that language ("Riktlinjer för formgivningar"; "RIKTLINJER FÖR
PRÖVNING AV REGISTRERADE EU-FORMGIVNINGAR"). The translations lag the English
edition by a few months, which is visible today: the current varumärkesutgåva
(Edition 2026, i force 2026-07-01) exists in 22 languages and Swedish is not
yet one of them, while the superseded Edition 2025 has it. This harvest takes
the **current** edition and the Swedish text of it where there is one, English
otherwise, and records `sprak`; a later run picks the Swedish up by itself.

**The citable unit is what EUIPO gives its own PDF, and that differs by
family.** A cover states the coordinate a citation names -- "Part C /
Opposition / Section 4 / Non-registered trade marks" -- and it is the coordinate
Swedish courts write ("EUIPO:s riktlinjer, del B, avsnitt 4"). Measured over
the three current publications:

  * *varumärken* -- every del publishes its own PDF and, inside it, every
    avsnitt publishes one too, so the avsnitt is the document: 22 of them
    (Del A whole, Del B 4, Del C 8, Del D 2, Del E 6, Del M whole). Del A is
    taken whole because its Avsnitt 10 Bevis publishes no PDF of its own and
    the del-level PDF is the only place its text exists; Del M has no avsnitt
    at all. Taking del *and* avsnitt where both exist would carry 187 of Del
    A:s 208 pages twice.
  * *formgivningar* and *geografiska beteckningar* -- the delar that carry the
    family's own guidance publish **no** PDF of their own. In formgivnings-
    riktlinjerna only Del A/B/E do, and those are the delar shared verbatim
    with varumärkespraxis; the two blocks that are the design guidance
    ("Prövning av ansökningar om registrerade EU-formgivningar", 223 topics,
    and "Prövning av ansökningar om ogiltigförklaring", 116) resolve to the
    whole-volume PDF, as do all nine delar of GI-riktlinjerna. So those two
    families are carried as the one volume EUIPO publishes -- 554 and 208
    pages, the size of a SOU -- and the volume's number is `all-parts`.

The varumärkesriktlinjerna are **not** carried whole: that volume is 1 776
pages and 18.9 MB, and every part of it is already carried as its own avsnitt.
The five topics of front matter (the redaktionella noten about the revision
process, and "1 Inledning") resolve to the whole-volume PDF and are the one
thing this harvest drops; they state no examination practice.

**What is left out and why.** EUIPO:s överklagandenämnders beslut are decisions
in named cases -- `avg`/`dv` material, not guidance stated in advance. The
verkställande direktörens beslut och meddelanden (EX-/COM-/ADM-, six JSON
manifests under ``euipo.europa.eu/tunnel-web/…/decisions_president/_json/``)
are instruments rather than guidance, and the ADM half is intern administration
of the kind `issuers.ACER` declines. The gemensamma praxisen CP1-CP14 is issued
by the European Union Intellectual Property Network and published on
``tmdn.org``, not by EUIPO on its own site.

Stored per document under ``site/data/downloaded/guidance/euipo/``: a
``euipo-<serie>-<nummer>.json`` record and the ``.pdf`` document.
"""

import re
import time
from collections import Counter

from ..lib.harvest import select_pending, walk_records
from ..lib.net import BROWSER_UA as USER_AGENT
from ..lib.net import make_session, request
from ..lib.util import normalize_space
from .issuers import EUIPO

# the delivery app, which is not the body's own www host
APP = "https://guidelines.euipo.europa.eu"

# the pdf.reference every topic that has no PDF of its own resolves to: the
# whole publication in one file. Constant across publications and languages.
WHOLE_VOLUME = "2000000000"

# the root node of a publication's innehållsförteckning
ROOT_NODE = "t1"

# the number of a family carried as the one volume EUIPO publishes, rather
# than del by del -- see the module docstring
ALL_PARTS = "all-parts"

# which language to take, best first. EUIPO publishes in all 24 official
# languages; this source carries the Swedish text where the *current* edition
# has one and the English otherwise (`lib`'s rule for every body here).
SPRAK_ORDNING = ("sv", "en")

# "PARTB" -> "B". A del designation is one letter; formgivningsvolymens own
# two blocks carry a scope that is a name instead ("PARTEXA RCD"), and those
# are content all the same -- see FRONT_MATTER_SCOPES for what is not.
RE_PART_SCOPE = re.compile(r"^PART([A-Z])$")

# the scopes of a volume's front matter: "1 Inledning" carries no scope at all
# and the redaktionella noten carries the catch-all one. Both resolve to the
# whole-volume PDF in every family, and neither states examination practice, so
# a family is not carried whole on their account -- they are simply dropped.
FRONT_MATTER_SCOPES = frozenset({"", "PARTOTHER"})
# "SECTION4" -> "4"
RE_SECTION_SCOPE = re.compile(r"^SECTION(\d+)$")
# what a cover prints for its own coordinate, in the two languages taken.
# "Part C" / "Del C"; "Section 0" / "Avsnitt 0".
RE_COVER_PART = re.compile(r"\b(?:Part|Del)\s+([A-Z])\b")
RE_COVER_SECTION = re.compile(r"\b(?:Section|Avsnitt)\s+(\d+)\b")


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------

def unit_nummer(part_scope, section_scope):
    """The URI/file number of one unit, from EUIPO:s own language-free scope
    codes. ``("PARTB", "SECTION4")`` -> ``part-b-section-4``;
    ``("PARTM", "")`` -> ``part-m``.

    The scope codes are used rather than the printed designation because they
    are the same in every language: the Swedish volume prints "Del B / Avsnitt
    4" over the same ``PARTB``/``SECTION4``, so one document keeps one address
    when EUIPO publishes the Swedish translation of an edition we first took in
    English."""
    part = RE_PART_SCOPE.match(part_scope)
    assert part, "not a del designation: %r" % part_scope
    if not section_scope:
        return "part-%s" % part.group(1).lower()
    section = RE_SECTION_SCOPE.match(section_scope)
    assert section, "not an avsnitt designation: %r" % section_scope
    return "part-%s-section-%s" % (part.group(1).lower(), section.group(1))


def cover_scope(text):
    """The coordinate a cover prints for itself, as ``(del, {avsnitt})``.

    ``"GUIDELINES FOR EXAMINATION … Part C Opposition Section 3 Unauthorised
    filing …"`` -> ``("C", {"3"})``. A del-level cover prints no avsnitt of its
    own and gives ``("A", set())``.

    The del is the **first** printed and the avsnitt a *set*, because the two
    halves are printed differently. A cover states its del once, at a fixed
    place in a fixed layout. Avsnitt numbers turn up more loosely: a del-level
    PDF concatenates the covers of the avsnitt inside it, so the caller has to
    ask whether the avsnitt it filed the document under is among the numbers
    printed rather than take whichever came first."""
    parts = RE_COVER_PART.findall(text)
    return (parts[0] if parts else None, set(RE_COVER_SECTION.findall(text)))


# how much of a document's opening is its cover. The layout is fixed: two
# masthead lines, the del designation, the del's name, and -- on an avsnitt's
# own PDF -- the avsnitt designation and its name. The seventh paragraph is
# already the body, or the next cover inside a del-level PDF.
COVER_PARAGRAPHS = 6


def iso_date(stamp):
    """The ISO date of an API timestamp ("2026-07-01T00:00:00" ->
    "2026-07-01")."""
    assert re.match(r"^\d{4}-\d{2}-\d{2}T", stamp or ""), \
        "not an EUIPO timestamp: %r" % stamp
    return stamp[:10]


def pick_publication(publications, family):
    """The publication this harvest takes for one produktfamilj: the current
    edition, in Swedish where that edition has a Swedish text and English
    otherwise.

    "Current" is EUIPO:s own ``IsPubObsolete`` flag *and* the latest
    ``EntryIntoForce`` among what it leaves: formgivningsriktlinjerna have two
    editions unflagged today (2023 in 18 languages and 2026 in 24), and the
    2023 one is superseded whatever the flag says."""
    live = [p for p in publications
            if p["ProductFamily"] == [family] and not p["IsPubObsolete"]]
    assert live, "EUIPO publishes no current %s" % family
    latest = max(iso_date(p["EntryIntoForce"]) for p in live)
    edition = [p for p in live if iso_date(p["EntryIntoForce"]) == latest]
    by_sprak = {p["Language"]: p for p in edition}
    for sprak in SPRAK_ORDNING:
        if sprak in by_sprak:
            return by_sprak[sprak]
    raise ValueError("the current %s exists in neither Swedish nor English, "
                     "only %s" % (family, ", ".join(sorted(by_sprak))))


def plan_units(parts):
    """Which PDFs one publication is carried as, from its delar and their
    avsnitt.

    `parts` is ``[(part_scope, title, ref, url, sections)]`` in listing order,
    where `sections` is ``[(section_scope, title, ref, url)]`` -- empty for a
    del with no avsnitt, and never read for a del taken whole. Returns
    ``([(nummer, part_title, section_title, ref, url)], declined)``, `declined`
    being ``[(reason, title)]`` for every del not carried.

    Four shapes, and the rule is the same one each time -- take the smallest
    PDF EUIPO publishes that no other taken PDF contains:

      * a node that is front matter rather than a del is dropped outright,
        and is not evidence that the family has to be carried whole.
      * a del whose own ref is the whole volume publishes no PDF: it can only
        be carried inside the whole volume, and the caller decides that for the
        family as a whole.
      * a del every one of whose avsnitt publishes its own PDF is carried
        avsnitt by avsnitt.
      * any other del -- no avsnitt at all, or an avsnitt that resolves to the
        del's own PDF -- is carried whole. Del A of varumärkesriktlinjerna is
        the last case: its Avsnitt 10 Bevis publishes no PDF, so taking
        Avsnitt 1-9 would drop it, and taking both would carry 187 of the
        del's 208 pages twice.
    """
    units, declined = [], []
    for part_scope, title, ref, url, sections in parts:
        if part_scope in FRONT_MATTER_SCOPES:
            declined.append(("inledande sidor", title))
            continue
        if ref == WHOLE_VOLUME:
            declined.append(("delar utan egen PDF", title))
            continue
        own = [s for s in sections if s[2] not in (ref, WHOLE_VOLUME)]
        if sections and len(own) == len(sections):
            units.extend((unit_nummer(part_scope, scope), title, sub,
                          sub_ref, sub_url)
                         for scope, sub, sub_ref, sub_url in sections)
        else:
            units.append((unit_nummer(part_scope, ""), title, None, ref, url))
    return units, declined


def unit_title(pub_title, part_title, section_title):
    """What one unit is called: the volume, the del, and the avsnitt where
    there is one. "Trade mark guidelines, Part C Opposition, Section 0
    Introduction"."""
    return ", ".join(normalize_space(t) for t
                     in (pub_title, part_title, section_title) if t)


# --------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------

def basefile(serie, nummer):
    """The harvest basefile of one unit ("euipo/varumarke/part-b-section-4")."""
    return "%s/%s/%s" % (EUIPO.kod, serie, EUIPO.serie(serie).slug(nummer))


def _api(session, path, delay):
    """One JSON call to the delivery app, paced."""
    data = request(session, "GET", APP + path, parse_json=True, timeout=120)
    time.sleep(delay)
    return data


def _page_meta(session, pub, page_id, delay):
    return _api(session, "/api/page/%s/%s" % (pub, page_id), delay)["Meta"]


def _node_page(node):
    """The topic id behind one innehållsförteckningsnod, off the address the
    node states ("/2319054/2231948/trade-mark-guidelines/section-4-…" ->
    "2231948"). Every node in a published publication states one."""
    assert node["Url"], "EUIPO node %r states no address" % node["Title"]
    return node["Url"].split("/")[2]


def _node_url(node):
    """The app's own page for one node, which is what a reader is sent to."""
    return APP + node["Url"]


def _parts(session, pub, delay):
    """One publication's delar in listing order, each as
    ``(part_scope, title, ref, url, sections)``.

    The avsnitt of a del that publishes no PDF of its own are never read: the
    family is then carried whole and the requests would be spent on a decision
    already made."""
    out = []
    for node in _api(session, "/api/toc/%s/%s" % (pub, ROOT_NODE), delay):
        meta = _page_meta(session, pub, _node_page(node), delay)
        ref = meta["pdf.reference.generated.value"]
        sections = []
        if ref != WHOLE_VOLUME and node["HasChildNodes"]:
            for sub in _api(session, "/api/toc/%s/%s" % (pub, node["Id"]),
                            delay):
                sub_meta = _page_meta(session, pub, _node_page(sub), delay)
                if sub_meta["section.scope.generated.value"]:
                    sections.append((
                        sub_meta["section.scope.generated.value"],
                        normalize_space(sub["Title"]),
                        sub_meta["pdf.reference.generated.value"],
                        _node_url(sub)))
        out.append((meta["part.scope.generated.value"],
                    normalize_space(node["Title"]), ref, _node_url(node),
                    sections))
    return out


def _document_fetcher(session, url):
    return lambda: request(session, "GET", url, timeout=300).content


def _record(serie, nummer, titel, publication, source_url, ref):
    """One harvest record. The identity is a coordinate, not a number, so the
    citation is the name the volume states for the unit, the way EASA:s and
    ACER:s ramriktlinjers records carry theirs."""
    return {
        "basefile": basefile(serie, nummer), "utgivare": EUIPO.kod,
        "serie": serie, "nummer": nummer,
        "sprak": publication["Language"], "titel": titel,
        "antagen": iso_date(publication["EntryIntoForce"]),
        "version": publication["ProductReleaseVersion"][0],
        "citation": titel, "konsultation_url": None, "amnesord": [],
        "source_url": source_url,
        "dokument_url": "%s/binary/%s/%s" % (APP, publication["Id"], ref),
    }


def _family_pending(session, serie, publication, delay, counts):
    """The records one produktfamilj contributes.

    A family whose main delar publish no PDF of their own is carried as the one
    volume EUIPO publishes; the others avsnitt by avsnitt, or del by del where
    an avsnitt publishes none. See the module docstring for what was measured
    behind that."""
    pub_title = normalize_space(publication["Title"])
    units, declined = plan_units(_parts(session, publication["Id"], delay))
    for reason, title in declined:
        del title
        counts["%s: %s" % (serie, reason)] += 1
    whole = [d for d in declined if d[0] == "delar utan egen PDF"]
    if whole or not units:
        # the delar that carry this family's own guidance publish no PDF of
        # their own -- every del of GI-riktlinjerna, and both design blocks of
        # formgivningsriktlinjerna. Taking the units that do would drop the
        # guidance, so the volume is the document and the units are not taken.
        counts["%s: hela volymen" % serie] += 1
        # the units this decision passes over: formgivningsvolymens Del A, Del
        # B and Del E do publish their own PDFs, and they are the delar shared
        # verbatim with varumärkespraxis. Counted rather than silent, so the
        # decision is visible in the run's own output.
        counts["%s: enheter inuti volymen" % serie] += len(units)
        return [_record(serie, ALL_PARTS, pub_title, publication,
                        "%s/%s" % (APP, publication["Id"]), WHOLE_VOLUME)]
    counts["%s: enheter" % serie] += len(units)
    return [_record(serie, nummer,
                    unit_title(pub_title, part_title, section_title),
                    publication, url, ref)
            for nummer, part_title, section_title, ref, url in units]


def euipo_sync(root, full=False, only=None, limit=None, delay=0.5):
    """Harvest EUIPO:s riktlinjer off the delivery app's JSON API.

    One scope: the three produktfamiljerna are three series but one host and
    one API, and the whole listing is enumerable in about seventy requests, so
    the EDPB/EBA idiom applies -- one walk per run, fetching what is new or
    changed, no watermark.

    Every del not carried is counted under the reason it was not, so a del that
    publishes no PDF of its own and a page shape this harvest has not seen
    never look alike in the output (rule:instrument-failures)."""
    session = make_session(USER_AGENT)
    publications = _api(session, "/api/publications", delay)
    counts = Counter()
    pending = []
    for serie in EUIPO.series:
        publication = pick_publication(publications, serie.doctype)
        counts["%s: %s %s" % (serie.kod, publication["ProductReleaseVersion"][0],
                              publication["Language"])] += 1
        pending.extend((record, _document_fetcher(session,
                                                  record["dokument_url"]))
                       for record in _family_pending(session, serie.kod,
                                                     publication, delay,
                                                     counts))
    pending = select_pending(pending, only,
                             "the EUIPO guidelines carry no document %s")
    seen, new = walk_records(root, pending, delay=delay, full=full,
                             limit=limit, scope=EUIPO.kod)
    print("euipo: %d publications listed, %d documents -> %s"
          % (len(publications), len(pending),
             ", ".join("%d %s" % (n, what)
                       for what, n in sorted(counts.items()))))
    return seen, new
