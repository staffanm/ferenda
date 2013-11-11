# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys
import tempfile
import shutil
import logging
import pkg_resources


# NOTE: by inserting cwd (which *should* be the top-level source code
# dir, with 'ferenda' and 'test' as subdirs) into sys.path as early as
# possible, we make it possible for pkg_resources to find resources in
# the 'ferenda' package even when we change the cwd later on. We also
# have to call a resource method to make it stick.
sys.path.insert(0,os.getcwd())
pkg_resources.resource_listdir('ferenda','res')

from ferenda.manager import setup_logger; setup_logger('CRITICAL')
from ferenda.compat import unittest, OrderedDict, Mock, MagicMock, patch, call
from ferenda.testutil import RepoTester, FerendaTestCase

import six
from six.moves import configparser, reload_module
builtins = "__builtin__" if six.PY2 else "builtins"

from lxml import etree as ET
import requests.exceptions

from ferenda import manager, decorators, util, errors
from ferenda import DocumentRepository, LayeredConfig, DocumentStore

class staticmockstore(DocumentStore):
    def list_basefiles_for(cls,action):
        return ["arg1","myarg","arg2"]

class staticmockclass(DocumentRepository):
    """Example class for testing"""
    alias = "staticmock"
    resourcebase = None
    documentstore_class = staticmockstore
    @decorators.action
    def mymethod(self, arg):
        """Frobnicate the bizbaz"""
        if arg == "myarg":
            return "ok!"

    def download(self):
        return "%s download ok" % self.alias

    def parse(self, basefile):
        return "%s parse %s" % (self.alias, basefile)

    def relate(self, basefile):
        return "%s relate %s" % (self.alias, basefile)

    def generate(self, basefile): 
        return "%s generate %s" % (self.alias, basefile)

    def toc(self): 
        return "%s toc ok" % (self.alias)

    def news(self): 
        return "%s news ok" % (self.alias)

    def internalmethod(self, arg):
        pass

    @classmethod
    def setup(cls, action, config): pass
    @classmethod
    def teardown(cls, action, config): pass
        
        
    def get_default_options(self):
        opts = super(staticmockclass, self).get_default_options()
        opts.update({'datadir': 'data',
                     'loglevel': 'DEBUG',
                     'cssfiles': [self.resourcebase + '/test.css'],
                     'jsfiles': [self.resourcebase + '/test.js']})
        return opts
                    
    
    
class staticmockclass2(staticmockclass):
    """Another class for testing"""
    alias="staticmock2"
    def mymethod(self, arg):
        """Frobnicate the bizbaz (alternate implementation)"""
        if arg == "myarg":
            return "yeah!"

class staticmockclass3(staticmockclass):
    """Yet another (overrides footer())"""
    alias="staticmock3"
    def footer(self):
        return (("About", "http://example.org/about"),
                ("Legal", "http://example.org/legal"),
                ("Contact", "http://example.org/contact")
        )
    
class API(unittest.TestCase, FerendaTestCase):
    """Test cases for API level methods of the manager modules (functions
       like enable and makeresources, including unit tests of internal
       helpers.

    """
    def setUp(self):
        self.maxDiff = None
        self.tempdir = tempfile.mkdtemp()
        staticmockclass.resourcebase = self.tempdir
        # FIXME: this creates (and tearDown deletes) a file in
        # cwd. Should be placed in self.tempdir, but tests need to be
        # adjusted to find it there.

        # NB: The section keys are different from the specified
        # classes alias properties. This is intended.
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
        
    def test_filter_argv(self):
        self.assertEqual(manager._filter_argv(["ecj", "parse", "62008J0034", "62008J0035"]),
                         ("ecj", "parse", ["62008J0034", "62008J0035"]))
        self.assertEqual(manager._filter_argv(["ecj", "parse", "62008J0034", "--force=True", "--frobnicate"]),
                         ("ecj", "parse", ["62008J0034"]))
        self.assertEqual(manager._filter_argv(["ecj", "--frobnicate"]),
                         ("ecj", None, []))

    def test_enable_class(self):
        # 1. test that a normal enabling goes well
        manager.enable("testManager.staticmockclass")
        # os.system("cat ferenda.ini")
        cfg = configparser.ConfigParser()
        cfg.read(["ferenda.ini"])
        self.assertEqual(cfg.get("staticmock","class"), "testManager.staticmockclass")
        # 2. test that an attempt to enable a nonexistent class fails"
        with self.assertRaises(ImportError):
            manager.enable("testManager.Nonexistent")

        # 3. test that an attempt to enable an alias fails
        with self.assertRaises(ValueError):
            manager.enable("staticmock")

    def test_run_class(self):
        enabled_classes = {'test':'testManager.staticmockclass'}
        argv = ["test", "mymethod","myarg"]
        self.assertEqual(manager._run_class(enabled_classes,argv),"ok!")

                    
    def test_list_enabled_classes(self):
        self.assertEqual(manager._list_enabled_classes(),
                         OrderedDict((("test","Example class for testing"),
                                      ("test2","Another class for testing"))))

    def test_list_class_usage(self):
        self.assertEqual(manager._list_class_usage(staticmockclass),
                         {'mymethod':'Frobnicate the bizbaz'})


    def test_makeresources(self):
        # Test1: No combining, resources specified by docrepos
        test = staticmockclass()
        # print("test.get_default_options %r" % test.get_default_options())
        test2 = staticmockclass2()
        s = os.sep
        want = {'css':[s.join(['rsrc', 'css','test.css'])],
                'js':[s.join(['rsrc', 'js','test.js'])],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = manager.makeresources([test,test2],self.tempdir+os.sep+'rsrc')
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

        # Test2: combining, resources specified by global config
        # (maybe we should use smaller CSS+JS files? Test takes 2+ seconds...)
        want = {'css':[s.join(['rsrc', 'css','combined.css'])],
                'js':[s.join(['rsrc', 'js','combined.js'])],
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


    def test_frontpage(self):
        test = staticmockclass()
        test2 = staticmockclass2()
        outfile = self.tempdir+'/index.html'
        manager.makeresources([test,test2], self.tempdir+'/rsrc')
        res = manager.frontpage([test,test2],
                                path=outfile)
        self.assertTrue(res)
        tree = ET.parse(outfile)
        header = tree.find(".//header/h1/a")
        self.assertEqual(header.get("href"), 'http://localhost:8000/')
        # FIXME: check that tree contains 2 divs, that they have id
        # staticmock and staticmock2, that the p text is "Handles
        # foaf:Document documents. Contains 3 published documents."
        divs = tree.findall(".//div[@class='section-wrapper']")
        self.assertEqual(2, len(list(divs)))
        self.assertEqual("staticmock", divs[0].get("id"))
        self.assertEqual("staticmock2", divs[1].get("id"))
        self.assertIn("Handles foaf:Document", divs[0].find("p").text)
        self.assertIn("Contains 3 published documents", divs[0].find("p").text)

    def test_frontpage_staticsite(self):
        test = staticmockclass(datadir=self.tempdir)
        test2 = staticmockclass2(datadir=self.tempdir)
        outfile = self.tempdir+'/index.html'
        manager.makeresources([test,test2], self.tempdir+'/rsrc')
        manager.frontpage([test,test2],
                          path=outfile,
                          staticsite=True)
        t = ET.parse(outfile)
        header = t.find(".//header/h1/a")
        self.assertEqual(header.get("href"), 'index.html')

        headernavlinks = t.findall(".//header/nav/ul/li/a")
        self.assertEqual(headernavlinks[0].get("href"), 'staticmock/toc/index.html')
        self.assertEqual(headernavlinks[1].get("href"), 'staticmock2/toc/index.html')

        css = t.findall("head/link[@rel='stylesheet']")
        self.assertRegex(css[0].get('href'), '^rsrc/css')
        
class Setup(RepoTester):

    @patch('ferenda.manager.setup_logger')
    def test_setup(self, mockprint):
        # restart the log system since setup() will do that otherwise
        manager.shutdown_logger()
        manager.setup_logger('CRITICAL')
        projdir = self.datadir+os.sep+'myproject'
        argv= ['ferenda-build.py', projdir]
        
        # test1: normal, setup succeeds
        res = manager.setup(force=True, verbose=False, unattended=True,
                            argv=argv)
        self.assertTrue(res)
        self.assertTrue(os.path.exists(projdir))

        # test2: directory exists, setup fails
        res = manager.setup(verbose=False, unattended=True,
                            argv=argv)
        self.assertFalse(res)
        shutil.rmtree(projdir)
        
        # test2: no argv, rely on sys.argv, assert False
        with patch('ferenda.manager.sys.argv'):
            self.assertFalse(manager.setup())
            self.assertFalse(os.path.exists(projdir))

        # test3: preflight fails
        with patch('ferenda.manager._preflight_check', return_value=False):
            self.assertFalse(manager.setup(unattended=True, argv=argv))
            self.assertFalse(os.path.exists(projdir))

            with patch('ferenda.manager.input', return_value="n") as input_mock:
                self.assertFalse(manager.setup(unattended=False, argv=argv))
                self.assertFalse(os.path.exists(projdir))
                self.assertTrue(input_mock.called)

        # test4: select_triplestore fails
        with patch('ferenda.manager._preflight_check', return_value=True):
            with patch('ferenda.manager._select_triplestore', return_value=(False, None, None)):
                self.assertFalse(manager.setup(unattended=True, argv=argv))
                self.assertFalse(os.path.exists(projdir))

                with patch('ferenda.manager.input', return_value="n") as input_mock:
                    self.assertFalse(manager.setup(unattended=False, argv=argv))
                    self.assertFalse(os.path.exists(projdir))
                    self.assertTrue(input_mock.called)

    def test_preflight(self):
        log = Mock()
        
        # test 1: python too old

        with patch('ferenda.manager.sys') as sysmock:
            sysmock.version_info = (2,5,6,'final',0)
            sysmock.version = sys.version
            self.assertFalse(manager._preflight_check(log, verbose=True))
            self.assertTrue(log.error.called)
            log.error.reset_mock()

        # test 2: modules are old / or missing
        with patch(builtins + '.__import__') as importmock:
            setattr(importmock.return_value, '__version__', '0.0.1')
            self.assertFalse(manager._preflight_check(log, verbose=True))
            self.assertTrue(log.error.called)
            log.error.reset_mock()

            importmock.side_effect = ImportError
            self.assertFalse(manager._preflight_check(log, verbose=True))
            self.assertTrue(log.error.called)
            log.error.reset_mock()

        # test 3: binaries are nonexistent or errors
        with patch('ferenda.manager.subprocess.call') as callmock:
            callmock.return_value = 127
            self.assertFalse(manager._preflight_check(log, verbose=True))
            self.assertTrue(log.error.called)
            log.error.reset_mock()

            callmock.side_effect = OSError
            self.assertFalse(manager._preflight_check(log, verbose=True))
            self.assertTrue(log.error.called)
            log.error.reset_mock()
            
    def test_select_triplestore(self):
        log = Mock()
        # first manipulate requests.get to give the impression that
        # fuseki or sesame either is or isn't available
        with patch('ferenda.manager.requests.get') as mock_get:
            r = manager._select_triplestore("sitename", log, verbose=True)
            self.assertEqual("FUSEKI", r[0])
            
            mock_get.side_effect = requests.exceptions.HTTPError
            r = manager._select_triplestore("sitename", log, verbose=True)
            self.assertNotEqual("FUSEKI", r[0])

            def get_sesame(url):
                if not 'openrdf-sesame' in url:
                    raise requests.exceptions.HTTPError
                resp = Mock()
                resp.text = "ok"
                return resp

            mock_get.side_effect = get_sesame
            r = manager._select_triplestore("sitename", log, verbose=True)
            self.assertEqual("SESAME", r[0])

            mock_get.side_effect = requests.exceptions.HTTPError
            r = manager._select_triplestore("sitename", log, verbose=True)
            self.assertNotEqual("SESAME", r[0])

            # all request.get calls still raises HTTP error
            with patch('ferenda.manager.TripleStore.connect') as mock_connect:
                r = manager._select_triplestore("sitename", log, verbose=True)
                self.assertEqual("SQLITE", r[0])
                def connectfail(storetype, location, repository):
                    if storetype == "SQLITE":
                        raise ImportError("BOOM")
                mock_connect.side_effect = connectfail
                r = manager._select_triplestore("sitename", log, verbose=True)
                self.assertNotEqual("SQLITE", r[0])

                r = manager._select_triplestore("sitename", log, verbose=True)
                self.assertEqual("SLEEPYCAT", r[0])
                mock_connect.side_effect = ImportError
                r = manager._select_triplestore("sitename", log, verbose=True)
                self.assertEqual(None, r[0])
                
    def test_select_fulltextindex(self):
        log = Mock()
        # first manipulate requests.get to give the impression that
        # elasticsearch either is or isn't available
        with patch('ferenda.manager.requests.get') as mock_get:
            r = manager._select_fulltextindex(log, "mysite", verbose=True)
            self.assertEqual("ELASTICSEARCH", r[0])
            self.assertEqual("http://localhost:9200/mysite/", r[1])
            mock_get.side_effect = requests.exceptions.HTTPError

            r = manager._select_fulltextindex(log, "mysite", verbose=True)
            self.assertEqual("WHOOSH", r[0])
            

    def test_runsetup(self):
        with patch('ferenda.manager.sys.exit') as mockexit:
            with patch('ferenda.manager.setup', return_value=True):
                manager.runsetup()
                self.assertFalse(mockexit.called)
                mockexit.reset_mock()
            with patch('ferenda.manager.setup', return_value=False):
                manager.runsetup()
                self.assertTrue(mockexit.called)
                

class Run(unittest.TestCase):
    """Tests manager interface using only the run() entry point used by ferenda-build.py"""

    def setUp(self):
        self.addTypeEqualityFunc(OrderedDict, self.assertDictEqual)
        self.tempdir = tempfile.mkdtemp()
        # self.modulename = hashlib.md5(self.tempdir.encode('ascii')).hexdigest()
        self.modulename = "example"
        self.orig_cwd = os.getcwd()
        # 1. create new blank ini file (FIXME: can't we make sure that
        # _find_config_file is called with create=True when using
        # run() ?)
        os.chdir(self.tempdir)
        util.writefile("ferenda.ini", """[__root__]
loglevel=WARNING
datadir = %s
url = http://localhost:8000
searchendpoint = /search/
apiendpoint = /api/
cssfiles = ['test.css', 'other.css']        
jsfiles = ['test.js']
        """ % self.tempdir)

        # 2. dump 2 example docrepo classes to example.py
        # FIXME: should we add self.tempdir to sys.path also (and remove it in teardown)?
        util.writefile(self.modulename+".py", """# Test code
from ferenda import DocumentRepository, DocumentStore, decorators, errors

class Teststore(DocumentStore):
    def list_basefiles_for(cls,action):
        return ["arg1","myarg","arg2"]
        
class Testrepo(DocumentRepository):
    alias = "test"
    documentstore_class = Teststore
        
    def get_default_options(self):
        opts = super(Testrepo, self).get_default_options()
        opts.update({'datadir': 'data',
                     'cssfiles': ['test.css'],
                     'jsfiles': ['test.js'],
                     'magic': 'less'})
        return opts

    # for inspecting the attributes of a docrepo instance
    @decorators.action
    def inspect(self, attr, subattr=None):
        a = getattr(self,attr)
        if subattr:
            return getattr(a, subattr)
        else:
            return a

    # general testing of arguments and return values (or lack thereof)
    @decorators.action
    def mymethod(self, arg):
        if arg == "myarg":
            return "ok!"

    @decorators.action
    def errmethod(self, arg):
        if arg == "arg1":
            raise Exception("General error")
        elif arg == "myarg":
            raise errors.DocumentRemovedError("Document was removed")
        elif arg == "arg2":
            e = errors.DocumentRemovedError("Document was removed")
            e.dummyfile = "dummyfile.txt"
            raise e

    def download(self):
        return "%s download ok (magic=%s)" % (self.alias, self.config.magic)

    def parse(self, basefile):
        return "%s parse %s" % (self.alias, basefile)

    def relate(self, basefile, otherrepos=[]):
        return "%s relate %s" % (self.alias, basefile)

    def generate(self, basefile, otherrepos=[]): 
        return "%s generate %s" % (self.alias, basefile)

    def toc(self, otherrepos=[]): 
        return "%s toc ok" % (self.alias)

    def news(self, otherrepos=[]): 
        return "%s news ok" % (self.alias)

    @classmethod
    def setup(cls, action, config): pass

    @classmethod
    def teardown(cls, action, config): pass

class CustomStore(DocumentStore):
    def custommethod(self):
        return "CustomStore OK"

    def list_basefiles_for(cls,action):
        return ["arg1","myarg","arg2"]

class Testrepo2(Testrepo):
    alias = "test2"
    storage_policy = "dir"
    downloaded_suffix = ".txt"
    documentstore_class = CustomStore

    @decorators.action
    def mymethod(self, arg):
        if arg == "myarg":
            return "yeah!"

    @decorators.action
    def callstore(self):
        return self.store.custommethod()

""")
        
        util.writefile(self.tempdir+"/test.js", "// test.js code goes here")
        util.writefile(self.tempdir+"/test.css", "/* test.css code goes here */")
        util.writefile(self.tempdir+"/other.css", "/* other.css code goes here */")
        sys.path.append(self.tempdir)

    def tearDown(self):
        manager.shutdown_logger()
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.tempdir)
        sys.path.remove(self.tempdir)


    def test_noconfig(self):
        os.unlink("ferenda.ini")
        with self.assertRaises(errors.ConfigurationError):
            manager.run(["test", "mymethod", "myarg"])
        
    # functionality used by most test methods except test_noconfig
    def _enable_repos(self):

        # 3. run('example.Testrepo', 'enable')
        with patch.object(logging.Logger, 'info') as mocklog:
            self.assertEqual("test",
                             manager.run([self.modulename+".Testrepo", "enable"]))
            # 4. verify that "alias foo enabled" is logged
            log_arg = mocklog.call_args[0][0]
            self.assertEqual("Enabled class %s.Testrepo (alias 'test')" % self.modulename,
                             log_arg)

            # 5. verify that ferenda.ini has changed
            cfg = configparser.ConfigParser()
            cfg.read(["ferenda.ini"])
            self.assertEqual(cfg.get("test","class"), self.modulename+".Testrepo")

            #  (same, with 'example.Testrepo2')
            self.assertEqual("test2",
                             manager.run([self.modulename+".Testrepo2", "enable"]))
            cfg = configparser.ConfigParser()
            cfg.read(["ferenda.ini"])
            self.assertEqual(cfg.get("test2","class"), self.modulename+".Testrepo2")

        with patch.object(logging.Logger, 'error') as mocklog:
            # 6. run('example.Nonexistent', 'enable') -- the ImportError must
            # be caught and an error printed.
            manager.run([self.modulename+".Nonexistent", "enable"])
            # 7. verify that a suitable error messsage is logged
            self.assertEqual("No class named '%s.Nonexistent'" % self.modulename,
                             mocklog.call_args[0][0])

    def test_run_enable(self):
        self._enable_repos()

    def test_run_single(self):
        # test1: run standard (custom) method
        self._enable_repos()
        argv = ["test","mymethod","myarg"]
        self.assertEqual(manager.run(argv),
                         "ok!")
        # test2: specify invalid alias
        argv[0] = "invalid"

        with patch('ferenda.manager.setup_logger'):
            self.assertEqual(manager.run(argv), None)

        with patch(builtins+'.print') as printmock:
            with patch('ferenda.manager.setup_logger'):
                # test3: specify invalid method
                argv = ["test", "invalid"]
                self.assertEqual(manager.run(argv), None)

                # test4: specify no method
                argv = ["test"]
                self.assertEqual(manager.run(argv), None)

    def test_run_single_errors(self):
        self._enable_repos()
        argv = ["test", "errmethod", "--all"]
        with patch('ferenda.manager.setup_logger'):
            with patch(builtins+'.print') as printmock:
                res = manager.run(argv)
        self.assertEqual(res[0][0], Exception)
        self.assertEqual(res[1][0], errors.DocumentRemovedError)
        self.assertEqual(res[2], None)
        self.assertTrue(os.path.exists("dummyfile.txt"))
        
    def test_run_single_all(self):
        self._enable_repos()
        argv = ["test","mymethod","--all"]
        with patch("example.Testrepo.setup", return_value=False):
            self.assertEqual(manager.run(argv), [])

    def test_run_all(self):
        self._enable_repos()
        argv = ["all","mymethod","myarg"]
        self.assertEqual(manager.run(argv),
                         ["ok!", "yeah!"])

        
    def test_run_all_all(self):
        self._enable_repos()
        argv = ["all", "mymethod", "--all"]
        # FIXME: Check to see if proper setup and teardown on Testrepo/Testrepo2 was properly called (how?)
        self.assertEqual(manager.run(argv),
                         [[None,"ok!",None],
                          [None,"yeah!",None]])

    # FIXME: This test magically fails every *other* run on Travis
    # with the following stacktrace:
    # 
    # Traceback (most recent call last):
    #   File "/home/travis/build/staffanm/ferenda/test/testManager.py", line 482, in test_run_all_allmethods
    #     got = manager.run(argv)
    #   File "ferenda/manager.py", line 694, in run
    #     results[action] = run(argscopy)
    #   File "ferenda/manager.py", line 681, in run
    #     return frontpage(**args)
    #   File "ferenda/manager.py", line 360, in frontpage
    #     xsltdir = repos[0].setup_transform_templates(os.path.dirname(stylesheet), stylesheet)
    #   File "ferenda/documentrepository.py", line 1747, in setup_transform_templates
    #     for f in pkg_resources.resource_listdir('ferenda',xsltdir):
    #   File "/home/travis/virtualenv/python2.7/local/lib/python2.7/site-packages/distribute-0.6.34-py2.7.egg/pkg_resources.py", line 932, in resource_listdir
    #     resource_name
    #   File "/home/travis/virtualenv/python2.7/local/lib/python2.7/site-packages/distribute-0.6.34-py2.7.egg/pkg_resources.py", line 1229, in resource_listdir
    #     return self._listdir(self._fn(self.module_path,resource_name))
    #   File "/home/travis/virtualenv/python2.7/local/lib/python2.7/site-packages/distribute-0.6.34-py2.7.egg/pkg_resources.py", line 1320, in _listdir
    #     return os.listdir(path)
    # OSError: [Errno 2] No such file or directory: 'ferenda/res/xsl'
    #
    # I can not figure out why. Skip for now.
    @unittest.skipIf('TRAVIS' in os.environ,
                 "Skipping test_run_all_allmethods on travis-ci")    
    def test_run_all_allmethods(self):
        self._enable_repos()
        argv = ["all", "all", "--magic=more"]
        s = os.sep
        want = OrderedDict(
            [('download', OrderedDict([('test','test download ok (magic=more)'),
                                       ('test2', 'test2 download ok (magic=more)')])),
             ('parse', OrderedDict([('test', ['test parse arg1',
                                              'test parse myarg',
                                              'test parse arg2']),
                                    ('test2', ['test2 parse arg1',
                                               'test2 parse myarg',
                                               'test2 parse arg2'])])),
             ('relate', OrderedDict([('test', ['test relate arg1',
                                               'test relate myarg',
                                               'test relate arg2']),
                                     ('test2', ['test2 relate arg1',
                                                'test2 relate myarg',
                                                'test2 relate arg2'])])),
             ('makeresources', {'css':[s.join(['rsrc', 'css','test.css']),
                                       s.join(['rsrc', 'css','other.css'])],
                                'js':[s.join(['rsrc', 'js','test.js'])],
                                'xml':[s.join(['rsrc', 'resources.xml'])]}),
             ('generate', OrderedDict([('test', ['test generate arg1',
                                                 'test generate myarg',
                                                 'test generate arg2']),
                                    ('test2', ['test2 generate arg1',
                                               'test2 generate myarg',
                                               'test2 generate arg2'])])),
             ('toc', OrderedDict([('test','test toc ok'),
                                ('test2', 'test2 toc ok')])),
             ('news', OrderedDict([('test','test news ok'),
                                   ('test2', 'test2 news ok')])),
             ('frontpage', True)])
        got = manager.run(argv)
        self.maxDiff = None
        self.assertEqual(want,got)
        
    # since this method also calls frontpage, it fails on travis in
    # the same way as test_run_all_allmethods.
    @unittest.skipIf('TRAVIS' in os.environ,
                 "Skipping test_run_single_allmethods on travis-ci")    
    def test_run_single_allmethods(self):
        self._enable_repos()
        argv = ["test","all"]
        s = os.sep
        self.maxDiff = None
        want = OrderedDict(
            [('download', OrderedDict([('test','test download ok (magic=less)'),
                                   ])),
             ('parse', OrderedDict([('test', ['test parse arg1',
                                              'test parse myarg',
                                              'test parse arg2']),
                                ])),
             ('relate', OrderedDict([('test', ['test relate arg1',
                                               'test relate myarg',
                                               'test relate arg2']),
                                 ])),
             ('makeresources', {'css':[s.join(['rsrc', 'css','test.css']),
                                       s.join(['rsrc', 'css','other.css'])],
                                'js':[s.join(['rsrc', 'js','test.js'])],
                                'xml':[s.join(['rsrc', 'resources.xml'])]}),
             ('generate', OrderedDict([('test', ['test generate arg1',
                                                 'test generate myarg',
                                                 'test generate arg2']),
                                   ])),
             ('toc', OrderedDict([('test','test toc ok'),
                              ])),
             ('news', OrderedDict([('test','test news ok'),
                               ])),
            ('frontpage', True)])

        self.assertEqual(manager.run(argv),
                         want)
        
    def test_run_makeresources(self):
        # 1. setup test_run_enable
        # 2. run('all', 'makeresources')
        # 3. verify that all css/jss files specified by default and in Testrepo gets copied
        #    (remove rsrc)
        # 4. run('all', 'makeresources', '--combine')
        # 5. verify that single css and js file is created
        self._enable_repos()
        s = os.sep
        want = {'css':[s.join(['rsrc', 'css','test.css']),
                       s.join(['rsrc', 'css','other.css'])],
                'js':[s.join(['rsrc', 'js','test.js'])],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = manager.run(['all', 'makeresources'])
        self.assertEqual(want,got)

        # 6. alter the ferenda.ini so that it doesn't specify any css/js files
        util.writefile("ferenda.ini", """[__root__]
loglevel=WARNING
datadir = %s
url = http://localhost:8000
searchendpoint = /search/
apiendpoint = /api/
        """ % self.tempdir)
        want = {'css':[],
                'js':[],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = manager.run(['all', 'makeresources'])
        self.assertEqual(want,got)

    def test_delayed_config(self):
        # Make sure configuration values gets stored properly in the instance created by run()
        self._enable_repos()

        self.assertEqual(".html",               manager.run(['test','inspect','downloaded_suffix']))
        self.assertEqual("file",                manager.run(['test','inspect','storage_policy']))
        self.assertEqual(self.tempdir,          manager.run(['test','inspect','config', 'datadir']))
        self.assertEqual(".html",               manager.run(['test','inspect','store', 'downloaded_suffix']))
        self.assertEqual("file",                manager.run(['test','inspect','store', 'storage_policy']))
        self.assertEqual(self.tempdir+os.sep+"test",  manager.run(['test','inspect','store', 'datadir']))
        
        self.assertEqual(".txt",                 manager.run(['test2','inspect','downloaded_suffix']))
        self.assertEqual("dir",                  manager.run(['test2','inspect','storage_policy']))
        self.assertEqual(self.tempdir,           manager.run(['test2','inspect','config', 'datadir']))
        self.assertEqual(".txt",                 manager.run(['test2','inspect','downloaded_suffix']))
        self.assertEqual("dir",                  manager.run(['test2','inspect','storage_policy']))
        self.assertEqual(self.tempdir+os.sep+"test2",  manager.run(['test2','inspect','store', 'datadir']))
        
    def test_custom_docstore(self):
        self._enable_repos()
        got = manager.run(['test2', 'callstore'])
        self.assertEqual("CustomStore OK", got)

    def test_named_logfile(self):
        self._enable_repos()
        self.assertFalse(os.path.exists("out.log"))
        argv = ["test","mymethod","myarg","--logfile=out.log"]
        manager.run(argv)
        self.assertTrue(os.path.exists("out.log"))
        os.unlink("out.log")

    def test_print_usage(self):
        builtins = "__builtin__" if six.PY2 else "builtins"
        self._enable_repos()
        with patch(builtins+'.print') as printmock:
            manager.run([])

        executable = sys.argv[0]
        got = "\n".join([x[1][0] for x in printmock.mock_calls])
        got = got.replace(executable, "[EXEC]")
        want = """Usage: [EXEC] [class-or-alias] [action] <arguments> <options>
   e.g. '[EXEC] ferenda.sources.EurlexCaselaw enable'
        '[EXEC] ecj parse 62008J0042'
        '[EXEC] all generate'
Available modules:
 * test: [Undocumented]
 * test2: [Undocumented]"""
        self.assertEqual(got, want)
        
    def test_runserver(self):
        self._enable_repos()
        m = Mock()
        with patch('ferenda.manager.make_server', return_value=m) as m2:
            manager.run(["all", "runserver"])
            self.assertTrue(m2.called)
            self.assertTrue(m.serve_forever.called)
        
        

import doctest
from ferenda import manager
def shutup_logger(dt):
    manager.setup_logger('CRITICAL')

def load_tests(loader,tests,ignore):
    tests.addTests(doctest.DocTestSuite(manager, setUp=shutup_logger))
    return tests
