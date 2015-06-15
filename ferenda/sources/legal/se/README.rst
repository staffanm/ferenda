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
    288:    def __init__(self, config=None, **kwargs):
    330:    def ontologies(self):
    361:    def commondata(self):
    380:    def config(self):
    389:    def config(self, config):
    396:    def lookup_resource(self, label, predicate=FOAF.name, cutoff=0.8, warn=True):
    445:    def get_default_options(cls):
    506:    def setup(cls, action, config):
    518:    def teardown(cls, action, config):
    531:    def get_archive_version(self, basefile):
    549:    def qualified_class_name(self):
    557:    def canonical_uri(self, basefile):
    571:    def dataset_uri(self, param=None, value=None):
    598:    def basefile_from_uri(self, uri):
    624:    def dataset_params_from_uri(self, uri):
    656:    def download(self, basefile=None):
    736:    def download_get_basefiles(self, source):
    762:    def download_single(self, basefile, url=None):
    812:    def _addheaders(self, filename=None):
    827:    def download_if_needed(self, url, basefile, archive=True, filename=None, sleep=1):
    932:    def download_name_file(self, tmpfile, basefile, assumedfile):
    935:    def download_is_different(self, existing, new):
    942:    def remote_url(self, basefile):
    966:    def generic_url(self, basefile, maindir, suffix):
    987:    def downloaded_url(self, basefile):
   1006:    def parse_all_setup(cls, config):
   1019:    def parse_all_teardown(cls, config):
   1030:    def parseneeded(self, basefile):
   1043:    def parse(self, doc):
   1053:        class read and write the files.
   1068:    def parse_entry_update(self, doc):
   1075:    def parse_entry_title(self, doc):
   1080:    def soup_from_basefile(self, basefile, encoding='utf-8', parser='lxml'):
   1101:    def parse_metadata_from_soup(self, soup, doc):
   1143:    def parse_document_from_soup(self, soup, doc):
   1177:    def patch_if_needed(self, basefile, text):
   1273:    def make_document(self, basefile=None):
   1297:    def make_graph(self):
   1311:    def create_external_resources(self, doc):
   1321:    def render_xhtml(self, doc, outfile=None):
   1354:    def render_xhtml_tree(self, doc):
   1366:        def render_head(g, uri, children=None):

ARN:

     75:class ARN(SwedishLegalSource, PDFDocumentRepository):
     91:    def download(self, basefile=None):
    126:    def download_get_basefiles(self, args):
    206:    def download_name_file(self, tmpfile, basefile, assumedfile):
    225:    def download_single(self, basefile, url, fragment):
    237:    def parse(self, doc):
    238:        def nextcell(key):
    269:    def parse_from_pdf(self, doc, filename, filetype=".pdf"):
    270:        def gluecondition(textbox, nextbox, prevbox):
    299:    def create_external_resources(self, doc):

Direktiv:
     63:class DirTrips(Trips):
     78:    def download(self, basefile=None):
     90:    def parse(self, doc):
    110:    def header_lines(self, header_chunk):
    142:    def make_meta(self, chunk, meta, uri, basefile):
    193:    def sanitize_rubrik(self, rubrik):
    200:    def sanitize_identifier(self, identifier):
    208:    def make_body(self, reader, body):
    228:    def guess_type(self, p, current_type):
    251:    def process_body(self, element, prefix, baseuri):
    259:    def canonical_uri(self, basefile):
    263:class DirAsp(SwedishLegalSource, PDFDocumentRepository):
    272:    def download(self, basefile=None):
    286:    def download_get_basefiles(self, depts):
    307:    def remote_url(self, basefile):
    312:    def canonical_uri(self, basefile):
    315:    def parse_from_pdfreader(self, pdfreader, doc):
    322:class DirRegeringen(Regeringen):
    334:    def sanitize_identifier(self, identifier):


DV
    200:class DV(SwedishLegalSource):
    227:    def relate_all_setup(cls, config):
    266:    def get_default_options(cls):
    273:    def canonical_uri(self, basefile):
    298:    def make_document(self, basefile=None):
    310:    def basefile_from_uri(self, uri):
    342:    def download(self, basefile=None):
    368:    def download_ftp(self, dirname, recurse, user=None, password=None, connection=None):
    401:    def download_www(self, dirname, recurse):
    440:    def process_all_zipfiles(self):
    447:    def process_zipfile(self, zipfilename):
    546:    def extract_notis(self, docfile, year, coll="HDO"):
    547:        def find_month_in_previous(basefile):
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
   1320:    def get_parser(basefile):
   1552:        def is_delmal(parser):
   1562:        def is_instans(parser, chunk=None):
   1589:        def is_equivalent_court(newcourt, oldcourt):
   1604:        def canonicalize_court(courtname):
   1612:        def is_heading(parser):
   1623:        def is_betankande(parser):
   1627:        def is_dom(parser):
   1632:        def is_domskal(parser):
   1637:        def is_domslut(parser):
   1641:        def is_skiljaktig(parser):
   1646:        def is_tillagg(parser):
   1651:        def is_endmeta(parser):
   1655:        def is_paragraph(parser):
   1667:        def split_sentences(text):
   1672:        def analyze_instans(strchunk):
   1712:        def analyze_dom(strchunk):
   1743:        def analyze_domskal(strchunk):
   1755:        def analyze_domslut(strchunk):
   1771:        def parse_constitution(strchunk):
   1799:        def make_body(parser):
   1803:        def make_delmal(parser):
   1808:        def make_instans(parser):
   1838:        def make_heading(parser):
   1843:        def make_betankande(parser):
   1849:        def make_dom(parser):
   1859:        def make_domskal(parser):
   1864:        def make_domslut(parser):
   1869:        def make_skiljaktig(parser):
   1875:        def make_tillagg(parser):
   1881:        def make_endmeta(parser):
   1886:        def make_paragraph(parser):
   1904:        def ordered(chunk):
   1908:        def transition_domskal(symbol, statestack):
   2007:    def _simplify_ooxml(self, filename, pretty_print=True):
   2030:    def _merge_ooxml(self, soup):
    

JK:
     26:class JK(SwedishLegalSource):
     34:    def download(self, basefile=None):
     51:    def download_get_basefiles(self, start_url):
     71:    def download_is_different(self, existing, new):
     83:    def parse_metadata_from_soup(self, soup, doc):
    109:    def parse_document_from_soup(self, soup, doc):
    134:    def make_parser():
    135:        def is_section(parser):
    138:        def is_subsection(parser):
    141:        def is_subsubsection(parser):
    144:        def is_paragraph(parser):
    148:        def make_body(parser):
    152:        def make_section(parser):
    157:        def make_subsection(parser):
    162:        def make_subsubsection(parser):
    166:        def make_paragraph(parser):


JO:
     49:class JO(SwedishLegalSource, PDFDocumentRepository):
     70:    def download(self, basefile=None):
     81:    def download_get_basefiles(self, start_url):
    109:    def download_single(self, basefile, url):
    131:    def parse(self, doc):
    135:        def gluecondition(textbox, nextbox, prevbox):
    161:    def parse_headnote(self, desc):
    164:    def removemeta(self, tree, desc):
    177:    def structure(self, doc, chunks):
    178:        def is_heading(parser):
    181:        def is_dnr(parser):
    187:        def is_datum(parser):
    193:        def is_nonessential(parser):
    198:        def is_abstract(parser):
    202:        def is_section(parser):
    208:        def is_blockquote(parser):
    213:        def is_normal(parser):
    218:        def is_paragraph(parser):
    222:        def make_body(parser):
    225:        def make_heading(parser):
    232:        def make_abstract(parser):
    237:        def make_section(parser):
    242:        def make_blockquote(parser):
    246:        def make_paragraph(parser):
    250:        def make_datum(parser):
    255:        def make_dnr(parser):
    259:        def skip_nonessential(parser):
    300:    def create_external_resources(self, doc):

   
Komitte
     19:class Kommitte(Trips):
     29:    def parse_from_soup(self, soup, basefile):

MyndFskr
     33:class MyndFskr(SwedishLegalSource):
     69:    def forfattningssamlingar(self):
     72:    def download_sanitize_basefile(self, basefile):
     87:    def download_get_basefiles(self, source):
    144:    def download_post_form(self, form, url):
    147:    def canonical_uri(self, basefile):
    165:    def basefile_from_uri(self, uri):
    175:    def parse(self, doc):
    185:    def textreader_from_basefile(self, basefile):
    216:    def sanitize_text(self, text, basefile):
    219:    def fwdtests(self):
    239:    def revtests(self):
    251:    def parse_metadata_from_textreader(self, reader, doc):
    318:    def sanitize_metadata(self, props, doc):
    336:    def polish_metadata(self, props, doc):
    359:            def makeurl(data):
    504:    def parse_document_from_textreader(self, reader, doc):
    534:    def facets(self):
    542:    def toc_item(self, binding, row):
    558:    def tabs(self, primary=False):
    562:class AFS(MyndFskr):
    583:    def sanitize_text(self, text, basefile):
    615:    def download_sanitize_basefile(self, basefile):
    619:class BOLFS(MyndFskr):
    628:class DIFS(MyndFskr):
    635:class DVFS(MyndFskr):
    646:    def remote_url(self, basefile):
    652:    def download_post_form(self, form, url):
    690:    def textreader_from_basefile(self, basefile):
    705:    def fwdtests(self):
    711:class EIFS(MyndFskr):
    717:    def download_sanitize_basefile(self, basefile):
    723:class ELSAKFS(MyndFskr):
    729:    def remote_url(self, basefile):
    742:class Ehalso(MyndFskr):
    748:class FFFS(MyndFskr):
    754:    def download(self, basefile=None):
    785:    def download_single(self, basefile):
    829:class FFS(MyndFskr):
    839:class FMI(MyndFskr):
    845:class FoHMFS(MyndFskr):
    850:class KFMFS(MyndFskr):
    855:class KOVFS(MyndFskr):
    860:class KVFS(MyndFskr):
    866:class LMFS(MyndFskr):
    871:class LIFS(MyndFskr):
    876:class LVFS(MyndFskr):
    881:class MIGRFS(MyndFskr):
    886:class MRTVFS(MyndFskr):
    891:class MSBFS(MyndFskr):
    896:class MYHFS(MyndFskr):
    902:class NFS(MyndFskr):
    909:    def download_sanitize_basefile(self, basefile):
    913:    def forfattningssamlingar(self):
    916:    def download_single(self, basefile, url):
    965:class RNFS(MyndFskr):
    970:class RAFS(MyndFskr):
    976:class RGKFS(MyndFskr):
    981:class SJVFS(MyndFskr):
    986:    def forfattningssamlingar(self):
    990:    def download_get_basefiles(self, source):
   1023:class SKVFS(MyndFskr):
   1036:    def forfattningssamlingar(self):
   1043:    def download_get_basefiles(self, source):
   1070:    def download_single(self, basefile, url):
   1097:    def textreader_from_basefile(self, basefile):
   1114:class SOSFS(MyndFskr):
   1120:    def _basefile_from_text(self, linktext):
   1127:    def download_get_basefiles(self, source):
   1206:    def download_single(self, basefile, url):
   1221:    def fwdtests(self):
   1226:    def parse_metadata_from_textreader(self, reader, doc):
   1239:class STAFS(MyndFskr):
   1248:    def download_single(self, basefile, mainurl):
   1305:class STFS(MyndFskr):
   1311:class SvKFS(MyndFskr):

Propositioner:
     33:class PropRegeringen(Regeringen):
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
    390:class PropRiksdagen(Riksdagen):
    399:class PropositionerStore(CompositeStore, SwedishLegalStore):
    403:class Propositioner(CompositeRepository, SwedishLegalSource):
    412:    def tabs(self, primary=False):

Regeringen:
     65:class Regeringen(SwedishLegalSource):
     90:    def download(self, basefile=None):
    136:    def download_get_basefiles(self, url):
    209:    def remote_url(self, basefile):
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
    715:    def toc_item(self, binding, row):
    720:    def toc(self, otherrepos=None):
    725:    def tabs(self, primary=False):

Riksdagen:
     24:class Riksdagen(SwedishLegalSource):
     61:    def download(self, basefile=None):
     69:    def download_get_basefiles(self, start_url):
    103:    def remote_url(self, basefile):
    125:    def download_single(self, basefile, url=None):
    203:    def parse(self, doc):
    280:    def parse_from_soup(self, soup, doc):
    287:    def canonical_uri(self, basefile):

SFS:
    301:class SFS(Trips):
    363:    def __init__(self, config=None, **kwargs):
    400:    def lagrum_parser(self):
    410:    def forarbete_parser(self):
    418:    def get_default_options(cls):
    425:    def canonical_uri(self, basefile, konsolidering=False):
    441:    def basefile_from_uri(self, uri):
    449:    def download(self, basefile=None):
    462:    def _set_last_sfsnr(self, last_sfsnr=None):
    482:    def download_new(self):
    528:    def download_base_sfs(self, wanted_sfs_nr):
    561:    def _check_for_sfs(self, year, nr):
    604:    def download_single(self, basefile, url=None):
    654:    def get_archive_version_nonworking(self, basefile, sfst_tempfile):
    712:    def _find_uppdaterad_tom(self, sfsnr, filename=None, reader=None):
    729:    def _find_upphavts_genom(self, filename):
    742:    def _checksum(self, filename):
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
   1622:    def makeAvdelning(self):
   1650:    def makeUpphavtKapitel(self):
   1658:    def makeKapitel(self):
   1693:    def makeRubrik(self):
   1711:    def makeUpphavdParagraf(self):
   1719:    def makeParagraf(self):
   1783:    def makeStycke(self):
   1804:    def makeNumreradLista(self):
   1839:    def makeBokstavslista(self):
   1859:    def makeStrecksatslista(self):
   1879:    def blankline(self):
   1883:    def eof(self):
   1887:    def makeOvergangsbestammelser(self, rubrik_saknas=False):
   1923:    def makeOvergangsbestammelse(self):
   1938:    def makeBilaga(self):  # svenska: bilaga
   1959:    def andringsDatum(self, line, match=False):
   1990:    def guess_state(self):
   2028:    def isAvdelning(self):
   2035:    def idOfAvdelning(self):
   2085:    def isUpphavtKapitel(self):
   2089:    def isKapitel(self, p=None):
   2092:    def idOfKapitel(self, p=None):
   2149:    def isRubrik(self, p=None):
   2237:    def isUpphavdParagraf(self):
   2241:    def isParagraf(self, p=None):
   2281:    def idOfParagraf(self, p):
   2299:    def isTabell(self, p=None, assumeTable=False, requireColumns=False):
   2435:    def makeTabell(self):
   2478:    def makeTabellrad(self, p, tabstops=None, kwargs={}):
   2483:        def makeTabellcell(text):
   2593:    def isFastbredd(self):
   2596:    def makeFastbredd(self):
   2599:    def isNumreradLista(self, p=None):
   2602:    def idOfNumreradLista(self, p=None):
   2626:    def isStrecksatslista(self, p=None):
   2634:    def isBokstavslista(self):
   2637:    def idOfBokstavslista(self):
   2645:    def isOvergangsbestammelser(self):
   2672:    def isOvergangsbestammelse(self):
   2675:    def isBilaga(self):
   2684:    def store_select(self, store, query_template, uri, context=None):
   2697:    def time_store_select(self, store, query_template, basefile,
   2715:    def prep_annotation_file(self, basefile):
   2874:        def ns(string):
   2961:    def display_title(self, uri, form="absolute"):
   3003:    def _forfattningskey(self, title):
   3033:    def facets(self):
   3034:        def forfattningskey(row, binding, resource_graph):
   3039:        def forfattningsselector(row, binding, resource_graph):
   3060:    def toc_item(self, binding, row):


SwedishLegalSource:
    132:class SwedishLegalSource(DocumentRepository):
    186:    def __init__(self, config=None, **kwargs):
    192:    def minter(self):
    213:    def get_default_options(cls):
    220:    def _swedish_ordinal(self, s):
    226:    def lookup_label(self, resource, predicate=FOAF.name):
    234:    def parse_iso_date(self, datestr):
    242:    def parse_swedish_date(self, datestr):
    286:    def infer_triples(self, d, basefile=None):
    349:    def tabs(self, primary=False):

Trips:
     25:class Trips(SwedishLegalSource):
     64:    def download(self, basefile=None):
     71:    def download_get_basefiles(self, params):
     92:    def download_get_basefiles_page(self, pagetree):
    113:    def download_single(self, basefile, url=None):
    121:    def download_is_different(self, existing, new):
    131:    def remote_url(self, basefile):
    136:    def canonical_uri(self, basefile):
