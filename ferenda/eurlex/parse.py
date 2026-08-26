"""Parse an EU document from Formex (the structured XML manifestation) into the
EurlexDoc model and project it to a JSON artifact.

Formex has two roots we handle: `ACT` (regulations, directives, decisions,
treaties) and `JUDGMENT` (Court of Justice case law). Both carry a
bibliographic header, an optional preamble (recitals + visas) and a body
(enacting terms / judgment contents + ruling). We walk the known structure into
an ordered list of typed blocks; inline markup (highlights, dates, OJ
references) is flattened to text and footnote NOTEs are dropped from the running
text. A `.fmx4.zip` manifestation bundles the main act with its annexes as
separate Formex files; we parse the main act (the lowest-sequence file) and note
the annexes (parsing them is a later step).

Body text is scanned for citations to EU legislation and CJEU case law with the
shared citation engine, the same way SFS/DV/forarbete are, so EU references link
into the rest of the corpus.
"""

import functools
import json
import re
from datetime import date

from ..lib import compress, eucasenaming, layout, markup, patch
from ..lib.cellar import notice_repeal_date, notice_work_date
from ..lib.datasets import NAMEDACTS
from ..lib.errors import SkipDocument
from ..lib.eu_structure import doctype, revision_base
from ..lib.formex import (
    QUOTATION,
    _text,
    act_metadata,
    append_annex,
    collect_notes,
    formex_roots,
    judgment_metadata,
    parse_act,
    parse_hearing_report,
    parse_judgment,
    parse_opinion,
    walk_content,
)
from ..lib.lagrum import (
    EULAGSTIFTNING,
    EURATTSFALL,
    LagrumParser,
    celex_of,
    eu_akttyp,
    interleave,
    yield_overlaps,
)
from .correspond import correspondence
from .definitions import build_matcher, extract_definitions, term_refs
from .model import BASE, EurlexDoc, official_short_title, short_label
from .parse_html import parse_html
from .parse_pdf import parse_pdf
from .structure import nest

LANG_PREFERENCE = ("swe", "eng")



# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------

def parse_formex(root, celex, lang):
    """A Formex root element -> EurlexDoc."""
    doc = EurlexDoc(celex=celex, uri=BASE % celex, doctype=doctype(celex),
                    lang=lang)
    if root.tag == "JUDGMENT":
        doc.date, doc.ecli = judgment_metadata(root)
        doc.title = parse_judgment(root, doc.body)
    elif root.tag == "CONCLUSION":          # an Advocate General's opinion (E4)
        doc.date, doc.ecli = judgment_metadata(root)
        doc.title = parse_opinion(root, doc.body)
    elif root.tag == "REPORT.HEARING":
        # for the oldest cases (Beentjes) the report for the hearing is the
        # only text CELLAR holds; its "Relevant legislation" section carries
        # the act citations the rail joins on, so it stands in for the
        # judgment body rather than rendering an empty page
        doc.date, doc.ecli = judgment_metadata(root)
        doc.title = parse_hearing_report(root, doc.body)
    elif root.tag == "ANNEX":
        # some older acts expose only an annex as their Formex manifestation;
        # render it rather than an empty page (a fuller manifestation, if any,
        # is a download-selection question)
        doc.date, doc.oj = act_metadata(root)
        doc.title = _text(root.find("TITLE"), "TI", "P") or _text(root, "TITLE")
        contents = root.find("CONTENTS")
        if contents is not None:
            walk_content(contents, doc.body)
    else:                                   # ACT (legislation, treaties)
        doc.date, doc.oj = act_metadata(root)
        doc.title = parse_act(root, doc.body)
    return doc


def parse_document(roots, celex, lang):
    """All Formex parts of a manifestation -> one EurlexDoc: the main
    act/judgment with its footnotes, then each annex embedded in order."""
    doc = parse_formex(roots[0], celex, lang)
    collect_notes(roots[0], doc.body)
    for root in roots[1:]:
        if root.tag == "ANNEX":
            append_annex(doc.body, root)
        else:
            walk_content(root, doc.body, level=1)
        collect_notes(root, doc.body)
    return doc


@functools.cache
def _refparser(lang="swe"):
    """Citation scanner for EU body text: EU legislation + CJEU case law. No
    SFS vocabulary (EU references are absolute CELEX/case numbers). `lang`
    "eng" loads the English citation surface -- pre-accession case law exists
    in no Swedish version, so those documents are parsed from their English
    manifestation ("Article 29 (5) of Directive 71/305/EEC")."""
    return LagrumParser({}, basefile="celex",
                        parse_types=[EULAGSTIFTNING, EURATTSFALL], lang=lang)


@functools.cache
def _namedacts():
    """The hand-edited EU named-act dataset, CELEX -> {label?, abbr?} (each a str
    or a list). Source of the established short name and the citing acronym we
    stamp onto the artifact for the document page heading."""
    return json.loads(NAMEDACTS.read_text(encoding="utf-8"))


def _first(value):
    """The dataset stores `label`/`abbr` as a str or a list (the namedacts
    convention); the page heading wants a single value -- the first when a list."""
    return value[0] if isinstance(value, list) else value


def _isodate(value):
    """Formex DATE@ISO is compact ('20200716'); the artifact carries the dashed
    ISO form (what the page shows and what CELLAR's work date uses)."""
    if value and re.fullmatch(r"\d{8}", value):
        return "%s-%s-%s" % (value[:4], value[4:6], value[6:8])
    return value


def _last_eu_act(cites):
    """The CELEX of the last EU *act* a block's citations name -- the act a
    quotation following that block reproduces. None where the block names no
    act: a judgment, a treaty and a Charter article all carry no act type, and
    none of them is something a quotation of this shape reproduces."""
    for ref in reversed(cites):
        celex = celex_of(str(ref.uri))
        if celex and eu_akttyp(celex):
            return celex
    return None


def to_artifact(doc):
    """Project to the artifact JSON: metadata + body blocks whose text is an
    inline-run list (plain runs + {predicate,uri,text} citation links). Defined
    terms are extracted first (anchoring the definition points), then every block
    is scanned both for citations and for in-act uses of those terms."""
    parser = _refparser("eng" if doc.lang == "eng" else "swe")
    parser.reset()                          # fresh per-document state
    # a legislative act's own body cites its own articles by a bare "artikel N";
    # tell the parser its identity so those self-refer to it rather than
    # anaphora-pinning onto an external act a recital named (a judgment has no
    # such self-act -- its bare articles do refer to the act under discussion)
    if doc.doctype != "judgment":
        parser.state.self_eu_act = doc.celex
    matcher, index = build_matcher(extract_definitions(doc.body, doc.lang),
                                   doc.lang)
    body = []
    lead_act = None
    for b in doc.body:
        # inside a verbatim quotation, a bare "artikel N" is an article of the
        # *quoted* act -- the act the paragraph introducing the quotation named
        # ("Artikel 23 i förordningen har följande lydelse:"). Left to the
        # running anaphora it pins on whatever act the judgment last named
        # instead: Schrems II quotes GDPR article 23's reference to "artiklarna
        # 12-22 och 34" and linked all three to directive 95/46, which has 34
        # articles and none of that content.
        # neither a keyword nor a quotation is the judgment speaking, so what
        # they name must not stay in the document's anaphoric focus. A keyword
        # is an index entry: Schrems II's list ends on the Privacy Shield
        # decision, and its paragraph 1 -- "tolkningen av artikel 3.2 första
        # strecksatsen, artiklarna 25, 26 och artikel 28.3 i ... direktiv
        # 95/46/EG", which names its act only at the end -- pinned the first
        # three of those articles on that decision. A quotation is another
        # act's text, and is additionally *given* the act its lead-in named, so
        # its own bare "artikel N" is that act's article.
        borrowed = b.kind in ("keyword", QUOTATION)
        focus = parser.state.eu_focus() if borrowed else None
        if b.kind == QUOTATION and lead_act:
            parser.state.remember_eu_act(lead_act)
        cites = parser.parse_text(b.text, context={})
        if borrowed:
            parser.state.restore_eu_focus(focus)
        # term-use links yield to a citation wherever the spans overlap (a
        # citation is the stronger, cross-document link)
        uses = yield_overlaps(
            term_refs(b.text, matcher, index, doc.uri, b.anchor), cites)
        block = {"type": b.kind, "text": interleave(b.text, cites + uses)}
        for key in ("num", "level", "depth", "label", "quoted"):
            if getattr(b, key) is not None:
                block[key] = getattr(b, key)
        # the citation anchor is the artifact `id` -- the key the catalog
        # registers fragments under and the renderer emits as the element id, so
        # a citation to `<celex>#<article>` (or `#<article>.<point>` for a
        # definition) resolves to this block
        if b.anchor is not None:
            block["id"] = b.anchor
        if b.defines is not None:
            block["defines"] = b.defines
        if b.kind == "paragraph":
            # only a numbered paragraph introduces a quotation, and a run of
            # them is usually introduced once and referred back to thereafter
            # ("I artikel 3 i direktiv 95/46", then "i detta direktiv", "i
            # nämnda direktiv"), so the act carries forward until a later
            # paragraph names another one
            lead_act = _last_eu_act(cites) or lead_act
        body.append(block)
    art = {"uri": doc.uri, "celex": doc.celex, "doctype": doc.doctype,
           "lang": doc.lang, "title": doc.title, "date": _isodate(doc.date),
           "structure": nest(body)}
    # a short, distinctive human handle shown instead of the bare CELEX (the page
    # heading, the browse index / search, an inbound-citation label). The two
    # document families derive it differently:
    if doc.doctype == "judgment":
        # a case: its Formex "title" is "Domstolens dom (...) den ..." -- no use
        # as a name. The heading is the case's usual name / case number
        # ("Schrems II", "C-176/09"); an inbound citation adds the case number
        # ("C-311/18 (Schrems II)"). Stamped from lib.eucasenaming so the pure
        # catalog + renderer read them off the artifact without recomputing.
        art["shortname"] = eucasenaming.case_name(doc.celex)
        art["label"] = eucasenaming.case_citation(doc.celex)
    else:
        label = short_label(doc.title)
        if label:
            art["label"] = label
        # the document page heading: the established short name + citing acronym.
        # The short name is the curated `label` from the named-act dataset (rare),
        # else the act's own trailing-parenthesis short title; the acronym (`abbr`)
        # is only shown when the dataset carries one. Both absent -> the page falls
        # back to the full official title (which always sits in the metadata list).
        entry = _namedacts().get(doc.celex) or {}
        shortname = _first(entry.get("label")) or official_short_title(doc.title)
        if shortname:
            art["shortname"] = shortname
        abbr = _first(entry.get("abbr"))
        if abbr:
            art["abbr"] = abbr
    if doc.ecli:
        art["ecli"] = doc.ecli
    if doc.oj:
        art["oj"] = doc.oj
    return art


# format precedence -> parser route: (filename token, route). fmx4 (richest) >
# xhtml > html > pdf (last resort). xhtml is checked before html since "html" is
# a substring of "xhtml".
_TIERS = (("fmx4.zip", "fmx4"), ("fmx4", "fmx4"), ("xhtml", "html"),
          ("html", "html"), ("pdf", "pdf"))


def _route(path):
    """(rank, parser-route) for a content file by format precedence, or None.

    Matches the exact trailing suffix (e.g. ".fmx4", ".fmx4.zip"), not a bare
    substring: a stale `swe.fmx4.tmp` left behind by a hard-killed download
    (write_atomic's temp file, orphaned when the process dies before the
    rename) must not be mistaken for a real `.fmx4` content file."""
    for rank, (token, route) in enumerate(_TIERS):
        if path.name.endswith("." + token):
            return rank, route
    return None


def content_file(doc_dir, languages=LANG_PREFERENCE):
    """The best content file in a document dir as (path, lang, route), preferring
    language (swe then eng) then format (fmx4 > xhtml > html > pdf). The download
    already kept only the best format per language; this picks across what landed.
    (None, None, None) if the dir has no content file."""
    for lang in languages:
        ranked = sorted((rank, route, cand)
                        for cand in compress.glob(doc_dir, lang + ".*")
                        if (r := _route(cand)) for rank, route in (r,))
        if ranked:
            _, route, path = ranked[0]
            return path, lang, route
    return None, None, None


def parse_content(path, route, celex, lang):
    """Dispatch a content file to its format's parser -> EurlexDoc."""
    if route == "fmx4":
        return parse_document(formex_roots(path, "eurlex", celex), celex, lang)
    if route == "html":
        data = compress.read_bytes(path)
        if patch.has_patch("eurlex", celex):
            data = patch.apply("eurlex", celex, markup.block_lines(
                data.decode("utf-8", "replace"))).encode("utf-8")
        return parse_html(data, celex, lang)
    if route == "pdf":
        return parse_pdf(path, celex, lang)
    raise ValueError("no parser for route %r" % route)


# a corrigendum CELEX: the parent act's number + 'R(NN)'
RE_CORRIGENDUM = re.compile(r"R\(\d+\)$")


def revision_repeal_date(celex):
    """The repeal date a '(NN)' revision inherits from the document it revises
    (`eu_structure.revision_base`), or None.

    CELLAR records `resource_legal_in-force` on the act, never on its
    corrigenda -- measured: of 537 held corrigenda whose base act we also hold,
    exactly none carry the flag. So repealing an act expires the act's own row
    and leaves its corrigenda ranked in search, listed by `/api/v1/documents`
    and standing on a paragraf's rail as if nothing had happened. (Not in the
    browse: `facets._is_browsable` drops every 'R(NN)' CELEX from the eurlex
    tree, so an act's corrigendum was never a row there.) Four held acts with
    five corrigenda are in that position today, all repealed during 2026, so it
    is a growing set rather than a historical quirk.

    A treaty revision is browsable, and is the sharper case:
    `facets._keep_latest_eu_revision` collapses '12019W/TXT' and
    '12019W/TXT(01)' onto one entry and keeps the *higher* revision, so there
    the repealed base is dropped and the unflagged revision is the row that
    survives.

    Reading the base's notice (not its artifact) keeps this a metadata lookup
    with no parse-order dependency; `build.eurlex_parse_notices` puts that file
    in the corrigendum's freshness inputs so repealing the act restales it."""
    base = revision_base(celex)
    return notice_repeal_date(layout.eurlex_dir(base)) if base else None


def _plausible_date(value):
    """A Formex DATE@ISO can be garbled at digitisation (61981CJ0025 carries
    '19820231' -- the 31st of February); an impossible calendar date cannot be
    the document's, so it yields to the notice work date."""
    try:
        date.fromisoformat(_isodate(value))
        return True
    except ValueError:
        return False


# Acts this corpus deliberately does not carry, CELEX -> why. `parse_dir` raises
# `SkipDocument` for them, and the driver writes the empty artifact that marks a
# document built-and-not-to-be-retried: the catalog then drops its row
# (`catalog`: an artifact with no content is not a document) and the index its
# units. It does **not** remove an already-rendered page -- nothing in the driver
# reaps `generated/`, so uncarrying an act that was previously carried means
# unlinking its html by hand, here and on prod.
#
# The bar is *the document cannot be served*, not "it is awkward": an act that
# merely parses badly is a bug to fix, and one that is genuinely repealed or empty
# already has its own path. Each entry states the measurement that meets that bar,
# in terms that stay true -- a fact about one build does not -- and enough for a
# later reader to retest the claim rather than inherit it. The downloaded Formex
# stays on disk, so the retest is always: drop the entry, `lagen eurlex parse
# <celex>`, reindex, and look for the failure named below.
UNCARRIED = {
    "32018R0688":
        "annex I is a 6,000-page table of the EBA's reference portfolios: a 97 MB "
        "Formex file that parses to 50,493,892 characters and renders to a 53 M "
        "character page. OpenSearch's JSON parser refuses a string field past "
        "50,000,000 -- bracketed against a live cluster, 50 MB accepted and 52 MB "
        "rejected with mapper_parsing_exception -- so the whole-document unit and "
        "its bilaga-1 fragment can never be indexed, and the page is past what a "
        "reader can be served in any case. Nothing in the corpus cites it (0 rows "
        "in `links`), so dropping it dangles no reference",
}


def parse_dir(doc_dir, celex):
    """A document dir -> artifact dict: the best content file parsed, the
    notice work date filling in a missing or impossible document date. None
    when the dir has no swe/eng content -- the parse pipeline's single entry
    point per CELEX.

    A corrigendum's Formex bibliography carries the *corrected act's* date, not
    its own; its notice work date (the correcting OJ's publication) is the
    document's actual date, so it wins there.

    An act CELLAR reports as no longer in force carries the date it stopped
    (`expired`, from notice_repeal_date), which is what drops it out of the
    listings. A corrigendum inherits its base act's date
    (`revision_repeal_date`): CELLAR flags the act, never its corrigenda, and
    the browse shows the corrigendum in the act's place.

    An `UNCARRIED` act raises SkipDocument before anything is opened: its source
    is on disk and will never be servable, so the driver's empty-artifact marker
    is the honest outcome -- the alternative is a per-document failure on every
    build, forever, which only teaches the operator to ignore a red exit."""
    if celex in UNCARRIED:
        raise SkipDocument("%s: %s" % (celex, UNCARRIED[celex]))
    path, lang, route = content_file(doc_dir)
    if path is None:
        return None
    doc = parse_content(path, route, celex, lang)
    if (doc.date is None or not _plausible_date(doc.date)
            or RE_CORRIGENDUM.search(celex)):
        doc.date = notice_work_date(doc_dir) or doc.date
    art = to_artifact(doc)
    repealed = notice_repeal_date(doc_dir) or revision_repeal_date(celex)
    if repealed:
        art["expired"] = repealed
    # the act's own jämförelsetabell, read *after* to_artifact because the
    # header's "Direktiv 2004/18/EG" is identified by the citation link minted
    # there. Empty for all but ~2% of sector-3 acts and every judgment, so this
    # costs nothing on the rest (correspond.correspondence).
    edges, _stats = correspondence(art)
    if edges:
        art["correspondence"] = edges
    return art
