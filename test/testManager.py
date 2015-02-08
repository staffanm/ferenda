# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import logging
import os
import pkg_resources
import shutil
import sys
import tempfile
from time import sleep
from subprocess import Popen, PIPE
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
from layeredconfig import LayeredConfig, Defaults

from ferenda import manager, decorators, util, errors
from ferenda import DocumentRepository, DocumentStore

class staticmockstore(DocumentStore):
    def list_basefiles_for(cls,action):
        return ["arg1","myarg","arg2"]

class staticmockclass(DocumentRepository):
    """Example class for testing"""
    alias = "staticmock"
    resourcebase = None
    documentstore_class = staticmockstore
    namespaces = ('foaf', 'rdfs', 'rdf', 'owl', 'skos')
    
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
                     'imgfiles': [self.resourcebase + '/test.png'],
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
        util.writefile(self.tempdir+"/test.png", "\x89\x50\x4e\x47\x0d\x0a\x1a\x0a PNG data goes here")
        util.writefile(self.tempdir+"/transformed.scss", "a { color: red + green; }")

    def tearDown(self):
        if os.path.exists("ferenda.ini"):
            os.remove("ferenda.ini")
        shutil.rmtree(self.tempdir)


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
        enabled_classes = {'test': 'testManager.staticmockclass'}
        argv = ["test", "mymethod", "myarg"]
        defaults = {'datadir': 'data',
                    'loglevel': 'INFO',
                    'logfile': None,
                    'staticmock': {}}
        config = manager._load_config(argv=argv, defaults=defaults)
        self.assertEqual(manager._run_class(enabled_classes,
                                            argv,
                                            config),
                         "ok!")

    def test_list_enabled_classes(self):
        self.assertEqual(manager._list_enabled_classes(),
                         OrderedDict((("test", "Example class for testing"),
                                      ("test2", "Another class for testing"))))

    def test_list_class_usage(self):
        self.assertEqual(manager._list_class_usage(staticmockclass),
                         {'mymethod':'Frobnicate the bizbaz'})


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
        # print("\n============== OUTFILE =====================")
        # print(util.readfile(outfile))
        # print("==============================================")
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
               

class RunBase(object):
    def setUp(self):
        self.addTypeEqualityFunc(OrderedDict, self.assertDictEqual)
        self.maxDiff = None
        self.modulename = "example"
        # When testing locally , we want to avoid cluttering cwd, so
        # we chdir to a temp dir, but when testing on travis-ci,
        # changing the wd makes subsequent calls to
        # pkg_resources.resource_listdir fail (at least for python <=
        # 3.2). Don't know why not the same thing happens locally.
        if 'TRAVIS' in os.environ:
            self.tempdir = os.getcwd()
        else:
            self.tempdir = tempfile.mkdtemp()
            self.orig_cwd = os.getcwd()
            os.chdir(self.tempdir)

        self._setup_files(self.tempdir,  self.modulename)
        sys.path.append(self.tempdir)

    def tearDown(self):
        manager.shutdown_logger()
        if 'TRAVIS' in os.environ:
            util.robust_remove("ferenda.ini")
        else:
            os.chdir(self.orig_cwd)
            shutil.rmtree(self.tempdir)
            sys.path.remove(self.tempdir)

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

    def _setup_files(self, tempdir, modulename):
        # 1. create new blank ini file (FIXME: can't we make sure that
        # _find_config_file is called with create=True when using
        # run() ?)
        util.writefile("ferenda.ini", """[__root__]
loglevel=WARNING
datadir = %s
url = http://localhost:8000/
searchendpoint = /search/
apiendpoint = /api/
cssfiles = ['test.css', 'other.css']        
jsfiles = ['test.js']
imgfiles = ['test.png']
indextype = WHOOSH
indexlocation = data/whooshindex        
    """ % tempdir)

        # 2. dump 2 example docrepo classes to example.py
        # FIXME: should we add self.tempdir to sys.path also (and remove it in teardown)?
        util.writefile(modulename+".py", """# Test code
import os
from time import sleep

from layeredconfig import LayeredConfig

from ferenda import DocumentRepository, DocumentStore
from ferenda import decorators, errors

class Teststore(DocumentStore):
    def list_basefiles_for(cls,action):
        return ["arg1","myarg","arg2"]
        
class Testrepo(DocumentRepository):
    alias = "test"
    documentstore_class = Teststore
    namespaces = ('foaf', 'rdfs', 'rdf', 'owl', 'skos')
        
    def get_default_options(self):
        opts = super(Testrepo, self).get_default_options()
        opts.update({'datadir': 'data',
                     'cssfiles': ['test.css'],
                     'jsfiles': ['test.js'],
                     'imgfiles': ['test.png'],
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
    def pid(self, arg):
        if 'clientname' in self.config:
            res = "%s:%s" % (self.config.clientname, os.getpid())
        else:
            res = os.getpid()
        self.log.info("%s: pid is %s" % (arg, os.getpid()))
        self.log.debug("%s: some more debug info" % (arg))
        sleep(2) # tasks need to run for some time in order to keep all subprocesses busy
        return(arg, os.getpid())

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

    @decorators.action
    def keyboardinterrupt(self, arg):
        raise KeyboardInterrupt()

    @decorators.action
    def save(self):
        self.config.saved = True
        LayeredConfig.write(self.config)

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
        util.writefile(tempdir+"/test.js", "// test.js code goes here")
        util.writefile(tempdir+"/test.css", "/* test.css code goes here */")
        util.writefile(tempdir+"/other.css", "/* other.css code goes here */")
        util.writefile(tempdir+"/test.png", "\x89\x50\x4e\x47\x0d\x0a\x1a\x0a PNG data goes here")
    

    
class Run(RunBase, unittest.TestCase):
    """Tests manager interface using only the run() entry point used by ferenda-build.py"""

    def test_noconfig(self):
        os.unlink("ferenda.ini")
        with self.assertRaises(errors.ConfigurationError):
            manager.run(["test", "mymethod", "myarg"])

    def test_noclobber(self):
        manager.run([self.modulename+".Testrepo", "enable"])
        manager.run([self.modulename+".Testrepo", "save"])
        cfg = configparser.ConfigParser()
        cfg.read(["ferenda.ini"])
        # make sure cfg has one section for testrepo and only two
        # attributes ('class' was created when enabling the module,
        # saved was created in the 'save' call)
        
        self.assertEqual(set((('class', 'example.Testrepo'),
                             ('saved', 'True'))),
                         set(cfg.items('test')))
        

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

                # # test4: specify no method -- no, that's now an error
                # argv = ["test"]
                # self.assertEqual(manager.run(argv), None)

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
        # Test 1: make sure that if setup signals that no work should
        # be done, this is respected
        with patch("example.Testrepo.setup", return_value=False):
            self.assertEqual(manager.run(list(argv)), [])
            # pass

        # Test 2: but if not, do the work
        self.assertEqual(manager.run(list(argv)), [None, "ok!", None])

    def test_run_all(self):
        self._enable_repos()
        argv = ["all", "mymethod", "myarg"]
        self.assertEqual(manager.run(argv),
                         ["ok!", "yeah!"])

    def test_run_all_all(self):
        self._enable_repos()
        argv = ["all", "mymethod", "--all"]
        # FIXME: Check to see if proper setup and teardown on Testrepo/Testrepo2 was properly called (how?)
        self.assertEqual(manager.run(argv),
                         [[None,"ok!",None],
                          [None,"yeah!",None]])

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
                                'img':[s.join(['rsrc', 'img','test.png'])],
                                'json': [s.join(['rsrc','api','context.json']),
                                         s.join(['rsrc','api','common.json']),
                                         s.join(['rsrc','api','terms.json'])],
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
        
        # 7. add an unrelated command line flag and verify that this
        # does not interfere with the processing
        argv.append('--downloadmax=50')
        got = manager.run(argv)
        self.assertEqual(want,got)


    def test_run_single_allmethods(self):
        self._enable_repos()
        argv = ["test", "all"]
        s = os.sep
        self.maxDiff = None
        want = OrderedDict(
            [('download', OrderedDict([('test',
                                        'test download ok (magic=less)'),
                                   ])),
             ('parse', OrderedDict([('test', ['test parse arg1',
                                              'test parse myarg',
                                              'test parse arg2']),
                                ])),
             ('relate', OrderedDict([('test', ['test relate arg1',
                                               'test relate myarg',
                                               'test relate arg2']),
                                 ])),
             ('makeresources',
              {'css': [s.join(['rsrc', 'css', 'test.css']),
                       s.join(['rsrc', 'css', 'other.css'])],
               'img':[s.join(['rsrc', 'img', 'test.png'])],
               'js':[s.join(['rsrc', 'js', 'test.js'])],
               'json': [s.join(['rsrc', 'api', 'context.json']),
                        s.join(['rsrc', 'api', 'common.json']),
                        s.join(['rsrc', 'api', 'terms.json'])],
               'xml':[s.join(['rsrc', 'resources.xml'])]}),
             ('generate', OrderedDict([('test', ['test generate arg1',
                                                 'test generate myarg',
                                                 'test generate arg2']),
                                   ])),
             ('toc', OrderedDict([('test', 'test toc ok'),
                              ])),
             ('news', OrderedDict([('test', 'test news ok'),
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
                'img': [s.join(['rsrc', 'img', 'test.png'])],
                'json': [s.join(['rsrc','api','context.json']),
                         s.join(['rsrc','api','common.json']),
                         s.join(['rsrc','api','terms.json'])],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = manager.run(['all', 'makeresources'])
        self.assertEqual(want,got)

        # 6. alter the ferenda.ini so that it doesn't specify any css/js files
        util.writefile("ferenda.ini", """[__root__]
loglevel=WARNING
datadir = %s
url = http://localhost:8000/
searchendpoint = /search/
apiendpoint = /api/
cssfiles = []        
jsfiles = []        
imgfiles = []        
        """ % self.tempdir)
        want = {'css':[],
                'js':[],
                'img': [],
                'json': [s.join(['rsrc','api','context.json']),
                         s.join(['rsrc','api','common.json']),
                         s.join(['rsrc','api','terms.json'])],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = manager.run(['all', 'makeresources'])
        self.assertEqual(want,got)


    def test_run_makeresources_defaultconfig(self):
        util.resource_extract('res/scripts/ferenda.template.ini', "ferenda.ini",
                              {'storetype': 'SQLITE',
                               'storelocation': 'data/ferenda.sqlite',
                               'storerepository': 'ferenda',
                               'indextype': 'WHOOSH',
                               'indexlocation': 'data/whooshindex',
                               'sitename': 'Test'})
        self._enable_repos()
        s = os.sep
        got = manager.run(['all', 'makeresources', '--loglevel=CRITICAL'])
        want = {'xml':[s.join(['rsrc', 'resources.xml'])],
                'json': [s.join(['rsrc','api','context.json']),
                         s.join(['rsrc','api','common.json']),
                         s.join(['rsrc','api','terms.json'])],
                'img': [s.join(['rsrc', 'img', 'navmenu-small-black.png']),
                        s.join(['rsrc', 'img', 'navmenu.png']),
                        s.join(['rsrc', 'img', 'search.png'])],
                'css': ['http://fonts.googleapis.com/css?family=Raleway:200,100',
                        s.join(['rsrc', 'css', 'normalize-1.1.3.css']),
                        s.join(['rsrc', 'css', 'main.css']),
                        s.join(['rsrc', 'css', 'ferenda.css'])],
                'js': [s.join(['rsrc', 'js', 'jquery-1.10.2.js']),
                       s.join(['rsrc', 'js', 'modernizr-2.6.3.js']),
                       s.join(['rsrc', 'js', 'respond-1.3.0.js']),
                       s.join(['rsrc', 'js', 'ferenda.js'])]}
        self.assertEqual(want, got)

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
        
    def test_config_init(self):
        # make sure that the sub-config object created by run() is
        # identical to the config object used by the instance
        self._enable_repos()
        argv = ['test', 'inspect', 'config']
        ourcfg = manager._load_config(argv=argv,
                                      defaults={'loglevel': 'CRITICAL',
                                                'logfile': None,
                                                'datadir': 'data',
                                                'test': {'hello': 'world'}})
        with patch('ferenda.manager._load_config', return_value=ourcfg):
            instcfg = manager.run(argv)
            self.assertIsInstance(instcfg, LayeredConfig)
            self.assertEqual(id(ourcfg.test),
                             id(instcfg))

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

    def test_run_ctrlc(self):
        self._enable_repos()
        argv = ["test", "keyboardinterrupt", "--all"]
        with self.assertRaises(KeyboardInterrupt):
            manager.run(argv)

 
class RunMultiproc(RunBase, unittest.TestCase):

    def tearDown(self):
        if sys.platform == "win32":
            sleep(1) # to allow subprocesses to clean up
        super(RunMultiproc, self).tearDown()
    
    def test_run_single_all_multiprocessing(self):
        self._enable_repos()
        argv = ["test", "pid", "--all", "--processes=3"]
        res = manager.run(argv)
        args = [x[0] for x in res]
        pids = [x[1] for x in res]
        self.assertEqual(args, ["arg1", "myarg", "arg2"])
        # assert that all pids are unique
        self.assertEqual(3, len(set(pids)))

    def test_run_single_all_multiprocessing_fail(self):
        self._enable_repos()
        # print("running multiproc for pid %s, datadir %s" % (os.getpid(), self.tempdir))
        argv = ["test","errmethod","--all", "--processes=3"]
        res = manager.run(argv)
        # this doesn't test that errors get reported immediately
        # (which they do)
        self.assertEqual(res[0][0], Exception)
        self.assertEqual(res[1][0], errors.DocumentRemovedError)
        self.assertEqual(res[2], None)
        self.assertTrue(os.path.exists("dummyfile.txt"))
            
    def test_run_ctrlc_multiprocessing(self):
        self._enable_repos()
        argv = ["test", "keyboardinterrupt", "--all", "--processes=2"]
        with self.assertRaises(KeyboardInterrupt):
            manager.run(argv)


class RunDistributed(RunBase, unittest.TestCase):
    def setUp(self):
        self.startcwd = os.getcwd()
        super(RunDistributed, self).setUp()
        pathtostart = os.path.relpath(self.startcwd, os.getcwd())
        # create a minimal version of ferenda-build.py with the
        # correct paths appended to sys.path
        with open("ferenda-build.py", "w") as fp:
            fp.write("""import sys, os
sys.path.append('%s')
            
from ferenda import manager
if __name__ == '__main__':
    manager.run(sys.argv[1:])
            """ % pathtostart.replace("\\", "/"))
                     
    def tearDown(self):
        if sys.platform == "win32":
            sleep(5) # to allow all 7 subprocess to close and release their
                     # handles
        super(RunDistributed, self).tearDown()

    @unittest.skipIf(sys.platform == "win32", "Queueless server mode not supported on Windows")
    def test_server(self):
        self._enable_repos()
        # create two out-of-process clients
        foo = Popen(['python', 'ferenda-build.py', 'all',
                     'buildclient', '--clientname=foo', '--processes=2'],
                    stderr=PIPE)
        bar = Popen(['python', 'ferenda-build.py', 'all',
                     'buildclient', '--clientname=bar', '--processes=2'],
                    stderr=PIPE)
        try:
            # run an in-process server
            argv = ["test", "pid", "--all", "--buildserver"]
            res = manager.run(argv)
            # same tests as for RunMultiproc.test_run_single_all
            args = [x[0] for x in res]
            pids = [x[1] for x in res]
            # we're not guaranteed any particular order, therefore we
            # compare sets instead of lists
            self.assertEqual(set(args), set(["arg1", "myarg", "arg2"]))
            self.assertEqual(3, len(set(pids)))
        finally:
            foo.terminate()
            bar.terminate()
        

    def test_queue(self):
        # runs a separate queue-handling process (which is the only
        # way to do it on windows). also, test with non-default port
        self._enable_repos()
        
        # create a out-of-process buildqueue server
        queue = Popen(['python', 'ferenda-build.py', 'all',
                       'buildqueue', '--serverport=3456'])

        # create two out-of-process clients
        foo = Popen(['python', 'ferenda-build.py', 'all',
                     'buildclient', '--clientname=foo', '--serverport=3456',
                     '--processes=2'],
                    stderr=PIPE)
        bar = Popen(['python', 'ferenda-build.py', 'all',
                     'buildclient', '--clientname=bar', '--serverport=3456',
                     '--processes=2'],
                    stderr=PIPE)
        sleep(2) # to allow both clients to spin up so that one won't
                 # be hogging all the jobs (since they have 2 procs
                 # each, that will lead to duplicated pids). NOTE: this
                 # does not guarantee anything
        try:
            # then, in-process, push jobs to that queue and watch them
            # return results
            argv = ["test", "pid", "--all", "--buildqueue", "--serverport=3456"]
            res = manager.run(argv)
            # same tests as for RunMultiproc.test_run_single_all
            args = [x[0] for x in res]
            pids = [x[1] for x in res]
            # we're not guaranteed any particular order, therefore we
            # compare sets instead of lists
            self.assertEqual(set(args), set(["arg1", "myarg", "arg2"]))
            self.assertEqual(3, len(set(pids)))
        finally:
            # NOTE: On Win32/py2 just terminate() will not kill children of foo
            # and bar (of which tree out of four are left hanging due
            # to us not being able to send DONE signals to them all).
            # See http://stackoverflow.com/questions/1230669/subprocess-deleting-child-processes-in-windows
            if sys.platform == "win32":
                import psutil
                for child in psutil.Process(foo.pid).children(recursive=True):
                    child.kill()
                for child in psutil.Process(bar.pid).children(recursive=True):
                    child.kill()
            
            foo.terminate()
            bar.terminate()
            queue.terminate()

import doctest
from ferenda import manager
from ferenda.testutil import Py23DocChecker

def shutup_logger(dt):
    manager.setup_logger('CRITICAL')

def load_tests(loader,tests,ignore):
    tests.addTests(doctest.DocTestSuite(manager, setUp=shutup_logger, checker=Py23DocChecker()))
    return tests
