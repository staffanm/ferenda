Misc notes about these docrepos
===============================

General structure of the parse step
-----------------------------------

This is a more fine-grained version of the structure in
DocumentRepository.parse. All URI-generating functions (primarily
canonical_uri, but also all parts that generate URIs to other docs)
should use self.minter.

parse() # -- returns True if ok
    # general extraction:
    # DV.py: rawhead, rawbody = parse_{not,ooxml,antiword_docbook}
    # SFS.py:  extract_sfst
    parse_metadata()
        extract_metadata()  # produces flat dict
	sanitize_metadata() # cleans up flat dict
	    sanitize_basefile()
	    sanitize_identifier()
	polish_metadata()   # converts dict to rdfgraph
            canonical_uri() # -- should use self.minter if possible
	infer_metadata() # -- maybe hang sameAs off here?
    parse_body() # SFS.py: parse_sfst 


Current method usage
--------------------

DocumentRepository:

     61:class DocumentRepository(object):
    330:    def ontologies(self): # property
    361:    def commondata(self): # property
    396:    def lookup_resource(self, label, predicate=FOAF.name, ...):
    557:    def canonical_uri(self, basefile):
    571:    def dataset_uri(self, param=None, value=None):
    598:    def basefile_from_uri(self, uri):
    624:    def dataset_params_from_uri(self, uri):
   1043:    def parse(self, doc):
   1080:            soup_from_basefile(self, basefile):
   1101:            parse_metadata_from_soup(self, soup, doc):
   1143:            parse_document_from_soup(self, soup, doc):
   1068:            parse_entry_update(self, doc):
   1075:                parse_entry_title(self, doc):
   1177:    def patch_if_needed(self, basefile, text): # not called by baseclass
   1311:    def create_external_resources(self, doc):

SwedishLegalSource:  # lacks top-level parse, parse_metadata_from_soup, etc
    132:class SwedishLegalSource(DocumentRepository):
    192:    def minter(self):
    220:    def _swedish_ordinal(self, s):
    226:    def lookup_label(self, resource, predicate=FOAF.name):
    234:    def parse_iso_date(self, datestr):
    242:    def parse_swedish_date(self, datestr):
    286:    def infer_triples(self, d, basefile=None):

ARN:
     75:class ARN(SwedishLegalSource, PDFDocumentRepository):
    237:    def parse(self, doc):  # metadata added here
    238:        def nextcell(key):
    269:    def parse_from_pdf(self, doc, filename, filetype=".pdf"):
    270:        def gluecondition(textbox, nextbox, prevbox):
    299:    def create_external_resources(self, doc):

Direktiv:
    263:class DirAsp(SwedishLegalSource, PDFDocumentRepository):
    287:    def download_get_basefiles(self, depts):  # download_santitize_basefile
    315:    def parse_from_pdfreader(self, pdfreader, doc):

DV:
    200:class DV(SwedishLegalSource):
    273:    def canonical_uri(self, basefile):
    298:    def make_document(self, basefile=None): # don't call canonical_uri
    310:    def basefile_from_uri(self, uri):
    684:    def parse(self, doc):
    722:    def parse_entry_title(self, doc):
    733:    def sanitize_body(self, rawbody):
    742:    def parse_not(self, text, basefile, filetype):
    882:    def parse_ooxml(self, text, basefile):
    951:    def parse_antiword_docbook(self, text, basefile):
   1014:    def sanitize_metadata(self, head, basefile):
   1139:    def polish_metadata(self, head, doc):
   1148:        def ref_to_uri(ref):
   1153:        def split_nja(value):
   1274:    def add_keyword_to_metadata(self, domdesc, keyword):
   1283:    def format_body(self, paras, basefile):
   1316:    def structure_body(self, paras, basefile):
   2007:    def _simplify_ooxml(self, filename, pretty_print=True):
   2030:    def _merge_ooxml(self, soup):

JK:
     26:class JK(SwedishLegalSource):
     83:    def parse_metadata_from_soup(self, soup, doc):
    109:    def parse_document_from_soup(self, soup, doc):

JO:
     49:class JO(SwedishLegalSource, PDFDocumentRepository):
    131:    def parse(self, doc):
    135:        def gluecondition(textbox, nextbox, prevbox):
    161:    def parse_headnote(self, desc):
    164:    def removemeta(self, tree, desc):
    300:    def create_external_resources(self, doc):

MyndFskr:
     33:class MyndFskr(SwedishLegalSource):
     69:    def forfattningssamlingar(self):
     72:    def download_sanitize_basefile(self, basefile):
    147:    def canonical_uri(self, basefile):
    165:    def basefile_from_uri(self, uri):
    175:    def parse(self, doc):
    185:    def textreader_from_basefile(self, basefile):
    216:        sanitize_text(self, text, basefile):
    251:    def parse_metadata_from_textreader(self, reader, doc):
    219:        fwdtests(self):
    239:        revtests(self):
    318:        sanitize_metadata(self, props, doc):
    336:        polish_metadata(self, props, doc):
                [calls SwedishLegalSource.infer_triples]
    359:            def makeurl(data):
    504:    def parse_document_from_textreader(self, reader, doc):
    562:class AFS(MyndFskr):
    583:    def sanitize_text(self, text, basefile):
    615:    def download_sanitize_basefile(self, basefile):
    635:class DVFS(MyndFskr):
    690:    def textreader_from_basefile(self, basefile):
    705:    def fwdtests(self):
    711:class EIFS(MyndFskr):
    717:    def download_sanitize_basefile(self, basefile):
    902:class NFS(MyndFskr):
    909:    def download_sanitize_basefile(self, basefile):
    913:    def forfattningssamlingar(self):
    981:class SJVFS(MyndFskr):
    986:    def forfattningssamlingar(self):
    990:    def download_get_basefiles(self, source):
   1023:class SKVFS(MyndFskr):
   1036:    def forfattningssamlingar(self):
   1097:    def textreader_from_basefile(self, basefile):
   1114:class SOSFS(MyndFskr):
   1120:    def _basefile_from_text(self, linktext):
   1221:    def fwdtests(self):
   1226:    def parse_metadata_from_textreader(self, reader, doc):

Propositioner:
     44:class PropTrips(Trips):
     58:    def get_default_options(cls):
     65:    def download(self, basefile=None):
     85:    def _basefile_to_base(self, basefile):
     91:    def download_get_basefiles_page(self, pagetree):
    155:    def remote_url(self, basefile):
    161:    def download_single(self, basefile, url=None):
    261:    def sanitize_basefile(self, basefile):
    285:    def parse(self, doc):
    368:    def parse_from_pdfreader(self, pdfreader, doc):
    372:    def parse_from_textreader(self, textreader, doc):
    399:class PropositionerStore(CompositeStore, SwedishLegalStore):
    403:class Propositioner(CompositeRepository, SwedishLegalSource):
    412:    def tabs(self, primary=False):

Regeringen:
     65:class Regeringen(SwedishLegalSource):
    225:    def canonical_uri(self, basefile, document_type=None):
    238:    def basefile_from_uri(self, uri):
    245:    def download_single(self, basefile, url=None):
    310:    def parse_metadata_from_soup(self, soup, doc):
    429:    def parse_document_from_soup(self, soup, doc):
    448:    def post_process_proposition(self, doc):
    455:        def _check_differing(describer, predicate, newval):
    532:    def sanitize_identifier(self, identifier):
    547:    def find_pdf_links(self, soup, basefile):
    564:    def select_pdfs(self, pdffiles):
    603:    def parse_pdf(self, pdffile, intermediatedir):
    616:    def parse_pdfs(self, basefile, pdffiles, identifier=None):
    668:    def create_external_resources(self, doc):

     33:class PropRegeringen(Regeringen):

    322:class DirRegeringen(Regeringen):
    334:    def sanitize_identifier(self, identifier):

Riksdagen:
     24:class Riksdagen(SwedishLegalSource):
     61:    def download(self, basefile=None):
     69:    def download_get_basefiles(self, start_url):
    103:    def remote_url(self, basefile):
    125:    def download_single(self, basefile, url=None):
    203:    def parse(self, doc):
    280:    def parse_from_soup(self, soup, doc):
    287:    def canonical_uri(self, basefile):

    390:class PropRiksdagen(Riksdagen):

Trips:
     25:class Trips(SwedishLegalSource):
    131:    def remote_url(self, basefile):
    136:    def canonical_uri(self, basefile):

    Kommitte
     19:class Kommitte(Trips):
     29:    def parse_from_soup(self, soup, basefile):

    DirTrips
     63:class DirTrips(Trips):
     90:    def parse(self, doc):
    110:    def header_lines(self, header_chunk):
    142:    def make_meta(self, chunk, meta, uri, basefile):
    193:    def sanitize_rubrik(self, rubrik):
    200:    def sanitize_identifier(self, identifier):
    208:    def make_body(self, reader, body):
    228:    def guess_type(self, p, current_type):
    251:    def process_body(self, element, prefix, baseuri):
    259:    def canonical_uri(self, basefile):

    SFS
    301:class SFS(Trips):
    363:    def __init__(self, config=None, **kwargs):
    425:    def canonical_uri(self, basefile, konsolidering=False):
    441:    def basefile_from_uri(self, uri):
    801:    def parse(self, doc):
    991:    def _forfattningstyp(self, forfattningsrubrik):
    999:    def _dict_to_graph(self, d, graph, uri):
   1015:    def parse_sfsr(self, filename, docuri):
   1176:    def clean_departement(self, val):
   1189:    def _find_utfardandedatum(self, sfsnr):
   1198:    def extract_sfst(self, filename):
   1216:    def _term_to_subject(self, term):
   1221:    def visit_node(self, node, clbl, state, debug=False):
   1246:    def attributes_to_resource(self, attributes):
   1249:        def uri(qname):
   1299:    def _construct_base_attributes(self, sfsid):
   1314:    def construct_id(self, node, state):
   1347:    def find_definitions(self, element, find_definitions):
   1481:    def find_references(self, node, state):
   1484:    def _count_elements(self, element):
   1497:    def parse_sfst(self, text, doc):
   1521:    def make_header(self, desc):
   1590:    def makeForfattning(self):

