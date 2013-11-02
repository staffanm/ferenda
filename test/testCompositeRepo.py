# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda import DocumentRepository, util, errors
from ferenda.testutil import RepoTester
#SUT
from ferenda import CompositeRepository

class SubrepoA(DocumentRepository):
    storage_policy = "dir"
    alias= "a"
    def download(self, basefile=None):
        util.writefile(self.store.downloaded_path("1"), "basefile 1, repo a")

    def parse(self, basefile):
        if basefile == "1":
            util.writefile(self.store.parsed_path("1"),
                           "basefile 1, parsed by a")
            util.writefile(self.store.parsed_path("1", attachment="extra.txt"),
                           "attachment for basefile 1, parsed by a")
            util.writefile(self.store.distilled_path("1"),
                           "basefile 1, metadata from a")
            return True
        else:
            return False # we don't even have this basefile
        
class SubrepoB(DocumentRepository):
    storage_policy = "dir"
    alias= "b"
    def download(self, basefile=None):
        util.writefile(self.store.downloaded_path("1"), "basefile 1, repo b")
        util.writefile(self.store.downloaded_path("2"), "basefile 2, repo b")

    def parse(self, basefile):
        if basefile == "1":
            util.writefile(self.store.parsed_path("1"),
                           "basefile 1, parsed by b")
            util.writefile(self.store.parsed_path("1", attachment="attach.txt"),
                           "attachment for basefile 1, parsed by b")
            util.writefile(self.store.distilled_path("1"),
                           "basefile 1, metadata from b")
            return True
        else:
            raise errors.ParseError("No can do!")


class CompositeExample(CompositeRepository):
    subrepos = SubrepoB, SubrepoA
    storage_policy = "dir"
    
class TestComposite(RepoTester):
    repoclass = CompositeExample
 
    def test_download(self):
        self.repo.download()
        self.assertEqual("basefile 1, repo a",
                         util.readfile(self.datadir+"/a/downloaded/1/index.html"))
        self.assertEqual("basefile 1, repo b",
                         util.readfile(self.datadir+"/b/downloaded/1/index.html"))
        self.assertEqual("basefile 2, repo b",
                         util.readfile(self.datadir+"/b/downloaded/2/index.html"))

    def test_list_basefiles_for(self):
        self.repo.download()
        # This doesn't work since self.repo.store.docrepos has
        # uninitialized classes, not objects
        self.assertEqual(set(["2", "1"]),
                         set(self.repo.store.list_basefiles_for("parse")))
        
    
    def test_parse(self):
        # we already know list_basefiles_for("parse") will return ["2", "1"]
        self.assertTrue(self.repo.parse("1")) # both A and B can handle this
        # but B should win
        self.assertEqual("basefile 1, parsed by b",
                         util.readfile(self.repo.store.parsed_path("1")))
        self.assertEqual("basefile 1, metadata from b",
                         util.readfile(self.repo.store.distilled_path("1")))
        self.assertTrue(["attach.txt"],
                        self.repo.store.list_attachments("1", "parsed"))
        self.assertFalse(self.repo.parse("2")) # none can handle this
                        
        # in this case, all files should be up-to-date, so no copying
        # should occur (triggering the "Attachments are (likely)
        # up-to-date branch")
        self.assertTrue(self.repo.parse("1")) 

        # and finally, list_basefiles_for("generate") should delegate
        # to DocumentStore.list_basefiles_for
        self.assertEqual(set(["1"]),
                         set(self.repo.store.list_basefiles_for("generate")))
        
        
