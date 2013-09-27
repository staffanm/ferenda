# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys
import os
import subprocess
import tempfile
import shutil
import re

import six

from ferenda import util
from ferenda.compat import unittest, patch
from ferenda.testutil import FerendaTestCase

# imports needed by the scripts. I do not fully understand exactly how
# imports are scoped when using exec, but this is the only way apart
# from importing inside of the functions that use the code to work.
from ferenda import elements, DocumentRepository, DocumentStore
from bs4 import BeautifulSoup
import requests
from six.moves.urllib_parse import urljoin

class TestIntegration(unittest.TestCase, FerendaTestCase):

    verbose = False

    # FIXME: copied from testExamples.py -- unittest makes it a lot of
    # work to inherit from other testcases
    def _test_pyfile(self, pyfile, want=True, comparator=None):
        pycode = compile(util.readfile(pyfile), pyfile, 'exec')
        result = six.exec_(pycode, globals(), locals())
        # the exec:ed code is expected to set return_value
        got = locals()['return_value']
        if not comparator:
            comparator = self.assertEqual
        comparator(want, got)

        

    def _test_shfile(self, shfile, workingdir=None, extraenv={}):
        self.maxDiff = None
        # these are not normal shell scripts, but rather docutils-like
        # interminglings of commands (prefixed by "$ ") and output.
        def _mask_temporal(s):
            # mask things that may differ from run to run
            masks =  [re.compile(r"^()(\d{2}:\d{2}:\d{2})()", re.MULTILINE),
                      re.compile(r"(finished in )(\d.\d+)( sec)"),
                      re.compile(r"(\()(\d.\d+)( sec\))")]
            for mask in masks:
                s = mask.sub(r"\1[MASKED]\3", s)
            return s

        env = dict(os.environ) # create a copy which we'll modify (maybe?)
        env.update(extraenv)
        expected = ""
        out = b""
        cmd_lineno = 0
        ferenda_setup = "python %s/ferenda-setup.py" % os.getcwd()
        if workingdir:
            self.datadir = workingdir
        else:
            self.datadir = os.getcwd()
        cwd = self.datadir
        for lineno, line in enumerate(open(shfile)):
            if line.startswith("#") or line.strip() == '':
                continue
            elif line.startswith("$ "):
                line = line.strip()
                # check that output from previous command was what was expected
                self.assertEqual(_mask_temporal(expected),
                                 _mask_temporal(out.decode("utf-8")),
                                 "Not expected output from %s at line %s" % (shfile, cmd_lineno))
                print("ok")
                out = b""
                expected = ""
                cmd_lineno = lineno
                cmdline = line[2:]
                # special hack to account for that ferenda-setup not being
                # available for a non-installed ferenda source checkout
                if self.verbose:
                    print("Running '%s'" % cmdline,
                          end="...",
                          flush=True)
                if cmdline.startswith("ferenda-setup"):
                    cmdline = cmdline.replace("ferenda-setup",
                                              ferenda_setup)
                if cmdline.startswith("cd "):
                    # emulate this shell functionality in our control
                    # logic. note: no support for quoting and therefore
                    # no support for pathnames with space
                    path = cmdline.strip().split(" ", 1)[1]
                    cwd = os.path.normpath(os.path.join(cwd, path))
                else:
                    process = subprocess.Popen(cmdline,
                                               shell=True,
                                               cwd=cwd,
                                               stdout=subprocess.PIPE,
                                               stderr=subprocess.STDOUT,
                                               env=env)
                    out, err = process.communicate()
                    retcode = process.poll()
            else:
                expected += line
        # check that final output was what was expected
        self.assertEqual(_mask_temporal(expected),
                         _mask_temporal(out.decode("utf-8")),
                         "Not expected output from %s at line %s" % (shfile, cmd_lineno))
        if self.verbose:
            print("ok")

    def test_firststeps_api(self):
        self._test_pyfile("doc/examples/firststeps-api.py")
        
    def test_firststeps(self):
        self.verbose = True
        workingdir = tempfile.mkdtemp()
        shutil.copy2("doc/examples/w3cstandards.py", workingdir)
        self._test_shfile("doc/examples/firststeps.sh", workingdir,
                          {'FERENDA_MAXDOWNLOAD': '3',
                           'PYTHONPATH': os.getcwd()})


    def test_intro_example_py(self):
        self._test_pyfile("doc/examples/intro-example.py")

    def test_intro_example_sh(self):
        self._test_shfile("doc/examples/intro-example.sh")


    def test_rfc(self):
        # perhaps setup rfc-annotations.rq and rfc.xsl?
        self._test_pyfile("doc/examples/rfc.py")

    def test_composite(self):
        self._test_shfile("doc/examples/composite-repository.sh")

    # w3cstandards is tested by firststeps.py/.sh
