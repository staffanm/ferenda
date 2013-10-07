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
# the 'ferenda' package. We also have to call a resource method
sys.path.insert(0,os.getcwd())
pkg_resources.resource_listdir('ferenda','res')

from ferenda.manager import setup_logger; setup_logger('CRITICAL')

from ferenda.compat import unittest
from ferenda.compat import OrderedDict

from six.moves import configparser, reload_module
try:
    # assume we're on py3.3 and fall back if not
    from unittest.mock import Mock, MagicMock, patch, call
except ImportError:
    from mock import Mock, MagicMock, patch, call

from lxml import etree as ET

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
    
class API(unittest.TestCase):
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
        self.assertTrue(os.path.exists(self.tempdir+'/rsrc/css/test.css'))
        self.assertTrue(os.path.exists(self.tempdir+'/rsrc/js/test.js'))
        tabs=tree.find("tabs")
        self.assertTrue(tabs)
        search=tree.find("search")
        self.assertTrue(search)

        # Test2: combining, resources specified by global config
        # (maybe we should use smaller CSS+JS files? Test takes 2+ seconds...)
        want = {'css':[s.join(['rsrc', 'css','combined.css'])],
                'js':[s.join(['rsrc', 'js','combined.js'])],
                'xml':[s.join(['rsrc', 'resources.xml'])]
        }
        got = manager.makeresources([test,test2],self.tempdir+os.sep+'rsrc',
                                    combine=True,
                                    cssfiles=['res/css/normalize.css',
                                              'res/css/main.css'],
                                    jsfiles=['res/js/jquery-1.9.0.js',
                                             'res/js/modernizr-2.6.2-respond-1.1.0.min.js'],
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
                        sum([os.path.getsize(x) for x in ("ferenda/res/css/normalize.css",
                                                          "ferenda/res/css/main.css")]))
        self.assertLess(os.path.getsize(self.tempdir+'/rsrc/js/combined.js'),
                        sum([os.path.getsize(x) for x in ("ferenda/res/js/jquery-1.9.0.js",
                                                          "ferenda/res/js/modernizr-2.6.2-respond-1.1.0.min.js")]))
        # Test3: No combining, make sure that a non-customized
        # DocumentRepository works
        repo = DocumentRepository()
        # but remove any external urls -- that's tested separately in Test5
        repo.config.cssfiles = [x for x in repo.config.cssfiles if not x.startswith("http://")]
        got = manager.makeresources([repo],self.tempdir+os.sep+'rsrc')
        s = os.sep
        want = {'css':[s.join(['rsrc', 'css','normalize.css']),
                       s.join(['rsrc', 'css','main.css']),
                       s.join(['rsrc', 'css','ferenda.css'])],
                'js':[s.join(['rsrc', 'js','jquery-1.9.0.js']),
                      s.join(['rsrc', 'js','modernizr-2.6.2-respond-1.1.0.min.js']),
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
        from ferenda.sources.general import Static
        static = Static()
        for b in static.store.list_basefiles_for("parse"):
            static.parse(b)
        got = manager.makeresources([Static()], self.tempdir+os.sep+'rsrc')
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
        # from pudb import set_trace; set_trace()
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
        # FIXME: check that tree contains 2 divs, that they have id
        # staticmock and staticmock2, that the p text is "Handles
        # foaf:Document documents. Contains 3 published documents."
        divs = tree.findall(".//div[@class='section-wrapper']")
        self.assertEqual(2, len(list(divs)))
        self.assertEqual("staticmock", divs[0].get("id"))
        self.assertEqual("staticmock2", divs[1].get("id"))
        self.assertIn("Handles foaf:Document", divs[0].find("p").text)
        self.assertIn("Contains 3 published documents", divs[0].find("p").text)


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
        """ % self.tempdir)

        # 2. dump 2 example docrepo classes to example.py
        # FIXME: should we add self.tempdir to sys.path also (and remove it in teardown)?
        util.writefile(self.modulename+".py", """# Test code
from ferenda import DocumentRepository, DocumentStore, decorators

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
        sys.path.append(self.tempdir)

    def tearDown(self):
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.tempdir)
        sys.path.remove(self.tempdir)

    # functionality used by most test methods
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
        self._enable_repos()
        argv = ["test","mymethod","myarg"]
        self.assertEqual(manager.run(argv),
                         "ok!")

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
             ('makeresources', {'css':[s.join(['rsrc', 'css','test.css'])],
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
        

    def test_run_makeresources(self):
        # 1. setup test_run_enable
        # 2. run('all', 'makeresources')
        # 3. verify that all css/jss files specified by default and in Testrepo gets copied
        #    (remove rsrc)
        # 4. run('all', 'makeresources', '--combine')
        # 5. verify that single css and js file is created

        self._enable_repos()
        s = os.sep
        want = {'css':[s.join(['rsrc', 'css','test.css'])],
                'js':[s.join(['rsrc', 'js','test.js'])],
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

import doctest
from ferenda import manager
def load_tests(loader,tests,ignore):
    tests.addTests(doctest.DocTestSuite(manager))
    return tests
