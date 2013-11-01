# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys
import os
import subprocess
import tempfile
import shutil

import six

from ferenda import util
from ferenda.compat import unittest, patch
from ferenda.testutil import FerendaTestCase
# This testcase tests those examples in the documentation that are
# more unit-like and can run without downloading stuff from the
# net. More integration-like tests are in integrationTestExamples (and
# thus aren't run in the normal test suite).

# imports needed by the scripts. I do not fully understand exactly how
# imports are scoped when using exec, but this is the only way apart
# from importing inside of the functions that use the code to work.
from ferenda import elements, DocumentRepository, DocumentStore, TocCriteria
from ferenda.decorators import managedparsing
import ferenda.citationpatterns
import ferenda.uriformats
from bs4 import BeautifulSoup
import requests
from six.moves.urllib_parse import urljoin
XMLPatents = HTMLPatents = ScannedPatents = None

class TestExamples(unittest.TestCase, FerendaTestCase):
    def _test_pyfile(self, pyfile, want=True, comparator=None):
        with open(pyfile, 'rb') as fp:
            pycode = compile(fp.read(), pyfile, 'exec')
        result = six.exec_(pycode, globals(), locals())
        # the exec:ed code is expected to set return_value
        got = locals()['return_value']
        if not comparator:
            comparator = self.assertEqual
        comparator(want, got)

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.orig_cwd = os.getcwd()
        os.chdir(self.tempdir)
        
    def tearDown(self):
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.tempdir)

    def test_elementclasses(self):
        # setup w3standards.py -- modify sys.path?
        self._test_pyfile(self.orig_cwd + "/doc/examples/elementclasses.py",
                          util.readfile(self.orig_cwd + "/doc/examples/elementclasses-part.xhtml", "rb"),
                          self.assertEqualXML)

    def test_fsmparser_example(self):
        self._test_pyfile(self.orig_cwd + "/doc/examples/fsmparser-example.py",
                          util.readfile(self.orig_cwd + "/doc/examples/fsmparser-result.xml"),
                          self.assertEqualXML)

    def test_keyconcepts_attachments(self):
        with patch('requests.get'):
            self._test_pyfile(self.orig_cwd + "/doc/examples/keyconcepts-attachments.py")

    def test_keyconcepts_file(self):
        self._test_pyfile(self.orig_cwd + "/doc/examples/keyconcepts-file.py")

    def test_metadata(self):
        self._test_pyfile(self.orig_cwd + "/doc/examples/metadata.py",
                          util.readfile(self.orig_cwd + "/doc/examples/metadata-result.xml"),
                          self.assertEqualXML)

    def test_citationparsing_urls(self):
        self._test_pyfile(self.orig_cwd + "/doc/examples/citationparsing-urls.py")
        
    def test_citationparsing_parsers(self):
        self._test_pyfile(self.orig_cwd + "/doc/examples/citationparsing-parsers.py",
                          util.readfile(self.orig_cwd + "/doc/examples/citationparsing-after.xhtml"),
                          self.assertEqualXML)
        
    def test_citationparsing_custom(self):
        self._test_pyfile(self.orig_cwd + "/doc/examples/citationparsing-custom.py")

    def test_composite(self):
        self._test_pyfile(self.orig_cwd + "/doc/examples/patents.py")

    def test_toc(self):
        self._test_pyfile(self.orig_cwd + "/doc/examples/toc.py")
