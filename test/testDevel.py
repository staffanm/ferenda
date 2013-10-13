# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os, tempfile
from tempfile import mkstemp
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

import six
from ferenda.compat import unittest, patch, call,  Mock, MagicMock
builtins = "__builtin__" if six.PY2 else "builtins"

from ferenda import DocumentRepository, DocumentStore, LayeredConfig, util

# SUT
from ferenda import Devel

class Main(unittest.TestCase):
    def test_dumprdf(self):
        fileno, tmpfile = mkstemp()
        fp = os.fdopen(fileno, "w")
        fp.write("""<html xmlns="http://www.w3.org/1999/xhtml">
        <head about="http://example.org/doc">
           <title property="http://purl.org/dc/terms">Doc title</title>
        </head>
        <body>...</body>
        </html>""")
        fp.close()
        d = Devel()
        mock = MagicMock()
        with patch(builtins+'.print', mock):
            d.dumprdf(tmpfile, format="nt")
        self.assertTrue(mock.called)
        want = '<http://example.org/doc> <http://purl.org/dc/terms> "Doc title" .\n\n'
        mock.assert_has_calls([call(want)])
        
    def test_dumpstore(self):
        d = Devel()
        d.config = Mock()
        # only test that Triplestore is called correctly, mock any
        # calls to any real database
        config = {'connect.return_value':
                  Mock(**{'get_serialized.return_value':
                          b'[fake store content]'})}
        printmock = MagicMock()
        with patch('ferenda.devel.TripleStore', **config):
            with patch(builtins+'.print', printmock):
                d.dumpstore(format="trix")
        want = "[fake store content]"
        printmock.assert_has_calls([call(want)])
        
    def test_mkpatch(self):
        tempdir = tempfile.mkdtemp()
        basefile = "1"
        # Test 1: A repo which do not use any intermediate files. In
        # this case, the user edits the downloaded file, then runs
        # mkpatch, which saves the edited file, re-downloads the file,
        # and computes the diff.
        store = DocumentStore(tempdir + "/base")
        downloaded_path = store.downloaded_path(basefile)
        def my_download_single(self):
            # this function simulates downloading
            with open(downloaded_path, "w") as fp:
                fp.write("""This is a file.
It has been downloaded.
""")
        
        repo = DocumentRepository(datadir=tempdir)
        with repo.store.open_downloaded(basefile, "w") as fp:
            fp.write("""This is a file.
It has been patched.
""")

        d = Devel()
        globalconf = LayeredConfig({'datadir':tempdir,
                                    'patchdir':tempdir,
                                    'devel': {'class':'ferenda.Devel'},
                                    'base': {'class':
                                             'ferenda.DocumentRepository'}},
                                   cascade=True)
        
        d.config = globalconf.devel
        with patch('ferenda.DocumentRepository.download_single') as mock:
            mock.side_effect = my_download_single
            patchpath = d.mkpatch("base", basefile, "Example patch")
        
        patchcontent = util.readfile(patchpath)
        self.assertIn("Example patch", patchcontent)
        self.assertIn("@@ -1,2 +1,2 @@", patchcontent)
        self.assertIn("-It has been downloaded.", patchcontent)
        self.assertIn("+It has been patched.", patchcontent)

        # test 2: Same, but with a multi-line desc
        with repo.store.open_downloaded(basefile, "w") as fp:
            fp.write("""This is a file.
It has been patched.
""")
        longdesc = """A longer comment
spanning
several lines"""
        with patch('ferenda.DocumentRepository.download_single') as mock:
            mock.side_effect = my_download_single
            patchpath = d.mkpatch("base", basefile, longdesc)
        patchcontent = util.readfile(patchpath)
        desccontent = util.readfile(patchpath.replace(".patch", ".desc"))
        self.assertEqual(longdesc, desccontent)
        self.assertFalse("A longer comment" in patchcontent)
        self.assertIn("@@ -1,2 +1,2 @@", patchcontent)
        self.assertIn("-It has been downloaded.", patchcontent)
        self.assertIn("+It has been patched.", patchcontent)

        
        


    def test_parsestring(self):
        d = Devel()
        with self.assertRaises(NotImplementedError):
            d.parsestring(None,None,None)
