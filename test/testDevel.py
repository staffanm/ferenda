# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys
import os
import tempfile
import re
import shutil
from tempfile import mkstemp

if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

import six
from layeredconfig import LayeredConfig, Defaults
from ferenda.compat import unittest, patch, call,  Mock, MagicMock
builtins = "__builtin__" if six.PY2 else "builtins"


from rdflib import Graph, URIRef, Namespace, Literal
DCTERMS = Namespace("http://purl.org/dc/terms/")
from ferenda import DocumentRepository, DocumentStore, util

# SUT
from ferenda import Devel

    
class Main(unittest.TestCase):

    def mask_time(self, s):
        return re.sub(r" in \d+\.\d{3}s", " in [MASKED]s", s)
    
    def test_dumprdf(self):
        fileno, tmpfile = mkstemp()
        fp = os.fdopen(fileno, "w")
        fp.write("""<html xmlns="http://www.w3.org/1999/xhtml">
        <head about="http://example.org/doc">
           <title property="http://purl.org/dc/terms/">Doc title</title>
        </head>
        <body>...</body>
        </html>""")
        fp.close()
        d = Devel()
        mock = MagicMock()
        with patch(builtins+'.print', mock):
            d.dumprdf(tmpfile, format="nt")
        os.unlink(tmpfile)
        self.assertTrue(mock.called)
        want = '<http://example.org/doc> <http://purl.org/dc/terms/> "Doc title" .\n\n'
        mock.assert_has_calls([call(want)])
        
    def test_dumpstore(self):
        d = Devel()
        d.config = Mock()
        # only test that Triplestore is called correctly, mock any
        # calls to any real database
        config = {'connect.return_value':
                  Mock(**{'get_serialized.return_value':
                          b'[fake store content]'})}
        printmock = MagicMock()
        with patch('ferenda.devel.TripleStore', **config):
            with patch(builtins+'.print', printmock):
                d.dumpstore(format="trix")
        want = "[fake store content]"
        printmock.assert_has_calls([call(want)])
        
        
    def test_fsmparse(self):
        try:
            # 1. write a new python module containing a class with a staticmethod
            with open("testparser.py", "w") as fp:
                fp.write("""
from six import text_type as str
from ferenda.elements import Body, Paragraph

class Testobject(object):
    @staticmethod
    def get_parser():
        return Parser()


class Parser(object):

    def parse(self, source):
        res = Body()
        for chunk in source:
            res.append(Paragraph([str(len(chunk.strip()))]))
        return res
            """)
            import imp
            fp, pathname, desc = imp.find_module("testparser")
            imp.load_module("testparser", fp, pathname, desc)
            # 2. write a textfile with two paragraphs
            with open("testparseinput.txt", "w") as fp:
                fp.write("""This is one paragraph.

And another.
    """)
            # 3. patch print and call fsmparse
            d = Devel()
            printmock = MagicMock()
            with patch(builtins+'.print', printmock):
                # 3.1 fsmparse dynamically imports the module and call the method
                #     with every chunk from the text file
                # 3.2 fsmparse asserts that the method returned a callable
                # 3.3 fsmparse calls it with a iterable of text chunks from the
                #     textfile
                # 3.4 fsmparse recieves a Element structure and prints a
                # serialized version 
                d.fsmparse("testparser.Testobject.get_parser", "testparseinput.txt")
            self.assertTrue(printmock.called)
            # 4. check that the expected thing was printed
            want = """
<Body>
  <Paragraph>
    <str>22</str>
  </Paragraph>
  <Paragraph>
    <str>12</str>
  </Paragraph>
</Body>
            """.strip()+"\n"
            printmock.assert_has_calls([call(want)])
        finally:
            util.robust_remove("testparser.py")
            util.robust_remove("testparser.pyc")
            util.robust_remove("testparseinput.txt")
            if os.path.exists("__pycache__") and os.path.isdir("__pycache__"):
                shutil.rmtree("__pycache__")
        
    def test_construct(self):
        uri = "http://example.org/doc"
        with open("testconstructtemplate.rq", "wb") as fp:
            fp.write("""PREFIX dcterms: <http://purl.org/dc/terms/>

CONSTRUCT { ?s ?p ?o . }
WHERE { ?s ?p ?o .
        <%(uri)s> ?p ?o . }
""".encode())            
        g = Graph()
        g.bind("dcterms", str(DCTERMS))
        g.add((URIRef(uri),
               DCTERMS.title,
               Literal("Document title")))
        config = {'connect.return_value':
                  Mock(**{'construct.return_value': g})}
        printmock = MagicMock()
        with patch('ferenda.devel.TripleStore', **config):
            with patch(builtins+'.print', printmock):
                d = Devel()
                d.config = LayeredConfig(Defaults({'storetype': 'a',
                                                   'storelocation': 'b',
                                                   'storerepository': 'c'}))
                d.construct("testconstructtemplate.rq", uri)
        want = """
# Constructing the following from b, repository c, type a
# PREFIX dcterms: <http://purl.org/dc/terms/>
# 
# CONSTRUCT { ?s ?p ?o . }
# WHERE { ?s ?p ?o .
#         <http://example.org/doc> ?p ?o . }
# 

@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xml: <http://www.w3.org/XML/1998/namespace> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<http://example.org/doc> dcterms:title "Document title" .


# 1 triples constructed in 0.001s
""".strip()
        got = "\n".join([x[1][0] for x in printmock.mock_calls])
        self.maxDiff = None
        self.assertEqual(self.mask_time(want),
                         self.mask_time(got))
        os.unlink("testconstructtemplate.rq")

    def test_select(self):
        uri = "http://example.org/doc"
        with open("testselecttemplate.rq", "wb") as fp:
            fp.write("""PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT ?p ?o
WHERE { <%(uri)s> ?p ?o . }
""".encode())

        result = """
[
    {
        "p": "http://purl.org/dc/terms/title", 
        "o": "Document title"
    }, 
    {
        "p": "http://purl.org/dc/terms/identifier", 
        "o": "Document ID"
    }
]""".lstrip().encode("utf-8")        
        config = {'connect.return_value': Mock(**{'select.return_value': result})}
        printmock = MagicMock()
        with patch('ferenda.devel.TripleStore', **config):
            with patch(builtins+'.print', printmock):
                d = Devel()
                d.config = LayeredConfig(Defaults({'storetype': 'a',
                                                   'storelocation': 'b',
                                                   'storerepository': 'c'}))
                d.select("testselecttemplate.rq", uri)
        want = """
# Constructing the following from b, repository c, type a
# PREFIX dcterms: <http://purl.org/dc/terms/>
# 
# SELECT ?p ?o
# WHERE { <http://example.org/doc> ?p ?o . }
# 

[
    {
        "p": "http://purl.org/dc/terms/title", 
        "o": "Document title"
    }, 
    {
        "p": "http://purl.org/dc/terms/identifier", 
        "o": "Document ID"
    }
]
# Selected in 0.001s
""".strip()
        got = "\n".join([x[1][0] for x in printmock.mock_calls])
        self.maxDiff = None
        self.assertEqual(self.mask_time(want),
                         self.mask_time(got))
        os.unlink("testselecttemplate.rq")


    def test_queryindex(self):
        res = [{'identifier': 'Doc #1',
                'about': 'http://example.org/doc1',
                'text': 'matching doc 1'},
               {'identifier': 'Doc #2',
                'about': 'http://example.org/doc2',
                'text': 'matching doc 2'}]
               
        config = {'connect.return_value': Mock(**{'query.return_value': res})}
        printmock = MagicMock()
        with patch('ferenda.devel.FulltextIndex', **config):
            with patch(builtins+'.print', printmock):
                d = Devel()
                d.config = LayeredConfig(Defaults({'indextype': 'a',
                                                   'indexlocation': 'b'}))
                d.queryindex("doc")
        want = """
Doc #1 (http://example.org/doc1): matching doc 1
Doc #2 (http://example.org/doc2): matching doc 2
""".strip()
        got = "\n".join([x[1][0] for x in printmock.mock_calls])
        self.maxDiff = None
        self.assertEqual(want, got)


    def test_parsestring(self):
        d = Devel()
        with self.assertRaises(NotImplementedError):
            d.parsestring(None,None,None)


class MockRepo(DocumentRepository):
    # alias = "base"

    def download_single(self, basefile):
        if self.config.download_text:
            with self.store.open_downloaded(basefile, "wb") as fp:
                fp.write(self.config.download_text)

    def parse(self, basefile):
        if self.config.intermediate_text:
            with self.store.open_intermediate(basefile, "wb") as fp:
                fp.write(self.config.intermediate_text)


class Koi8Repo(MockRepo):
    source_encoding = "koi8_r"
    pass
        

class Mkpatch(unittest.TestCase):

    def setUp(self):
        self.datadir = tempfile.mkdtemp()
        self.basefile = "1"
        self.store = DocumentStore(self.datadir + "/base")
        self.d = Devel()
        self.globalconf = LayeredConfig(
            Defaults({'datadir': self.datadir,
                      'patchdir': self.datadir,
                      'download_text': None,
                      'intermediate_text': None,
                      'devel': {'class': 'ferenda.Devel'},
                      'base': {'class':
                               'testDevel.MockRepo'},
                      'koi8': {'class':
                               'testDevel.Koi8Repo'}}),
            cascade=True)
        self.d.config = self.globalconf.devel
        self.d.config.download_text
        self.d.config.download_text = "what"

    def tearDown(self):
        shutil.rmtree(self.datadir)
        
    def test_download(self):
        # Test 1: A repo which do not use any intermediate files. In
        # this case, the user edits the downloaded file, then runs
        # mkpatch, which saves the edited file, re-downloads the file,
        # and computes the diff.
        dconf = self.globalconf.base
        dconf.download_text = b"This is a file.\nIt has been downloaded.\n"

        repo = MockRepo(datadir=self.datadir)
        with repo.store.open_downloaded(self.basefile, "wb") as fp:
            fp.write(b"This is a file.\nIt has been patched.\n")

        patchpath = self.d.mkpatch("base", self.basefile, "Example patch")
        self.assertTrue(patchpath)
        patchcontent = util.readfile(patchpath)
        self.assertIn("Example patch", patchcontent)
        self.assertIn("@@ -1,2 +1,2 @@", patchcontent)
        self.assertIn("-It has been downloaded.", patchcontent)
        self.assertIn("+It has been patched.", patchcontent)

    def test_longdesc(self):
        # test 2: Same, but with a multi-line desc
        dconf = self.globalconf.base
        dconf.download_text = b"This is a file.\nIt has been downloaded.\n"

        repo = MockRepo(datadir=self.datadir)
        with repo.store.open_downloaded(self.basefile, "wb") as fp:
            fp.write(b"This is a file.\nIt has been patched.\n")
        longdesc = "A longer comment\nspanning\nseveral lines"

        patchpath = self.d.mkpatch("base", self.basefile, longdesc)
        self.assertTrue(patchpath)
        patchcontent = util.readfile(patchpath)
        desccontent = util.readfile(patchpath.replace(".patch", ".desc"))
        self.assertEqual(longdesc, desccontent)
        self.assertFalse("A longer comment" in patchcontent)
        self.assertIn("@@ -1,2 +1,2 @@", patchcontent)
        self.assertIn("-It has been downloaded.", patchcontent)
        self.assertIn("+It has been patched.", patchcontent)

    def test_intermediate(self):
        # test 3: If intermediate file exists, patch that one instead
        dconf = self.globalconf.base
        dconf.intermediate_text = b"This is a intermediate file.\nIt has been processed.\n"

        repo = MockRepo(datadir=self.datadir)
        with repo.store.open_intermediate(self.basefile, "wb") as fp:
            fp.write(b"This is a intermediate file.\nIt has been patched.\n")

        patchpath = self.d.mkpatch("base", self.basefile, "Example patch")
        self.assertTrue(patchpath)
        patchcontent = util.readfile(patchpath)
        self.assertIn("@@ -1,2 +1,2 @@ Example patch", patchcontent)
        self.assertIn(" This is a intermediate file", patchcontent)
        self.assertIn("-It has been processed.", patchcontent)
        self.assertIn("+It has been patched.", patchcontent)

    def test_unicode(self):
        # test 4: Unicode characters (note the 'ş' character) 
        dconf = self.globalconf.base
        dconf.intermediate_text = b"This is a intermediate file.\nIt has been processed.\n"

        repo = MockRepo(datadir=self.datadir)
        with repo.store.open_intermediate(self.basefile, "wb") as fp:
            fp.write("This is a intermediate file.\nKardeş Gibiydiler\n".encode("utf-8"))

        patchpath = self.d.mkpatch("base", self.basefile, "Example patch")
        self.assertTrue(patchpath)
        patchcontent = util.readfile(patchpath, encoding="utf-8")
        self.assertIn("+Kardeş Gibiydiler", patchcontent)

    def test_encoding(self):
        # test 5: Non-default charset for patch
        dconf = self.globalconf.koi8
        dconf.intermediate_text = b"This is a intermediate file.\nIt has been processed.\n"

        repo = Koi8Repo(datadir=self.datadir)
        with repo.store.open_intermediate(self.basefile, "wb") as fp:
            fp.write("This is a intermediate file.\nБойцовский клуб\n".encode("koi8_r"))
            
        patchpath = self.d.mkpatch("koi8", self.basefile, "Example patch")
        self.assertTrue(patchpath)
        patchcontent = util.readfile(patchpath, encoding="koi8_r")
        self.assertIn("+Бойцовский клуб", patchcontent)
        
