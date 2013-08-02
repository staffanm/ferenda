<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">

    <!--
        Title: Grit XML to RDF/XML GRDDL
        Last modified: 2010-03-28
        Copyright: Niklas Lindström <lindstream@gmail.com>
        License: BSD-style
    -->

    <xsl:param name="base" select="/*/@xml:base[position()=1]"/>
    <xsl:variable name="all-namespaces" select="//*/namespace::*"/>

    <xsl:template match="graph">
        <rdf:RDF>
            <xsl:copy-of select="$all-namespaces"/>
            <xsl:copy-of select="$base"/>
            <xsl:apply-templates/>
        </rdf:RDF>
    </xsl:template>

    <xsl:template match="resource" name="resource">
        <rdf:Description>
            <xsl:apply-templates select="@*|*"/>
        </rdf:Description>
    </xsl:template>

    <xsl:template match="a">
        <rdf:type rdf:resource="{namespace-uri(*)}{local-name(*)}"/>
    </xsl:template>

    <xsl:template match="li">
        <xsl:call-template name="resource"/>
    </xsl:template>

    <xsl:template match="*[@fmt='datatype']">
        <xsl:copy>
            <xsl:attribute name="rdf:datatype">
                <xsl:value-of select="concat(namespace-uri(*), local-name(*))"/>
            </xsl:attribute>
            <xsl:apply-templates select="*/node()"/>
        </xsl:copy>
    </xsl:template>

    <xsl:template match="*[@fmt='xml']">
        <xsl:copy>
            <xsl:attribute name="rdf:parseType">Literal</xsl:attribute>
            <xsl:copy-of select="node()"/>
        </xsl:copy>
    </xsl:template>

    <xsl:template match="@uri | li/@ref">
        <xsl:call-template name="uri-or-nodeid">
            <xsl:with-param name="attr-name" select="'rdf:about'"/>
        </xsl:call-template>
    </xsl:template>

    <xsl:template match="@ref">
        <xsl:call-template name="uri-or-nodeid">
            <xsl:with-param name="attr-name" select="'rdf:resource'"/>
        </xsl:call-template>
    </xsl:template>

    <xsl:template match="@xml:lang">
        <xsl:copy/>
    </xsl:template>

    <xsl:template match="*">
        <xsl:copy>
            <xsl:apply-templates select="@*"/>
            <xsl:choose>
                <xsl:when test="li">
                    <xsl:attribute name="rdf:parseType">Collection</xsl:attribute>
                    <xsl:for-each select="*">
                        <xsl:call-template name="resource"/>
                    </xsl:for-each>
                </xsl:when>
                <xsl:when test="*">
                    <xsl:call-template name="resource"/>
                </xsl:when>
                <xsl:otherwise>
                    <xsl:apply-templates select="node()"/>
                </xsl:otherwise>
            </xsl:choose>
        </xsl:copy>
    </xsl:template>

    <xsl:template name="uri-or-nodeid">
        <xsl:param name="attr-name"/>
        <xsl:choose>
            <xsl:when test="starts-with(., '_:')">
                <xsl:attribute name="rdf:nodeID">
                    <xsl:value-of select="substring-after(., '_:')"/>
                </xsl:attribute>
            </xsl:when>
            <xsl:otherwise>
                <xsl:attribute name="{$attr-name}">
                    <xsl:value-of select="."/>
                </xsl:attribute>
            </xsl:otherwise>
        </xsl:choose>
    </xsl:template>

</xsl:stylesheet>
