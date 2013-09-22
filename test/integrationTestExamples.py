import sys
import os
import subprocess

from ferenda import util
from ferenda.compat import unittest
from ferenda.testutil import FerendaTestCase

class TestExamples(unittest.TestCase, FerendaTestCase):
    def _test_pyfile(self, pyfile, want=True, comparator=None):
        pycode = compile(util.readfile(pyfile), pyfile, 'exec')
        result = exec(pycode, globals(), locals())
        # the exec:ed code is expected to set return_value
        got = locals()['return_value']
        if not comparator:
            self.assertEqual(want, got)
        else:
            comparator(want, got)

    def _test_shfile(self, shfile):
        # these are not normal shell scripts, but rather docutils-like
        # interminglings of commands (prefixed by "$ ") and output.
        env = dict(os.environ) # create a copy which we'll modify (maybe?)
        expected = ""
        out = b""
        from pudb import set_trace; set_trace()
        for line in open(shfile):
            if line.startswith("#") or line.strip() == '':
                continue
            elif line.startswith("$ "):
                # check that output from previous command was what was expected
                self.assertEqual(expected, out.decode("utf-8"))
                out = b""
                expected = ""
                cmdline = line[2:]
                # special hack to account for that ferenda-setup not being
                # available for a non-installed ferenda source checkout
                if cmdline.startswith("ferenda-setup"):
                    cmdline = cmdline.replace("ferenda-setup",
                                              "python ferenda-setup.py")
                process = subprocess.Popen(cmdline,
                                           shell=True,
                                           stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT,
                                           env=env)
                out, err = process.communicate()
                retcode = process.poll()
            else:
                expected += line
        # check that final output was what was expected
        self.assertEqual(expected, out)

    def test_elementclasses(self):
        # setup w3standards.py -- modify sys.path?
        self._test_pyfile("doc/examples/elementclasses.py",
                          util.readfile("doc/examples/elementclasses-part.xhtml", "rb"),
                          self.assertEqualXML)
    
    def test_firststeps_api(self):
        self._test_pyfile("doc/examples/firststeps-api.py")
        
    def test_firststeps(self):
        # setup w3cstandards.py
        self._test_shfile("doc/examples/firststeps.sh")

    def test_fsmparser_example(self):
        self._test_pyfile("doc/examples/fsmparser-example.py",
                          util.readfile("doc/examples/fsparser-result.xml"))

    def test_intro_example_py(self):
        self._test_pyfile("doc/examples/intro-example.py")

    def test_intro_example_sg(self):
        self._test_shfile("doc/examples/intro-example.sh")

    def test_keyconcepts_attachments(self):
        self._test_pyfile("doc/examples/keyconcepts-attachments.py")

    def test_keyconcepts_file(self):
        self._test_pyfile("doc/examples/keyconcepts-file.py")

    def test_metadata(self):
        self._test_pyfile("doc/examples/metadata.py",
                          util.readfile("doc/examples/metadata-result.xml"))
    def test_rfc(self):
        # perhaps setup rfc-annotations.rq and rfc.xsl?
        self._test_pyfile("doc/examples/metadata.py",
                          util.readfile("doc/examples/metadata-result.xml"))

    # w3cstandards is tested by firststeps.py/.sh

    def test_citationparsing_urls(self):
        self._test_pyfile("doc/examples/citationparsing-parsers.py")
        
    def test_citationparsing_parsers(self):
        # FIXME: read before.xhtml, compare result to after.xhtml
        self._test_pyfile("doc/examples/citationparsing-parsers.py")
        
    def test_citationparsing_custom(self):
        self._test_pyfile("doc/examples/citationparsing-custom.py")

        

        
