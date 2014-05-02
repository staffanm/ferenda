# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
import sys
if sys.version_info[:2] == (3,2): # remove when py32 support ends
    import uprefix
    uprefix.register_hook()
    from future.builtins import *
    uprefix.unregister_hook()
else:
    from future.builtins import *

import sys
import os
import subprocess
import tempfile
import shutil
import re

from ferenda import util
from ferenda.compat import unittest, patch
from ferenda.testutil import FerendaTestCase

# imports needed by the scripts. I do not fully understand exactly how
# imports are scoped when using exec, but this is the only way apart
# from importing inside of the functions that use the code to work.
from ferenda import elements, DocumentRepository, DocumentStore, TextReader
from ferenda.decorators import downloadmax

from bs4 import BeautifulSoup
from datetime import datetime, date
from itertools import islice
import requests

class Examples(unittest.TestCase, FerendaTestCase):

    verbose = False

    # FIXME: copied from testExamples.py -- unittest makes it a lot of
    # work to inherit from other testcases
    def _test_pyfile(self, pyfile, workingdir=None, want=True, comparator=None):
        if not workingdir:
            workingdir = os.getcwd()
        oldwd = os.getcwd()
        pycode = compile(util.readfile(pyfile), pyfile, 'exec')
        # result = six.exec_(pycode, globals(), locals())
        exec(pycode, globals(), locals())
        # the exec:ed code is expected to set return_value
        got = locals()['return_value']
        if not comparator:
            comparator = self.assertEqual
        comparator(want, got)

        
    def mask(self, s):
        """Given a log output string, mask things like timestamps, filenames
        and URLs that may change from run to run

        """
        masks = [
            re.compile(r"^(\d{2}:\d{2}:\d{2})", re.MULTILINE), # looks like a HH:MM:SS time
            re.compile(r"finished in (\d+\.\d+) sec"),
            re.compile(r"\((\d+.\d+) sec\)"),
            re.compile(r" INFO ([\w\-]+): downloaded from http"),
            re.compile(r": downloaded from (http://[\w\.\-/]+)"),
            re.compile(r" INFO ([\w\-]+): (parse|relate|generate) OK "),
            re.compile(r" DEBUG ([\w\-]+): Created "),
            re.compile(r" INFO Created data/w3c/toc/([\w/]+).html"),
            re.compile(r": Created ([\w\-\./]+).xhtml"),
            re.compile(r" DEBUG ([\w\-]+): (?:Starting|Skipped)"),
            re.compile(r" DEBUG ([\w\-]+: \d+) triples extracted to "),
            re.compile(r" triples extracted to ([\w\-\./]+).rdf"),
            re.compile(r"^([\w\-]+).html(?:|.etag)", re.MULTILINE),
            re.compile(r"(?:download|parse): ([\w\-, :\.\(\)]+)", re.MULTILINE),
            re.compile(r" INFO Dumped (\d+) triples from context "),
            
             ]
        for mask in masks:
            m = mask.search(s)
            while m:
                s = m.string[:m.start(1)] + "[MASKED]" + m.string[m.end(1):]
                m = mask.search(s)
        return s

    def test_internal_mask(self):
        for logstr, want in (
                ("20:16:42 w3c INFO Downloading max 3 documents",
                 "[MASKED] w3c INFO Downloading max 3 documents"),
                ("20:16:43 w3c INFO rdfa-core: downloaded from http://www.w3.org/TR/2013/REC-rdfa-core-20130822/\n20:16:44 w3c INFO xhtml-rdfa: downloaded from http://www.w3.org/TR/2013/REC-xhtml-rdfa-20130822/\n",
                 "[MASKED] w3c INFO [MASKED]: downloaded from [MASKED]\n[MASKED] w3c INFO [MASKED]: downloaded from [MASKED]\n"),
                ("20:16:44 root INFO w3c download finished in 14.666 sec",
                 "[MASKED] root INFO w3c download finished in [MASKED] sec"),
                ("14:45:57 w3c INFO rdfa-core: parse OK (2.051 sec)",
                 "[MASKED] w3c INFO [MASKED]: parse OK ([MASKED] sec)"),
                ("15:44:50 w3c DEBUG html-rdfa: Starting",
                 "[MASKED] w3c DEBUG [MASKED]: Starting"),
                ("15:44:48 w3c DEBUG xhtml-rdfa: Created data/w3c/parsed/xhtml-rfa.xhtml",
                 "[MASKED] w3c DEBUG [MASKED]: Created [MASKED].xhtml"),
                ("16:11:39 w3c INFO Created data/w3c/toc/title/h.html",
                 "[MASKED] w3c INFO Created data/w3c/toc/[MASKED].html"),
                ("html-rdfa.html\nhtml-rdfa.html.etag\n",
                 "[MASKED].html\n[MASKED].html.etag\n"),
                ("""Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: xhtml-rdfa, rdfa-core, html-rdfa.
 parse: None. Todo: xhtml-rdfa, rdfa-core, html-rdfa.
 generated: None.""",
                 """Status for document repository 'w3c' (w3cstandards.W3CStandards)
 download: [MASKED]
 parse: [MASKED]
 generated: None."""),
                ("12:16:13 w3c INFO Dumped 34 triples from context http://localhost:8000/dataset/w3c to data/w3c/distilled/dump.nt",
                 "[MASKED] w3c INFO Dumped [MASKED] triples from context http://localhost:8000/dataset/w3c to data/w3c/distilled/dump.nt"),

        ):
            self.assertEqual(want, self.mask(logstr))
                     
                     
    def _test_shfile(self, shfile, workingdir=None, extraenv={}, check_output=True):
        self.maxDiff = None
        # these are not normal shell scripts, but rather docutils-like
        # interminglings of commands (prefixed by "$ ") and output.
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
        with open(shfile+".log", "w") as fp:
            for lineno, line in enumerate(open(shfile)):
                if line.startswith("#") or line.strip() == '':
                    fp.write(line)
                    continue
                elif line.startswith("$ "):
                    fp.write(line)
                    line = line.strip()
                    # check that output from previous command was what was expected
                    if check_output:
                        self.assertEqual(self.mask(expected),
                                         self.mask(out.decode("utf-8")),
                                         "Not expected output from %s at line %s" % (shfile, cmd_lineno))
                    if self.verbose:
                        print("ok")
                    out = b""
                    expected = ""
                    cmd_lineno = lineno
                    cmdline = line[2:].split("#")[0].strip()
                    # special hack to account for that ferenda-setup not being
                    # available for a non-installed ferenda source checkout
                    if self.verbose:
                        print("Running '%s'" % cmdline,
                              end=" ... ")
                        sys.stdout.flush()
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
                        if out:
                            fp.write(out.decode('utf-8'))
                        else:
                            out = b''
                        if err:
                            fp.write(err.decode('utf-8'))
                        else:
                            err = b''
                        retcode = process.poll()
                        self.assertEqual(0, retcode, "STDOUT:\n%s\nSTDERR:\n%s" % (out.decode('utf-8'),
                                                                                   err.decode('utf-8')))
                else:
                    expected += line
            # check that final output was what was expected
            if check_output:
                self.assertEqual(self.mask(expected),
                                 self.mask(out.decode("utf-8")),
                                 "Not expected output from %s at line %s" % (shfile, cmd_lineno))
        if self.verbose:
            print("ok")

    def test_firststeps_api(self):
        from ferenda.manager import setup_logger; setup_logger('CRITICAL')
        # FIXME: consider mocking print() here
        workingdir = tempfile.mkdtemp()
        os.environ['FERENDA_HOME'] = os.getcwd()
        self._test_pyfile("doc/examples/firststeps-api.py", workingdir)
        shutil.rmtree(workingdir)
        
    def test_firststeps(self):
        # this test might fail whenever new W3C standards are added,
        # depending on where rdfa-core ends up alphabetically in
        # between the other three downloaded documents.
        self.verbose = True
        workingdir = tempfile.mkdtemp()
        shutil.copy2("doc/examples/w3cstandards.py", workingdir)
        self._test_shfile("doc/examples/firststeps.sh", workingdir,
                          {'FERENDA_DOWNLOADMAX': '3',
                           'PYTHONPATH': os.getcwd(),
                           'FERENDA_TRIPLESTORE_LOCATION': '',
                           'FERENDA_FULLTEXTINDEX_LOCATION': ''})
        shutil.rmtree(workingdir)

    # FIXME: Both intro-example.py and intro-example.sh ends with a
    # call to runserver, which never returns. We need to mock this
    # call somehow (should be simple for intro-example.py as
    # everything's running in the same process, more difficult for
    # intro-example.sh unless we specifically check for calls to
    # runserver and disable them)
    def test_intro_example_py(self):
        os.environ['FERENDA_DOWNLOADMAX'] = '3'
        workingdir = tempfile.mkdtemp()
        self._test_pyfile("doc/examples/intro-example.py", workingdir)
        shutil.rmtree(workingdir)

    def test_intro_example_sh(self):
        workingdir = tempfile.mkdtemp()
        self.verbose = True
        self._test_shfile("doc/examples/intro-example.sh", workingdir,
                          {'FERENDA_DOWNLOADMAX': '3',
                           'PYTHONPATH': os.getcwd()
                       },
                          check_output=False)
        shutil.rmtree(workingdir)

    def test_rfc(self):
        workingdir = tempfile.mkdtemp()
        try:
            shutil.copy("doc/examples/rfc-annotations.rq", workingdir+"/rfc-annotations.rq")
            shutil.copy("doc/examples/rfc.xsl", workingdir+"/rfc.xsl")
            self._test_pyfile("doc/examples/rfcs.py", workingdir)
        finally:
            shutil.rmtree(workingdir)

    def test_composite(self):
        workingdir = tempfile.mkdtemp()
        shutil.copy2("doc/examples/patents.py", workingdir)
        self._test_shfile("doc/examples/composite-repository.sh", workingdir,
                          {'FERENDA_DOWNLOADMAX': '3',
                           'PYTHONPATH': os.getcwd(),
                           'FERENDA_TRIPLESTORE_LOCATION': '',
                           'FERENDA_FULLTEXTINDEX_LOCATION': ''},
                          check_output=False)
        shutil.rmtree(workingdir)

    # w3cstandards is tested by firststeps.py/.sh
