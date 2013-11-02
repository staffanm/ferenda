# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os, datetime
from ferenda.compat import unittest, Mock, MagicMock, patch
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda import DocumentRepository, Document
from ferenda.errors import DocumentRemovedError, ParseError
# SUT
from ferenda.decorators import timed, parseifneeded, render, handleerror, makedocument, recordlastdownload, downloadmax

class Decorators(unittest.TestCase):

    def test_timed(self):
        # Test that a wrapped method...
        @timed
        def testfunc(repo,doc):
            pass

        # ...passed a particular docrepo and doc
        mockrepo = Mock()
        mockdoc = Mock()
        mockdoc.basefile = "1234"
        
        # ...has it's instances logger called...
        testfunc(mockrepo,mockdoc)
        call_args = mockrepo.log.info.call_args

        # ...with the correct method and arguments
        self.assertEqual(len(call_args[0]), 3)
        self.assertEqual(call_args[0][0], '%s: OK (%.3f sec)')
        self.assertEqual(call_args[0][1], "1234")

    def test_parseifneeded(self):
        @parseifneeded
        def testfunc(repo,doc):
            repo.called = True

        mockdoc = Mock()
        mockrepo = Mock()
        mockrepo.called = False
        mockrepo.config.force = False
        # test 1: Outfile is newer - the parseifneeded decorator
        # should make sure the actual testfunc code is never reached
        with patch('ferenda.util.outfile_is_newer', return_value=True):
            testfunc(mockrepo,mockdoc)

        self.assertFalse(mockrepo.called)
        mockrepo.called = False

        # test 2: Outfile is older
        with patch('ferenda.util.outfile_is_newer', return_value=False):
            testfunc(mockrepo,mockdoc)
        self.assertTrue(mockrepo.called)
        mockrepo.called = False

        # test 3: Outfile is newer, but the global force option was set
        mockrepo.config.force = True
        with patch('ferenda.util.outfile_is_newer', return_value=True):
            testfunc(mockrepo,mockdoc)
        self.assertTrue(mockrepo.called)
        mockrepo.config.force = None
        mockrepo.called = False

        # test 4: Outfile is newer, but the module parseforce option was set
        mockrepo.config.parseforce = True
        with patch('ferenda.util.outfile_is_newer', return_value=True):
            testfunc(mockrepo,mockdoc)
        self.assertTrue(mockrepo.called)
        mockrepo.called = False

    @patch('ferenda.documentrepository.Graph')
    def test_render(self,mock_graph):
        @render
        def testfunc(repo,doc):
            pass

        mockdoc = Mock()
        mockrepo = Mock()
        mockrepo.store.parsed_path.return_value = "parsed_path.xhtml"
        with open("parsed_path.xhtml", "w") as fp:
            fp.write("""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML+RDFa 1.0//EN" "http://www.w3.org/MarkUp/DTD/xhtml-rdfa-1.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:dct="http://purl.org/dc/terms/">
  <head about="http://example.org/doc">
     <title property="dct:title">Document title</title>
  </head>
  <body>
     <h1>Hello!</h1>
  </body>
</html>""")

        mockrepo.store.distilled_path.return_value = "distilled_path.xhtml"
        mockrepo.get_globals.return_value = {'symbol table':'fake'}
        mockdoc.meta = MagicMock() # need Magicmock which supports magic funcs like __iter__
        bodypart = MagicMock()
        bodypart.meta  = MagicMock()
        mockdoc.body = [bodypart]
        mockdoc.meta.__iter__.return_value = []
        mockdoc.uri = "http://example.org/doc"
        with patch('ferenda.util.ensure_dir', return_value=True):
            testfunc(mockrepo, mockdoc)
        
        # 1 ensure that DocumentRepository.render_xhtml is called with
        # four arguments
        mockrepo.render_xhtml.assert_called_with(mockdoc, "parsed_path.xhtml")

        # 2 ensure that DocumentRepository.create_external_resources
        # is called with 1 argument
        mockrepo.create_external_resources.assert_called_with(mockdoc)
        
        # 3 ensure that a Graph object is created, its parse and
        # serialize methods called

        # FIXME: Why doesn't the patching work?!
        # self.assertTrue(mock_graph().parse.called)
        # self.assertTrue(mock_graph().serialize.called)
        
        # (4. ensure that a warning gets printed if doc.meta and
        # distilled_graph do not agree)
        mock_graph().__iter__.return_value = ['a','b']
        mockdoc.meta.__iter__.return_value = ['a','b','c']
        mockdoc.meta.serialize.return_value = b"<c>"

        with patch('ferenda.util.ensure_dir', return_value=True):
            testfunc(mockrepo, mockdoc)
        self.assertTrue(mockrepo.log.warning.called)
        os.remove("parsed_path.xhtml")
        os.remove("distilled_path.xhtml")

    def test_handleerror(self):
        @handleerror
        def testfunc(repo,doc):
            if doc.exception:
                raise doc.exception
            else:
                return True

        mockrepo = Mock()
        mockdoc = Mock()
        # 1. should not raise an exception (but should call log.info
        #    and util.robust_remove, and return false)
        with patch('ferenda.util.robust_remove') as robust_remove:
            mockdoc.exception = DocumentRemovedError
            self.assertFalse(testfunc(mockrepo, mockdoc))
            self.assertTrue(mockrepo.log.info.called)
            self.assertTrue(robust_remove.called)

        # 2. should raise the same exception
        mockdoc.exception = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            testfunc(mockrepo, mockdoc)

        # 3.1 Should raise the same exeption
        mockdoc.exception = ParseError
        mockrepo.config.fatalexceptions = True
        with self.assertRaises(ParseError):
            testfunc(mockrepo, mockdoc)
        mockrepo.config.fatalexceptions = None

        # 3.2 Should not raise an exception (but should call log.error and return false)
        mockdoc.exception = ParseError
        self.assertFalse(testfunc(mockrepo, mockdoc))
        self.assertTrue(mockrepo.log.error.called)

        # 4.1 Should raise the same exception
        mockdoc.exception = Exception
        mockrepo.config.fatalexceptions = True
        with self.assertRaises(Exception):
            testfunc(mockrepo, mockdoc)
        mockrepo.config.fatalexceptions = None

        # 4.2 Should not raise an exception
        mockdoc.exception = Exception
        self.assertFalse(testfunc(mockrepo, mockdoc))
        self.assertTrue(mockrepo.log.error.called)

        # 5. No exceptions - everything should go fine
        mockdoc.exception = None
        self.assertTrue(testfunc(mockrepo, mockdoc))
        
    def test_makedocument(self):
        @makedocument
        def testfunc(repo,doc):
            return doc

        doc = testfunc(DocumentRepository(),"base/file")
        self.assertIsInstance(doc,Document)
        self.assertEqual(doc.basefile, "base/file")

    def test_recordlastdownload(self):
        @recordlastdownload
        def testfunc(repo):
            pass
        mockrepo = Mock()
        with patch('ferenda.decorators.LayeredConfig.write') as mockconf:
            testfunc(mockrepo)
            # check that config.lastdownload has been set to a datetime
            self.assertIsInstance(mockrepo.config.lastdownload,
                                  datetime.datetime)
            # and that LayeredConfig.write has been called
            self.assertTrue(mockconf.called)
        
    def test_downloadmax(self):
        @downloadmax
        def testfunc(repo, source):
            for x in range(100):
                yield x
        mockrepo = Mock()
        mockrepo.config.downloadmax = None
        self.assertEqual(100, len(list(testfunc(mockrepo, None))))
        
        os.environ["FERENDA_DOWNLOADMAX"] = "10"
        self.assertEqual(10, len(list(testfunc(mockrepo, None))))
        
        del os.environ["FERENDA_DOWNLOADMAX"]
        mockrepo.config.downloadmax = 20
        self.assertEqual(20, len(list(testfunc(mockrepo, None))))
        
            
            

