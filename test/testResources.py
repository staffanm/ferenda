# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys
import pkg_resources
sys.path.insert(0,os.getcwd())
pkg_resources.resource_listdir('ferenda','res')

from ferenda.manager import setup_logger; setup_logger('CRITICAL')
from ferenda.testutil import RepoTester, FerendaTestCase
from ferenda.compat import unittest

# SUT
from ferenda import Resources

class Make(unittest.TestCase, FerendaTestCase):
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
        want = {'css':[s.join(['rsrc', 'css','combined.css'])],
                'js':[s.join(['rsrc', 'js','combined.js'])],
                'json': ['rsrc/api/context.json',
                         'rsrc/api/common.json',
                         'rsrc/api/terms.json'],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = manager.makeresources([test,test2],self.tempdir+os.sep+'rsrc',
                                    combine=True,
                                    cssfiles=['res/css/normalize-1.1.3.css',
                                              'res/css/main.css'],
                                    jsfiles=['res/js/jquery-1.10.2.js',
                                             'res/js/modernizr-2.6.3.js',
                                             'res/js/respond-1.3.0.js'],
                                    sitename="Blahonga",
                                    sitedescription="A non-default value")
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
        # Test3: No combining, make sure that a non-customized
        # DocumentRepository works
        repo = DocumentRepository()
        # but remove any external urls -- that's tested separately in Test5
        repo.config.cssfiles = [x for x in repo.config.cssfiles if not x.startswith("http://")]
        got = manager.makeresources([repo],self.tempdir+os.sep+'rsrc')
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

        # test4: Make sure staticsite works (ie no search form in resources.xml):
        repo = DocumentRepository()
        got = manager.makeresources([repo],self.tempdir+os.sep+'rsrc', staticsite = True)
        tree = ET.parse(self.tempdir+os.sep+got['xml'][0])
        search=tree.find("search")
        self.assertFalse(search)

        # test5: include one external resource, combine=False
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
        got = manager.makeresources([test],self.tempdir+os.sep+'rsrc')
        self.assertEqual(want,got)
                                    
        # test6: include one external resource but with combine=True, which is unsupported
        with self.assertRaises(errors.ConfigurationError):
            got = manager.makeresources([test],self.tempdir+os.sep+'rsrc', combine=True)

        # test7: test the footer() functionality
        test = staticmockclass3()
        got = manager.makeresources([test], self.tempdir+os.sep+'rsrc')
        tree = ET.parse(self.tempdir+os.sep+got['xml'][0])
        footerlinks=tree.findall("footerlinks/nav/ul/li")
        self.assertTrue(footerlinks)
        self.assertEqual(3,len(footerlinks))

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
            got = manager.makeresources([test], self.tempdir+os.sep+'rsrc')
            self.assertEqual(want,got)
        finally:
            os.sep = realsep
            
        # test9: nonexistent resources should not be included
        test = staticmockclass()
        test.config.cssfiles = ['nonexistent.css']
        want = {'css':[],
                'js':[s.join(['rsrc', 'js','test.js'])],
                'json':[s.join(['rsrc', 'api','context.json']),
                        s.join(['rsrc', 'api','common.json']),
                        s.join(['rsrc', 'api','terms.json'])],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = manager.makeresources([test], self.tempdir+os.sep+'rsrc')
        self.assertEqual(want,got)
        
        # test10: scss files should be transformed to css
        # disabled until pyScss is usable on py3 again
        # test = staticmockclass()
        # test.config.cssfiles[0] = test.config.cssfiles[0].replace("test.css", "transformed.scss")
        # want = {'css':[s.join(['rsrc', 'css','transformed.css'])],
        #        'js':[s.join(['rsrc', 'js','test.js'])],
        #        'xml':[s.join(['rsrc', 'resources.xml'])]
        # }
        # got = manager.makeresources([test], self.tempdir+os.sep+'rsrc')
        # self.assertEqual(want,got)
