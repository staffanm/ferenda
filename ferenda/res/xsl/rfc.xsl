<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rfc="http://example.org/ontology/rfc/"
		xml:space="preserve"
		exclude-result-prefixes="xhtml rdf">

  <xsl:include href="base.xsl"/>

  <!-- Implementations of templates called by base.xsl -->
  <xsl:template name="headtitle"><xsl:value-of select="//xhtml:title"/> | <xsl:value-of select="$configuration/sitename"/></xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>
  <xsl:template name="bodyclass">rfc</xsl:template>
  <xsl:template name="pagetitle">
    <h1><xsl:value-of select="../xhtml:head/xhtml:title"/></h1>
  </xsl:template>

  <xsl:template match="xhtml:a"><a href="{@href}"><xsl:value-of select="."/></a></xsl:template>

  <xsl:template match="xhtml:pre[1]">
    <pre><xsl:apply-templates/>
    </pre>
    <xsl:if test="count(ancestor::*) = 2">
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="../@about"/>
      </xsl:call-template>
    </xsl:if>
  </xsl:template>

  <!-- everything that has an @about attribute, i.e. _is_ something
       (with a URI) gets a <section> with an <aside> for inbound links etc -->
  <xsl:template match="xhtml:div[@about]">
    
    <div class="section-wrapper" about="{@about}"><!-- needed? -->
      <section id="{substring-after(@about,'#')}">
	<xsl:variable name="sectionheading"><xsl:if test="xhtml:span[@property='bibo:chapter']/@content"><xsl:value-of select="xhtml:span[@property='bibo:chapter']/@content"/>. </xsl:if><xsl:value-of select="@content"/></xsl:variable>
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
    <xsl:apply-templates select="xhtml:div[@about]"/>
  </xsl:template>

  <!-- remove spans which only purpose is to contain RDFa data -->
  <xsl:template match="xhtml:span[@property and @content and not(text())]"/>
  
  <!-- construct the side navigation -->
  <xsl:template match="xhtml:div[@about]" mode="toc">
    <li><a href="#{substring-after(@about,'#')}"><xsl:if test="xhtml:span/@content"><xsl:value-of select="xhtml:span[@property='bibo:chapter']/@content"/>. </xsl:if><xsl:value-of select="@content"/></a><xsl:if test="xhtml:div[@about]">
    <ul><xsl:apply-templates mode="toc"/></ul>
    </xsl:if></li>
  </xsl:template>

  <!-- named template called from other templates which match
       xhtml:div[@about] and pre[1] above, and which creates -->
  <xsl:template name="aside-annotations">
    <xsl:param name="uri"/>
    <xsl:if test="$annotations/resource[@uri=$uri]/dcterms:isReferencedBy">
      <aside class="annotations">
	<h2>References to <xsl:value-of select="$annotations/resource[@uri=$uri]/dcterms:identifier"/></h2>
	<xsl:for-each select="$annotations/resource[@uri=$uri]/rfc:isObsoletedBy">
	  <xsl:variable name="referencing" select="@ref"/>
	  Obsoleted by
	  <a href="{@ref}">
	    <xsl:value-of select="$annotations/resource[@uri=$referencing]/dcterms:identifier"/>
	  </a><br/>
	</xsl:for-each>
	<xsl:for-each select="$annotations/resource[@uri=$uri]/rfc:isUpdatedBy">
	  <xsl:variable name="referencing" select="@ref"/>
	  Updated by
	  <a href="{@ref}">
	    <xsl:value-of select="$annotations/resource[@uri=$referencing]/dcterms:identifier"/>
	  </a><br/>
	</xsl:for-each>
	<xsl:for-each select="$annotations/resource[@uri=$uri]/dcterms:isReferencedBy">
	  <xsl:variable name="referencing" select="@ref"/>
	  Referenced by
	  <a href="{@ref}">
	    <xsl:value-of select="$annotations/resource[@uri=$referencing]/dcterms:identifier"/>
	  </a><br/>
	</xsl:for-each>
      </aside>
    </xsl:if>
  </xsl:template>

  <!-- default template: translate everything from whatever namespace
       it's in (usually the XHTML1.1 NS) into the default namespace
       -->
  <xsl:template match="*"><xsl:element name="{local-name(.)}"><xsl:apply-templates select="node()"/></xsl:element></xsl:template>

  <!-- default template for toc handling: do nothing -->
  <xsl:template match="@*|node()" mode="toc"/>
  
</xsl:stylesheet>
