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
		xmlns:ext="http://exslt.org/common"
		exclude-result-prefixes="xhtml rdf">

  <xsl:import href="annotations-panel.xsl"/>
  <xsl:include href="base.xsl"/>

  <!-- Implementations of templates called by base.xsl -->
  <xsl:template name="headtitle"><xsl:value-of select="//xhtml:title"/> | <xsl:value-of select="$configuration/sitename"/></xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>
  <xsl:template name="bodyclass">generic</xsl:template>
  <xsl:template name="pagetitle">
    <h1><xsl:value-of select="../xhtml:head/xhtml:title"/></h1>
  </xsl:template>
  <xsl:param name="dyntoc" select="false()"/>
  <xsl:param name="fixedtoc" select="true()"/>
  <xsl:param name="content-under-pagetitle" select="false()"/>
      
  <xsl:template name="aside-annotations">
    <xsl:param name="uri"/>
    <xsl:variable name="markup">
      <xsl:for-each select="$annotations/resource[@uri=$uri]/dcterms:isReferencedBy">
	<xsl:variable name="referencing" select="@ref"/>
	<a href="{@ref}"><xsl:value-of select="$annotations/resource[@uri=$referencing]/dcterms:identifier"/></a>
      </xsl:for-each>
    </xsl:variable>

    <xsl:if test="$annotations/resource[@uri=$uri]">
      <aside class="panel-group col-sm-4" role="tablist" id="panel-top" aria-multiselectable="true">
      <xsl:call-template name="aside-annotations-panel">
	<xsl:with-param name="title">Annotations</xsl:with-param>
	<xsl:with-param name="badgecount"/>
	<xsl:with-param name="nodeset" select="ext:node-set($markup)"/>
	<xsl:with-param name="panelid">top</xsl:with-param>
	<xsl:with-param name="paneltype">metadata</xsl:with-param>
	<xsl:with-param name="expanded" select="true()"/>
      </xsl:call-template>
      </aside>
    </xsl:if>
  </xsl:template>

  <xsl:template match="xhtml:body/xhtml:div">
    <div class="section-wrapper toplevel">
      <section id="{substring-after(@about,'#')}" class="col-sm-8">
	<xsl:if test="@content">
	  <h2><xsl:value-of select="@content"/></h2>
	</xsl:if>
	<xsl:apply-templates select="*[not(xhtml:div[@about])]"/>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="../@about"/>
      </xsl:call-template>
    </div>
    <!--
    <xsl:comment>top level: docparts start</xsl:comment>
    <xsl:apply-templates select="xhtml:div[@about]"/>
    <xsl:comment>top level: docparts end</xsl:comment>
    -->
  </xsl:template>

  <!-- everything that has an @about attribute, i.e. _is_ something
       (with a URI) gets a <section> with an <aside> for inbound links etc -->
  <xsl:template match="xhtml:div[@about]">
    
    <div class="section-wrapper" about="{@about}"><!-- needed? -->
      <section id="{substring-after(@about,'#')}">
	<xsl:variable name="sectionheading"><xsl:if test="xhtml:span/@content"><xsl:value-of select="xhtml:span/@content"/>. </xsl:if><xsl:value-of select="@content"/></xsl:variable>
	<xsl:if test="count(ancestor::*) = 2">
	    <h2><xsl:value-of select="$sectionheading"/></h2>
	</xsl:if>
	<xsl:if test="count(ancestor::*) = 3">
	  <h3><xsl:value-of select="$sectionheading"/></h3>
	</xsl:if>
	<xsl:if test="count(ancestor::*) = 4">
	  <h4><xsl:value-of select="$sectionheading"/></h4>
	</xsl:if>
	<xsl:apply-templates select="*[not(@about)]"/>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
    <!--
    <xsl:comment>docpart level: subparts start</xsl:comment>
    -->
    <xsl:apply-templates select="xhtml:div[@about]"/>
    <!--
    <xsl:comment>docpart level: subparts end</xsl:comment>
    -->
  </xsl:template>


  <!-- remove spans which only purpose is to contain RDFa data -->
  <xsl:template match="xhtml:span[@property and @content and not(text())]"/>
  

  <xsl:template match="xhtml:div[@about]" mode="toc">
    <li><a href="#{substring-after(@about,'#')}"><xsl:if test="xhtml:span/@content"><xsl:value-of select="xhtml:span/@content"/>. </xsl:if><xsl:value-of select="@content"/></a><xsl:if test="xhtml:div[@about]">
    <ul><xsl:apply-templates mode="toc"/></ul>
    </xsl:if></li>
  </xsl:template>
  
  <!-- default template: translate everything from whatever namespace
       it's in (usually the XHTML1.1 NS) into the default namespace
       -->
  <xsl:template match="*">
    <xsl:element name="{local-name()}">
      <xsl:for-each select="@*">
        <xsl:attribute name="{local-name()}">
          <xsl:value-of select="."/>
        </xsl:attribute>
      </xsl:for-each>
      <xsl:apply-templates/>
    </xsl:element>
  </xsl:template>

  <!-- toc handling (do nothing) -->
  <xsl:template match="@*|node()" mode="toc"/>
  
</xsl:stylesheet>

