# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda.testutil import RepoTester, DocumentRepository, util
from ferenda.compat import unittest
#SUT
from ferenda import CompositeRepository

class SubrepoA(DocumentRepository):
    alias= "a"
    def download(self, basefile=None):
        util.writefile(self.store.downloaded_path("1"), "basefile 1, repo a")

class SubrepoB(DocumentRepository):
    alias= "b"
    def download(self, basefile=None):
        util.writefile(self.store.downloaded_path("1"), "basefile 1, repo b")
        util.writefile(self.store.downloaded_path("2"), "basefile 2, repo b")


class CompositeExample(CompositeRepository):
    subrepos = SubrepoB, SubrepoA
    
class TestComposite(RepoTester):
    repoclass = CompositeExample
 
    def test_download(self):
        self.repo.download()
        self.assertEqual("basefile 1, repo a",
                         util.readfile(self.datadir+"/a/downloaded/1.html"))
        self.assertEqual("basefile 1, repo b",
                         util.readfile(self.datadir+"/b/downloaded/1.html"))
        self.assertEqual("basefile 2, repo b",
                         util.readfile(self.datadir+"/b/downloaded/2.html"))

    @unittest.expectedFailure
    def test_list_basefiles_for(self):
        self.repo.download()
        # This doesn't work since self.repo.store.docrepos has
        # uninitialized classes, not objects
        self.assertEqual(["1", "2"],
                        list(self.repo.store.list_basefiles_for("parse")))
    
    
