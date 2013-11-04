# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import tempfile
import shutil
import os
import sys

from ferenda import errors
from ferenda.compat import unittest, patch

# SUT
from ferenda import util

class Main(unittest.TestCase):

    def p(self, path, prepend_datadir=True):
        if prepend_datadir:
            path = self.datadir + "/" + path
        return path.replace('/', '\\') if os.sep == '\\' else path

    def setUp(self):
        self.datadir = tempfile.mkdtemp()
        self.dname = self.datadir + os.sep + "foo"
        self.fname = self.datadir + os.sep + "foo/bar.txt"
        self.fname2 = self.datadir + os.sep + "foo/baz.txt"

    def tearDown(self):
        shutil.rmtree(self.datadir)

    def test_ensure_dir(self):
        self.assertFalse(os.path.exists(self.dname))
        util.ensure_dir(self.fname)
        self.assertTrue(os.path.exists(self.dname))
        self.assertTrue(os.path.isdir(self.dname))
        util.ensure_dir(self.fname)
        os.rmdir(self.dname)
        with patch('ferenda.util.mkdir', side_effect=OSError):
            util.ensure_dir(self.fname)

    def test_robust_rename(self):
        # only test the IOError branch
        util.writefile(self.fname, "Hello")
        util.writefile(self.fname2, "Hello")
        with patch('ferenda.util.shutil.move', side_effect=IOError):
            util.robust_rename(self.fname, self.fname2)

    def test_robust_remove(self):
        util.writefile(self.fname, "Hello")
        util.robust_remove(self.fname)
        util.robust_remove(self.fname)
        
    def test_runcmd(self):
        filename = self.dname+os.sep+"räksmörgås.txt"
        util.writefile(filename, "räksmörgås")
        if sys.platform == "win32":
            cmd = "type"
        else:
            cmd = "cat"
        cmdline = "%s %s" % (cmd, filename)
        (retcode, stdout, stderr) = util.runcmd(cmdline)
        self.assertEqual(0, retcode)
        self.assertEqual("räksmörgås", stdout)
        self.assertEqual("", stderr)
        
        cmdline = "non-existing-binary foo"
        (retcode, stdout, stderr) = util.runcmd(cmdline)
        self.assertNotEqual(0, retcode)
        self.assertNotEqual("", stderr)

        with self.assertRaises(errors.ExternalCommandError):
            (retcode, stdout, stderr) = util.runcmd(cmdline,
                                                    require_success=True)

    def test_listdirs(self):
        util.writefile(self.p("foo.txt"), "Hello")
        util.writefile(self.p("bar.txt"), "Hello")
        util.writefile(self.p("foo/2.txt"), "Hello")
        util.writefile(self.p("foo/10.txt"), "Hello")
        util.writefile(self.datadir+"/foo/baz.text", "Hello")
        generator = util.list_dirs(self.datadir, ".txt")
        self.assertEqual(self.p("bar.txt"), next(generator))
        self.assertEqual([self.p("foo.txt"),
                          self.p("foo/2.txt"),
                          self.p("foo/10.txt")], list(generator))

    def test_replace_if_different(self):
        # test 1: dst does not exist
        util.writefile(self.fname, "Hello")
        self.assertTrue(util.replace_if_different(self.fname, self.fname2))
        self.assertFalse(os.path.exists(self.fname))
        self.assertTrue(os.path.exists(self.fname2))

        # test 2: dst exists, but is different (gets overwritten)
        util.writefile(self.fname, "Hello (different)")
        self.assertTrue(util.replace_if_different(self.fname, self.fname2))
        self.assertFalse(os.path.exists(self.fname))
        self.assertEqual("Hello (different)",
                         util.readfile(self.fname2))

        # test 3: src and dst is identical (src gets removed)
        util.writefile(self.fname, "Hello (different)")
        self.assertFalse(util.replace_if_different(self.fname, self.fname2))
        self.assertFalse(os.path.exists(self.fname))

        # test 4: dst exist, is different, gets archived
        newfile = self.dname+"/new.txt"
        archivefile = self.dname+"/archive.txt"
        util.writefile(newfile, "Hello (archiving)")
        self.assertTrue(util.replace_if_different(newfile, self.fname2, archivefile))
        self.assertFalse(os.path.exists(newfile))
        self.assertEqual("Hello (archiving)",
                         util.readfile(self.fname2))
        self.assertEqual("Hello (different)",
                         util.readfile(archivefile))

    def test_copy_if_different(self):
        # test 1: dst does not exist
        util.writefile(self.fname, "Hello")
        self.assertTrue(util.copy_if_different(self.fname, self.fname2))
        self.assertTrue(os.path.exists(self.fname))
        self.assertTrue(os.path.exists(self.fname2))

        # test 2: dst does exist, is different
        util.writefile(self.fname, "Hello (different)")
        self.assertTrue(util.copy_if_different(self.fname, self.fname2))
        self.assertTrue(os.path.exists(self.fname))
        self.assertTrue(os.path.exists(self.fname2))
        self.assertEqual("Hello (different)",
                         util.readfile(self.fname2))

        # test 3: dst does exist, is identical
        self.assertFalse(util.copy_if_different(self.fname, self.fname2))


from ferenda import util
import doctest
def load_tests(loader,tests,ignore):
    tests.addTests(doctest.DocTestSuite(util))
    return tests

