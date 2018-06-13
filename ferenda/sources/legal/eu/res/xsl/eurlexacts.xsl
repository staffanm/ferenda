<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:xsd="http://www.w3.org/2001/XMLSchema#"
		exclude-result-prefixes="xhtml rdf dcterms xsd"
		>
  <xsl:include href="base.xsl"/>
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

  <xsl:template match="xhtml:div[@class='preamble']">
    <!-- <xsl:apply-templates select="allt före första @class=consid"/> -->
    <dl class="dl-horizontal">
      <xsl:apply-templates select="xhtml:p[@class='consid']"/>
    </dl>
    <!-- <xsl:apply-templates select="allt efter sista @class=consid
         (eller allt med @class='final'?)"/> -->
  </xsl:template>
  
  <xsl:template match="xhtml:p[@class='consid']">
    <dt id="{substring-after(@about, '#')}"><xsl:value-of select="@content"/></dt>
    <dd><xsl:apply-templates/></dd>
  </xsl:template>


  <xsl:template match="xhtml:div[@class='preamble']">
    <h2 id="preamble">preambel</h2>
    <!-- <xsl:apply-templates select="allt före första @class=consid"/> -->
    <dl class="dl-horizontal">
      <xsl:apply-templates select="xhtml:p[@class='consid']"/>
    </dl>
    <!-- <xsl:apply-templates select="allt efter sista @class=consid
         (eller allt med @class='final'?)"/> -->
  </xsl:template>


  <xsl:template match="xhtml:div[@class='enacting-terms']">
    <h2 id="enacting-terms">artikeldel</h2>
    <xsl:apply-templates/>
  </xsl:template>
  

  <xsl:template match="xhtml:div[@typeof='bibo:DocumentPart']">
    <h3 id="{substring-after(@about, '#')}"><xsl:value-of select="@content"/></h3>
    <xsl:apply-templates/>
  </xsl:template>

  <xsl:template match="xhtml:div[@typeof='cdm:article_legal']">
    <div class="article_legal">
      <h4 id="{substring-after(@about, '#')}"><xsl:value-of select="@content"/>: <xsl:value-of select="xhtml:span[@rel='dcterms:identifier']"/></h4>
      <xsl:apply-templates/>
    </div>
  </xsl:template>



  <!-- TABLE OF CONTENTS (TOC) HANDLING -->
  <xsl:template match="xhtml:div[@about]" mode="toc"/>

  <xsl:template match="xhtml:div[@class='preamble']" mode="toc">
    <li><a href="#preamble"><b>Skäl</b></a>
    <ul class="nav list-inline">
      <xsl:apply-templates mode="toc"/>
    </ul>
    </li>
  </xsl:template>

  <xsl:template match="xhtml:p[@class='consid']" mode="toc">
    <li><!--<a href="#{substring-after(@about, '#')}">--><xsl:value-of select="@content"/><!--</a>--></li>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='enacting-terms']" mode="toc">
    <li><a href="#enacting-terms"><b>Artiklar</b></a>
    <ul class="nav">
      <xsl:apply-templates mode="toc"/>
    </ul>
    </li>
  </xsl:template>
  
  <xsl:template match="xhtml:div[@typeof='bibo:DocumentPart']" mode="toc">
    <li><a href="#{substring-after(@about, '#')}"><xsl:value-of select="@content"/></a>
    <ul class="nav">
      <xsl:apply-templates mode="toc"/>
    </ul>
    </li>
  </xsl:template>

  <xsl:template match="xhtml:div[@typeof='cdm:article_legal']" mode="toc">
    <li><a href="#{substring-after(@about, '#')}"><xsl:value-of select="@content"/>: <xsl:value-of select="xhtml:span[@rel='dcterms:identifier']"/></a></li>
  </xsl:template>
  
  <!-- otherwise do nothing -->
  <xsl:template match="@*|node()" mode="toc"/>
  
</xsl:stylesheet>
