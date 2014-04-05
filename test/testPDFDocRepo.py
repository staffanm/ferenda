# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import sys
import os
import shutil

from ferenda import util, errors

# SUT
from ferenda import PDFDocumentRepository
from ferenda.testutil import RepoTester

if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())
from ferenda.manager import setup_logger; setup_logger('CRITICAL')

class Repo(RepoTester):
    repoclass = PDFDocumentRepository
    def test_parse(self):
        util.ensure_dir(self.repo.store.downloaded_path("sample"))
        shutil.copy2("test/files/pdfreader/sample.pdf",
                     self.repo.store.downloaded_path("sample"))
        try:
            self.repo.required_predicates = []
            self.repo.parse("sample")
        except errors.ExternalCommandError:
            # print("pdftohtml error: retrying")
            # for systems that don't have pdftohtml, we copy the expected
            # intermediate files, so that we can test the rest of the logic
            targetdir = os.path.dirname(self.repo.store.intermediate_path("sample"))
            # print("working around by copying test/files/pdfreader/intermediate tree to %s" % targetdir)
            if os.path.exists(targetdir):
                shutil.rmtree(targetdir)
            shutil.copytree("test/files/pdfreader/intermediate",
                            targetdir)
            # make really sure the xml file has a newer timestamp than the PDF
            from time import sleep
            sleep(0.01)
            os.utime(targetdir+"/index.xml", None)
            try:
                self.repo.parse("sample")
            except errors.ExternalCommandError as e:
                print("ExternalCommandError on rerun.\n    targetdir: %s\n    %s exists: %s\n    message: %s" %
                      (targetdir, targetdir+"/index.xml", os.path.exists(targetdir+"/index.xml"), e))
            # print("Workaround succeeded: %s" % os.path.exists(targetdir+"/index.xml"))
        
        p = self.repo.store.datadir
        self.assertTrue(os.path.exists(p+'/intermediate/sample/index001.png'))
        self.assertFalse(os.path.exists(p+'/intermediate/sample/index.pdf'))
        self.assertTrue(os.path.exists(p+'/intermediate/sample/index.xml'))
        self.assertTrue(os.path.exists(p+'/parsed/sample/index001.png'))
        self.assertTrue(os.path.exists(p+'/parsed/sample/index.css'))
        self.assertTrue(os.path.exists(p+'/parsed/sample/index.xhtml'))
    
