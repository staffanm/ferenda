# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os, tempfile, shutil
from datetime import date
from six import text_type as str
from lxml import etree
from lxml.builder import ElementMaker
from bs4 import BeautifulSoup
from rdflib import Graph, Namespace
from ferenda.compat import unittest

if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())
from ferenda.manager import setup_logger; setup_logger('CRITICAL')
from ferenda.citationpatterns import url as urlparser
from ferenda.testutil import FerendaTestCase
from ferenda import util
# SUT
from ferenda.elements import (serialize, deserialize, AbstractElement,
                              UnicodeElement, CompoundElement,
                              TemporalElement, OrdinalElement,
                              PredicateElement, Body, Section, Paragraph,
                              Link, html)
from ferenda import elements as el


class Main(unittest.TestCase):

    def test_serialize_roundtrip(self):
        # Create a elements object tree
        tree = Body([Section([Paragraph(["Hello"]),
                              Paragraph(["World"])],
                             ordinal="1",
                             title="Main section"),
                     Section([42,
                              date(2013,11,27),
                              b'bytestring',
                              {'foo': 'bar',
                               'x': 'y'}],
                             ordinal=2,
                             title="Native types")
                 ])
        # roundtrip using the default XML format 
        serialized = serialize(tree)
        self.assertIsInstance(serialized, str)
        newtree = deserialize(serialized, caller_globals=globals())
        self.assertEqual(tree, newtree)

        # make another section with special (but commonly used) types
        # and try to roundtrip them. The XML serialization format does
        # not support this.
        graph = Graph().parse(data="""@prefix dcterms: <http://purl.org/dc/terms/> .

<http://example.org/1> dcterms:title "Hello world"@en .
""", format="turtle")
        parseresult = urlparser.parseString("http://example.org/1")
        tree.append(Section([parseresult,
                             graph],
                            meta=graph))
        
        # roundtrip using JSON (which uses fully qualified classnames,
        # so we don't need to pass globals() into deserialize()
        serialized = serialize(tree, format="json")
        self.assertIsInstance(serialized, str)
        newtree = deserialize(serialized, format="json")

        # two pyparsing.ParseResult objects cannot be directly
        # compared (they don't implement __eq__), therefore we compare
        # their XML representations
        tree[2][0] = util.parseresults_as_xml(tree[2][0])
        newtree[2][0] = util.parseresults_as_xml(newtree[2][0])
        self.assertEqual(tree, newtree)

    def test_json_roundtrip(self):
        # a more realistic roundtrip example with some hairy parts
        from ferenda import PDFDocumentRepository, PDFReader
        d = PDFDocumentRepository()
        doc = d.make_document("sample")
        # make SURE that the intermediate files are newer than the pdf
        os.utime("test/files/pdfreader/intermediate/sample.xml", None)
        reader = PDFReader(filename="test/files/pdfreader/sample.pdf",
                           workdir="test/files/pdfreader/intermediate")
        d.parse_from_pdfreader(reader, doc)
        jsondoc = serialize(doc, format="json")
        newdoc = deserialize(jsondoc, format="json")
        self.assertEqual(doc, newdoc)

    def test_serialize_pyparsing(self):
        # these objects can't be roundtripped
        from ferenda.citationpatterns import url
        x = url.parseString("http://example.org/foo?param=val")
        serialized = serialize(Body([x]))
        self.assertEqual("""<Body>
  <url>
    <scheme>http</scheme>
    <netloc>example.org</netloc>
    <path>/foo</path>
    <query>param=val</query>
  </url>
</Body>
""", serialized)

    def test_abstract(self):
        x = AbstractElement()
        with self.assertRaises(AttributeError):
            x.foo = "bar"

        self.assertEqual(b'<abstractelement xmlns="http://www.w3.org/1999/xhtml"/>',
                         etree.tostring(x.as_xhtml()))

    def test_compound(self):
        x = CompoundElement(["hello", "world"], id="42", foo="bar")
        x.foo = "baz"
        with self.assertRaises(AttributeError):
            x.y = "z"
        x.append(os.listdir) # a non-serializable object (in this case a function)
        self.assertEqual(b'<compoundelement xmlns="http://www.w3.org/1999/xhtml" id="42">helloworld&lt;built-in function listdir&gt;</compoundelement>',
                         etree.tostring(x.as_xhtml()))
        self.assertEqual(Body([Section([Paragraph(["Hello"]),
                                        Paragraph(["World"])])]).as_plaintext(),
                         "Hello World")
        
    def test_unicode(self):
        x = UnicodeElement("Hello world", id="42")
        self.assertEqual(b'<unicodeelement xmlns="http://www.w3.org/1999/xhtml" id="42">Hello world</unicodeelement>',
                         etree.tostring(x.as_xhtml()))

        with self.assertRaises(TypeError):
            UnicodeElement(b'bytestring')
        
    def test_temporal(self):
        class TemporalString(UnicodeElement, TemporalElement):
            pass
        x = TemporalString("Hello", entryintoforce=date(2013, 1, 1),
                           expires=date(2014, 1, 1))
        self.assertFalse(x.in_effect(date(2012, 7, 1)))
        self.assertTrue(x.in_effect(date(2013, 7, 1)))
        self.assertFalse(x.in_effect(date(2014, 7, 1)))
                                                        
    def test_ordinal(self):
        class OrdinalString(UnicodeElement, OrdinalElement):
            pass
        x = OrdinalString("Foo", ordinal="2")
        y = OrdinalString("Bar", ordinal="2 a")
        z = OrdinalString("Baz", ordinal="10")
        w = OrdinalString("Duplicate of Foo", ordinal="2")
        self.assertTrue(x < y < z)
        self.assertTrue(z > y > x)
        self.assertTrue(x != y)
        self.assertTrue(x == w)
        self.assertTrue(x <= w <= y)
        self.assertTrue(y >= w >= x)
        
    def test_predicate(self):
        class PredicateString(UnicodeElement, PredicateElement):
            pass
        # known vocabulary used
        x = PredicateString("This is my title",
                            predicate="http://purl.org/dc/terms/title")
        self.assertEqual("dcterms:title", x.predicate)

        # unknown vocabulary used
        y = PredicateString("This is my title",
                            predicate="http://example.org/vocab/title")
        self.assertEqual("http://example.org/vocab/title", y.predicate)

        # No predicate used --- default to rdfs:Resource
        z = PredicateString("This is a resource")
        from rdflib import RDFS
        self.assertEqual(RDFS.Resource, z.predicate)

    def test_link(self):
        x = Link("Link text", uri="http://example.org/")
        self.assertEqual(str(x), "Link text")
        self.assertEqual(repr(x), "Link('Link text', uri=http://example.org/)")

        # y = Link("Räksmörgås", uri="http://example.org/")
        # self.assertEqual(str(y), "Räksmörgås")
        # self.assertEqual(repr(y),
        #                  "Link('Räksmörgås', uri=http://example.org/)")
        
    def test_elements_from_soup(self):
        soup = BeautifulSoup("""<html>
<head>
  <title>Example doc</title>
</head>
<body>
  <marquee>Hello world</marquee>
  <!-- Hello world -->
  <center>Hello world</center>
  <p>That's enough of this nonsense</p>
</body>""")
        got = html.elements_from_soup(soup.html)
        self.assertEqual(html.HTML([html.Head([html.Title(["Example doc"])]),
                                    html.Body([html.P(["That's enough of this nonsense"])])]),
                         got)


class AsXHTML(unittest.TestCase, FerendaTestCase):

    def _test_asxhtml(self, want, body):
        uri = "http://localhost:8000/res/base/basefile"
        got = etree.tostring(body.as_xhtml(uri), pretty_print=True)
        self.assertEqualXML(want, got)

    def test_simple(self):
        # Test 1: Simple document using our own element objects
        body = el.Body([el.Heading(['Toplevel heading'], level=1),
                        el.Paragraph(['Introductory preamble']),
                        el.Section([el.Paragraph(['Some text']),
                                    el.Subsection([el.Paragraph(['More text'])],
                                                  ordinal='1.1',
                                                  title="First subsection")],
                                   ordinal='1', title='First section'),
                        el.Section([el.Paragraph(['Even more text'])],
                                   ordinal='2', title='Second section')])
        want = """
<body xmlns="http://www.w3.org/1999/xhtml"
      about="http://localhost:8000/res/base/basefile">
  <h1>Toplevel heading</h1>
  <p>Introductory preamble</p>
  <div content="First section"
       about="http://localhost:8000/res/base/basefile#S1"
       property="dcterms:title"
       typeof="bibo:DocumentPart"
       class="section">
    <span href="http://localhost:8000/res/base/basefile"
          rel="dcterms:isPartOf"></span>
    <span content="1" about="http://localhost:8000/res/base/basefile#S1"
          property="bibo:chapter"/>
    <p>Some text</p>
    <div content="First subsection"
         about="http://localhost:8000/res/base/basefile#S1.1"
         property="dcterms:title"
         typeof="bibo:DocumentPart"
         class="subsection">
      <span href="http://localhost:8000/res/base/basefile#S1"
            rel="dcterms:isPartOf"></span>
      <span content="1.1" about="http://localhost:8000/res/base/basefile#S1.1"
            property="bibo:chapter"/>
      <p>More text</p>
    </div>
  </div>
  <div content="Second section"
       about="http://localhost:8000/res/base/basefile#S2"
       property="dcterms:title"
       typeof="bibo:DocumentPart"
       class="section">
    <span href="http://localhost:8000/res/base/basefile"
          rel="dcterms:isPartOf"></span>
    <span content="2" about="http://localhost:8000/res/base/basefile#S2"
          property="bibo:chapter"/>
    <p>Even more text</p>
  </div>
</body>"""
        self._test_asxhtml(want, body)

    def test_html(self):
        # test 2: use element.html elements only, to make a similar
        # document (although without metadata about
        # sections/subsection and classses). Uses some HTML5 elements
        # that are converted to divs when rendering as XHTML 1.1
        body = html.Body([html.H1(['Toplevel heading']),
                          html.Summary(['Introductory preamble']),
                          html.Section([html.H2(['First section']),
                                        html.P(['Some text']),
                                        html.Section([
                                            html.H3(['First subsection']),
                                            html.P(['More text'])])]),
                          html.Section([html.H2(['Second section']),
                                        html.P(['Even more text'])])])
        want = """
<body xmlns="http://www.w3.org/1999/xhtml"
      about="http://localhost:8000/res/base/basefile">
  <h1>Toplevel heading</h1>
  <div class="summary">Introductory preamble</div>
  <div class="section">
    <h2>First section</h2>
    <p>Some text</p>
    <div class="section">
      <h3>First subsection</h3>
      <p>More text</p>
    </div>
  </div>
  <div class="section">
    <h2>Second section</h2>
    <p>Even more text</p>
  </div>
</body>
"""
        self._test_asxhtml(want, body)

    def test_meta(self):
        # test 3: use a mix of our own elements and html elements,
        # with meta + uri attached to some nodes
        g1 = Graph().parse(format='n3', data="""
@prefix bibo: <http://purl.org/ontology/bibo/> .
@prefix dcterms: <http://purl.org/dc/terms/> .

<http://localhost:8000/res/base/basefile#S1> a bibo:DocumentPart;
        dcterms:title "First section";
        bibo:chapter "1" .
        """)
        g2 = Graph().parse(format='n3', data="""
@prefix bibo: <http://purl.org/ontology/bibo/> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<http://localhost:8000/res/base/basefile#S2> a bibo:DocumentPart;
        dcterms:title "Second section";
        bibo:chapter "2";
        dcterms:creator "Fred Bloggs"@en-GB;
        dcterms:issued "2013-05-10"^^xsd:date;
        owl:sameAs <http://example.org/s2> .

<http://example.org/s2> dcterms:title "Same same but different" .
       
<http://localhost:8000/res/base/unlrelated> dcterms:title "Unrelated document" .
        
        """)
        
        body = el.Body([el.Heading(['Toplevel heading'], level=1),
                        html.P(['Introductory preamble']),
                        html.Div([html.P(['Some text']),
                                  el.Subsection([el.Paragraph(['More text'])],
                                                ordinal='1.1',
                                                title="First subsection")],
                                 uri = 'http://localhost:8000/res/base/basefile#S1',
                                 meta = g1),
                        el.Section([el.Paragraph(['Even more text'])],
                                   uri = 'http://localhost:8000/res/base/basefile#S2',
                                   meta = g2)])
        want  = """
<body xmlns="http://www.w3.org/1999/xhtml"
      about="http://localhost:8000/res/base/basefile">
  <h1>Toplevel heading</h1>
  <p>Introductory preamble</p>
  <div about="http://localhost:8000/res/base/basefile#S1"
       content="First section"
       property="dcterms:title"
       typeof="bibo:DocumentPart">
    <span href="http://localhost:8000/res/base/basefile"
          rel="dcterms:isPartOf"/>
    <span content="1"
          property="bibo:chapter"
          xml:lang=""/>
    <p>Some text</p>
    <div about="http://localhost:8000/res/base/basefile#S1.1"
         content="First subsection"
         property="dcterms:title"
         typeof="bibo:DocumentPart"
         class="subsection">
      <span href="http://localhost:8000/res/base/basefile#S1"
            rel="dcterms:isPartOf"/>
      <span about="http://localhost:8000/res/base/basefile#S1.1"
            content="1.1"
            property="bibo:chapter"/>
      <p>More text</p>
    </div>
  </div>
  <div about="http://localhost:8000/res/base/basefile#S2"
      class="section"
      content="Second section"
      property="dcterms:title"
      typeof="bibo:DocumentPart">
    <span href="http://localhost:8000/res/base/basefile"
          rel="dcterms:isPartOf"/>
    <span href="http://example.org/s2"
          rel="owl:sameAs">
      <span content="Same same but different"
            property="dcterms:title"
            xml:lang=""/>
    </span>
    <span content="2"
          property="bibo:chapter"
          xml:lang=""/>
    <span content="2013-05-10"
          property="dcterms:issued"
          datatype="xsd:date"/>
    <span content="Fred Bloggs"
          property="dcterms:creator"
          xml:lang="en-GB"/>
    <p>Even more text</p>
  </div>
</body>"""
        self._test_asxhtml(want, body)

    def test_custom(self):
        # test 4: define a CompoundElement subclass and override
        # as_xhtml
        class Preamble(el.CompoundElement):
            tagname = "div"
            classname = "preamble"
            
            def as_xhtml(self, uri, parent_uri=None):
                # a fairly complicated custom serialization that
                # inserts a new child node where before there was only
                # text, and so that text has to be moved from the
                # parent.text to child.tail
                E = ElementMaker(namespace="http://www.w3.org/1999/xhtml")
                element = super(Preamble, self).as_xhtml(uri)
                note  = E('span', {'class': 'preamble-note'},
                          self.note + ": ")
                note.tail = element.text
                element.text = None
                element.insert(0, note)
                return element
        body = el.Body([el.Heading(['Toplevel heading'], level=1),
                        Preamble(['Introductory preamble'],
                                 note='Read this first'),
                        el.Section([el.Paragraph(['Some text'])],
                                   ordinal='1', title='First section')])

        want = """
<body xmlns="http://www.w3.org/1999/xhtml"
      about="http://localhost:8000/res/base/basefile">
  <h1>Toplevel heading</h1>
  <div class="preamble"><span class="preamble-note">Read this first: </span>Introductory preamble</div>
  <div content="First section"
       about="http://localhost:8000/res/base/basefile#S1"
       property="dcterms:title"
       typeof="bibo:DocumentPart"
       class="section">
    <span href="http://localhost:8000/res/base/basefile"
          rel="dcterms:isPartOf"></span>
    <span content="1" about="http://localhost:8000/res/base/basefile#S1"
          property="bibo:chapter"/>
    <p>Some text</p>
  </div>
</body>
"""
        self._test_asxhtml(want, body)


    def test_nested(self):
        # Make sure nested sections have the expected dcterms:isPartOf
        # relation if they have a @about property (could be from a
        # .uri property or dynamically constructed like
        # SectionalElement.as_xhtml)

        class MySection(el.CompoundElement):
            tagname = "div"
            classname = "mysection"
            partrelation = Namespace(util.ns['schema']).isPartOf
        
        body = el.Body([el.Section([el.Paragraph(['Some text']),
                                    el.Link("txt", uri="http://ex.org/ext"),
                                    el.Subsection([el.Paragraph(['More text']),
                                                   el.Subsubsection([el.Paragraph(['Even more text'])],
                                                                    ordinal="1.1.1",
                                                                    title="First subsubsection")],
                                                  ordinal="1.1",
                                                  title="First subsection")],
                                   ordinal="1",
                                   title="First section"),
                        MySection([el.Paragraph(['Even more text'])],
                                   uri="http://example.org/s2")])
                                   
        want = """
<body xmlns="http://www.w3.org/1999/xhtml"
      about="http://localhost:8000/res/base/basefile">
  <div content="First section"
       property="dcterms:title"
       about="http://localhost:8000/res/base/basefile#S1"
       typeof="bibo:DocumentPart"
       class="section">
    <span rel="dcterms:isPartOf"
          href="http://localhost:8000/res/base/basefile"/>
    <span content="1" about="http://localhost:8000/res/base/basefile#S1"
          property="bibo:chapter"/>
    <p>Some text</p>
    <a href="http://ex.org/ext">txt</a>
    <div content="First subsection"
         property="dcterms:title"
         about="http://localhost:8000/res/base/basefile#S1.1"
         typeof="bibo:DocumentPart"
         class="subsection">
      <span rel="dcterms:isPartOf"
            href="http://localhost:8000/res/base/basefile#S1"/>
      <span content="1.1"
            about="http://localhost:8000/res/base/basefile#S1.1"
            property="bibo:chapter"/>
      <p>More text</p>
      <div content="First subsubsection"
           property="dcterms:title"
           about="http://localhost:8000/res/base/basefile#S1.1.1"
           typeof="bibo:DocumentPart"
           class="subsubsection">
        <span rel="dcterms:isPartOf"
              href="http://localhost:8000/res/base/basefile#S1.1"/>
        <span content="1.1.1"
              about="http://localhost:8000/res/base/basefile#S1.1.1"
              property="bibo:chapter"/>
        <p>Even more text</p>
      </div>
    </div>
  </div>
  <div about="http://example.org/s2"
       class="mysection">
    <span rel="schema:isPartOf"
          href="http://localhost:8000/res/base/basefile"/>
    <p>Even more text</p>
  </div>
</body>"""
        self._test_asxhtml(want, body)

    def test_malformed(self):
        # Test 5: Illegal indata (raw ESC character in string)
        body = el.Body(['Toplevel\x1b heading'])
        want = """
<body xmlns="http://www.w3.org/1999/xhtml"
      about="http://localhost:8000/res/base/basefile">Toplevel heading</body>
"""
        self._test_asxhtml(want, body)


class HTML(unittest.TestCase):
    def test_elements_from_soup(self):
        from ferenda.elements import html
        soup = BeautifulSoup("""<body>
<h1>Sample</h1>
<div class="main">
<img src="xyz.png"/>
<p>Some <b>text</b></p>
<dl>
<dt>Term 1</dt>
<dd>Definition 1</dd>
</dl>
</div>
<div id="foot">
<hr/>
<a href="/">home</a> - <a href="/about">about</a>
</div>
</body>""")
        body = html.elements_from_soup(soup.body)
        # print("Body: \n%s" % serialize(body))
        result = html.Body([html.H1(["Sample"]),
                            html.Div([html.Img(src="xyz.png"),
                                      html.P(["Some ",
                                              html.B(["text"])]),
                                      html.DL([html.DT(["Term 1"]),
                                               html.DD(["Definition 1"])])
                                  ], **{"class": "main"}),
                            html.Div([html.HR(),
                                      html.A(["home"], href="/"),
                                      " - ",
                                      html.A(["about"], href="/about")
                                  ], id="foot")])
        self.maxDiff = 4096
        self.assertEqual(serialize(body), serialize(result))

        
import doctest
def load_tests(loader,tests,ignore):
    from ferenda.elements import elements
    tests.addTests(doctest.DocTestSuite(elements))
    return tests
