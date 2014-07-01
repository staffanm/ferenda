# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys
import pkg_resources
import tempfile
import shutil
sys.path.insert(0,os.getcwd())
pkg_resources.resource_listdir('ferenda','res')

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
        util.writefile(self.tempdir+"/transformed.scss", "a { color: red + green; }")

    def tearDown(self):
        if os.path.exists("ferenda.ini"):
            os.remove("ferenda.ini")
        shutil.rmtree(self.tempdir)
        

    def test_basic(self):
        # Test1: No combining, resources specified by docrepos
        s = os.sep
        want = {'css':[s.join(['rsrc', 'css','test.css'])],
                'js':[s.join(['rsrc', 'js','test.js'])],
                'json': ['rsrc/api/context.json',
                         'rsrc/api/common.json',
                         'rsrc/api/terms.json'],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = Resources([staticmockclass(),staticmockclass2()],
                        self.tempdir+os.sep+'rsrc').make()
        self.assertEqual(want, got)
        tree = ET.parse(self.tempdir+os.sep+got['xml'][0])
        stylesheets=tree.find("stylesheets").getchildren()
        self.assertEqual(len(stylesheets),1)
        self.assertEqual(stylesheets[0].attrib['href'],'rsrc/css/test.css')
        javascripts=tree.find("javascripts").getchildren()
        self.assertEqual(len(javascripts),1)
        self.assertEqual(javascripts[0].attrib['src'],'rsrc/js/test.js')
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
                'json': ['rsrc/api/context.json',
                         'rsrc/api/common.json',
                         'rsrc/api/terms.json'],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = Resources([test,test2],self.tempdir+os.sep+'rsrc',
                        combine=True,
                        cssfiles=['res/css/normalize-1.1.3.css',
                                  'res/css/main.css'],
                        jsfiles=['res/js/jquery-1.10.2.js',
                                 'res/js/modernizr-2.6.3.js',
                                 'res/js/respond-1.3.0.js'],
                        sitename="Blahonga",
                        sitedescription="A non-default value").make()
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

    def test_custom_docrepo(self):
        # Test3: No combining, make sure that a non-customized
        # DocumentRepository works
        s = os.sep
        repo = DocumentRepository()
        # but remove any external urls -- that's tested separately in Test5
        repo.config.cssfiles = [x for x in repo.config.cssfiles if not x.startswith("http://")]
        got = Resources([repo],self.tempdir+os.sep+'rsrc').make()
        s = os.sep
        want = {'css':[s.join(['rsrc', 'css','normalize-1.1.3.css']),
                       s.join(['rsrc', 'css','main.css']),
                       s.join(['rsrc', 'css','ferenda.css'])],
                'js':[s.join(['rsrc', 'js','jquery-1.10.2.js']),
                      s.join(['rsrc', 'js','modernizr-2.6.3.js']),
                      s.join(['rsrc', 'js','respond-1.3.0.js']),
                      s.join(['rsrc', 'js','ferenda.js'])],
                'json':[s.join(['rsrc', 'api','context.json']),
                        s.join(['rsrc', 'api','common.json']),
                        s.join(['rsrc', 'api','terms.json'])],
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
                'json':[s.join(['rsrc', 'api','context.json']),
                        s.join(['rsrc', 'api','common.json']),
                        s.join(['rsrc', 'api','terms.json'])],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = Resources([test],self.tempdir+os.sep+'rsrc').make()
        self.assertEqual(want,got)
                                    
    def test_external_combine(self):
        # test6: include one external resource but with combine=True, which is unsupported
        test = staticmockclass()
        test.config.cssfiles.append('http://example.org/css/main.css')
        with self.assertRaises(errors.ConfigurationError):
            got = Resources([test],self.tempdir+os.sep+'rsrc', combine=True).make()

    def test_footer(self):
        # test7: test the footer() functionality
        test = staticmockclass3()
        got = Resources([test], self.tempdir+os.sep+'rsrc').make()
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
                'json':['rsrc\\api\\context.json',
                        'rsrc\\api\\common.json',
                        'rsrc\\api\\terms.json'],
                'xml':['rsrc\\resources.xml']}
        try:
            realsep = os.sep
            os.sep = "\\"
            got = Resources([test], self.tempdir+os.sep+'rsrc').make()
            self.assertEqual(want,got)
        finally:
            os.sep = realsep

    def test_nonexistent_resource(self):
        # test9: nonexistent resources should not be included
        s = os.sep
        test = staticmockclass()
        test.config.cssfiles = ['nonexistent.css']
        want = {'css':[],
                'js':[s.join(['rsrc', 'js','test.js'])],
                'json':[s.join(['rsrc', 'api','context.json']),
                        s.join(['rsrc', 'api','common.json']),
                        s.join(['rsrc', 'api','terms.json'])],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = Resources([test], self.tempdir+os.sep+'rsrc').make()
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
        # got = Resources([test], self.tempdir+os.sep+'rsrc').make()
        # self.assertEqual(want,got)
