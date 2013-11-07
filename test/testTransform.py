# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os

from ferenda.testutil import RepoTester
from ferenda import util

from ferenda import Transformer

class Transform(RepoTester):

    def test_transform_html(self):
        base = self.datadir+os.sep
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
        with open(base+"paramfile.xml","w") as fp:
            fp.write("""<root><node key='value'><subnode>textnode</subnode></node></root>""")

        with open(base+"infile.xml","w") as fp:
            fp.write("""<doc><title>Document title</title></doc>""")
        t = Transformer("XSLT", base+"teststyle.xslt", ["res/xsl"], "")
        t.transform_file(base+"infile.xml", base+"outfile.xml",
                         {'value':'blahonga',
                          'file':base+'paramfile.xml'})
        self.assertEqualXML(util.readfile(base+"outfile.xml"),"""
        <output>
            <paramvalue>blahonga</paramvalue>
            <paramfile><node key='value'><subnode>textnode</subnode></node></paramfile>
            <infile>Document title</infile>
        </output>""")

    # FIXME: We should isolate parts of the tests in
    # testDocRepo.Generate, testDocRepo.TOC and testWSGI.Search that
    # deals with transformation, and bring them here instead.

    def test_depth(self):
        xsltfile = self.datadir+os.sep+"notused.xslt"
        util.writefile(xsltfile, '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"/>')
        t = Transformer("XSLT", xsltfile, ["res/xsl"], "data")
        self.assertEqual(0, t._depth("data", "data/index.html"))
        self.assertEqual(1, t._depth("data/repo", "data/index.html"))
        self.assertEqual(3, t._depth("data/repo/toc/title", "data/index.html"))
