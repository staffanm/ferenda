# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import os

from ferenda.testutil import RepoTester
from ferenda import util

# SUT
from ferenda import Transformer

class Transform(RepoTester):

    def _setup_files(self, paramfile):
        base = self.datadir+os.sep
        util.ensure_dir(base+"teststyle.xslt")
        with open(base+"teststyle.xslt","w") as fp:
            fp.write("""<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:param name="value"/>
    <xsl:param name="file"/>
    <xsl:variable name="content" select="document($file)/root/*"/>
    <xsl:template match="/">
        <output>
            <paramvalue><xsl:value-of select="$value"/></paramvalue>
            <paramfile><xsl:copy-of select="$content"/></paramfile>
            <infile><xsl:value-of select="/doc/title"/></infile>
        </output>
    </xsl:template>
</xsl:stylesheet>
""")
        with open(base+paramfile,"w") as fp:
            fp.write("""<root><node key='value'><subnode>textnode</subnode></node></root>""")

        with open(base+"infile.xml","w") as fp:
            fp.write("""<doc><title>Document title</title></doc>""")
        return Transformer("XSLT", base+"teststyle.xslt", "xsl", None, "")
    
    def test_transform_html(self):
        base = self.datadir+os.sep
        t = self._setup_files(paramfile="paramfile.xml")
        t.transform_file(base+"infile.xml", base+"outfile.xml",
                         {'value':'blahonga',
                          'file':base+'paramfile.xml'})
        self.assertEqualXML("""
        <output>
            <paramvalue>blahonga</paramvalue>
            <paramfile><node key='value'><subnode>textnode</subnode></node></paramfile>
            <infile>Document title</infile>
        </output>""", util.readfile(base+"outfile.xml"))

    def test_transform_nonascii_fileparam(self):
        base = self.datadir+os.sep
        t = self._setup_files(paramfile="räksmörgås.xml")
        t.transform_file(base+"infile.xml", base+"outfile.xml",
                         {'value':'blahonga',
                          'file':base+'räksmörgås.xml'})
        self.assertEqualXML("""
        <output>
            <paramvalue>blahonga</paramvalue>
            <paramfile><node key='value'><subnode>textnode</subnode></node></paramfile>
            <infile>Document title</infile>
        </output>""", util.readfile(base+"outfile.xml"))


    # FIXME: We should isolate parts of the tests in
    # testDocRepo.Generate, testDocRepo.TOC and testWSGI.Search that
    # deals with transformation, and bring them here instead.

    def test_depth(self):
        xsltfile = self.datadir+os.sep+"notused.xslt"
        util.writefile(xsltfile, '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"/>')
        t = Transformer("XSLT", xsltfile, "xsl", None, "data")
        self.assertEqual(0, t._depth("data", "data/index.html"))
        self.assertEqual(1, t._depth("data/repo", "data/index.html"))
        self.assertEqual(3, t._depth("data/repo/toc/title", "data/index.html"))
