# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os, tempfile
from tempfile import mkstemp
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

import six
from ferenda.compat import unittest, patch, call,  Mock, MagicMock
builtins = "__builtin__" if six.PY2 else "builtins"


from rdflib import Graph, URIRef, Namespace, Literal
DCT = Namespace("http://purl.org/dc/terms/")
from ferenda import DocumentRepository, DocumentStore, LayeredConfig, util

# SUT
from ferenda import Devel

class Main(unittest.TestCase):
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
        
    def test_mkpatch(self):
        tempdir = tempfile.mkdtemp()
        basefile = "1"
        # Test 1: A repo which do not use any intermediate files. In
        # this case, the user edits the downloaded file, then runs
        # mkpatch, which saves the edited file, re-downloads the file,
        # and computes the diff.
        store = DocumentStore(tempdir + "/base")
        downloaded_path = store.downloaded_path(basefile)
        def my_download_single(self):
            # this function simulates downloading
            with open(downloaded_path, "wb") as fp:
                fp.write("""This is a file.
It has been downloaded.
""".encode())
        
        repo = DocumentRepository(datadir=tempdir)
        with repo.store.open_downloaded(basefile, "wb") as fp:
            fp.write("""This is a file.
It has been patched.
""".encode())

        d = Devel()
        globalconf = LayeredConfig({'datadir':tempdir,
                                    'patchdir':tempdir,
                                    'devel': {'class':'ferenda.Devel'},
                                    'base': {'class':
                                             'ferenda.DocumentRepository'}},
                                   cascade=True)
        
        d.config = globalconf.devel
        with patch('ferenda.DocumentRepository.download_single') as mock:
            mock.side_effect = my_download_single
            patchpath = d.mkpatch("base", basefile, "Example patch")
        
        patchcontent = util.readfile(patchpath)
        self.assertIn("Example patch", patchcontent)
        self.assertIn("@@ -1,2 +1,2 @@", patchcontent)
        self.assertIn("-It has been downloaded.", patchcontent)
        self.assertIn("+It has been patched.", patchcontent)

        # test 2: Same, but with a multi-line desc
        with repo.store.open_downloaded(basefile, "wb") as fp:
            fp.write("""This is a file.
It has been patched.
""".encode())
        longdesc = """A longer comment
spanning
several lines"""
        with patch('ferenda.DocumentRepository.download_single') as mock:
            mock.side_effect = my_download_single
            patchpath = d.mkpatch("base", basefile, longdesc)
        patchcontent = util.readfile(patchpath)
        desccontent = util.readfile(patchpath.replace(".patch", ".desc"))
        self.assertEqual(longdesc, desccontent)
        self.assertFalse("A longer comment" in patchcontent)
        self.assertIn("@@ -1,2 +1,2 @@", patchcontent)
        self.assertIn("-It has been downloaded.", patchcontent)
        self.assertIn("+It has been patched.", patchcontent)

        # test 3: If intermediate file exists, patch that one
        intermediate_path = store.intermediate_path(basefile)
        util.ensure_dir(intermediate_path)
        with open(intermediate_path, "wb") as fp:
            fp.write("""This is a intermediate file.
It has been patched.
""".encode())
        intermediate_path = store.intermediate_path(basefile)
        def my_parse(self, basefile=None):
            # this function simulates downloading
            with open(intermediate_path, "wb") as fp:
                fp.write("""This is a intermediate file.
It has been processed.
""".encode())
        with patch('ferenda.DocumentRepository.parse') as mock:
            mock.side_effect = my_parse
            patchpath = d.mkpatch("base", basefile, "Example patch")
        patchcontent = util.readfile(patchpath)
        self.assertIn("@@ -1,2 +1,2 @@ Example patch", patchcontent)
        self.assertIn(" This is a intermediate file", patchcontent)
        self.assertIn("-It has been processed.", patchcontent)
        self.assertIn("+It has been patched.", patchcontent)
        
    def test_fsmparse(self):
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
        os.unlink("testparser.py")
        os.unlink("testparseinput.txt")
        
    def test_construct(self):
        uri = "http://example.org/doc"
        with open("testconstructtemplate.rq", "wb") as fp:
            fp.write("""PREFIX dct: <http://purl.org/dc/terms/>

CONSTRUCT { ?s ?p ?o . }
WHERE { ?s ?p ?o .
        <%(uri)s> ?p ?o . }
""".encode())            
        g = Graph()
        g.bind("dct", str(DCT))
        g.add((URIRef(uri),
               DCT.title,
               Literal("Document title")))
        config = {'connect.return_value': Mock(**{'construct.return_value': g})}
        printmock = MagicMock()
        with patch('ferenda.devel.TripleStore', **config):
            with patch(builtins+'.print', printmock):
                d = Devel()
                d.config = LayeredConfig({'storetype': 'a',
                                          'storelocation': 'b',
                                          'storerepository': 'c'})
                d.construct("testconstructtemplate.rq", uri)
        want = """
# Constructing the following from b, repository c, type a
# PREFIX dct: <http://purl.org/dc/terms/>
# 
# CONSTRUCT { ?s ?p ?o . }
# WHERE { ?s ?p ?o .
#         <http://example.org/doc> ?p ?o . }
# 

@prefix dct: <http://purl.org/dc/terms/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xml: <http://www.w3.org/XML/1998/namespace> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<http://example.org/doc> dct:title "Document title" .


# 1 triples constructed in 0.0 s
""".strip()
        got = "\n".join([x[1][0] for x in printmock.mock_calls])
        self.maxDiff = None
        self.assertEqual(want, got)
        os.unlink("testconstructtemplate.rq")

    def test_select(self):
        uri = "http://example.org/doc"
        with open("testselecttemplate.rq", "wb") as fp:
            fp.write("""PREFIX dct: <http://purl.org/dc/terms/>

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
                d.config = LayeredConfig({'storetype': 'a',
                                          'storelocation': 'b',
                                          'storerepository': 'c'})
                d.select("testselecttemplate.rq", uri)
        want = """
# Constructing the following from b, repository c, type a
# PREFIX dct: <http://purl.org/dc/terms/>
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
# Selected in 0.0 s
""".strip()
        got = "\n".join([x[1][0] for x in printmock.mock_calls])
        self.maxDiff = None
        self.assertEqual(want, got)
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
                d.config = LayeredConfig({'indextype': 'a',
                                          'indexlocation': 'b'})
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
