# -*- coding: utf-8 -*-
from __future__ import unicode_literals
# Intermediate base class containing some small functionality useful
# for handling data sources of swedish law.

from datetime import datetime, date
import difflib
import os
import re

from rdflib import URIRef, RDF, RDFS, Graph, Namespace
from six import text_type as str

from ferenda import DocumentRepository, DocumentStore, FSMParser, CitationParser
from ferenda import util
from ferenda.sources.legal.se.legalref import Link
from ferenda.elements import Paragraph, Section, Body, CompoundElement, SectionalElement
from ferenda.pdfreader import Page

from . import RPUBL
DCT = Namespace(util.ns['dct'])
PROV = Namespace(util.ns['prov'])

class Stycke(Paragraph):
    pass


class Sektion(Section):
    pass


class PreambleSection(CompoundElement):
    tagname = "div"
    classname = "preamblesection"
    counter = 0
    uri = None
    def as_xhtml(self, uri):
        if not self.uri:
            self.__class__.counter += 1
            self.uri = uri + "#PS%s" % self.__class__.counter
        element = super(PreambleSection, self).as_xhtml(uri)
        element.set('property', 'dct:title')
        element.set('content', self.title)
        element.set('typeof', 'bibo:DocumentPart')
        return element

class UnorderedSection(CompoundElement):
    tagname = "div"
    classname = "unorderedsection"
    counter = 0
    uri = None
    def as_xhtml(self, uri):
        if not self.uri:
            self.__class__.counter += 1
            # note that this becomes a document-global running counter
            self.uri = uri + "#US%s" % self.__class__.counter
        element = super(UnorderedSection, self).as_xhtml(uri)
        element.set('property', 'dct:title')
        element.set('content', self.title)
        element.set('typeof', 'bibo:DocumentPart')
        return element

class Appendix(SectionalElement): 
    tagname = "div"
    classname = "appendix"
    def as_xhtml(self, uri):
        if not self.uri:
            self.uri = uri + "#B%s" % self.ordinal

        return super(Appendix, self).as_xhtml(uri)

class Coverpage(CompoundElement):
    tagname = "div"
    classname = "coverpage"


class SwedishLegalStore(DocumentStore):

    """Customized DocumentStore."""

    def basefile_to_pathfrag(self, basefile):
        # "2012/13:152" => "2012-13/152"
        # "2012:152"    => "2012/152"
        return basefile.replace("/", "-").replace(":", "/")

    def pathfrag_to_basefile(self, pathfrag):
        # "2012-13/152" => "2012/13:152"
        # "2012/152"    => "2012:152"
        return pathfrag.replace("/", ":").replace("-", "/")

    def intermediate_path(self, basefile, attachment=None):
        return self.path(basefile, "intermediate", ".xml", attachment=attachment)


class SwedishLegalSource(DocumentRepository):
    documentstore_class = SwedishLegalStore
    namespaces = ['rdf', 'rdfs', 'xsd', 'dct', 'skos', 'foaf',
                  'xhv', 'xsi', 'owl', 'prov', 'bibo',
                  ('rpubl', 'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#'),
                  ('rinfoex', 'http://lagen.nu/terms#')]
    lang="sv"

    rdf_type = RPUBL.Rattsinformationsdokument # subclasses override this

    # This is according to the RPUBL vocabulary: All
    # rpubl:Rattsinformationsdokument should have dct:title,
    # dct:issued (must be a xsd:date), dct:publisher and
    # dct:identifier
    required_predicates = [RDF.type, DCT.title, DCT.issued, DCT.identifier, PROV.wasGeneratedBy]

    swedish_ordinal_list = ('f\xf6rsta', 'andra', 'tredje', 'fj\xe4rde',
                            'femte', 'sj\xe4tte', 'sjunde', '\xe5ttonde',
                            'nionde', 'tionde', 'elfte', 'tolfte')
    swedish_ordinal_dict = dict(list(zip(
        swedish_ordinal_list, list(range(1, len(swedish_ordinal_list) + 1)))))

    swedish_months = {"januari": 1,
                      "februari": 2,
                      "mars": 3,
                      "april": 4,
                      "maj": 5,
                      "juni": 6,
                      "juli": 7,
                      "augusti": 8,
                      "september": 9,
                      "oktober": 10,
                      "november": 11,
                      "december": 12,
                      "\xe5r": 12}

    def get_default_options(self):
        resource_path = os.path.normpath(
            os.path.dirname(__file__) + "../../../../res/etc/authrec.n3")
        opts = super(SwedishLegalSource, self).get_default_options()
        opts['authrec'] = resource_path
        opts['pdfimages'] = False 
        return opts

    def _swedish_ordinal(self, s):
        sl = s.lower()
        if sl in self.swedish_ordinal_dict:
            return self.swedish_ordinal_dict[sl]
        return None

    def _load_resources(self, resource_path):
        # returns a mapping [resource label] => [resource uri]
        # resource_path is given relative to cwd
        graph = Graph()
        graph.load(resource_path, format='n3')
        d = {}
        for uri, label in graph.subject_objects(RDFS.label):
            d[str(label)] = str(uri)
        return d

    def lookup_resource(self, resource_label, cutoff=0.8, warn=True):
        """Given a text label refering to some kind of organization,
        person or other entity, eg. 'Justitiedepartementet Gransk',
        return a URIRef for that entity. The text label does not need to
        match exactly byte-for-byte, a fuzziness matching function
        returns any reasonably similar (adjusted by the cutoff
        parameter) entity."""
        keys = []
        if not hasattr(self, 'org_resources'):
            self.org_resources = self._load_resources(self.config.authrec)

        for (key, value) in list(self.org_resources.items()):
            if resource_label.lower().startswith(key.lower()):
                return URIRef(value)
            else:
                keys.append(key)

        fuzz = difflib.get_close_matches(resource_label, keys, 1, cutoff)
        if fuzz:
            if warn:
                self.log.warning("Assuming that '%s' should be '%s'?" %
                                 (resource_label, fuzz[0]))
            return URIRef(self.lookup_resource(fuzz[0]))
        else:
            self.log.warning("No good match for '%s'" % (resource_label))
            raise KeyError(resource_label)

    def lookup_label(self, resource):
        if not hasattr(self, 'org_resources'):
            self.org_resources = self._load_resources(self.config.authrec)
        for (key, value) in list(self.org_resources.items()):
            if resource == value:
                return key

        raise KeyError(resource)

    def sameas_uri(self, uri):
        # "http://localhost:8000/res/dir/2012:35" => "http://rinfo.lagrummet.se/publ/dir/2012:35",
        # "http://localhost:8000/res/dv/hfd/2012:35" => "http://rinfo.lagrummet.se/publ/rattsfall/hdf/2012:35",
        assert uri.startswith(self.config.url)
        # FIXME: This hardcodes the res/ part of our local URIs
        # needlessly -- make configurable
        maps = (("res/dv/", "publ/rattsfall/"),
                ("res/", "publ/"))
        for fr, to in maps:
            if self.config.url + fr in uri:
                return uri.replace(self.config.url + fr,
                                   "http://rinfo.lagrummet.se/" + to)

    def parse_iso_date(self, datestr):
        # only handles YYYY-MM-DD now. Look into dateutil or isodate
        # for more complete support of all ISO 8601 variants
        return datetime.strptime(datestr, "%Y-%m-%d")

    def parse_swedish_date(self, datestr):
        """ Parses a number of common forms of expressing swedish dates with varying precision.
        >>> parse_swedish_date("3 februari 2010")
        datetime.date(2010, 2, 3)
        >>> parse_swedish_date("vid utgången av december 1999")
        datetime.date(1999, 12, 31)
        >>> parse_swedish_date("november 1999")
        ferenda.util.gYearMonth(1999, 11)
        >>> parse_swedish_date("1998")
        ferenda.util.gYear(1999)
        """
        day = month = year = None
        # assume strings on the form "3 februari 2010"
        # strings on the form "vid utg\xe5ngen av december 1999"
        if datestr.startswith("vid utg\xe5ngen av"):
            import calendar
            (x, y, z, month, year) = datestr.split()
            month = self.swedish_months[month]
            year = int(year)
            day = calendar.monthrange(year, month)[1]
        else:
            # assume strings on the form "3 februari 2010"
            components =  datestr.split()
            year = int(components[-1])
            if len(components) >= 2:
                month = self.swedish_months[components[-2]]
            if len(components) >= 3:
                day = int(components[-3])

        # return the best we can
        if day:
            return date(year, month, day)
        if month:
            return util.gYearMonth(year, month)
        else:
            return util.gYear(year)


    def infer_triples(self, d, basefile):
        try:
            identifier = d.getvalue(self.ns['dct'].identifier)
            # if the identifier is incomplete, eg "2010/11:68" instead
            # of "Prop. 2010/11:68", the following triggers a
            # ValueError, which is handled the same as if no
            # identifier is available at all. Ideally,
            # sanitize_identifier should prevent all preventable
            # occurrences of this.
            (doctype, arsutgava, lopnummer) = re.split("[ :]", identifier)
        except (KeyError, ValueError):
            # Create one from basefile. First guess prefix
            if self.rdf_type == self.ns['rpubl'].Direktiv:
                prefix = "Dir. "
            elif self.rdf_type == self.ns['rpubl'].Utredningsbetankande:
                if d.getvalue(self.ns['rpubl'].utrSerie) == "http://rinfo.lagrummet.se/serie/utr/ds":
                    prefix = "Ds "
                else:
                    prefix = "SOU "
            elif self.rdf_type == self.ns['rpubl'].Proposition:
                prefix = "Prop. "
            elif self.rdf_type == self.ns['rpubl'].Forordningsmotiv:
                prefix = "Fm "
            else:
                raise ValueError("Cannot create dct:identifier for rdf_type %r" % self.rdf_type)
            identifier = "%s%s" % (prefix, basefile)
            
            self.log.warning(
                "%s: No dct:identifier, assuming %s" % (basefile, identifier))
            d.value(self.ns['dct'].identifier, identifier)

        # self.log.debug("Identifier %s" % identifier)
        (doctype, arsutgava, lopnummer) = re.split("[ :]", identifier)
        d.value(self.ns['rpubl'].arsutgava, arsutgava)
        d.value(self.ns['rpubl'].lopnummer, lopnummer)

    def toc_query(self):
        return """PREFIX dct:<http://purl.org/dc/terms/>
                  PREFIX rpubl:<http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#>
                  SELECT DISTINCT ?uri ?title ?identifier ?arsutgava ?lopnummer ?departement
                  FROM <%s>
                  WHERE {?uri dct:title ?title;
                              dct:identifier ?identifier;
                              rpubl:arsutgava ?arsutgava;
                              rpubl:lopnummer ?lopnummer;
                              rpubl:departement ?departement;
                  }""" % self.context()

    def toc_criteria(self):
        return (
            {'predicate': self.ns['rpubl']['arsutgava'],
             'binding': 'arsutgava',
             'label': 'Efter \xe5rtal',
             'sorter': cmp,
             'pages': []},
            {'predicate': self.ns['dct']['title'],
             'binding': 'title',
             'label': 'Efter rubrik',
             'selector': lambda x: x[0].lower(),
             'sorter': cmp,
             'pages': []},
            {'predicate': self.ns['rpubl']['departement'],
             'binding': 'departement',
             'label': 'Efter departement',
             'selector': self.lookup_label,
             'sorter': cmp,
             'pages': []},
        )

    def toc_item(self, binding, row):
        return {'uri': row['uri'],
                'label': row['identifier'] + ": " + row['title']}

def offtryck_parser(basefile="0", preset="proposition", metrics={}):
    presets = {'default': {},
               'dir': {'footer': 920,
                       'header': 82,
                       'leftmargin': 105,
                       'rightmargin': 566,
                       'headingsize': 14,
                       'subheadingsize': 14,
                       'subheadingfamily': 'TimesNewRomanPS-ItalicMT',
                       'subsubheadingsize': None,
                       'textsize': 14},
               'proposition': {'footer': 920,
                               'header': 65, # make sure this is correct
                               'leftmargin': 160,
                               'rightmargin': 628,
                               'headingsize': 20,
                               'subheadingsize': 17,
                               'subheadingfamily': 'Times New Roman',
                               'subsubheadingsize': 15,
                               'textsize': 13},
               'sou': {'header': 49, # or rather 49 + 15
                       'header': 65, # make sure this is correct
                       'footer': 940,
                       'leftmargin': 84,
                       'rightmargin': 813,
                       'titlesize': 41,
                       'headingsize': 26,
                       'subheadingsize': 16,
                       'subheadingfamily': 'TradeGothic,Bold',
                       'subsubheadingsize': 14,
                       'textsize': 14
                   },
               'ds': {'header': 49, # or rather 49 + 15
                      'header': 65, # make sure this is correct
                      'footer': 940,
                      'leftmargin': 84,
                      'rightmargin': 813,
                      'titlesize': 41,
                      'headingsize': 26,
                      'subheadingsize': 16,
                      'subheadingfamily': 'TradeGothic,Bold',
                      'subsubheadingsize': 14,
                      'textsize': 14
                   }
               }
    if preset:
        metrics = presets[preset]

    # a mutable variable, which is accessible from the nested
    # functions
    state = {'pageno': 0,
             'appendixno': None,
             'preset': preset}

    def is_pagebreak(parser):
        return isinstance(parser.reader.peek(), Page)

    # page numbers, headings.
    def is_nonessential(parser):
        chunk = parser.reader.peek()
        if chunk.top > metrics['footer'] or chunk.bottom < metrics['header']:
            return True  # page numbers
        if (int(chunk.getfont()['size']) <= metrics['textsize'] and
                (chunk.left < metrics['leftmargin'] or
                 chunk.left > metrics['rightmargin']) and
            (15 <= len(str(chunk)) <= 29)): # matches both "Prop. 2013/14:1" and "Prop. 1999/2000:123 Bilaga 12"
            return True

    def is_coverpage(parser):
        # first 2 pages of a SOU are coverpages
        return isinstance(parser.reader.peek(), Page) and state['preset'] == "sou" and state['pageno'] < 2

            
    def is_preamblesection(parser):
        chunk = parser.reader.peek()
        if isinstance(chunk, Page):
            return False
        txt = str(chunk).strip()
        fontsize = int(chunk.getfont()['size'])
        if not metrics['subheadingsize'] <= fontsize <= metrics['headingsize']:
            return False

        for validheading in ('Propositionens huvudsakliga innehåll',
                             'Innehållsförteckning',
                             'Till statsrådet',
                             'Innehåll',
                             'Sammanfattning'):
            if txt.startswith(validheading):
                return True

    def is_section(parser):
        (ordinal, title) = analyze_sectionstart(parser)
        if ordinal:
            return ordinal.count(".") == 0

    def is_subsection(parser):
        (ordinal, title) = analyze_sectionstart(parser)
        if ordinal:
            return ordinal.count(".") == 1

    def is_unorderedsection(parser):
        # Subsections in "Författningskommentar" sections are
        # not always numbered. As a backup, check font size and family as well
        chunk = parser.reader.peek()
        return (int(chunk.getfont()['size']) == metrics['subheadingsize'] and
                chunk.getfont()['family'] == metrics['subheadingfamily'])

    def is_subsubsection(parser):
        (ordinal, title) = analyze_sectionstart(parser)
        if ordinal:
            return ordinal.count(".") == 2

    def is_appendix(parser):
        chunk = parser.reader.peek()
        txt = str(chunk).strip()
        if (chunk.getfont()['size'] == metrics['headingsize'] and txt.startswith("Bilaga ")):
            return True
        elif (int(chunk.getfont()['size']) == metrics['textsize'] and
              (chunk.left < metrics['leftmargin'] or
               chunk.left > metrics['rightmargin'])):
            m = re.search("Bilaga (\d)", str(chunk))
            if m:
                ordinal = int(m.group(1))
                if ordinal != state['appendixno']:
                    return True

    def is_paragraph(parser):
        return True

    def make_body(parser):
        return p.make_children(Body())
    setattr(make_body, 'newstate', 'body')

    def make_paragraph(parser):
        # if "Regeringen beslutade den 8 april 2010 att" in str(parser.reader.peek()):
        #     raise ValueError("OK DONE")
        return parser.reader.next()

    def make_coverpage(parser):
        state['pageno'] += 1
        parser.reader.next() # throwaway the Page object itself
        c = Coverpage()
        return parser.make_children(c)
    setattr(make_coverpage, 'newstate', 'coverpage')
        

    def make_preamblesection(parser):
        s = PreambleSection(title=str(parser.reader.next()).strip())
        if s.title == "Innehållsförteckning":
            parser.make_children(s) # throw away
            return None
        else:
            return parser.make_children(s)
    setattr(make_preamblesection, 'newstate', 'preamblesection')


    def make_unorderedsection(parser):
        s = UnorderedSection(title=str(parser.reader.next()).strip())
        return parser.make_children(s)
    setattr(make_unorderedsection, 'newstate', 'unorderedsection')

    def make_appendix(parser):
        # now, an appendix can begin with either the actual
        # headline-like title, or by the sidenote in the
        # margin. Find out which it is, and plan accordingly.
        done = False
        while not done:
            chunk = parser.reader.next()
            if isinstance(chunk, Page):
                continue
            m = re.search("Bilaga (\d)", str(chunk))
            if m:
                state['appendixno'] = int(m.group(1))
            if int(chunk.getfont()['size']) >= metrics['subheadingsize']:
                done = True
        s = Appendix(title=str(chunk).strip(),
                     ordinal=str(state['appendixno']),
                     uri=None)
        return parser.make_children(s)
    setattr(make_appendix, 'newstate', 'appendix')

    # this is used for subsections and subsubsections as well --
    # probably wont work due to the newstate property
    def make_section(parser):
        ordinal, title = analyze_sectionstart(parser, parser.reader.next())
        if ordinal:
            identifier = "Prop. %s, avsnitt %s" % (basefile, ordinal)
            s = Section(ordinal=ordinal, title=title)
        else:
            s = Section(title=str(title))
        return parser.make_children(s)
    setattr(make_section, 'newstate', 'section')

    def skip_nonessential(parser):
        parser.reader.next()
        return None

    def skip_pagebreak(parser):
        # increment pageno
        state['pageno'] += 1
        parser.reader.next()
        return None

    re_sectionstart = re.compile("^(\d[\.\d]*) +(.*[^\.])$").match
    def analyze_sectionstart(parser, textbox=None):
        if not textbox:
            textbox = parser.reader.peek()
        if not (metrics['headingsize'] >= int(textbox.getfont()['size']) >= metrics['subsubheadingsize']):
            return (None, textbox)
        txt = str(textbox)
        m = re_sectionstart(txt)
        if m:
            ordinal = m.group(1).rstrip(".")
            title = m.group(2)
            return (ordinal, title.strip())
        else:
            return (None, textbox)

    p = FSMParser()

    p.set_recognizers(is_coverpage,
                      is_pagebreak,
                      is_appendix,
                      is_nonessential,
                      is_section,
                      is_subsection,
                      is_subsubsection,
                      is_preamblesection,
                      is_unorderedsection,
                      is_paragraph)
    commonstates = ("body","preamblesection","section", "subsection", "unorderedsection", "subsubsection", "appendix")
    p.set_transitions({(commonstates, is_nonessential): (skip_nonessential, None),
                       (commonstates, is_pagebreak): (skip_pagebreak, None),
                       (commonstates, is_unorderedsection): (make_unorderedsection, "unorderedsection"),
                       (commonstates, is_paragraph): (make_paragraph, None),
                       ("body", is_coverpage): (make_coverpage, "coverpage"),
                       ("body", is_preamblesection): (make_preamblesection, "preamblesection"),
                       ("coverpage", is_coverpage): (False, None),
                       ("coverpage", is_preamblesection): (False, None),
                       ("coverpage", is_paragraph): (make_paragraph, None),
                       ("preamblesection", is_preamblesection): (False, None),
                       ("preamblesection", is_section): (False, None),
                       ("body", is_section): (make_section, "section"),
                       ("section", is_section): (False, None),
                       ("section", is_subsection): (make_section, "subsection"),
                       ("unorderedsection", is_preamblesection): (False, None),
                       ("unorderedsection", is_unorderedsection): (False, None),
                       ("unorderedsection", is_section): (False, None),
                       ("unorderedsection", is_appendix): (False, None),
                       ("subsection", is_subsection): (False, None),
                       ("subsection", is_section): (False, None),
                       ("subsection", is_subsubsection): (make_section, "subsubsection"),
                       ("subsubsection", is_subsubsection): (False, None),
                       ("subsubsection", is_subsection): (False, None),
                       ("subsubsection", is_section): (False, None),
                       ("body", is_appendix): (make_appendix, "appendix"),
                       (("appendix","subsubsection", "subsection", "section"), is_appendix):
                       (False, None)
                       })

    p.initial_state = "body"
    p.initial_constructor = make_body
    return p


def offtryck_gluefunc(textbox, nextbox, prevbox):
    linespacing = int(nextbox.getfont()['size']) / 2
    parindent = int(nextbox.getfont()['size'])
    if (textbox.getfont()['size'] == nextbox.getfont()['size'] and
        textbox.getfont()['family'] == nextbox.getfont()['family'] and
        textbox.top + textbox.height + linespacing > nextbox.top and
        ((prevbox.top + prevbox.height == nextbox.top + nextbox.height) or # compare baseline, not topline
         (prevbox.left == nextbox.left) or
         (parindent * 2 >= (prevbox.left - nextbox.left) >= parindent)
     )):
     return True
    
# (ab)use the CitationClass, with it's useful parse_recursive method,
# to use a legalref based parser instead of a set of pyparsing
# grammars.
class SwedishCitationParser(CitationParser):
    def __init__(self, legalrefparser):
        self._legalrefparser = legalrefparser

    def parse_string(self, string):
        unfiltered = self._legalrefparser.parse(string, predicate="dct:references")
        # remove those references that we cannot fully resolve (should
        # be an option in LegalRef, but...
        filtered = []
        for node in unfiltered:
            if isinstance(node, Link) and "sfs/9999:999" in node.uri:
                filtered.append(str(node))
            else:
                filtered.append(node)
        return filtered
