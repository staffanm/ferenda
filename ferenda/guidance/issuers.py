"""Which bodies' vägledningar this source carries, as *data*: what each body is
called, how it divides its output into series, how a document in a series is
numbered, and how that number is cited.

The source exists because EU soft law is the reading a Swedish reader of an EU
rättsakt actually meets. A riktlinje binds nobody -- the body states, in
advance and in general, how a förordning or ett direktiv is to be read, and the
myndigheter and domstolar that apply it are free to read it otherwise. What
makes it worth carrying is measured, not assumed: one Swedish EBA-riktlinje
(EBA/GL/2021/05, om intern styrning) cites 143 rättsakter, 138 of which point
at acts this site already holds, and 59 of those at kapitaltäckningsdirektivet
alone. Each of those becomes a rail entry on the artikel it interprets.

**Identity is the number the issuing body gave the document**, never a CELEX
and never a diarienummer. That is the rule `edpb` established and `avg`/`rs`
follow, and it is what a citation names: 122 förarbeten cite an ECB-yttrande as
"CON/2013/82" and none of them cite it as "52013AB0082". The URI reproduces the
citation, which is why the series is a segment rather than decoration -- in
every series carried here the series *is* part of the number ("Riktlinjer
05/2020", "EBA/GL/2021/05", "CON/2013/82"). Where a body numbers its output in
one sequence across types the segment is simply absent, as it is for ESRB
(ESRB/2014/1 is a rekommendation, ESRB/2015/1 a beslut, and no number is used
twice).

Dropping the series segment is not an option this registry leaves open, because
for at least one body it loses documents: the EDPB restarts its numbering at 01
in each series every year, so `01-2020` is both a riktlinje and a
rekommendation. Six numbers collide that way -- twelve of the sixty documents.

**Two harvest routes**, named per issuer:

  * ``site`` -- the body's own pages. The only route for the ESA:ernas
    regulatory series, which the EU:s publikationsbyrå does not hold: a SPARQL
    census of what CELLAR carries under the EBA:s corporate-body URI returns
    169 works, and they are vacancy notices, not riktlinjer.
  * ``eurlex`` -- guidance published in EUT, harvested through the CELLAR
    machinery `eurlex/download.py` already runs but stored and served here,
    under this body's own kod. `eurlex` carries sector 1, parts of 3 and parts
    of 6; guidance is none of those, and putting an ECB-yttrande under a CELEX
    address would give it an identity nobody cites it by.

Nothing in this module reaches into a body's pages. The per-issuer harvest
routines do that; this states what is true about each series so the shared
engine can be told rather than taught.
"""

import re
from dataclasses import dataclass

from ..lib.util import own_number_slug

# ---------------------------------------------------------------------------
# numbering
# ---------------------------------------------------------------------------
# A series number has two components -- the löpnummer and the year -- and the
# bodies disagree about their order. The EDPB writes the löpnummer first
# ("Riktlinjer 05/2020"), the EBA the year ("EBA/GL/2021/05"). Both are kept in
# the body's own order in the slug, so the address reads like the citation; the
# only normalisation is padding the löpnummer to two digits, because a body
# pads inconsistently ("Riktlinjer 05/2020" beside "Riktlinjer 1/2018") and a
# citation copies whichever it saw. One document, one address, either spelling.
LOPNUMMER_FORST = ("lopnummer", "ar")
AR_FORST = ("ar", "lopnummer")
# A body that does not number NN/ÅÅÅÅ at all has one component: the whole
# number. Esma writes "ESMA35-43-3448" -- a kommittéprefix, a serie and a
# löpnummer, with no year anywhere -- and its older documents "ESMA/2016/1477",
# so there is nothing to reorder and nothing to pad. The slug is then the
# number's own characters (`own_number_slug`), which keeps the address reading
# like the citation without claiming a component structure the body does not use.
HELA_NUMRET = ("nummer",)

RE_PAR = re.compile(r"\b(\d{1,4})/(\d{1,4})\b")
# every run of characters a URI segment should not carry


def number_slug(number, order):
    """The URI/file form of a series number, in the issuing body's own
    component order. ``("5/2020", LOPNUMMER_FORST)`` -> ``05-2020``;
    ``("2021/5", AR_FORST)`` -> ``2021-05``."""
    match = RE_PAR.fullmatch(number.strip())
    assert match, "not a series number: %r" % number
    parts = dict(zip(order, match.groups(), strict=True))
    assert len(parts["ar"]) == 4, \
        "year component is not a year: %r (order %r)" % (number, order)
    return "%s-%s" % ((parts["ar"], "%02d" % int(parts["lopnummer"]))
                      if order == AR_FORST
                      else ("%02d" % int(parts["lopnummer"]), parts["ar"]))


@dataclass(frozen=True)
class Series:
    """One running series of one issuing body."""
    kod: str | None             # our short code, the URI segment
                                # ("riktlinjer"); None for a body that numbers
                                # in one sequence across its document types,
                                # whose address then carries no series segment
    label: str                  # what the collection is called in Swedish
    identifier: str             # citation form, %-formatted with the number
    order: tuple | None         # component order of the number, None when the
                                # series is not numbered NN/ÅÅÅÅ at all (the
                                # closed WP29 series, numbered "WP 248")
    doctype: str | None = None  # the body's own type segment/facet, where its
                                # index is filtered by one; None where the
                                # series is enumerated some other way
    publisher: str | None = None    # who issues it, when that is not the
                                    # issuer itself -- the artikel 29-gruppens
                                    # vägledningar are carried under the EDPB,
                                    # which endorsed them, but the working
                                    # party wrote them
    note: str = ""              # what is peculiar about this series

    def slug(self, nummer):
        """This series' URI segment for one number."""
        if self.order == HELA_NUMRET:
            return own_number_slug(nummer)
        return nummer if self.order is None else number_slug(nummer, self.order)


@dataclass(frozen=True)
class Issuer:
    """One issuing body."""
    kod: str                    # the URI segment ("edpb", "eba")
    namn: str                   # its Swedish name (dcterms:publisher)
    kortnamn: str               # how it is cited in running text ("EBA")
    route: str                  # "site" | "eurlex"
    series: tuple               # its Series, in the order a listing shows them
    base: str                   # its own site, for source_url resolution
    feta_rubriker: bool = False     # does the body's template mark a heading
                                    # **bold**? Most templates carried here do
                                    # not, so the block layer reads a size
                                    # above the running text as the heading
                                    # signal (`pdftext.heading_levels`'
                                    # by_size). EUIPO:s does -- its running
                                    # head and footer are set at one size and
                                    # its body text at another, so "larger than
                                    # the commonest size" calls every paragraph
                                    # of prose a heading, while bold marks the
                                    # headings and nothing else.
    upprepat_sidhuvud: bool = False  # does the body's template reprint a
                                    # running head at the top of every page?
                                    # Most set only a footer, which the shared
                                    # masthead pattern names. EUIPO reprints
                                    # the avsnittets own name, which differs
                                    # per document and can only be found by its
                                    # repetition in the page margin
                                    # (`pdftext.strip_page_furniture`).
    note: str = ""

    @property
    def koder(self):
        return tuple(s.kod for s in self.series)

    def serie(self, kod):
        return BY_SERIE[(self.kod, kod)]


EDPB_NAMN = "Europeiska dataskyddsstyrelsen"
EDPS_NAMN = "Europeiska datatillsynsmannen"
WP29_NAMN = "Artikel 29-gruppen"
EBA_NAMN = "Europeiska bankmyndigheten"
EASA_NAMN = "Europeiska unionens byrå för luftfartssäkerhet"
ENISA_NAMN = "Europeiska unionens cybersäkerhetsbyrå"
ACER_NAMN = ("Europeiska unionens byrå för samarbete mellan "
             "energitillsynsmyndigheter")
ESMA_NAMN = "Europeiska värdepappers- och marknadsmyndigheten"
BEREC_NAMN = ("Organet för europeiska regleringsmyndigheter för "
              "elektronisk kommunikation")
EIOPA_NAMN = ("Europeiska försäkrings- och "
              "tjänstepensionsmyndigheten")
EUIPO_NAMN = "Europeiska unionens immaterialrättsmyndighet"


EDPB = Issuer(
    kod="edpb", namn=EDPB_NAMN, kortnamn="EDPB", route="site",
    base="https://www.edpb.europa.eu",
    series=(
        Series(kod="riktlinjer", label="Riktlinjer", identifier="Riktlinjer %s",
               order=LOPNUMMER_FORST, doctype="guideline",
               note="the main series; the EDPB numbers it inconsistently "
                    "(01/2020 beside 1/2018), so the number is normalised for "
                    "the URI and kept verbatim for the citation"),
        Series(kod="rekommendationer", label="Rekommendationer",
               identifier="Rekommendation %s", order=LOPNUMMER_FORST,
               doctype="recommendation",
               note="the same numbering; the EDPB's own Swedish titles "
                    "alternate between 'Rekommendation 01/2019' and "
                    "'Rekommendationer 02/2020', and the title is left as the "
                    "document wrote it"),
        Series(kod="wp", label="Artikel 29-gruppens vägledningar",
               identifier="WP %s", order=None, doctype=None,
               publisher=WP29_NAMN,
               note="closed: the WP29 documents the EDPB endorsed on 25 May "
                    "2018, enumerated in `edpb_data.WP29`. No NN/ÅÅÅÅ number "
                    "-- these are cited by the working party's WP number"),
    ),
    note="the first source of EU soft law here, and the one whose shape the "
         "rest follow: identity is the body's own number, the numbered punkt "
         "is the citable unit, and the language and version of the text served "
         "are stated on the page rather than left to be assumed")

EBA = Issuer(
    kod="eba", namn=EBA_NAMN, kortnamn="EBA", route="site",
    base="https://www.eba.europa.eu",
    series=(
        Series(kod="gl", label="Riktlinjer", identifier="EBA/GL/%s",
               order=AR_FORST,
               note="the artikel 16-series. Its Swedish text exists by law "
                    "rather than by favour: förordning (EU) nr 1093/2010 "
                    "makes a riktlinje effective only once translated into "
                    "every official language, and publication of the "
                    "translations starts the two months in which "
                    "Finansinspektionen must state whether it complies"),
        Series(kod="rec", label="Rekommendationer", identifier="EBA/REC/%s",
               order=AR_FORST,
               note="the same procedure, far fewer documents"),
    ),
    note="the index is NOT the publications listing. Its 'Guidelines' facet "
         "returns 149 rows, but they are final reports and consolidated texts "
         "*about* guidelines, they carry no EBA/GL number, and they link "
         "straight to a PDF with no document page. The numbered riktlinjer "
         "live in the single-rulebook tree: 36 topic pages under "
         "/regulation-and-policy/<ämne>, each listing its documents as leaves "
         "under /activities/single-rulebook/regulatory-activities/<ämne>/"
         "<slug>. Walking the 36 gives 289 leaves, of which 127 are riktlinjer "
         "and 138 are tekniska standarder -- the standarder become "
         "kommissionsförordningar in EUT and belong to `eurlex`, not here")


EASA = Issuer(
    kod="easa", namn=EASA_NAMN, kortnamn="EASA", route="site",
    base="https://www.easa.europa.eu",
    series=(
        Series(kod="amc-gm", label="AMC & GM", identifier="%s", order=None,
               note="an annex holding both the godtagbara sätten att uppfylla "
                    "kraven (AMC) and the vägledande materialet (GM) to one "
                    "rule -- 453 of the 566 documents in the library. The "
                    "series is a segment because it is the lead of the "
                    "document's own name and the rest of the name is not "
                    "unique without it; see the `amc` note"),
        Series(kod="amc", label="AMC", identifier="%s", order=None,
               note="an annex holding the godtagbara sätten alone. Splitting "
                    "AMC from GM is not cosmetic: 'GM to Part M — Amendment 4' "
                    "(2015) and 'AMC to Part-M — Amendment 4' (2008) are two "
                    "documents differing in nothing but this lead, because AMC "
                    "and GM to one rule run separate amendment sequences"),
        Series(kod="gm", label="GM", identifier="%s", order=None,
               note="an annex holding the vägledande materialet alone"),
    ),
    note="**the ED Decision is the instrument, not the document.** EASA issues "
         "its AMC/GM as numbered annexes to a decision of the Executive "
         "Director ('ED Decision 2026/006/R'), and one decision carries "
         "several: 2026/006/R issues four annexes, each with its own page in "
         "the document library and its own text. Filing them under the "
         "decision number would give four documents one address, so the "
         "decision is recorded as `beslut` and the identity is the annex's own "
         "name -- which is what the annex's cover prints for itself: 'Annex IV "
         "to ED Decision 2022/005/R \u2018AMC and GM to Annex IV (Part-CAT) to "
         "Commission Regulation (EU) No 965/2012 \u2014 Issue 2, Amendment "
         "20\u2019'. A reader cites the AMC/GM item inside it ('AMC1 "
         "CAT.OP.MPA.100'), and the item belongs to a rule annex at a stated "
         "Issue and Amendment.\n"
         "The name is not a number in the sense the other bodies here use, so "
         "`order` is None and the `nummer` is the name's own slug "
         "('part-cat-issue-2-amendment-20'); `identifier` is never reached, "
         "because every EASA document carries a `citation` -- the name "
         "verbatim -- the way the one unnumbered WP29 document does.\n"
         "The amendment belongs to the identity rather than to `version`: "
         "these are as-published immutables. EASA keeps every amendment, each "
         "PDF holds only that amendment's own text, and a superseded one is "
         "marked 'Repealed' rather than replaced.\n"
         "EASA publishes in English only. There is no Swedish AMC/GM to take, "
         "so every record says so and the page tells the reader why")

ACER = Issuer(
    kod="acer", namn=ACER_NAMN, kortnamn="ACER", route="site",
    base="https://www.acer.europa.eu",
    series=(
        Series(kod="ramriktlinjer", label="Ramriktlinjer", identifier="%s",
               order=None, doctype="framework-guidelines",
               note="the closest thing ACER has to a riktlinje: artikel 59 in "
                    "förordning (EU) 2019/943 has the kommissionen ask ACER "
                    "for a 'non-binding framework guideline' setting out the "
                    "principles a coming nätföreskrift must follow, and the "
                    "nätföreskrifter it seeds (CACM-förordningen (EU) "
                    "2015/1222, driftförordningen (EU) 2017/1485) are "
                    "rättsakter this site holds.\n"
                    "**They carry no number.** Only 5 of the 11 print a code "
                    "of their own, the code is not one series (FG-2011-E-001, "
                    "FG-2011-G-001 and FGB-2011-G-002 are three prefixes), and "
                    "the six newest print none at all -- so `order` is None, "
                    "the `nummer` is a slug of the ramriktlinjens own name "
                    "('demand-response', 'electricity-grid-connections') and "
                    "`identifier` is never reached, because every one carries "
                    "a `citation`: the name ACER lists it under, which is also "
                    "how ACER cites it ('the TYNDP Scenarios Framework "
                    "Guidelines adopted in January 2023', yttrande 13/2026)"),
        Series(kod="rekommendationer", label="Rekommendationer",
               identifier="ACER Recommendation No %s", order=LOPNUMMER_FORST,
               doctype="recommendations",
               note="artikel 6(2) in förordning (EU) 2019/942: ACER "
                    "recommends to the nationella tillsynsmyndigheterna and to "
                    "ENTSO how a nätföreskrift is to be applied or amended. "
                    "41 listed, 40 numbered"),
        Series(kod="yttranden", label="Yttranden",
               identifier="ACER Opinion No %s", order=LOPNUMMER_FORST,
               doctype="opinions",
               note="the largest series and the ECB-yttrandenas counterpart: "
                    "ACER states in advance how a stated artikel applies to a "
                    "document put before it -- an ENTSO tioårig "
                    "nätutvecklingsplan, a nationell tillsynsmyndighets "
                    "beslut, a utkast till nätföreskrift. 222 numbers over "
                    "2011-2026, of which only 6 are missing from the listing"),
    ),
    note="**the löpnummer comes first and the series segment is load-bearing.** "
         "ACER's covers print 'OPINION No 13/2026' and 'RECOMMENDATION No "
         "02/2026', so the number reads like the EDPB:s and unlike the EBA:s. "
         "Each series restarts at 01 every year and the sequences are "
         "independent, so 01/2013 is a yttrande, a rekommendation *and* a "
         "beslut -- three documents, one number, exactly the collision the "
         "EDPB entry above describes.\n"
         "ACER numbers its beslut in one sequence across the two kinds it "
         "publishes: 01-2011 to 06-2011 are the general ones and 01-2014 "
         "onward the individual ones, with no restart between them.\n"
         "**Three of the five kategorier under /documents/official-documents "
         "are left out**, and each for its own reason:\n"
         "  * *individual decisions* (154) are bindande beslut in a named "
         "    case, taken under artikel 6(10) in förordning (EU) 2019/942 when "
         "    the nationella tillsynsmyndigheterna fail to agree, addressed to "
         "    the parties that asked and appealable to ACER:s "
         "    överklagandenämnd. That is adjudication, which is what `avg` and "
         "    `dv` are for, not guidance stated in advance and in general.\n"
         "  * *general decisions* (7) are the instrument, not the document: "
         "    six of them adopt a ramriktlinje that this source already "
         "    carries in full, and filing the one-page adoption act beside it "
         "    would give one text two addresses. The seventh, ACER:s guidance "
         "    on the evaluation procedure for network code amendment "
         "    proposals (2013), is genuine guidance and is the one document "
         "    this exclusion costs.\n"
         "  * *director decisions* (30) are intern administration -- public "
         "    holidays, reserve lists, traineeship rules -- and are numbered "
         "    the other way round ('Director Decision 2025-24'), which is the "
         "    tell that they are not part of ACER:s numbered utåtriktade "
         "    output at all.\n"
         "ACER publishes in English only, so every record says so.")

ESMA = Issuer(
    kod="esma", namn=ESMA_NAMN, kortnamn="Esma", route="site",
    base="https://www.esma.europa.eu",
    series=(
        Series(kod="riktlinjer", label="Riktlinjer och rekommendationer",
               identifier="%s", order=HELA_NUMRET, doctype="45",
               note="one series, because Esma runs one: the library's own "
                    "document-type facet lumps riktlinjer and rekommendationer "
                    "under a single term ('Guidelines & Recommendations', term "
                    "id 45, which is what `doctype` holds), the covers "
                    "alternate between 'Riktlinjer' and 'Riktlinjer och "
                    "rekommendationer' for documents in the same numbering, "
                    "and nothing in the number says which a document is. "
                    "Splitting them would be our editorial act, not Esmas.\n"
                    "**The series segment is not part of the citation here**, "
                    "and that is a departure from every other body in this "
                    "registry. A reader cites 'ESMA35-43-3448' with no series "
                    "token in it at all. The segment is kept because it is "
                    "what Esmas own library types the document as, and "
                    "because the number stays whole inside it -- the address "
                    "`guidance/esma/riktlinjer/esma35-43-3448` still reads as "
                    "the number. `order=HELA_NUMRET` says the same thing about "
                    "the number: it has no löpnummer/år components to reorder"),
    ),
    note="**the identity is the library's own Reference column.** Esma runs a "
         "register, not just a listing: every row of "
         "/databases-library/esma-library carries the document's number in a "
         "column of its own, and the document prints the same number on its "
         "cover in 123 of the 126 covers read. The three that differ are "
         "Esmas own inconsistencies, and in one of them the *cover* is wrong "
         "-- ESMA70-151-435 ('Samarbete mellan myndigheter enligt artiklarna "
         "17 och 23') prints ESMA70-151-294 in its footer, which is another "
         "riktlinje entirely. So the cover corroborates the number and is "
         "counted every run; it does not overrule it, which is the opposite "
         "of the EBA rule and for the opposite reason (the EBA leaf pages "
         "state no number at all).\n"
         "Esma writes the number three ways and the library only two of them: "
         "the modern 'ESMA35-43-3448' verbatim, the Joint Committee's "
         "'JC/GL/2024/36' verbatim, and the pre-2017 documents as '2016/1477' "
         "in the column where every one of the 34 covers prints "
         "'ESMA/2016/1477'. The harvest restores that prefix, because the "
         "prefixed form is what the document itself is cited by.\n"
         "**The listing is one row per language before 2017.** A pre-2017 "
         "document is filed once per translation, each row carrying that "
         "language's own title and file, so the 641 rows of the Guidelines & "
         "Recommendations facet are 153 documents. The Swedish row is where "
         "the Swedish title of an older riktlinje comes from; the modern rows "
         "carry the translations in an expandable panel instead and title them "
         "only in English.\n"
         "**What is left out, and why.** The facet is not a clean index: it "
         "types 22 documents as guidelines whose own covers say they are "
         "something else -- 8 slutrapporter, 3 vacancy notices, an OPINION, a "
         "Joint Consultation Paper, a DECISION OF THE BOARD OF SUPERVISORS "
         "withdrawing a riktlinje, a NOTE, and a blank compliance-confirmation "
         "form. Three more link an .xlsx or a .zip behind a .pdf address. Two "
         "are CESR's (CESR/09-219, CESR/04-505b), predate Esma and are cited "
         "by CESR's number, and six carry the literal text 'Joint Committee' "
         "where the number should be. All of these are declined against the "
         "document's own cover and counted per reason, never dropped "
         "silently.\n"
         "**Yttranden (Opinions) are deliberately not carried.** The facet "
         "holds 268, and they are supervisory administration rather than "
         "guidance: 78 approve one position limit on one commodity contract, "
         "33 approve one national emergency measure, 18 comment on a draft "
         "RTS, 7 are budget discharge reports, and dozens more approve one "
         "member state's product intervention. None of the 268 exists in "
         "Swedish. Q&A (53) and compliance tables (94) are left out for a "
         "plainer reason: a compliance table states which national authority "
         "follows a riktlinje, which is metadata about a riktlinje rather "
         "than guidance, and it is published as a spreadsheet.")


BEREC = Issuer(
    kod="berec", namn=BEREC_NAMN, kortnamn="Berec", route="site",
    base="https://www.berec.europa.eu",
    series=(
        Series(kod="riktlinjer", label="Riktlinjer", identifier="%s",
               order=HELA_NUMRET,
               note="one series, because Berec runs one numbering for "
                    "everything it publishes. 'BoR (22) 81' is these "
                    "riktlinjer, 'BoR (22) 80' a rapport and 'BoR (22) 163' "
                    "ett yttrande -- one sequence, no restart per type -- so "
                    "the number carries no series token and `identifier` is "
                    "the number itself. `order=HELA_NUMRET` says the same: a "
                    "two-digit year in parentheses and a serial are not the "
                    "löpnummer/år pair `number_slug` reorders, and the slug "
                    "keeps the number whole instead "
                    "(`guidance/berec/riktlinjer/bor-22-81`). The series "
                    "segment is ours, not Berecs, for the reason the Esma "
                    "entry above gives: it is what Berecs own register types "
                    "the document as"),
    ),
    note="**the site is a document register, and the register states the "
         "number.** Berec runs its listing under "
         "/en/all-documents/<gren> (mirrored at /en/document-categories/), "
         "each category page declaring its own row count and printing a "
         "four-column table whose first column is the document number. All 78 "
         "rows of the Guidelines category carry one, all 78 leaf pages repeat "
         "it as a 'Document number' field, and the two agree everywhere -- so "
         "unlike the EBA this body needs no cover read to be named. "
         "/sitemap.xml is useless: five language home pages and nothing "
         "else.\n"
         "**The cover still rules on spelling.** The register writes "
         "'BoR(22)147' with no spaces and 'BoR (10) 44  Rev 1' with two, where "
         "both covers write the number the way the other 76 rows do. "
         "`berec_download.nummer` rewrites the register's text into that "
         "spelling, and `parse._berec_fields` re-reads the cover so a file "
         "that changes behind its URL fails the parse.\n"
         "**43 of the 78 rows are taken.** Declined and counted: 18 utkast "
         "('Draft BEREC Guidelines …', one a track-changes copy), which Berec "
         "numbers separately from the text it later adopts; 1 comparison "
         "document, a diff of the 2020 and 2022 open-internet riktlinjer that "
         "Berec files under BoR (22) 81 -- *the riktlinjes own number* -- so "
         "taking it would put two documents at one address; and 16 rows whose "
         "register entry has lost its file, which Berecs own page shows by "
         "printing 'PDF - ' with no size and leaving the title unlinked. "
         "Those 16 are the 7 scoping-och-förslagsdokument, the 5 internal "
         "guidelines on Berecs own working procedure, and 4 others.\n"
         "**The neighbouring categories are counted, not assumed**, and are "
         "not this source's business: 560 rapporter, 185 yttranden, 96 "
         "beslut, 74 arbetsprogram and 2,142 samrådssvar. The two arguable "
         "ones sit beside the riktlinjer under Regulatory Best Practices -- 12 "
         "gemensamma ståndpunkter and 7 metoddokument -- and are left for a "
         "decision of their own.\n"
         "Berec publishes in English only; the leaf pages offer no other "
         "language, so every record says so.")


ENISA = Issuer(
    kod="enisa", namn=ENISA_NAMN, kortnamn="ENISA", route="site",
    base="https://www.enisa.europa.eu",
    series=(
        Series(kod="rapporter", label="Rapporter", identifier="%s", order=None,
               note="ENISA divides its output into no series at all: one flat "
                    "listing, one 'Publication type' field on the leaf, and "
                    "two values in it. The rapporter are the guidance -- "
                    "'NIS2 Technical Implementation Guidance', the 'Secure by "
                    "Design and Default Playbook' -- and this is the series "
                    "they file under. The other value, corporate documents, "
                    "is not carried; see the issuer note"),
    ),
    note="**the body's own site is the index.** /publications lists 587 "
         "rapporter over 59 pages, while a CELLAR census under ENISA:s "
         "corporate-body URI returns 249 works, so routing this through the "
         "EU:s publikationsbyrå would lose more than half the corpus.\n"
         "**ENISA numbers nothing.** No series code, no year-serial, no "
         "diarienummer: the covers carry a title and a month and that is all. "
         "So `order` is None and the `nummer` is the last segment of the "
         "report's own address ('enisa-secure-by-design-and-default-playbook'), "
         "which is the only stable key ENISA publishes -- unique across all "
         "587 and asserted to stay so at harvest. `identifier` is never "
         "reached, because every ENISA record carries a `citation`: the "
         "report's title, which is what a citation to one actually names. That "
         "makes this body unlike every numbered series here, and it is carried "
         "anyway because of what the reports link *to*: the citation grammar "
         "already found 6 correct CELEX references in the Secure by Design "
         "playbook with no new work, and its Annex C maps 11 principles onto "
         "CRA-bilaga I punkter (ANNEX-1.PT1.2.d and its like, 85 occurrences) "
         "-- outbound links into rättsakter this site holds is the whole "
         "payload.\n"
         "**Corporate documents are declined**, and counted: the annual "
         "activity reports, the single programming documents and the "
         "stakeholder strategy are ENISA:s own administration, not guidance on "
         "how a rättsakt is to be met. The untyped leaves -- the older "
         "briefings and info notes, published before ENISA had the field -- "
         "are taken, because they are reports in everything but the label.\n"
         "**English only, with one exception measured**: 585 of the 587 leaves "
         "offer English alone, and the SME cybersecurity guide exists in all "
         "24 official languages, Swedish included. The Swedish text is taken "
         "where there is one, and every record says which language it holds.\n"
         "The site is behind CloudFront with a rate rule that answers 429 and "
         "an unparsable 'Retry-After: 0.000'; `enisa_download` paces itself "
         "and mounts its own transport retry for it.")

EDPS = Issuer(
    kod="edps", namn=EDPS_NAMN, kortnamn="EDPS", route="site",
    base="https://www.edps.europa.eu",
    series=(
        Series(kod="riktlinjer", label="Riktlinjer", identifier="%s",
               order=None, doctype="guidelines",
               note="the EDPS's reading of förordning (EU) 2018/1725, which is "
                    "the EDPB's dataskyddsförordning written for unionens egna "
                    "institutioner -- so these are the EDPB's riktlinjer with "
                    "the other addressee. 42 documents, English only: none has "
                    "ever been issued in Swedish"),
        Series(kod="yttranden", label="Yttranden", identifier="%s",
               order=None, doctype="opinions",
               note="the artikel 42-yttranden on the kommissionens "
                    "lagförslag, 400 of them back to 2004. 130 hold the "
                    "EDPS's own Swedish text, because a yttrande was printed "
                    "whole in EUT C in every official language until 2015; "
                    "after that the EDPS translates a sammanfattning and "
                    "publishes the yttrande itself in English"),
    ),
    note="**the EDPS numbered nothing until 2020, and numbers its riktlinjer "
         "still not at all.** A yttrande has carried 'Opinion NN/ÅÅÅÅ' since "
         "2020 (111 of 400 do); a riktlinje's cover prints a title and a date "
         "and no number, in all 42. So both series take `order=None` and every "
         "document carries a `citation`, the way EASA's do -- but the two "
         "spellings of a `nummer` differ by whether the EDPS gave one: a "
         "numbered yttrande is filed under it, slugged with `number_slug` "
         "exactly as the EDPB's are ('11-2023' for Opinion 11/2023, so the "
         "address still reproduces the citation), and everything else is filed "
         "under the date it was published and its own URL segment "
         "('2018-03-16-guidelines-use-cloud-computing-services-european').\n"
         "The number is read off the **PDF cover**, never off the listing: two "
         "rows in three drop it from the title, and the row titled 'Digital "
         "Services Act' is Opinion 1/2021.\n"
         "The citation form is ours and the EDPS's jointly: the EDPS's own "
         "name for the series plus the year it published ('EDPS yttrande "
         "(2018)'), because the body itself cites an unnumbered document by "
         "its title and year and there is no number to put in its place. The "
         "title carries the subject on the page beside it.\n"
         "Route `site` is measured, not assumed. CELLAR holds 367 EDPS works: "
         "111 full yttranden dated 2004-10-22 to 2017-03-21, 119 "
         "*sammanfattningar* whose own title says the full text is on the "
         "EDPS's site, and 137 vacancy notices, arbetsordningar and "
         "årsrapporter. It holds no riktlinje and nothing published after "
         "2023-11-20; the site holds all of it, the old EUT offprints "
         "included.")


EIOPA = Issuer(
    kod="eiopa", namn=EIOPA_NAMN, kortnamn="Eiopa", route="site",
    base="https://www.eiopa.europa.eu",
    series=(
        Series(kod="riktlinjer", label="Riktlinjer", identifier="%s",
               order=HELA_NUMRET, doctype="guidelines",
               note="the artikel 16-series, and the reason a Swedish text "
                    "exists at all: förordning (EU) nr 1094/2010 makes a "
                    "riktlinje effective only once it is translated into every "
                    "official language, and publication of the translations "
                    "starts the two months in which Finansinspektionen must "
                    "state whether it complies. Eiopa cites artikel 73 in the "
                    "same förordning on the library page itself"),
        Series(kod="rekommendationer", label="Rekommendationer",
               identifier="%s", order=HELA_NUMRET, doctype="recommendations",
               note="the same procedure and the same numbering; a separate "
                    "series only because Eiopas own library keeps a separate "
                    "facet for it. The number cannot tell the two apart -- "
                    "riktlinjer and rekommendationer share one Board of "
                    "Supervisors sequence -- so the facet a document is listed "
                    "under is what types it, which is Eiopas own act, not ours"),
    ),
    note="**the number is a Board of Supervisors register number, not a "
         "löpnummer/år pair.** Eiopa writes it as a two-digit year and a "
         "serial and spells it every way a text can be spelled: "
         "'EIOPA-BoS-14/253', 'EIOPA BoS 14/253', 'EIOPA-BoS-20-002', "
         "'EIOPA-BoS-2021/456', 'EIOPA-21/260', 'EIOPA 16/858', "
         "'Eiopa – 17/651'. Swedish documents cite it just as loosely "
         "('EIOPA-BoS/18-114' in a föreskrift, 'EIOPA-BoS-15/035' in a "
         "remissvar). So `number_slug` is not used: its year component is "
         "four digits by assertion, and normalising 14/253 to 2014/253 would "
         "print a citation nobody writes. `order=HELA_NUMRET` keeps the "
         "number whole and `own_number_slug` folds every spelling of one "
         "number onto one address -- 'EIOPA-BoS-14/253' and "
         "'EIOPA-BoS-14-253' both give guidance/eiopa/riktlinjer/"
         "eiopa-bos-14-253.\n"
         "**The identity is on the cover and only there.** No leaf page prints "
         "a number and no file name carries one before 2025, so naming a "
         "document costs a download. Eiopa also gives the whole dossier one "
         "number -- EIOPA-BoS-25/660 is printed on the riktlinje and on the "
         "slutrapport about it -- so the number cannot type the document "
         "either; the cover's own lead does.\n"
         "**Joint guidance is deliberately not carried here.** The guidelines "
         "facet lists the gemensamma riktlinjer the three ESA:erna issue "
         "together, numbered 'JC 2024 34' / 'JC/GL/2024/36' / 'ESA 2024 28'. "
         "That is not Eiopas number, and filing it under Eiopas kod would give "
         "the same document three addresses once EBA and Esma carry theirs. "
         "They are declined by number and counted. So is the pre-2011 output "
         "of Eiopas predecessor CEIOPS ('CEIOPS-DOC-76/10'), which is cited by "
         "CEIOPS' number.")


EUIPO = Issuer(
    kod="euipo", namn=EUIPO_NAMN, kortnamn="EUIPO", route="site",
    base="https://www.euipo.europa.eu", feta_rubriker=True,
    upprepat_sidhuvud=True,
    series=(
        Series(kod="varumarke", label="Riktlinjer för varumärken",
               identifier="%s", order=None,
               doctype="Trade mark Guidelines",
               note="the granskningsriktlinjerna for förordning (EU) "
                    "2017/1001, and the only family EUIPO splits into "
                    "separate PDFs the whole way down: every del publishes "
                    "one and, inside it, every avsnitt. 22 documents -- Del A "
                    "whole, Del B 4 avsnitt, Del C 8, Del D 2, Del E 6, Del M "
                    "whole. Del A is taken whole because its Avsnitt 10 Bevis "
                    "publishes no PDF of its own; Del M has no avsnitt"),
        Series(kod="formgivning", label="Riktlinjer för formgivningar",
               identifier="%s", order=None,
               doctype="Design Guidelines",
               note="carried as one volume of 554 pages. Only Del A, Del B "
                    "and Del E publish a PDF of their own, and those three "
                    "are the delar shared verbatim with varumärkespraxis; the "
                    "two blocks that *are* the formgivningsriktlinjerna "
                    "(prövning av ansökningar, 223 topics, and prövning av "
                    "ogiltigförklaring, 116) publish none"),
        Series(kod="gi",
               label="Riktlinjer för geografiska beteckningar",
               identifier="%s", order=None,
               doctype="Craft GI Guidelines",
               note="förordning (EU) 2023/2411 om geografiska beteckningar "
                    "för hantverks- och industriprodukter, in force since "
                    "2026-05-21. Carried as one volume of 208 pages: none of "
                    "its nine delar publishes a PDF of its own"),
    ),
    note="**the identity is a coordinate, not a number.** EUIPO gives its "
         "riktlinjer no running number of any kind. What a citation names is "
         "the place in the volume -- 'EUIPO:s riktlinjer, del B, avsnitt 4' -- "
         "and that is what the volume's own cover prints for the document: "
         "'GUIDELINES FOR EXAMINATION … Part C / Opposition / Section 0 / "
         "Introduction'. So `order` is None, the `nummer` is that coordinate "
         "in EUIPO:s own language-free scope codes ('part-b-section-4', "
         "'part-m'), and `identifier` is never reached, because every record "
         "carries a `citation`: the volume, the del and the avsnitt as the "
         "publication names them.\n"
         "The scope codes rather than the printed designation, because the "
         "printed one is translated: the Swedish volume writes 'Del B / "
         "Avsnitt 4' over the same PARTB/SECTION4. One document keeps one "
         "address when EUIPO publishes the Swedish translation of an edition "
         "this source first took in English.\n"
         "**The utgåva is a version, not an identity.** EUIPO revises the "
         "riktlinjerna about once a year ('Edition 2026', in force "
         "2026-07-01) and the new utgåva supersedes the old at the same "
         "coordinate, which is exactly what `Vagledning.version` is for. Only "
         "the current utgåva is carried; the app keeps ten of them.\n"
         "**Swedish exists and lags.** The delivery app publishes all 24 "
         "official languages, and both its own metadata and the PDF cover "
         "are in that language. The translations trail the English utgåva by "
         "a few months: today the current varumärkesutgåva has 22 languages "
         "and Swedish is not one of them, while the superseded Edition 2025 "
         "has it. So the *current* utgåva is taken and its Swedish text where "
         "there is one, English otherwise, with `sprak` recording which.\n"
         "**What is left out.** Överklagandenämndernas beslut are decisions "
         "in named cases -- `avg`/`dv` material. The verkställande direktörens "
         "beslut och meddelanden (EX-/COM-/ADM-) are instruments rather than "
         "guidance, and the ADM half is intern administration of the kind the "
         "ACER entry above declines. The gemensamma praxisen CP1-CP14 is "
         "issued by the European Union Intellectual Property Network and "
         "published on tmdn.org, not by EUIPO on its own site.")


ECB_NAMN = "Europeiska centralbanken"
ESRB_NAMN = "Europeiska systemrisknämnden"


ECB = Issuer(
    kod="ecb", namn=ECB_NAMN, kortnamn="ECB", route="eurlex",
    base="https://www.ecb.europa.eu",
    # 1 168 of the ECB's 1 354 yttranden come out of CELLAR as the PDF the ECB
    # itself set, and that template marks a heading bold at the running text's
    # own size and reprints "ECB-PUBLIC" at the top of every page. Read by size
    # instead, one sampled document in three showed no heading at all: on 60 of
    # them the two flags together lift 219 headings where size alone found 59.
    upprepat_sidhuvud=True, feta_rubriker=True,
    series=(
        Series(kod="con", label="Yttranden", identifier="CON/%s",
               order=AR_FORST,
               note="the yttranden the ECB gives on national and EU draft "
                    "legislation in its field. The segment is the citation's "
                    "own: a förarbete cites this as CON/2013/82, and 122 of "
                    "them do, while none cites it as 52013AB0082. The ECB's "
                    "rättsakter carry ECB/ÅÅÅÅ/N in the same shape and are "
                    "binding, not guidance, so they are not taken here"),
    ),
    note="route eurlex: the ECB publishes its yttranden in EUT rather than as "
         "a series on its own site, and CELLAR states the number itself in "
         "resource_legal_internal_number_prefix/_year/_sequential_number. "
         "1 716 of its 3 214 works carry such a number; 1 633 are yttranden")

ESRB = Issuer(
    kod="esrb", namn=ESRB_NAMN, kortnamn="ESRB", route="eurlex",
    base="https://www.esrb.europa.eu",
    series=(
        Series(kod=None, label="Rekommendationer, varningar och beslut",
               identifier="ESRB/%s", order=AR_FORST,
               note="one series because the ESRB has one sequence: its 62 "
                    "rekommendationer, 23 beslut, 20 varningar and 2 råd are "
                    "numbered ESRB/ÅÅÅÅ/N together and no number is used "
                    "twice, so the number alone names the document and the "
                    "address carries no series segment"),
    ),
    note="route eurlex. Unlike the ECB the ESRB states its number in only a "
         "quarter of its works -- 26 of 113 English expressions carry the "
         "number predicates and 83 more print ESRB/ÅÅÅÅ/N in the title, which "
         "is where `eurlex_download` reads it for the rest. The remaining 4 "
         "are vacancy notices and an announcement, the same CELLAR noise the "
         "EBA census turned up, and are not guidance")


REGISTRY = (EDPB, EBA, EASA, ACER, ESMA, ENISA, BEREC, EDPS, EIOPA,
            EUIPO, ECB, ESRB)

BY_KOD = {issuer.kod: issuer for issuer in REGISTRY}
KODER = tuple(issuer.kod for issuer in REGISTRY)
BY_SERIE = {(issuer.kod, s.kod): s for issuer in REGISTRY for s in issuer.series}

# the (utgivare, serie) pairs a harvest walks, per route
SITE_SCOPES = tuple("%s/%s" % (issuer.kod, s.kod)
                    for issuer in REGISTRY if issuer.route == "site"
                    for s in issuer.series)


# the bodies harvested through the CELLAR machinery rather than off their own
# pages. One scope per body, not per series: a body's works come out of one
# enumeration of what CELLAR holds under its corporate-body URI.
EURLEX_SCOPES = tuple(issuer.kod for issuer in REGISTRY
                      if issuer.route == "eurlex")


def publisher_of(utgivare, serie):
    """Who issues a document: the series' own publisher where it names one --
    the artikel 29-gruppens vägledningar are the working party's, not the
    EDPB's -- otherwise the issuing body."""
    return BY_SERIE[(utgivare, serie)].publisher or BY_KOD[utgivare].namn
