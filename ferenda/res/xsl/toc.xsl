<?xml version="1.0" encoding="utf-8"?>
<!--
Note: this template expects XHTML1.1, outputs HTML5

It's a generic template for TOC pages (assumes that there exists a <ul role="navigation"> for internal navigation between different TOC pages  and a <div role="main"> for the main list of links for this particular TOC page.
-->
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		exclude-result-prefixes="xhtml rdf dcterms rinfo rinfoex">

  <xsl:import href="uri.xsl"/>
  <xsl:include href="base.xsl"/>


  <xsl:template name="headtitle"><xsl:value-of select="xhtml:title"/></xsl:template>
  <xsl:template name="metarobots"><xsl:comment>Robot metatag goes here</xsl:comment></xsl:template>
  <xsl:template name="linkalternate"><xsl:comment>Alternate link(s)</xsl:comment></xsl:template>
  <xsl:template name="headmetadata"><xsl:comment>headmetadata?</xsl:comment></xsl:template>
  <xsl:template name="bodyclass">toc</xsl:template>
  <xsl:template name="pagetitle">
    <h1><xsl:value-of select="../xhtml:head/xhtml:title"/></h1>
  </xsl:template>
      

  <xsl:template match="xhtml:h1"><h1><xsl:apply-templates/></h1></xsl:template>

  <xsl:template match="xhtml:div[@role='main']"><div class="maintext"><xsl:apply-templates/></div></xsl:template>


  <xsl:template match="xhtml:ul[@role='navigation']"><!-- do nothing, this part of the source document is processed during mode='toc' --></xsl:template>
  
  <xsl:template match="xhtml:a">
    <!-- calling link templ -->
    <xsl:call-template name="link"/>
  </xsl:template>

  <xsl:template match="xhtml:ul[@role='navigation']" mode="toc">
    <xsl:comment>Navigation toc rule applied</xsl:comment>
    <xsl:apply-templates/>
  </xsl:template>
    
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
  <!--
  <xsl:template match="@*|node()">
    <xsl:copy>
      <xsl:apply-templates select="@*|node()"/>
    </xsl:copy>
  </xsl:template>
  -->
  
  <xsl:template match="@*|node()" mode="toc">
    <!-- do nothing -->
  </xsl:template>
  
</xsl:stylesheet>

