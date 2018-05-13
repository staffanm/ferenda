<?xml version="1.0" encoding="utf-8"?>
<!--
Note: this template expects XHTML1.1, outputs HTML5

It's a generic template for error pages.
-->
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		exclude-result-prefixes="xhtml rdf dcterms rinfo rinfoex">

  <xsl:include href="base.xsl"/>

  <xsl:template name="headtitle"><xsl:value-of select="xhtml:title"/></xsl:template>
  <xsl:template name="metarobots"><xsl:comment>Robot metatag goes here</xsl:comment></xsl:template>
  <xsl:template name="linkalternate"><xsl:comment>Alternate link(s)</xsl:comment></xsl:template>
  <xsl:template name="headmetadata"><xsl:comment>headmetadata?</xsl:comment></xsl:template>
  <xsl:template name="bodyclass">error</xsl:template>
  <xsl:template name="pagetitle"/>
  <xsl:param name="dyntoc" select="false()"/>
  <xsl:param name="fixedtoc" select="true()"/>
  <xsl:param name="content-under-pagetitle" select="false()"/>


  <!-- no rules for default mode, just let default template translate
       everything in source doc from XHTML 1.1 NS to HTML5 -->
        

  <!-- do nothing in toc mode -->
  <xsl:template match="@*|node()" mode="toc"/>

  <!-- default template: translate everything from whatever namespace
       it's in (usually the XHTML1.1 NS) into the default namespace
  -->
  <xsl:template match="*">
    <xsl:element name="{name()}">
      <xsl:apply-templates select="@*|node()"/>
    </xsl:element>
  </xsl:template>
  <xsl:template match="@*">
    <xsl:copy><xsl:apply-templates/></xsl:copy>
  </xsl:template>
</xsl:stylesheet>
