#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from ferenda.testutil import RepoTester
from ferenda import util

from ferenda import Transformer

class Transform(RepoTester):

    def test_transform_html(self):
        with open("_teststyle.xslt","w") as fp:
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
        with open("_paramfile.xml","w") as fp:
            fp.write("""<root><node key='value'><subnode>textnode</subnode></node></root>""")

        with open("_infile.xml","w") as fp:
            fp.write("""<doc><title>Document title</title></doc>""")
        t = Transformer("XSLT", "_teststyle.xslt", ["res/xsl"], "")
        t.transform_file("_infile.xml", "_outfile.xml", {'value':'blahonga',
                                                         'file':'_paramfile.xml'})
        self.assertEqualXML(util.readfile("_outfile.xml"),"""
        <output>
            <paramvalue>blahonga</paramvalue>
            <paramfile><node key='value'><subnode>textnode</subnode></node></paramfile>
            <infile>Document title</infile>
        </output>""")
