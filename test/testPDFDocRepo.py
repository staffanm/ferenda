# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import sys
import os
import shutil

from ferenda import util



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
        self.repo.parse("sample")
        p = self.repo.store.datadir
        self.assertTrue(os.path.exists(p+'/intermediate/sample/index001.png'))
        self.assertTrue(os.path.exists(p+'/intermediate/sample/index.pdf'))
        self.assertTrue(os.path.exists(p+'/intermediate/sample/index.xml'))
        self.assertTrue(os.path.exists(p+'/parsed/sample/index001.png'))
        self.assertTrue(os.path.exists(p+'/parsed/sample/index.css'))
        self.assertTrue(os.path.exists(p+'/parsed/sample/index.xhtml'))
    
