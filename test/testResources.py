# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys
import json
import pkg_resources
import tempfile
import shutil
sys.path.insert(0,os.getcwd())
pkg_resources.resource_listdir('ferenda','res')
from lxml import etree as ET

from ferenda.manager import setup_logger; setup_logger('CRITICAL')
from ferenda import DocumentRepository
from ferenda import util, errors
from ferenda.testutil import RepoTester, FerendaTestCase
from ferenda.compat import unittest

from examplerepos import staticmockclass, staticmockclass2, staticmockclass3
# SUT
from ferenda import Resources



class Make(unittest.TestCase, FerendaTestCase):
    def setUp(self):
        self.maxDiff = None
        self.tempdir = tempfile.mkdtemp()
        # FIXME: this creates (and tearDown deletes) a file in
        # cwd. Should be placed in self.tempdir, but tests need to be
        # adjusted to find it there.

        # NB: The section keys are different from the specified
        # classes alias properties. This is intended.
        staticmockclass.resourcebase = self.tempdir
        util.writefile("ferenda.ini", """[__root__]
datadir = %s
loglevel = CRITICAL
[test]
class=testManager.staticmockclass
[test2]
class=testManager.staticmockclass2
"""%self.tempdir)
        util.writefile(self.tempdir+"/test.js", "// test.js code goes here")
        util.writefile(self.tempdir+"/test.css", "/* test.css code goes here */")
        util.writefile(self.tempdir+"/test.png", "\x89\x50\x4e\x47\x0d\x0a\x1a\x0a PNG data goes here")
        util.writefile(self.tempdir+"/transformed.scss", "a { color: red + green; }")

    def tearDown(self):
        if os.path.exists("ferenda.ini"):
            os.remove("ferenda.ini")
        shutil.rmtree(self.tempdir)
        

    def test_basic(self):
        # Test1: No combining, resources specified by docrepos only
        # (skip the default css/js files)
        s = os.sep
        want = {'css':[s.join(['rsrc', 'css','test.css'])],
                'js':[s.join(['rsrc', 'js','test.js'])],
                'img':[s.join(['rsrc', 'img','test.png'])],
                'json': [s.join(['rsrc','api','context.json']),
                         s.join(['rsrc','api','common.json']),
                         s.join(['rsrc','api','terms.json'])],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = Resources([staticmockclass(),staticmockclass2()],
                        self.tempdir+os.sep+'rsrc',
                        cssfiles=[],
                        jsfiles=[],
                        imgfiles=[]).make()
        self.assertEqual(want, got)
        tree = ET.parse(self.tempdir+os.sep+got['xml'][0])
        stylesheets=tree.find("stylesheets").getchildren()
        self.assertEqual(len(stylesheets),1)
        self.assertEqual(stylesheets[0].attrib['href'],'rsrc/css/test.css')
        javascripts=tree.find("javascripts").getchildren()
        self.assertEqual(len(javascripts),1)
        self.assertEqual(javascripts[0].attrib['src'],'rsrc/js/test.js')
        # the javascript tag must have some text content to avoid it
        # being self-closed (which doesn't go well with HTML5)
        self.assertTrue(javascripts[0].text)
        self.assertEqual(tree.find("sitename").text,"MySite")
        self.assertEqual(tree.find("sitedescription").text,"Just another Ferenda site")
        self.assertEqual(tree.find("url").text,"http://localhost:8000/")
        self.assertTrue(os.path.exists(self.tempdir+'/rsrc/css/test.css'))
        self.assertTrue(os.path.exists(self.tempdir+'/rsrc/js/test.js'))
        tabs=tree.find("tabs")
        self.assertTrue(tabs is not None)
        search=tree.find("search")
        self.assertTrue(search is not None)

    def test_combining(self):
        # Test2: combining, resources specified by global config
        # (maybe we should use smaller CSS+JS files? Test takes 2+ seconds...)
        s = os.sep
        want = {'css':[s.join(['rsrc', 'css','combined.css'])],
                'js':[s.join(['rsrc', 'js','combined.js'])],
                'img': [],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = Resources([staticmockclass(),staticmockclass2()],self.tempdir+os.sep+'rsrc',
                        combineresources=True,
                        cssfiles=['res/css/normalize-1.1.3.css',
                                  'res/css/main.css'],
                        jsfiles=['res/js/jquery-1.10.2.js',
                                 'res/js/modernizr-2.6.3.js',
                                 'res/js/respond-1.3.0.js'],
                        sitename="Blahonga",
                        sitedescription="A non-default value").make(api=False)
        self.assertEqual(want,got)
        tree = ET.parse(self.tempdir+'/'+got['xml'][0])
        stylesheets=tree.find("stylesheets").getchildren()
        self.assertEqual(len(stylesheets),1)
        self.assertEqual(stylesheets[0].attrib['href'],'rsrc/css/combined.css')
        javascripts=tree.find("javascripts").getchildren()
        self.assertEqual(len(javascripts),1)
        self.assertEqual(javascripts[0].attrib['src'],'rsrc/js/combined.js')
        self.assertEqual(tree.find("sitename").text,"Blahonga")
        self.assertEqual(tree.find("sitedescription").text,"A non-default value")
        self.assertTrue(os.path.exists(self.tempdir+'/rsrc/css/combined.css'))
        self.assertTrue(os.path.exists(self.tempdir+'/rsrc/js/combined.js'))
        # check that the combining/minifying indeed saved us some space
        # physical path for these: relative to the location of ferenda/manager.py.
        self.assertLess(os.path.getsize(self.tempdir+'/rsrc/css/combined.css'),
                        sum([os.path.getsize(x) for x in ("ferenda/res/css/normalize-1.1.3.css",
                                                          "ferenda/res/css/main.css")]))
        self.assertLess(os.path.getsize(self.tempdir+'/rsrc/js/combined.js'),
                        sum([os.path.getsize(x) for x in ("ferenda/res/js/jquery-1.10.2.js",
                                                          "ferenda/res/js/modernizr-2.6.3.js",
                                                          "ferenda/res/js/respond-1.3.0.js")]))

    def test_default_docrepo(self):
        # Test3: No combining, make sure that a non-customized
        # DocumentRepository works
        s = os.sep
        repo = DocumentRepository()
        # but remove any external urls -- that's tested separately in Test5
        repo.config.cssfiles = [x for x in repo.config.cssfiles if not x.startswith("http://")]
        got = Resources([repo],self.tempdir+os.sep+'rsrc',
                        cssfiles=[],
                        jsfiles=[],
                        imgfiles=[]).make(api=False)
        s = os.sep
        want = {'css':[s.join(['rsrc', 'css','normalize-1.1.3.css']),
                       s.join(['rsrc', 'css','main.css']),
                       s.join(['rsrc', 'css','ferenda.css'])],
                'img':[s.join(['rsrc', 'img','navmenu-small-black.png']),
                       s.join(['rsrc', 'img','navmenu.png']),
                       s.join(['rsrc', 'img','search.png'])],
                'js':[s.join(['rsrc', 'js','jquery-1.10.2.js']),
                      s.join(['rsrc', 'js','modernizr-2.6.3.js']),
                      s.join(['rsrc', 'js','respond-1.3.0.js']),
                      s.join(['rsrc', 'js','ferenda.js'])],
                'xml':[s.join(['rsrc', 'resources.xml'])]
                      }
        self.assertEqual(want,got)

    def test_staticsite(self):
        # test4: Make sure staticsite works (ie no search form in resources.xml):
        repo = DocumentRepository()
        got = Resources([repo],self.tempdir+os.sep+'rsrc', staticsite = True).make()
        tree = ET.parse(self.tempdir+os.sep+got['xml'][0])
        search=tree.find("search")
        self.assertFalse(search)

    def test_external_resource(self):
        # test5: include one external resource, combine=False
        s = os.sep
        test = staticmockclass()
        test.config.cssfiles.append('http://example.org/css/main.css')
        want = {'css':[s.join(['rsrc', 'css','test.css']),
                       'http://example.org/css/main.css'],
                'js':[s.join(['rsrc', 'js','test.js'])],
                'img':[s.join(['rsrc', 'img','test.png'])],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = Resources([test],self.tempdir+os.sep+'rsrc',
                        cssfiles=[],
                        jsfiles=[],
                        imgfiles=[]).make(api=False)
        self.assertEqual(want,got)
                                    
    def test_external_combine(self):
        # test6: include one external resource but with combine=True, which is unsupported
        test = staticmockclass()
        test.config.cssfiles.append('http://example.org/css/main.css')
        with self.assertRaises(errors.ConfigurationError):
            got = Resources([test],self.tempdir+os.sep+'rsrc', combineresources=True).make()

    def test_footer(self):
        # test7: test the footer() functionality (+ disabling CSS/JS handling)
        s = os.sep
        got = Resources([staticmockclass3()], self.tempdir+os.sep+'rsrc').make(css=False, js=False, img=False, api=False)
        want = {'xml':[s.join(['rsrc', 'resources.xml'])]}
        self.assertEqual(want, got)
        tree = ET.parse(self.tempdir+os.sep+got['xml'][0])
        footerlinks=tree.findall("footerlinks/nav/ul/li")
        self.assertTrue(footerlinks)
        self.assertEqual(3,len(footerlinks))

    def test_windows_paths(self):
        # test8: test win32 path generation on all OS:es, including one full URL
        test = staticmockclass()
        test.config.cssfiles.append('http://example.org/css/main.css')
        want = {'css':['rsrc\\css\\test.css',
                       'http://example.org/css/main.css'],
                'js':['rsrc\\js\\test.js'],
                'img':['rsrc\\img\\test.png'],
                'xml':['rsrc\\resources.xml']}
        try:
            realsep = os.sep
            os.sep = "\\"
            from ferenda import resources
            resources.os.sep = "\\"
            got = Resources([test], self.tempdir+os.sep+'rsrc',
                            cssfiles=[],
                            jsfiles=[],
                            imgfiles=[]).make(api=False)
            self.assertEqual(want,got)
        finally:
            os.sep = realsep

    def test_nonexistent_resource(self):
        # test9: nonexistent resources should not be included
        s = os.sep
        test = staticmockclass()        
        test.config.cssfiles = ['nonexistent.css']
        want = {'css':[],
                'img':[s.join(['rsrc', 'img','test.png'])],
                'js':[s.join(['rsrc', 'js','test.js'])],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = Resources([test], self.tempdir+os.sep+'rsrc',
                        cssfiles=[],
                        jsfiles=[],
                        imgfiles=[]).make(api=False)
        self.assertEqual(want,got)

    # def test_scss_transform(self):
        # test10: scss files should be transformed to css
        # disabled until pyScss is usable on py3 again
        # test = staticmockclass()
        # test.config.cssfiles[0] = test.config.cssfiles[0].replace("test.css", "transformed.scss")
        # want = {'css':[s.join(['rsrc', 'css','transformed.css'])],
        #        'js':[s.join(['rsrc', 'js','test.js'])],
        #        'xml':[s.join(['rsrc', 'resources.xml'])]
        # }
        # got = Resources([staticmockclass()], self.tempdir+os.sep+'rsrc').make()
        # self.assertEqual(want,got)

# this tests generation of static assets (context.json, terms.json and
# common.json)
class BaseAPI(RepoTester):

    def test_files(self):
        # it'd be better to test _get_context, _get_common_graph and
        # _get_term_graph in isolation, but _create_api_files contains
        # common code to serialize the structures to files. Easier to
        # test all three.
        #
        # don't include all default ontologies to cut down on test
        # time, SKOS+FOAF ought to be enough.
        self.repo.ns = ({"foaf": "http://xmlns.com/foaf/0.1/",
                         'rdfs': 'http://www.w3.org/2000/01/rdf-schema#',
                         'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
                         'owl': 'http://www.w3.org/2002/07/owl#',
                         'skos': 'http://www.w3.org/2004/02/skos/core#'})

        got = Resources([self.repo], self.datadir + "/data/rsrc",
                        legacyapi=True).make(css=False, js=False, img=False, xml=False, api=True)
        s = os.sep
        want = {'json': [s.join(['rsrc','api','context.json']),
                         s.join(['rsrc','api','common.json']),
                         s.join(['rsrc','api','terms.json'])]}
        self.assertEqual(want, got)

        got  = json.load(open(self.datadir + "/data/rsrc/api/context.json"))
        want = json.load(open("test/files/api/jsonld-context.json"))
        self.assertEqual(want, got)

        got  = json.load(open(self.datadir + "/data/rsrc/api/terms.json"))
        want = json.load(open("test/files/api/var-terms.json"))
        self.assertEqual(want, got)

        got  = json.load(open(self.datadir + "/data/rsrc/api/common.json"))
        want = json.load(open("test/files/api/var-common.json"))
        self.assertEqual(want,got)

class ComplexAPI(RepoTester):
    def test_files(self):
        # use three repos (staticmockclass*) that each define their
        # own ontologies and terms. Make sure these show up in
        # context, terms and common
        pass
