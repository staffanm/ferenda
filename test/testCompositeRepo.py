# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys, os
if os.getcwd() not in sys.path: sys.path.insert(0,os.getcwd())

from ferenda import DocumentRepository, LayeredConfig, util, errors
from ferenda.testutil import RepoTester
#SUT
from ferenda import CompositeRepository

class SubrepoA(DocumentRepository):
    storage_policy = "file"
    alias= "a"
    def download(self, basefile=None):
        util.writefile(self.store.downloaded_path("1"), "basefile 1, repo a")
        util.writefile(self.store.downloaded_path("3"), "basefile 3, repo a")

    def parse(self, basefile):
        if basefile in ("1", "3"):
            util.writefile(self.store.parsed_path(basefile),
                           "basefile %s, parsed by a" % basefile)
            util.writefile(self.store.distilled_path(basefile),
                           "basefile %s, metadata from a" % basefile)
            return True
        else:
            return False # we don't even have this basefile

    def custom(self):
        return False
        
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

    def get_default_options(self):
        opts = super(SubrepoB, self).get_default_options()
        opts['customproperty'] = "Hello world!"
        return opts

    def custom(self):
        return self.config.customproperty


class CompositeExample(CompositeRepository):
    subrepos = SubrepoB, SubrepoA
    storage_policy = "dir"

    def custom(self):
        for c in self.subrepos:
            inst = self.get_instance(c, self.myoptions)
            ret = inst.custom()
            if ret:
                return ret
        raise RuntimeError("No subrepo could perform custom method")
    

class TestComposite(RepoTester):
    repoclass = CompositeExample
 
    def test_download(self):
        self.repo.download()
        self.assertEqual("basefile 1, repo a",
                         util.readfile(self.datadir+"/a/downloaded/1.html"))
        self.assertEqual("basefile 1, repo b",
                         util.readfile(self.datadir+"/b/downloaded/1/index.html"))
        self.assertEqual("basefile 2, repo b",
                         util.readfile(self.datadir+"/b/downloaded/2/index.html"))
        self.assertEqual("basefile 3, repo a",
                         util.readfile(self.datadir+"/a/downloaded/3.html"))

    def test_list_basefiles_for(self):
        self.repo.download()
        # This doesn't work since self.repo.store.docrepos has
        # uninitialized classes, not objects
        self.assertEqual(set(["3", "2", "1"]),
                         set(self.repo.store.list_basefiles_for("parse")))

    def test_parse(self):
        # we already know list_basefiles_for("parse") will return
        # ["3", "2", "1"]
        self.assertTrue(self.repo.parse("1")) # both A and B can handle this
        # but B should win
        self.assertEqual("basefile 1, parsed by b",
                         util.readfile(self.repo.store.parsed_path("1")))
        self.assertEqual("basefile 1, metadata from b",
                         util.readfile(self.repo.store.distilled_path("1")))
        self.assertEqual(["attach.txt"],
                         list(self.repo.store.list_attachments("1", "parsed")))
        with self.assertRaises(errors.ParseError):
            self.assertFalse(self.repo.parse("2")) # none can handle this
        self.assertTrue(self.repo.parse("3")) # only A can handle this
        self.assertEqual("basefile 3, parsed by a",
                         util.readfile(self.repo.store.parsed_path("3")))
        self.assertEqual("basefile 3, metadata from a",
                         util.readfile(self.repo.store.distilled_path("3")))
        self.assertEqual([], # this repo supports attachment, but
                             # underlying repo A did not
                         list(self.repo.store.list_attachments("3", "parsed")))
                        
        # in this case, all files should be up-to-date, so no copying
        # should occur (triggering the "Attachments are (likely)
        # up-to-date branch")
        self.assertTrue(self.repo.parse("1")) 

        # and finally, list_basefiles_for("generate") should delegate
        # to DocumentStore.list_basefiles_for
        self.assertEqual(set(["1", "3"]),
                         set(self.repo.store.list_basefiles_for("generate")))

    def test_config(self):
        # test it with self.repo being initialized with some kwargs parameters
        self.repo.download()
        got = self.repo.custom()
        self.assertEqual("Hello world!", got)

        # now test with external config object -- this is where it'll fail
#        self.repo = CompositeExample()
#        self.repo.config = LayeredConfig({'datadir': self.datadir})
#        got = self.repo.custom()
#        self.assertEqual("Hello world!", got)
