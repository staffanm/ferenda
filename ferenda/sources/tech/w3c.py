# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from datetime import datetime
from operator import itemgetter
import os
import re
import sys

from six import text_type as str

from rdflib import Literal, Graph, URIRef, RDF, Namespace

from .rfc import PreambleSection
from ferenda import Describer, DocumentRepository, FSMParser
from ferenda import util, decorators
from ferenda.elements import serialize, html, Body, Section, Subsection, Subsubsection


class W3Standards(DocumentRepository):
    alias = "w3c"
    start_url = "http://www.w3.org/TR/tr-status-all"
    rdf_type = Namespace(util.ns['bibo']).Standard
    document_url_regex = "http://www.w3.org/TR/(?P<year>\d{4})/REC-(?P<basefile>.*)-(?P<date>\d+)"
    document_url_template = None  # no simple way of creating a url
                                 # from a basefile alone (we also need
                                 # the published date)
    basefile_regex = None  # Link text on index page do not contain basefile
    parse_content_selector = "body"
    parse_filter_selectors = ["div.toc", "div.head"]

    # NOTES:
    #
    # While the W3C standards do look very similar (except for the
    # very earliest standards), the structure of HTML varies
    # greatly. In particular, there is no standardized way that
    # section headings, ordinals of section headings, and anchors to
    # section headings are marked up. In about 50% of documents
    # (evenly distributed over the years), the HTML isn't nested in a
    # way that matches the logical structure of the text, ie section
    # 2.1 isn't a sub-element of section 2, but instead a
    # sibling. This makes it simple to just iterate through all
    # children of doc.body and use a FSMParser to recreate the logical
    # nesting.
    #
    # There are, of course, exceptions.
    #
    # xslt-xquery-serialization: uses nested divs for structure. Each
    # preamblesection is within a un-classed <div>, and each section
    # is within a <div class="div[n]"> where [n] == nesting depth, ie
    # Section = div1, Subsection = div2, Subsubsection = div3. These
    # divs nest. The same goes for other specs in the same package, eg xqueryx
    #
    # Upon closer examination, this seems to be the case for about 35%
    # of all documents.

    @decorators.action
    def stats(self):
        """Stats of amount of triples and things (RDF classes) within each parsed document."""
        stuff = []
        for basefile in self.store.list_basefiles_for("generate"):
            g = Graph()
            g = g.parse(self.store.distilled_path(basefile))
            uri = self.canonical_uri(basefile)
            stuff.append((basefile,
                          g.value(URIRef(uri), self.ns['dct'].issued),
                          len(g),
                          len(list(g.subject_objects(RDF.type)))
                          ))
        print("\t".join(("identifier", "issued", "triples", "things")))
        for docstat in sorted(stuff, key=itemgetter(3)):
            print("\t".join([str(x) for x in docstat]))

    @staticmethod  # so as to be easily called from command line
    def get_parser():

        def is_header(parser):
            chunk = parser.reader.peek()
            if type(chunk) in (html.H1, html.H2, html.H3, html.H4):
                return True
            else:
                return False

        def is_preamblesection(parser):
            if not is_header(parser):
                return False
            chunk = parser.reader.peek()
            return chunk.as_plaintext().lower() in ("abstract",
                                                    "status of this document",
                                                    "table of contents",
                                                    "appendices")

        def is_preambleending(parser):
            chunk = parser.reader.peek()

            return type(chunk) in (html.HR,)

        def is_section(parser):
            if not is_header(parser):
                return False
            chunk = parser.reader.peek()
            (ordinal, title) = analyze_sectionstart(chunk.as_plaintext())
            return section_segments_count(ordinal) == 1

        def is_subsection(parser):
            if not is_header(parser):
                return False
            chunk = parser.reader.peek()
            (ordinal, title) = analyze_sectionstart(chunk.as_plaintext())
            return section_segments_count(ordinal) == 2

        def is_subsubsection(parser):
            if not is_header(parser):
                return False
            chunk = parser.reader.peek()
            (ordinal, title) = analyze_sectionstart(chunk.as_plaintext())
            return section_segments_count(ordinal) == 3

        def is_other(parser, chunk=None):
            return True

        def make_body(parser):
            return p.make_children(Body())
        setattr(make_body, 'newstate', 'body')

        def make_preamble_section(parser):
            s = PreambleSection(title=parser.reader.next().as_plaintext())
            return p.make_children(s)
        setattr(make_preamble_section, 'newstate', 'preamblesection')

        def make_other(parser):
            return p.reader.next()

        def make_section(parser):
            (secnumber, title) = analyze_sectionstart(parser.reader.next().as_plaintext())
            s = Section(ordinal=secnumber, title=title, uri=None, meta=None)
            return parser.make_children(s)
        setattr(make_section, 'newstate', 'section')

        def make_subsection(parser):
            (secnumber, title) = analyze_sectionstart(parser.reader.next().as_plaintext())
            s = Subsection(ordinal=secnumber, title=title, uri=None, meta=None)
            return parser.make_children(s)
        setattr(make_subsection, 'newstate', 'subsection')

        def make_subsubsection(parser):
            (secnumber, title) = analyze_sectionstart(parser.reader.next().as_plaintext())
            s = Subsubsection(ordinal=secnumber, title=title, uri=None, meta=None)
            return parser.make_children(s)
        setattr(make_subsubsection, 'newstate', 'subsubsection')

        # Some helpers for the above
        def section_segments_count(s):
            return ((s is not None) and
                    len(list(filter(None, s.split(".")))))

        # Matches
        # "1 Blahonga" => ("1","Blahonga")
        # "1.2.3. This is a subsubsection" => ("1.2.3", "This is a subsection")
        re_sectionstart = re.compile("^(\d[\.\d]*) +(.*[^\.])$").match

        def analyze_sectionstart(chunk):
            m = re_sectionstart(chunk)
            if m:
                return (m.group(1).rstrip("."), m.group(2))
            else:
                return (None, chunk)

        p = FSMParser()

        p.set_recognizers(is_section,
                          is_subsection,
                          is_subsubsection,
                          is_preamblesection,
                          is_preambleending,
                          is_header,
                          is_other)
        commonstates = ("body", "preamblesection", "section", "subsection", "subsubsection")
        p.set_transitions(
            {("body", is_preamblesection): (make_preamble_section, "preamblesection"),
             ("preamblesection", is_preamblesection): (False, None),
             ("preamblesection", is_preambleending): (False, None),
             ("preamblesection", is_section): (False, None),
             ("body", is_section): (make_section, "section"),
             (commonstates, is_other): (make_other, None),
             ("section", is_subsection): (make_subsection, "subsection"),
             ("section", is_section): (False, None),
             ("subsection", is_subsubsection): (make_subsubsection, "subsubsection"),
             ("subsection", is_subsection): (False, None),
             ("subsection", is_section): (False, None),
             ("subsubsection", is_subsubsection): (False, None),
             ("subsubsection", is_subsection): (False, None),
             ("subsubsection", is_section): (False, None),
             })
        p.initial_state = "body"
        p.initial_constructor = make_body
        return p

    def parse_metadata_from_soup(self, soup, doc):
        doc.lang = self.lang
        d = Describer(doc.meta, doc.uri)
        d.rdftype(self.rdf_type)
        dct = self.ns['dct']

        # dct:title
        d.value(dct.title, soup.find("title").string, lang=doc.lang)
        d.value(dct.identifier, doc.basefile)
        # dct:abstract
        abstract = soup.find(_class="abstract")
        if abstract:
            d.value(dct['abstract'], abstract.string, lang=doc.lang)

        # dct:published
        datehdr = soup.find(lambda x: x.name in ('h2', 'h3')
                            and re.search("W3C\s+Recommendation,?\s+", x.text))
        if datehdr:
            datestr = " ".join(datehdr.text.split())
            m = re.search("(\d+)[ \-](\w+),?[ \-](\d{4})", datestr)
            if not m:
                self.log.warning("%s: Couldn't parse datestr %s" %
                                 (doc.basefile, datestr))
            else:
                datestr = " ".join(m.groups())
                date = None
                try:
                    # 17 December 1996
                    date = util.strptime(datestr, "%d %B %Y").date()
                except ValueError:
                    try:
                        # 17 Dec 1996
                        date = util.strptime(datestr, "%d %b %Y").date()
                    except ValueError:
                        self.log.warning("%s: Could not parse datestr %s" %
                                         (doc.basefile, datestr))
                if date:
                    d.value(dct.issued, date)

        # dct:editor
        editors = soup.find("dt", text=re.compile("Editors?:"))
        if editors:
            for editor in editors.find_next_siblings("dd"):
                editor_string = " ".join(x for x in editor.stripped_strings if not "@" in x)
                editor_name = editor_string.split(", ")[0]
                d.value(dct.editor, editor_name)

        # assure we got exactly one of each of the required properties
        for required in (dct.title, dct.issued):
            d.getvalue(required)  # throws KeyError if not found (or more than one)

    def parse_document_from_soup(self, soup, doc):
        # first run inherited version to get a doc.body tree that's
        # close to the actual HTML
        super(W3Standards, self).parse_document_from_soup(soup, doc)
        # then clean up doc.body best as you can with a FSMParser

        parser = self.get_parser()
        if not self.config.fsmdebug:
            self.config.fsmdebug = 'FERENDA_FSMDEBUG' in os.environ
        parser.debug = self.config.fsmdebug
        try:
            doc.body = parser.parse(doc.body)
        except:
            print("Exception")
            if parser.debug:
                import traceback
                (type, value, tb) = sys.exc_info()
                traceback.print_exception(type, value, tb)
            raise

        PreambleSection.counter = 0
        self.decorate_bodyparts(doc.body, doc.uri)

        if parser.debug:
            print(serialize(doc.body))

    def decorate_bodyparts(self, part, baseuri):
        if isinstance(part, str):
            return
        if isinstance(part, (Section, Subsection, Subsubsection)):
            # print("Decorating %s %s" % (part.__class__.__name__,part.ordinal))
            part.uri = "%s#S%s" % (baseuri, part.ordinal)
            part.meta = self.make_graph()
            desc = Describer(part.meta, part.uri)
            desc.rdftype(self.ns['bibo'].DocumentPart)
            desc.value(self.ns['dct'].title, Literal(part.title, lang="en"))
            desc.value(self.ns['bibo'].chapter, part.ordinal)
            # desc.value(self.ns['dct'].isPartOf, part.parent.uri) # implied
        for subpart in part:
            self.decorate_bodyparts(subpart, baseuri)

    def tabs(self):
        return [("W3C standards", self.dataset_uri())]
