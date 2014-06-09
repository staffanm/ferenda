<?xml version="1.0" encoding="utf-8"?>
<!--
Note: this template expects XHTML1.1, outputs HTML5

It's a generic template for any kind of content
-->
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xml:space="preserve"
		exclude-result-prefixes="xhtml rdf">

  <xsl:import href="uri.xsl"/>
  <xsl:include href="base.xsl"/>

  <!-- Implementations of templates called by base.xsl -->
  <xsl:template name="headtitle"><xsl:value-of select="//xhtml:title"/></xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>
  <xsl:template name="bodyclass">frontpage</xsl:template>
  <xsl:template name="pagetitle"/>
      
  <xsl:template match="xhtml:a">
    <xsl:call-template name="link"/>
  </xsl:template>

  <xsl:template match="xhtml:body/xhtml:div">
    <div class="section-wrapper" id="{@id}">
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <!-- default template: translate everything from whatever namespace
       it's in (usually the XHTML1.1 NS) into the default namespace
       -->
  <!-- results in error 'Attribute nodes must be added before any child nodes to an element.' -->
  <!--
  <xsl:template match="@*">
    <xsl:copy><xsl:apply-templates/></xsl:copy>
  </xsl:template>
  --> 
  <xsl:template match="*">
    <xsl:element name="{local-name(.)}"><xsl:apply-templates select="@*|node()"/></xsl:element>
  </xsl:template>

  <!-- default toc handling (do nothing) -->
  <xsl:template match="@*|node()" mode="toc"/>

</xsl:stylesheet>

