#!/usr/bin/env python
# -*- coding: utf-8 -*-
""":py:mod:`unittest`-based classes and accompanying functions to
create some types of ferenda-specific tests easier."""
from __future__ import unicode_literals
import sys
import os
import tempfile
import shutil
import time
import json
import codecs
import collections
import filecmp
from io import BytesIO, StringIO
from difflib import unified_diff
from ferenda.compat import unittest 
from ferenda.compat import Mock, patch

import six
from six import text_type as str
from six import binary_type as bytes

import rdflib
from rdflib.compare import graph_diff
from rdflib.util import guess_format
from lxml import etree


from ferenda import DocumentRepository
from ferenda import TextReader
from ferenda import elements
from ferenda import util

class FerendaTestCase(object):
    """Convenience class with extra AssertEqual methods. Note that even
though this method provides :py:class:`unittest.TestCase`-like assert methods, it
does not derive from :py:class:`~unittest.TestCase`. When creating a test case that
makes use of these methods, you need to inherit from both :py:class:`~unittest.TestCase` and
this class, ie::

        class MyTestcase(unittest.TestCase, ferenda.testutil.FerendaTestCase):
            def test_simple(self):
                self.assertEqualXML("<foo arg1='x' arg2='y'/>", "<foo arg2='y' arg1='x'/>")

    """
    # FIXME: Some of these should (at least optionally) be registered
    # with TestCase.assertEqual through .addTypeEqualityFunc, but some
    # (eg. assertEqualDirs) have non-unique types

    def assertEqualGraphs(self, want, got, exact=True):
        """Assert that two RDF graphs are identical (isomorphic).

        :param want: The graph as expected
        :type  want: rdflib.Graph object or the filename of a serialized graph
        :param got: The actual graph
        :type  got: rdflib.Graph object or the filename of a serialized graph
        :param exact: Whether to require that the graphs are exactly alike (True) or only if all triples in want exists in got (False)
        :type  exact: bool
        """
           
        def _loadgraph(filename):
            g = rdflib.Graph()
            g.parse(filename, format=guess_format(filename))
            return g

        if not isinstance(want, rdflib.Graph):
            want = _loadgraph(want)
        if not isinstance(got, rdflib.Graph):
            got = _loadgraph(got)

        (in_both, in_first, in_second) = graph_diff(want, got)
        msg = ""
        if in_first:
            for (s, p, o) in sorted(in_first, key=lambda t:(t[0], t[1],t[2])):
                msg += "- %s %s %s\n" % (s.n3(), p.n3(), o.n3())
        if (exact and in_second) or in_first:
            for (s, p, o) in sorted(in_second, key=lambda t:(t[0], t[1],t[2])):
                msg += "+ %s %s %s\n" % (s.n3(), p.n3(), o.n3())
        if ((len(in_first) > 0) or (len(in_second) > 0 and exact)):
            if len(in_first) > 0:
                msg = "%s expected triples were not found\n" % len(in_first) + msg
            if len(in_second) > 0:
                msg = "%s unexpected triples were found\n" % len(in_second) + msg
            msg = "%r != %r\n" % (want, got) + msg
            self.fail(msg)
        
    def assertAlmostEqualDatetime(self, datetime1, datetime2, delta=1):
        """Assert that two datetime objects are reasonably equal.

        :param datetime1: The first datetime to compare
        :type datetime1: datetime
        :param datetime2: The second datetime to compare
        :type datetime2: datetime
        :param delta: How much the datetimes are allowed to differ, in seconds.
        :type delta: int
        """
        # if the datetimes differ with max 1 second, they're almost
        # equal)
        time1 = time.mktime(datetime1.timetuple())
        time2 = time.mktime(datetime2.timetuple())
        absdiff = abs(time1 - time2)
        self.assertLessEqual(absdiff, delta, "Difference between %s and %s "
                             "is %s seconds which is NOT almost equal" %
                             (datetime1.isoformat(), datetime2.isoformat(),
                              absdiff))

    def assertEqualXML(self, want, got):
        """Assert that two xml trees are canonically identical.

        :param want: The XML document as expected
        :type  want: A string, byte string or ElementTree element
        :param got: The actual XML document
        :type  got: A string, byte string or ElementTree element
        """
        # Adapted from formencode, https://bitbucket.org/ianb/formencode/
        def xml_compare(want, got, reporter):
            if want.tag != got.tag:
                reporter("Tags do not match: 'want': %s, 'got': %s" % (want.tag, got.tag))
                return False
            for name, value in want.attrib.items():
                if got.attrib.get(name) != value:
                    reporter("Attributes do not match: 'want': %s=%r, 'got': %s=%r"
                             % (name, value, name, got.attrib.get(name)))
                    return False
            for name in got.attrib.keys():
                if name not in want.attrib:
                    reporter("'got' has an attribute 'want' is missing: %s"
                             % name)
                    return False
            if not text_compare(want.text, got.text):
                reporter("text: 'want': %r, 'got': %r" % (want.text, got.text))
                return False
            if not text_compare(want.tail, got.tail):
                reporter("tail: 'want': %r != 'got': %r" % (want.tail, got.tail))
                return False
            cl1 = want.getchildren()
            cl2 = got.getchildren()
            if len(cl1) != len(cl2):
                reporter("children length differs, 'want': %i, 'got': %i"
                         % (len(cl1), len(cl2)))
                return False
            i = 0
            for c1, c2 in zip(cl1, cl2):
                i += 1
                if not xml_compare(c1, c2, reporter=reporter):
                    reporter('children %i do not match: %s'
                             % (i, c1.tag))
                    return False
            return True

        def text_compare(want, got):
            if not want and not got:
                return True
            return (want or '').strip() == (got or '').strip()

        def treeify(something):
            if isinstance(something, str):
                
                fp = BytesIO(something.encode('utf-8'))
                # return etree.fromstring(something)
                return etree.parse(fp)
            elif isinstance(something, bytes):
                fp = BytesIO(something)
                # return etree.parse(fp).getroot()
                return etree.parse(fp)
            elif isinstance(want, etree._Element):
                # FIXME: wrap in ElementTree
                return something
            else:
                raise ValueError("Can't convert a %s into an ElementTree" % type(something))

        def c14nize(tree):
            tmp = BytesIO()
            tree.write_c14n(tmp)
            return tmp.getvalue().decode('utf-8')
            
        errors = []
        want_tree = treeify(want)
        got_tree = treeify(got)
        xml_compare(want_tree.getroot(),
                    got_tree.getroot(),
                    errors.append)
        
        if errors:
            want_lines = [x + "\n" for x in c14nize(want_tree).split("\n")]
            got_lines = [x + "\n" for x in c14nize(got_tree).split("\n")]
            diff = unified_diff(want_lines, got_lines, "want.xml", "got.xml")
            msg = "".join(diff) + "\n\nERRORS:" + "\n".join(errors)
            raise AssertionError(msg)
        
    def assertEqualDirs(self, want, got, suffix=None, filterdir="entries"):
        """Assert that two directory trees contains identical files

        :param want: The expected directory tree
        :type  want: str
        :param got: The actual directory tree
        :type  got: str
        :param suffix: If given, only check files ending in suffix (otherwise check all the files
        :type  suffix: str
        :param filterdir: If given, don't compare the parts of the tree that starts with filterdir
        :type  suffix: str
        """
        wantfiles = [x[len(want) + 1:] for x in util.list_dirs(want, suffix) if not x.startswith(want+os.sep+filterdir)]
        gotfiles = [x[len(got) + 1:] for x in util.list_dirs(got, suffix) if not x.startswith(got+os.sep+filterdir)]
        self.maxDiff = None
        self.assertEqual(wantfiles, gotfiles)  # or assertIn?
        for f in gotfiles:
            self.assertTrue(filecmp.cmp(os.path.join(want, f),
                                        os.path.join(got, f),
                                        shallow=False))
    

class RepoTester(unittest.TestCase, FerendaTestCase):
    
    """A unittest.TestCase-based convenience class for creating file-based
       integration tests for an entire docrepo. To use this, you only
       need a very small amount of boilerplate code, and some files
       containing data to be downloaded or parsed. The actual tests
       are dynamically created from these files. The boilerplate can
       look something like this::

           class TestRFC(RepoTester):
               repoclass = RFC  # the docrepo class to test
               docroot = os.path.dirname(__file__)+"/files/repo/rfc"
           
           parametrize_repotester(TestRFC)

    """

    # A subclass must override these two
    repoclass = DocumentRepository
    """The actual documentrepository class to be tested. Must be
       overridden when creating a testcase class."""
    
    docroot = '/tmp'
    """The location of test files to create tests from. Must be overridden
       when creating a testcase class"""

    def setUp(self):
        self.datadir = tempfile.mkdtemp()
        self.repo = self.repoclass(datadir=self.datadir,
                                   storelocation=self.datadir+"/ferenda.sqlite",
                                   indexlocation=self.datadir+"/whoosh",)
            
    def tearDown(self):
        # print("Not removing %s" % self.datadir)
        shutil.rmtree(self.datadir)

    def filename_to_basefile(self, filename):
        """Converts a test filename to a basefile. Default implementation simply returns a hard-coded basefile.
        
        :param filename: The test file
        :type filename: str
        :returns: Corresponding basefile
        :rtype: str

        """
        return "1"
        
    def download_test(self, specfile):
        from pudb import set_trace; set_trace()
        def my_get(url, **kwargs):
            
            urlspec = spec[url]
            if isinstance(urlspec, str):
                urlspec = {'file': urlspec}
            if 'charset' not in urlspec:
                urlspec['charset'] = 'utf-8'
            url_location = os.path.join(os.path.dirname(specfile),
                                        urlspec['file'])
            res = Mock()
            # load up both .text and .content properties
            with codecs.open(url_location, "r", encoding=urlspec['charset']) as fp:
                res.text = fp.read()
            with open(url_location, "rb") as fp:
                res.content = fp.read()
            res.headers = collections.defaultdict(lambda: None)
            res.headers['X-These-Headers-Are'] = 'Faked'
            res.status_code = 200
            return res
        with open(specfile) as fp:
            spec = json.load(fp)
        with patch('requests.get', side_effect=my_get):
            self.repo.download()

        # organize a temporary copy of files that we can compare our results to
        wantdir = "%s/%s-want" % (self.datadir, self.repoclass.alias)
        for url in spec:
            if "expect" in spec[url]:
                sourcefile = os.path.join(os.path.dirname(specfile),
                                          spec[url]['file'])
                wantfile = "%s/%s" % (wantdir, spec[url]['expect'])
                util.copy_if_different(sourcefile,wantfile)

        self.assertEqualDirs(wantdir,
                             "%s/%s" % (self.datadir,
                                        self.repoclass.alias))

    def distill_test(self, downloaded_file, rdf_file, docroot):
        try:
            prefixlen = len(docroot+"/downloaded/")
            suffixlen = len(self.repo.store.downloaded_suffix)
            pathfrag  = downloaded_file[prefixlen:-suffixlen]
            basefile = self.repo.store.pathfrag_to_basefile(pathfrag)
        except:
            basefile = self.filename_to_basefile(downloaded_file)
        with patch('ferenda.DocumentStore.downloaded_path',
                   return_value=downloaded_file):
            # self.repo.config.fsmdebug = True
            self.repo.parse(basefile)
        if 'FERENDA_SET_TESTFILES' in os.environ:
            print("Overwriting %s with result of parse(%s)" % (rdf_file, basefile))
            g = rdflib.Graph()
            g.parse(self.repo.store.distilled_path(basefile))
            util.robust_rename(rdf_file, rdf_file+"~")
            g.serialize(rdf_file, format="turtle")
            return 
        self.assertEqualGraphs(rdf_file,
                               self.repo.store.distilled_path(basefile),
                               exact=False)
                     
        
    def parse_test(self, downloaded_file, xhtml_file, docroot):
        # patch method so we control where the downloaded doc is
        # loaded from.
        basefile = self.filename_to_basefile(downloaded_file)
        with patch('ferenda.DocumentStore.downloaded_path',
                   return_value=downloaded_file):
            self.repo.parse(basefile)
        if 'FERENDA_SET_TESTFILES' in os.environ:
            print("Overwriting %s with result of parse(%s)" % (xhtml_file, basefile))
            util.robust_rename(xhtml_file, xhtml_file+"~")
            shutil.copy2(self.repo.store.parsed_path(basefile), xhtml_file)
            return 
        self.assertEqualXML(util.readfile(xhtml_file),
                            util.readfile(self.repo.store.parsed_path(basefile)))

    # for win32 compatibility and simple test case code
    def p(self, path, prepend_datadir=True):
        if prepend_datadir:
            path = self.datadir + "/" + path
        return path.replace('/', '\\') if os.sep == '\\' else path


def parametrize(cls, template_method, name, params, wrapper=None):
    """Creates a new test method on a TestCase class, which calls a
    specific template method with the given parameters (ie. a
    parametrized test). Given a testcase like this::

        class MyTest(unittest.TestCase):
            def my_general_test(self, parameter):
                self.assertEqual(parameter, "hello")

    and the following top-level initalization code::

        parametrize(MyTest,MyTest.my_general_test, "test_one", ["hello"])
        parametrize(MyTest,MyTest.my_general_test, "test_two", ["world"])

    you end up with a test case class with two methods. Using
    e.g. ``unittest discover`` (or any other unittest-compatible test
    runner), the following should be the result::

        test_one (test_parametric.MyTest) ... ok
        test_two (test_parametric.MyTest) ... FAIL
        
        ======================================================================
        FAIL: test_two (test_parametric.MyTest)
        ----------------------------------------------------------------------
        Traceback (most recent call last):
          File "./ferenda/testutil.py", line 365, in test_method
            template_method(self, *params)
          File "./test_parametric.py", line 6, in my_general_test
            self.assertEqual(parameter, "hello")
        AssertionError: 'world' != 'hello'
        - world
        + hello
        
    :param cls: TestCase to add the parametrized test to.
    :type  cls: class
    :param template_method: The method to use for parametrization
    :type  template_method: callable (bound method)
    :param name: The name for the new test method
    :type  name: str
    :param params: The parameter list (Note: keyword parameters are not supported)
    :type  params: iterable
    :param wrapper: A unittest decorator like :py:meth:`unittest.skip` or :py:meth:`unittest.expectedFailure`.
    :param wrapper: callable (decorator)

    """
    # internal entrypoint for tesst
    def test_method(self):
        template_method(self, *params)

    # py2 compat: name is a unicode object, func.__name__ must be a str(?)
    if six.PY3:
        test_method.__name__ = name
    else:
        # note that we have redefined str to six.text_type
        test_method.__name__ = bytes(name)
    # wrapper is a unittest decorator like skip or expectedFailure
    if wrapper:
        setattr(cls, name, wrapper(test_method))
    else:
        setattr(cls, name, test_method)


def file_parametrize(cls, directory, suffix, filter=None, wrapper=None):

    """Creates a test for each file in a given directory. Call with
     any class that subclasses unittest.TestCase and which has a
     method called `` parametric_test``, eg::
    
        class MyTest(unittest.TestCase):
            def parametric_test(self,filename):
                self.assertTrue(os.path.exists(filename))
        
        from ferenda.testutil import file_parametrize
        
        file_parametrize(Parse,"test/files/legaluri",".txt")

    For each .txt file in the directory ``test/files/legaluri``, a
    corresponding test is created, which calls ``parametric_test``
    with the full path to the .txt file as parameter.

    :param cls: TestCase to add the parametrized test to.
    :type  cls: class
    :param directory: The path to the files to turn into tests
    :type  directory: str
    :param suffix: Suffix of the files that should be turned into tests (other files in the directory are ignored)
    :type  directory: str
    :param filter: Will be called with the name of each matching file. If the ``filter`` callable returns True, no test is created
    :type  params: callable
    :param wrapper: A unittest decorator like :py:meth:`unittest.skip` or :py:meth:`unittest.expectedFailure`.
    :param wrapper: callable (decorator)

    """    
    params = []
    for filename in os.listdir(directory):
        if filename.endswith(suffix):
            if filter and filter(filename):
                continue
            testname = filename[:-len(suffix)]
            testname = "test_" + testname.replace("-", "_")
            params.append((testname, directory + os.path.sep + filename))

    for (name, param) in params:
        parametrize(cls, cls.parametric_test, name, (param,), wrapper)


def parametrize_repotester(cls):
    """Helper function to activate a
:py:class:`ferenda.testutil.RepoTester` based class (see the
documentation for that class)."""
    
    docroot = cls.docroot
    # 1. download tests
    for filename in os.listdir(docroot + "/source"):
        if filename.endswith(".json"):
            testname = "test_download_" + filename[:-5].replace("-", "_")
            fullname = docroot + "/source/" + filename
            parametrize(cls, cls.download_test, testname, (fullname,))
    # 2. parse tests
    suf = cls.repoclass.downloaded_suffix
    # for filename in os.listdir(docroot + "/downloaded"):
    basedir = docroot + "/downloaded"
    for filename in util.list_dirs(basedir, suffix=suf):
        filename = filename[len(basedir)+1:]
        if filename.endswith(suf):
            downloaded_file = "%s/downloaded/%s" % (docroot, filename)
            basefile = os.path.splitext(filename)[0] # shld we use store.pathfrag_to_basefile?
            basefile = basefile.replace("\\", "/")
            basetest = basefile.replace("-","_").replace("/", "_")
            # Test 1: is rdf distilled correctly?
            rdf_file = "%s/distilled/%s.ttl" % (docroot, basefile)
            testname = ("test_distill_" + basetest)

            wrapper = unittest.expectedFailure if not os.path.exists(rdf_file) else None 
            parametrize(cls, cls.distill_test, testname, (downloaded_file, rdf_file, docroot), wrapper)

            # Test 2: is xhtml parsed correctly?
            xhtml_file = "%s/parsed/%s.xhtml" % (docroot, basefile)
            testname = ("test_parse_" + basetest)

            wrapper = unittest.expectedFailure if not os.path.exists(xhtml_file) else None 
            parametrize(cls, cls.parse_test, testname, (downloaded_file, xhtml_file, docroot), wrapper)


def testparser(testcase, parser, filename):
    """Helper function to test :py:class:`~ferenda.FSMParser` based parsers."""
    wantfilename = filename.replace(".txt", ".xml")
    if not os.path.exists(wantfilename) or 'FERENDA_FSMDEBUG' in os.environ:
        parser.debug = True

    tr = TextReader(filename, encoding="utf-8", linesep=TextReader.UNIX)
    b = parser.parse(tr.getiterator(tr.readparagraph))

    if 'FERENDA_FSMDEBUG' in os.environ:
        print(elements.serialize(b))
    testcase.maxDiff = 4096
    if os.path.exists(wantfilename):
        with codecs.open(wantfilename, encoding="utf-8") as fp:
            want = fp.read().strip()
        got = elements.serialize(b).strip()
        testcase.assertEqualXML(want,got)
    else:
        raise AssertionError("Want file not found. Result of parse:\n" +
                             elements.serialize(b))
