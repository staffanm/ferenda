# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os,sys
from ferenda.compat import unittest


import shutil
from ferenda import TextReader, util
from ferenda.testutil import RepoTester, file_parametrize

# SUT
from ferenda.sources.legal.se import myndfskr

class Parse(RepoTester):
    repoclass = myndfskr.MyndFskr  # in some cases we might need to get a
    # specific one like SOSFS, see below

    def parametric_test(self,filename):
        # these options adjusts the constructed URIs. by default, the
        # official rpubl URIs are minted.
        # 
        # self.repo.config.localizeuri = True
        # self.repo.config.url = "http://example.org/"
        # self.repo.config.urlpath = ''


        # a few of the subclasses have specialized rules. make sure we
        # instantiate the correct class
        repo = os.path.basename(filename).split("-")[0]
        basefile = os.path.splitext(os.path.basename(filename))[0].replace("-", "/", 1).replace("-", ":")
        repoclass = {'afs': myndfskr.AFS,
                     'sosfs': myndfskr.SOSFS,
                     'dvfs': myndfskr.DVFS}.get(repo, myndfskr.MyndFskr)
        if repoclass != self.repoclass:
            self.repo = repoclass(datadir=self.datadir,
                                  storelocation=self.datadir + "/ferenda.sqlite",
                                  indexlocation=self.datadir + "/whoosh",)
        doc = self.repo.make_document(basefile)
        text = self.repo.sanitize_text(util.readfile(filename), basefile)
        reader = TextReader(string=text, encoding='utf-8')
        self.repo.parse_metadata_from_textreader(reader, doc)
        wantfile = filename.replace(".txt", ".n3")
        if os.path.exists(wantfile):
            self.assertEqualGraphs(wantfile, doc.meta, exact=False)
        else:
            self.fail("Expected a %s with the following content:\n\n%s" %
                      (wantfile, doc.meta.serialize(format="n3").decode("utf-8")))

file_parametrize(Parse, "test/files/myndfskr", ".txt")
