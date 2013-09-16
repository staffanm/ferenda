# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re
import os
from datetime import datetime, date
from itertools import islice
import requests
import requests.exceptions

import six
from rdflib import URIRef, Graph
from pyparsing import Word, CaselessLiteral, Optional, nums

from ferenda import DocumentRepository
from ferenda import TextReader, Describer, FSMParser, CitationParser, URIFormatter
from ferenda import util
from ferenda.decorators import action, recordlastdownload, managedparsing, downloadmax
from ferenda.elements import Body, Heading, Preformatted, Paragraph, UnorderedList, ListItem, Section, Subsection, Subsubsection, UnicodeElement, CompoundElement, Link, serialize
from ferenda.errors import ParseError


class RFCHeader(UnicodeElement):
    pass


class DocTitle(UnicodeElement):
    pass


class Pagebreak(CompoundElement):
    pass


class PreambleSection(CompoundElement):
    tagname = "div"
    counter = 0

    def _get_classname(self):
        return self.__class__.__name__.lower()
    classname = property(_get_classname)

    # FIXME: For Sections (incl Sub- and Subsubsections), we use
    # decorate_bodyparts to create .uri and .meta properties, which
    # then the default as_xhtml serializes neatly. For this, we set
    # the attributes "manually". This is not consistent. We should
    # probably extend decorate_bodyparts to handle preamblesections as
    # well.
    def as_xhtml(self, uri):
        element = super(PreambleSection, self).as_xhtml(uri)
        element.set('property', 'dct:title')
        element.set('content', self.title)
        element.set('typeof', 'bibo:DocumentPart')
        self.__class__.counter += 1
        element.set('about', uri + "#PS" + str(self.__class__.counter))
        # alternate uri strategy
        # element.set('about', uri + "#" + self.title.lower().replace(" ", "_"))
        return element


class RFC(DocumentRepository):
    alias = "rfc"
    start_url = "http://www.ietf.org/download/rfc-index.txt"
    document_url_template = "http://tools.ietf.org/rfc/rfc%(basefile)s.txt"
    document_url_regex = "http://tools.ietf.org/rfc/rfc(?P<basefile>\w+).txt"
    downloaded_suffix = ".txt"
    namespaces = ('rdf',  # always needed
                  'dct',  # title, identifier, etc (could be replaced by equiv bibo prop?)
                  'bibo',  # Standard and DocumentPart classes, chapter prop
                  'xsd',  # datatypes
                  'foaf',  # rfcs are foaf:Documents for now
                  ('rfc', 'http://example.org/ontology/rfc/')  # custom (fake) ontology
                  )
    sparql_annotations = "res/sparql/rfc-annotations.rq"
    xslt_template = "res/xsl/rfc.xsl"
    rdf_type = URIRef("http://example.org/ontology/rfc/RFC")
    # NOTES:
    #
    # Like many large document collections that has existed for a long
    # time, the RFCs aren't using a thurough standard formatting,
    # particularly for older RFC. Here are examples:
    #
    # Most older RFCs - headers are often all-caps (can be handled by
    # parser or constructor)
    #
    # RFC 759 - Header is totally different (can be patched), header
    #           has a IEN: 113 field
    # RFC 869 - Header is totally different (RFC 759-like), most
    #           paragraphs are double-spaced
    # RFC 889 - Header isn't right-justified but instead ragged
    # RFC 909 - Header is totally different (RFC 759-like), document
    #           headings are centered (can be handled by alternate
    #           recognizer)


    @action
    @recordlastdownload
    def download(self, basefile=None):
        """Download rfcs starting from http://www.ietf.org/download/rfc-index.txt"""
        if basefile and self.document_url_template:
            return self.download_single(basefile)
        res = requests.get(self.start_url)
        indextext = res.text
        reader = TextReader(string=indextext, linesep=TextReader.UNIX)  # see TextReader class
        iterator = reader.getiterator(reader.readparagraph)
        for (basefile, url) in self.download_get_basefiles(iterator):
            try:
                if not os.path.exists(self.store.downloaded_path(basefile)):
                    self.download_single(basefile)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    # create a empty dummy file in order to
                    # avoid looking for it over and over again:
                    with open(self.store.downloaded_path(basefile), "w"):
                        pass

    @downloadmax
    def download_get_basefiles(self, source):
        for p in reversed(list(source)):
            if re.match("^(\d{4}) ", p):  # looks like a RFC number
                if not "Not Issued." in p:  # Skip RFC known to not exist
                    basefile = str(int(p[:4]))  # eg. '0822' -> '822'
                    yield (basefile, None)

    @staticmethod  # so as to be easily called from command line
    def get_parser(basefile="0"):

        # recognizers, constructors and helpers are created as nested
        # ordinary functions, but could just as well be staticmethods
        # (or module-global functions)

        def is_rfcheader(parser, chunk=None, lenient=True):
            if not chunk:
                chunk = parser.reader.peek()
            (leftlines, rightlines, linelens) = _splitcolumns(chunk)
            # all rfc headers are at least 2 lines long (eg. rfc 889)
            if len(linelens) < 2:
                return False
            targetlen = linelens[0]
            for (idx, length) in enumerate(linelens):
                if rightlines[idx] == "" and length > 40:
                    return False
                elif rightlines[idx] != "" and length != targetlen and not lenient:
                    return False
                    # Most modern RFC has justified right margin
                    # (which is what this test targets) but some older
                    # RFCs (like 889) have ragged right margin (or
                    # rather left-justified two columns). However,
                    # since make_rfcheader checks next chunk as well
                    # (if there is a spurious double newline right in
                    # the middle of the header, which is a thing that
                    # has happened (RFC 6912)), this recognizer has a
                    # lenient and a non-lenient mode.
            return True

        # FIXME: use this in parse_header as well
        def _splitcolumns(chunk):
            linelens = []
            leftlines = []
            rightlines = []
            for line in chunk.split("\n"):
                linelens.append(len(line))
                if "   " in line:
                    (left, right) = line.split("   ", 1)
                else:
                    (left, right) = line, ""
                leftlines.append(left)
                rightlines.append(right)
            return (leftlines, rightlines, linelens)

        def is_doctitle(parser, chunk=None):
            return True

        def is_pagebreak(parser, chunk=None):
            if not chunk:
                chunk = parser.reader.peek()
            return ('\f' in chunk)

        def is_header(parser, chunk=None):
            if not chunk:
                chunk = parser.reader.peek()
            stripchunk = chunk.strip()
            # a header should be non-emtpy, be on a single line, not
            # end with "." and not start with an indent.
            if ((stripchunk != "") and
                (len(stripchunk.split("\n")) == 1) and
                (not stripchunk.endswith('.')) and
                    (not chunk.startswith(' '))):
                return True

        def is_section(parser, chunk=None):
            (ordinal, title, identifier) = analyze_sectionstart(parser, chunk)
            return section_segments_count(ordinal) == 1

        def is_subsection(parser, chunk=None):
            (ordinal, title, identifier) = analyze_sectionstart(parser, chunk)
            return section_segments_count(ordinal) == 2

        def is_subsubsection(parser, chunk=None):
            (ordinal, title, identifier) = analyze_sectionstart(parser, chunk)
            return section_segments_count(ordinal) == 3

        def is_preformatted(parser, chunk=None):
            if not chunk:
                chunk = parser.reader.peek()
            # all paragraphs start with a three space indent -- start
            # by removing this
            stripped = "\n".join([x[3:] for x in chunk.split("\n")])
            # replace double spaces after end of sentences to avoid
            # false positives:
            stripped = stripped.replace(".  ", ". ")
            # If any double spaces left, probably preformatted text
            # (eg. tables etc). Same if several periods are present
            # (indicative of leaders in TOCs)
            return ("  " in stripped or
                    "...." in stripped or
                    ". . . " in stripped)

        def is_bnf(parser, chunk=None):
            if not chunk:
                chunk = parser.reader.peek()
                return (is_preformatted(parser, chunk) and " = " in chunk)

        def is_paragraph(parser, chunk=None):
            return True

        def is_ul_listitem(parser, chunk=None):
            if not chunk:
                chunk = parser.reader.peek()
            return chunk.strip().startswith("o  ")

        def is_definition_title(parser, chunk=None):
            # looks like header but starts indented
            return False

        def is_definition(parser, chunk=None):
            # entire p is indented 6 spaces instead of 3. But if it
            # follows a ul li, problably continuation of that.
            return False

        def make_body(parser):
            return p.make_children(Body())
        setattr(make_body, 'newstate', 'body')

        def make_preamble_section(parser):
            s = PreambleSection(title=parser.reader.next())
            return p.make_children(s)
        setattr(make_preamble_section, 'newstate', 'preamble-section')

        # used for older rfcs
        def make_abstract(parser):
            s = PreambleSection(title="(Abstract)")
            return p.make_children(s)
        setattr(make_abstract, 'newstate', 'preamble-section')

        def skip_pagebreak(parser):
            chunk = parser.reader.next()
            lastline = chunk.split("\n")[-1]
            parts = re.split("  +", lastline)
            if len(parts) > 2:
                return Pagebreak(shorttitle=parts[1])
            else:
                return None

        def make_header(parser):
            chunk = parser.reader.next()
            h = Heading(chunk.strip())
            return h

        def make_paragraph(parser):
            chunk = p.reader.next()
            return Paragraph([" ".join(chunk.split())])

        def make_preformatted(parser):
            chunk = p.reader.next()
            return Preformatted([chunk])

        def make_bnf(parser):
            chunk = p.reader.next()
            return Preformatted([chunk], **{'class': 'bnf'})

        def make_section(parser):
            (secnumber, title, identifier) = analyze_sectionstart(parser, parser.reader.next())
            s = Section(ordinal=secnumber,
                        title=title,
                        identifier=identifier)
            return parser.make_children(s)
        setattr(make_section, 'newstate', 'section')

        def make_subsection(parser):
            (secnumber, title, identifier) = analyze_sectionstart(parser, parser.reader.next())
            s = Subsection(ordinal=secnumber,
                           title=title,
                           identifier=identifier)
            return parser.make_children(s)
        setattr(make_subsection, 'newstate', 'subsection')

        def make_subsubsection(parser):
            (secnumber, title, identifier) = analyze_sectionstart(parser, parser.reader.next())
            s = Subsubsection(ordinal=secnumber,
                              title=title,
                              identifier=identifier)
            return parser.make_children(s)
        setattr(make_subsubsection, 'newstate', 'subsubsection')

        def make_unordered_list(parser):
            (listtype, ordinal, separator, rest) = analyze_listitem(parser.reader.peek())
            ol = UnorderedList(type=listtype)  # should
            ol.append(parser.make_child(make_listitem, "listitem"))
            return parser.make_children(ol)
        setattr(make_unordered_list, 'newstate', 'unorderedlist')

        def make_listitem(parser):
            chunk = parser.reader.next()
            (listtype, ordinal, separator, rest) = analyze_listitem(chunk)
            li = ListItem(ordinal=ordinal)
            li.append(rest)
            return parser.make_children(li)
        setattr(make_listitem, 'newstate', 'listitem')

        def make_rfcheader(parser):
            headerchunk = parser.reader.next()
            if is_rfcheader(parser, lenient=False):
                headerchunk += "\n" + parser.reader.next()
            return RFCHeader(headerchunk)

        def make_doctitle(parser):
            return DocTitle(parser.reader.next())

        # Some helpers for the above
        def section_segments_count(s):
            return ((s is not None) and
                    len(list(filter(None, s.split(".")))))

        # Matches
        # "1 Blahonga" => ("1","Blahonga", "RFC 1234, section 1")
        # "1.2.3. This is a subsubsection" => ("1.2.3", "This is a subsection", "RFC 1234, section 1.2.3")
        # "   Normal paragraph" => (None, "   Normal paragraph", None)
        re_sectionstart = re.compile("^(\d[\.\d]*) +(.*[^\.])$").match

        def analyze_sectionstart(parser, chunk=None):
            if not chunk:
                chunk = parser.reader.peek()
            m = re_sectionstart(chunk)
            if m:
                ordinal = m.group(1).rstrip(".")
                title = m.group(2)
                identifier = "RFC %s, section %s" % (basefile, ordinal)
                return (ordinal, title, identifier)
            else:
                return (None, chunk, None)

        def analyze_listitem(chunk):
            # returns: same as list-style-type in CSS2.1, sans
            # 'georgian', 'armenian' and 'greek', plus 'dashed'
            listtype = ordinal = separator = None

            # FIXME: Tighten these patterns to RFC conventions
            # match "1. Foo..." or "14) bar..." but not "4 This is a heading"
            if chunk.startswith("   o  "):
                return ("disc", None, None, chunk[6:])

            return (listtype, ordinal, separator, chunk)  # None * 3

        p = FSMParser()

        p.set_recognizers(is_pagebreak,
                          is_rfcheader,
                          is_doctitle,
                          is_section,
                          is_subsection,
                          is_subsubsection,
                          is_header,
                          is_ul_listitem,
                          is_preformatted,
                          is_definition_title,
                          is_definition,
                          is_paragraph)
        # start_state: "body" or "rfcheader", then "title", then
        # "preamble" (consisting of preamblesections that has title
        # (eg "Abstract", "Status of This Memo" + content), then "section".
        commonstates = ("section", "subsection", "subsubsection")
        p.set_transitions({("body", is_rfcheader): (make_rfcheader, "doctitle"),
                           ("doctitle", is_doctitle): (make_doctitle, "preamble"),
                           ("preamble", is_header): (make_preamble_section, "preamble-section"),
                           ("preamble", is_paragraph): (make_abstract, "preamble-section"),
                           ("preamble-section", is_paragraph): (make_paragraph, None),
                           ("preamble-section", is_header): (False, None),
                           ("preamble-section", is_pagebreak): (skip_pagebreak, None),
                           ("preamble-section", is_section): (False, "after-preamble"),
                           ("after-preamble", is_section): (make_section, "section"),
                           ("section", is_subsection): (make_subsection, "subsection"),
                           ("section", is_section): (False, None),
                           ("subsection", is_subsubsection): (make_subsubsection, "subsubsection"),
                           ("subsection", is_subsection): (False, None),
                           ("subsection", is_section): (False, None),
                           ("subsubsection", is_subsubsection): (False, None),
                           ("subsubsection", is_subsection): (False, None),
                           ("subsubsection", is_section): (False, None),
                           (commonstates, is_ul_listitem): (make_unordered_list, "ul-list"),
                           ("ul-list", is_ul_listitem): (make_listitem, "listitem"),
                           ("ul-list", is_paragraph): (False, None),
                           ("listitem", is_paragraph): (False, None),
                           (commonstates, is_bnf): (make_bnf, None),
                           (commonstates, is_preformatted): (make_preformatted, None),
                           (commonstates, is_paragraph): (make_paragraph, None),
                           (commonstates, is_pagebreak): (skip_pagebreak, None),
                           })
        p.initial_state = "body"
        p.initial_constructor = make_body
        return p

    def make_citation_parser(self):
        def rfc_uriformatter(parts):
            uri = ""
            if 'RFC' in parts:
                uri += self.canonical_uri(parts['RFC'].lstrip("0"))
            if 'Sec' in parts:
                uri += "#S" + parts['Sec'].rstrip(".")
            return uri
        section_citation = (CaselessLiteral("section") + Word(
            nums + ".").setResultsName("Sec")).setResultsName("SecRef")
        rfc_citation = (
            Optional("[") + "RFC" + Word(nums).setResultsName("RFC") + Optional("]")).setResultsName("RFCRef")
        section_rfc_citation = (section_citation + "of" + rfc_citation).setResultsName("SecRFCRef")
        citparser = CitationParser(section_rfc_citation,
                                   section_citation,
                                   rfc_citation)
        citparser.set_formatter(URIFormatter(("SecRFCRef", rfc_uriformatter),
                                             ("SecRef", rfc_uriformatter),
                                             ("RFCRef", rfc_uriformatter)))
        return citparser


    @action
    @managedparsing
    def parse(self, doc):
        """Parse downloaded documents into structured XML and RDF."""

        reader = TextReader(self.store.downloaded_path(doc.basefile),
                            linesep=TextReader.UNIX)
        # Some more preprocessing: Remove the faux-bold formatting
        # used in some RFCs (using repetitions of characters
        # interleaved with backspace control sequences). Note: that
        # is '\b' as in backspace, not r'\b' as in word boundary
        # docstring = re.sub('.\b','',docstring)
        cleanparagraphs = (re.sub('.\b', '', x) for x in
                           reader.getiterator(reader.readparagraph))

        parser = self.get_parser(doc.basefile)

        if not self.config.fsmdebug:
            self.config.fsmdebug = 'FERENDA_FSMDEBUG' in os.environ
        parser.debug = self.config.fsmdebug
        doc.body = parser.parse(cleanparagraphs)

        header = doc.body.pop(0)  # body.findByClass(RFCHeader)
        title = " ".join(doc.body.pop(0).split())  # body.findByClass(DocHeader)
        for part in doc.body:
            if isinstance(part, PreambleSection) and part.title == "Table of Contents":
                doc.body.remove(part)
                break

        # create (RDF) metadata for document Note: The provided
        # basefile may be incorrect -- let whatever is in the header
        # override
        realid = self.get_rfc_num(header)
        if not realid:  # eg RFC 100 -- fallback to basefile in that case
            realid = doc.basefile
        doc.uri = self.canonical_uri(realid)
        desc = Describer(doc.meta, doc.uri)
        desc.rdftype(self.ns['rfc'].RFC)
        desc.value(self.ns['dct'].title, title, lang="en")
        self.parse_header(header, desc)
        if not desc.getvalues(self.ns['dct'].identifier):
            desc.value(self.ns['dct'].identifier, "RFC %s" % doc.basefile)

        doc.lang = "en"

        # process body - remove the temporary Pagebreak objects, after
        # having extracted the shortTitle found in them
        shorttitle = self.cleanup_body(doc.body)
        if shorttitle and (desc.getvalue(self.ns['dct'].title) != shorttitle):
            desc.value(self.ns['bibo'].shortTitle, shorttitle, lang="en")

        # process body - add good metadata
        citparser = self.make_citation_parser()
        doc.body = citparser.parse_recursive(doc.body)
        PreambleSection.counter = 0
        # self.decorate_bodyparts(doc.body,doc.uri)
        if self.config.fsmdebug:
            print(serialize(doc.body))

    def cleanup_body(self, part):
        shorttitle = None
        newparts = []  # a copy of the children w/o any Pagebreaks
        for subpart in part:
            if isinstance(subpart, Pagebreak):
                shorttitle = subpart.shorttitle
            else:
                if isinstance(subpart, six.text_type):
                    pass
                else:
                    short = self.cleanup_body(subpart)
                    if shorttitle is None:
                        shorttitle = short
                newparts.append(subpart)
        part[:] = newparts
        return shorttitle

    def get_rfc_num(self, header):
        lines = header.split("\n")
        left = [x.split("   ", 1)[0].strip() for x in lines]
        for line in left[1:]:
            if ":" not in line:
                continue
            (key, val) = (x.strip() for x in line.split(": "))
            if key == "Request for Comments":
                # only return integer part
                return re.sub("\D", "", val)

        raise ParseError("Couldn't find RFC number in header")

    def parse_header(self, header, desc):
        # split header in left-hand and right-hand side, and line by line
        lines = header.split("\n")
        left = [x.split("   ", 1)[0].strip() for x in lines]
        right = [x.split("   ", 1)[1].strip() for x in lines if "   " in x]
        # first line of lefthand side is publishing organization (?)
        desc.value(self.ns['dct'].publisher, left[0])
        # following lefthand side are key-value headers
        for line in left[1:]:
            if line.strip() == "":
                continue
            if ": " not in line:
                self.log.warning("Cannot treat %r as a key-value header" % line)
                continue

            (key, value) = (x.strip() for x in line.split(": "))
            if key == "Request for Comments":
                # make sure we only extract the numeric part --
                # normally value should be numeric, but we've seen
                # "RFC 1006", "#154" and there are doubtless other
                # variants
                value = re.sub("\D", "", value)
                if value:  # eg RFC 100
                    desc.value(self.ns['dct'].identifier, "RFC %s" % value)
            elif key == "Category":
                desc.value(self.ns['dct'].subject, value)
            elif key == "ISSN":
                desc.value(self.ns['dct'].issn, value)
            elif key in ("Updates", "Obsoletes"):
                pred = {'Updates': self.ns['rfc'].updates,
                        'Obsoletes': self.ns['rfc'].obsoletes}[key]

                for valuepart in value.split(", "):
                    rfcmatch = re.search('\d+', valuepart)
                    if rfcmatch:
                        uri = self.canonical_uri(rfcmatch.group(0))
                        desc.rel(pred, uri)
                    else:
                        self.log.warning("Can't pick out RFC number from line %s" % line)
            elif key == "BCP":
                desc.value(self.ns['rfc'].BCP, value)
            elif key == "STD":
                desc.value(self.ns['rfc'].STD, value)
            elif key == "FYI":
                desc.value(self.ns['rfc'].FYI, value)
            else:
                # Unknown headers seen: BCP, STD, FYI
                self.log.warning("Unknown header key %s (value %s)" % (key, value))

        # For right hand side, any line beginning with a single letter
        # followed by '. ' is probably a name
        for line in right:
            if re.match("[A-Z]\. ", line):
                desc.value(self.ns['dct'].creator, line)
            elif re.match("\w+ \d{4}$", line):
                # NOTE: this requires english locale!
                with util.c_locale():
                    dt = datetime.strptime(line, "%B %Y")
                d = date(dt.year, dt.month, dt.day)
                desc.value(self.ns['dct'].issued, d)
            else:
                # company affiliation - include that separate from
                # personal author identity
                desc.value(self.ns['dct'].rightsHolder, line)

    def toc_predicates(self):
        return [self.ns['dct'].identifier,
                self.ns['dct'].title,
                self.ns['dct'].issued,
                self.ns['dct'].subject]

    def toc_criteria(self, predicates=None):
        from ferenda import TocCriteria

        return [TocCriteria(binding='identifier',
                            label='Sorted by RFC #',
                            pagetitle='RFCs %(select)s--99',
                            selector=lambda x: x['identifier'][4:-2] + "00",  # "RFC 6998" => "69"
                            key=lambda x: int(x['identifier'][4:]),
                            selector_descending=True,
                            key_descending=True),   # "RFC 6998" => 6998

                TocCriteria(binding='title',
                            label='Sorted by title',
                            pagetitle='Documents starting with "%(select)s"',
                            # "The 'view-state'" property => "v"
                            selector=lambda x: util.title_sortkey(x['title'])[0],
                            key=lambda x: util.title_sortkey(x['title'])),

                TocCriteria(binding='issued',
                            label='Sorted by year',
                            pagetitle='Documents published in %(select)s',
                            selector=lambda x: x['issued'][:4],  # '2013-08-01' => '2013'
                            key=lambda x: x['issued'],
                            selector_descending=True,
                            key_descending=True),

                TocCriteria(binding='subject',
                            label='Sorted by category',
                            pagetitle='Documents in the %(select)s category',
                            selector=lambda x: x['subject'],
                            key=lambda x: int(x['identifier'][4:]),
                            key_descending=True
                            )]

    def toc_item(self, binding, row):
        return [row['identifier'] + ": ",
                Link(row['title'],
                     uri=row['uri'])]

    def news_criteria(self):
        from ferenda import Describer, NewsCriteria

        # function that returns a closure, which acts as a custom
        # selector function for the NewsCriteria objects.
        def selector_for(category):
            def selector(entry):
                graph = Graph()
                with self.store.open_distilled(entry.basefile) as fp:
                    graph.parse(data=fp.read())
                desc = Describer(graph, entry.id)
                return desc.getvalue(self.ns['dct'].subject) == category
            return selector

        return [NewsCriteria('all', 'All RFCs'),
                NewsCriteria('informational', 'Informational RFCs',
                             selector=selector_for("Informational")),
                NewsCriteria('bcp', 'Best Current Practice RFCs',
                             selector=selector_for("Best Current Practice")),
                NewsCriteria('experimental', 'Experimental RFCs',
                             selector=selector_for("Experimental")),
                NewsCriteria('standards', 'Standards Track RFCs',
                             selector=selector_for("Standards Track"))]

    def frontpage_content(self, primary=False):
        from rdflib import URIRef
        items = ""
        for entry in islice(self.news_entries(), 5):
            graph = Graph()
            with self.store.open_distilled(entry.basefile) as fp:
                graph.parse(data=fp.read())

            data = {
                'identifier': graph.value(URIRef(entry.id), self.ns['dct'].identifier).toPython(),
                'uri': entry.id,
                'title': entry.title}
            items += '<li>%(identifier)s <a href="%(uri)s">%(title)s</a></li>' % data
        return ("""<h2><a href="%(uri)s">Request for comments</a></h2>
                   <p>A complete archive of RFCs in Linked Data form. Contains %(doccount)s documents.</p>
                   <p>Latest 5 documents:</p>
                   <ul>
                      %(items)s
                   </ul>""" % {'uri': self.dataset_uri(),
                               'items': items,
                               'doccount': len(list(self.store.list_basefiles_for("_postgenerate")))})

    def tabs(self):
        return [("RFCs", self.dataset_uri())]
