"""The three series this vertical carries, as *data*: what each is called, how
a document in it is numbered, and how that number is cited.

Europeiska dataskyddsstyrelsen (EDPB) publishes its guidance in two open,
running series -- ``Riktlinjer NN/ÅÅÅÅ`` and ``Rekommendation(er) NN/ÅÅÅÅ`` --
which is the identity a citation names and the identity these documents get
here. The third series is **closed**: the artikel 29-gruppens vägledningar that
the EDPB endorsed whole at its first plenary on 25 May 2018 (Endorsement
1/2018). Those carry no NN/ÅÅÅÅ number at all; they are cited by the WP number
the working party gave them ("WP 248", "wp248rev.01"), and since the group
ceased to exist with the GDPR, no more can appear. So they are listed here
individually rather than harvested from an index -- a closed corpus of sixteen
documents is *data*, and writing it down is what lets each entry be verified.

The sixteen are the endorsement's own list, in its own order (`ENDORSEMENT`
links the page; the decision itself is Endorsement 1/2018), and all sixteen are
carried. Two took a route of their own: the working party issued the BCR
application forms as Word *forms* rather than as documents, so the only PDFs of
them anywhere are conversions, and each entry records what its conversion was
verified against.

Why they need listing at all: the EDPB publishes a document page for eight of
the sixteen, and the seven of those that are `/documents/guideline/` pages are
**stubs**. Five carry no file whatsoever (only a link out to the Commission
newsroom archive where the working party actually published), one links an
unrelated Danish decision, and one -- the breach notification guideline, WP250
-- is titled "Dataskyddsombud", which is a different document entirely. Two
separate pages exist for WP242 and two for WP260. None of that is harvestable
as a series, and none of it can be trusted for identity, so what the stub page
supplies is exactly one thing: the URL a reader should be sent to as the
source. Everything else comes from the newsroom item (the WP number and
revision, in the item's own title) and from the Swedish PDF (its title, off the
cover). The eighth page is the position paper's, under `/documents/other-
guidance/`, and it is the one that is *not* a stub: it carries no file either,
but its title and adoption date are right, which is why that entry takes both
from it. The eight documents with no page at all are sourced to the endorsement
page, which is the EDPB's own statement that they belong here.

The newsroom item id is the stable identity on that side; it is recorded here
rather than resolved through the EDPB stub, because the stub's link is the part
that has already been observed wrong.
"""

from dataclasses import dataclass

EDPB_NAME = "Europeiska dataskyddsstyrelsen"
WP29_NAME = "Artikel 29-gruppen"
BASE = "https://www.edpb.europa.eu"
# where the working party's own documents live: the Commission newsroom archive
# the EDPB stubs point into. An item page names the WP number in its title and
# links the English PDF beside a ZIP of every language version.
NEWSROOM = "https://ec.europa.eu/newsroom/article29/items/%s"
# Hessens tillsynsmyndighet (Der Hessische Beauftragte für Datenschutz und
# Informationsfreiheit), which publishes a PDF conversion of the two endorsed
# BCR application forms the working party issued as Word files. The only route
# to those two; see the WP264/WP265 entries below for how each was verified.
HBDI = "https://datenschutz.hessen.de/sites/datenschutz.hessen.de/files/2022-11/"



@dataclass(frozen=True)
class Wp29:
    """One endorsed artikel 29-gruppen document."""
    slug: str                # its segment in the URI and basefile ("248")
    number: str | None       # the WP number, bare ("248") -- None for the one
                             # endorsed document the working party numbered not
                             # at all, the ställningstagande on artikel 30.5
    revision: str | None     # the revision the endorsed text carries
                             # ("rev.01"), None where the endorsed text is the
                             # first and only one (WP 253, the two BCR forms,
                             # the position paper)
    item: str                # its Commission newsroom item id
    page: str                # the EDPB page that is the source for it -- its
                             # own where the EDPB publishes one, otherwise the
                             # endorsement page, which is the EDPB's own
                             # statement that the document belongs here
    subject: str             # what it is about, for the harvest report only
    # what the source does not supply, for the documents whose source does not
    # supply it. None throughout rather than "" -- these say *absent*, and the
    # model takes each straight, with nothing translating one spelling of
    # absent into another on the way
    citation: str | None = None   # how it is named where there is no WP number
    titel: str | None = None      # its title, where it sets no cover
    antagen: str | None = None    # ISO adoption date, where it states none
    document: str | None = None   # the text, where the newsroom item does not
                                  # serve it -- the two BCR forms below


WP29 = (
    Wp29("259", "259", "rev.01", "623051", BASE + "/endorsed-wp29-guidelines_en",
         "samtycke"),
    Wp29("260", "260", "rev.01", "622227", BASE + "/documents/guideline/"
         "article-29-working-party-guidelines-on-transparency-under-regulation-2016679",
         "öppenhet"),
    Wp29("251", "251", "rev.01", "612053", BASE + "/documents/guideline/"
         "automated-decision-making-and-profiling",
         "automatiserat beslutsfattande och profilering"),
    Wp29("250", "250", "rev.01", "612052", BASE + "/documents/guideline/"
         "guidelines-on-personal-data-breach-notification-under-regulation-2016679-wp250",
         "anmälan av personuppgiftsincidenter"),
    Wp29("242", "242", "rev.01", "611233", BASE + "/documents/guideline/"
         "right-to-data-portability", "dataportabilitet"),
    Wp29("248", "248", "rev.01", "611236", BASE + "/documents/guideline/"
         "data-protection-impact-assessments-high-risk-processing",
         "konsekvensbedömning"),
    Wp29("243", "243", "rev.01", "612048", BASE + "/documents/guideline/"
         "data-protection-officer", "dataskyddsombud"),
    Wp29("244", "244", "rev.01", "611235", BASE + "/documents/guideline/"
         "guidelines-for-identifying-a-controller-or-processors-lead-supervisory",
         "ansvarig tillsynsmyndighet"),
    # the one endorsed document with no WP number: the working party set its
    # title in the running text rather than on a cover of its own and dated it
    # nowhere in the document, so the title and date are the EDPB's own page's
    # (that page carries both and no file, which is why the text still comes
    # from the newsroom). It is filed and named by what it is about.
    Wp29("artikel-30-5", None, None, "624045",
         BASE + "/documents/other-guidance/position-paper-on-the-derogations-"
         "from-the-obligation-to-maintain-records_en",
         "undantaget från skyldigheten att föra register",
         citation="Artikel 29-gruppens ställningstagande om artikel 30.5",
         titel="Position Paper on the derogations from the obligation to "
               "maintain records of processing activities pursuant to "
               "Article 30(5) GDPR",
         antagen="2018-04-19"),
    Wp29("263", "263", "rev.01", "623056", BASE + "/endorsed-wp29-guidelines_en",
         "godkännandeförfarandet för bindande företagsbestämmelser"),
    Wp29("257", "257", "rev.01", "614110", BASE + "/endorsed-wp29-guidelines_en",
         "bindande företagsbestämmelser för personuppgiftsbiträden"),
    Wp29("256", "256", "rev.01", "614109", BASE + "/endorsed-wp29-guidelines_en",
         "bindande företagsbestämmelser för personuppgiftsansvariga"),
    Wp29("254", "254", "rev.01", "614108", BASE + "/endorsed-wp29-guidelines_en",
         "referensdokument om adekvat skyddsnivå"),
    Wp29("253", "253", None, "611237", BASE + "/endorsed-wp29-guidelines_en",
         "administrativa sanktionsavgifter"),
    # the two BCR application forms. The working party published these as Word
    # *forms*, not as documents, so there is no authoritative PDF of either and
    # never was -- every PDF of them is somebody's conversion -- and the
    # newsroom compounds it for WP264: item 623850 has the right title and
    # date, but the file behind its download link is the *WP263* PDF,
    # byte-identical to what item 623056 serves, cover and all.
    #
    # Hessens tillsynsmyndighet (HBDI) publishes a conversion of each in its
    # own BCR guidance, and what makes a conversion trustworthy here is not the
    # host but what it can be checked against. WP264 was compared word for word
    # against the Greek tillsynsmyndighets independent conversion
    # (https://www.dpa.gr/sites/default/files/2020-09/WP264_BCR_EN.pdf): 4,507
    # words, identical but for line breaking. WP265 was compared against the
    # working party's own Word file from the newsroom -- see the entry below.
    # `parse.wp_cover` re-checks on every parse that each file names its own WP
    # number, so a mirror that ever serves something else fails the parse
    # rather than filing the wrong text.
    Wp29("264", "264", None, "623850", BASE + "/endorsed-wp29-guidelines_en",
         "ansökningsformuläret för personuppgiftsansvarigas bindande "
         "företagsbestämmelser",
         document=HBDI + "wp_264.pdf"),
    # ... and its processor twin, which the newsroom does serve -- as the Word
    # file the working party published it as (item 623848 is a .doc), which
    # this vertical cannot read. The same authority's conversion is taken, and
    # here the check is better than two hosts agreeing: the PDF carries the
    # Word original's own author metadata, and comparing it against that
    # original -- fetched from the newsroom -- leaves nothing unaccounted for
    # but 18 footnote markers the two extractors glue differently.
    Wp29("265", "265", None, "623848", BASE + "/endorsed-wp29-guidelines_en",
         "ansökningsformuläret för personuppgiftsbiträdens bindande "
         "företagsbestämmelser",
         document=HBDI + "wp_265.pdf"),
)

WP29_BY_SLUG = {wp.slug: wp for wp in WP29}

# the EDPB pages that duplicate an entry above -- a second page for the same
# WP29 document, kept out of the harvest so one document does not arrive twice
# under two identities
WP29_DUPLICATE_PAGES = frozenset((
    BASE + "/documents/guideline/transparency",                    # WP260 again
    BASE + "/documents/guideline/"
    "guidelines-on-the-right-to-data-portability-under-regulation-2016679-wp242",
))
