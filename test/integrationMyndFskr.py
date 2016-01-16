# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *
import os
import sys
import shutil
import inspect

from ferenda import TextReader, util
from ferenda.testutil import RepoTester, file_parametrize
from ferenda.compat import unittest

# SUT
from ferenda.sources.legal.se import myndfskr


class Parse(RepoTester):
    repoclass = myndfskr.MyndFskrBase  # in some cases we might need to get a
    # specific one like SOSFS, see below

    aliases = {}  # setUpClass fills this in

    @classmethod
    def setUpClass(cls):
        super(Parse, cls).setUpClass()
        # enumerate all classes defined in the module where
        # MyndFskrBase is defined, check their static property 'alias'
        # and use it to add to cls.aliases
        for name, obj in inspect.getmembers(myndfskr):
            if inspect.isclass(obj) and hasattr(obj, 'alias'):
                cls.aliases[obj.alias] = obj

    def parametric_test(self, filename):
        # these options adjusts the constructed URIs. by default, the
        # official rpubl URIs are minted.
        # 
        # self.repo.config.localizeuri = True
        # self.repo.config.url = "http://example.org/"
        # self.repo.config.urlpath = ''
        # a few of the subclasses have specialized rules. make sure we
        # instantiate the correct class
        repo = os.path.basename(filename).split("-")[0]
        basefile = os.path.splitext(
            os.path.basename(filename))[0].replace("-",
                                                   "/", 1).replace("-", ":")
        repoclass = self.aliases[repo]
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
