<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rpubl="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xmlns:bibo="http://purl.org/ontology/bibo/"
		xmlns:ext="http://exslt.org/common"
		xml:space="preserve"
		exclude-result-prefixes="xhtml rdf">

  <xsl:import href="annotations-panel.xsl"/>
  <xsl:include href="base.xsl"/>

  <!-- Implementations of templates called by base.xsl -->
  <xsl:template name="headtitle"><xsl:value-of select="//xhtml:meta[@property='dcterms:identifier']/@content"/> | <xsl:value-of select="$configuration/sitename"/></xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>
  <xsl:template name="bodyclass">avg</xsl:template>

  <xsl:template name="pagetitle">
    <div class="section-wrapper toplevel">
      <section class="col-sm-7">
	<h1><xsl:value-of select="//xhtml:meta[@property='dcterms:identifier']/@content"/></h1>
	<p class="lead"><xsl:value-of select="//*[@property='dcterms:title']"/></p>
	<xsl:apply-templates/>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
  </xsl:template>
  <xsl:param name="dyntoc" select="true()"/>
  <xsl:param name="fixedtoc" select="true()"/>
  <xsl:param name="content-under-pagetitle" select="true()"/>

  <xsl:template name="aside-annotations">
    <xsl:param name="uri"/>
    <xsl:variable name="metadata">
      <dl class="dl-horizontal">
	<dt>Myndighet</dt>
	<dd><xsl:value-of select="//xhtml:link[@rel='dcterms:publisher']/@href"/></dd>
	<dt>Beslutdatum</dt>
	<dd><xsl:value-of select="//xhtml:meta[@property='rpubl:beslutsdatum']/@content"/></dd>
	<dt>Diarienummer</dt>
	<dd><xsl:value-of select="//xhtml:meta[@property='rpubl:diarienummer']/@content"/></dd>
	<dt>Källa</dt>
	<dd>
	  <a href="{//xhtml:head/xhtml:link[@rel='prov:alternateOf']/@href}">
	    <xsl:value-of select="//xhtml:head/xhtml:meta[@about=//xhtml:head/xhtml:link[@rel='prov:wasDerivedFrom']/@href]/@content"/>
	  </a>
	</dd>
      </dl>
    </xsl:variable>

    <xsl:variable name="avgoranden" select="$annotations/resource[a/rpubl:VagledandeMyndighetsavgorande]"/>

    <xsl:variable name="avgoranden-markup">
      <xsl:for-each select="$avgoranden">
	<li><a href="{@uri}"><b><xsl:value-of select="dcterms:identifier"/></b>:</a> <xsl:value-of select="dcterms:title"/></li>
      </xsl:for-each>
    </xsl:variable>

    <xsl:variable name="forarbeten" select="$annotations/resource[a/rpubl:Proposition]"/>
    <xsl:variable name="forarbeten-markup">
      <xsl:for-each select="$forarbeten">
	<li><b><xsl:value-of select="dcterms:identifier"/></b>: <xsl:value-of select="dcterms:title"/>
	<xsl:for-each select="bibo:chapter">
	  <a href="{@uri}#{.}"><xsl:value-of select="."/></a>
	</xsl:for-each>
	</li>
      </xsl:for-each>
    </xsl:variable>

    <aside class="panel-group col-sm-5" role="tablist" id="panel-top" aria-multiselectable="true">
      <xsl:call-template name="aside-annotations-panel">
	<xsl:with-param name="title">Metadata</xsl:with-param>
	<xsl:with-param name="badgecount"/>
	<xsl:with-param name="panelid">top</xsl:with-param>
	<xsl:with-param name="paneltype">metadata</xsl:with-param>
	<xsl:with-param name="expanded" select="true()"/>
	<xsl:with-param name="nodeset" select="ext:node-set($metadata)"/>
      </xsl:call-template>

      <xsl:if test="$avgoranden">
	<xsl:call-template name="aside-annotations-panel">
	  <xsl:with-param name="title">Praxis som hänvisar till detta</xsl:with-param>
	  <xsl:with-param name="badgecount" select="count($avgoranden)"/>
	  <xsl:with-param name="nodeset" select="ext:node-set($avgoranden-markup)"/>
	  <xsl:with-param name="panelid">top</xsl:with-param>
	  <xsl:with-param name="paneltype">rattsfall</xsl:with-param>
	  <xsl:with-param name="expanded" select="true()"/>
	</xsl:call-template>
      </xsl:if>

      <xsl:if test="$forarbeten">
	<xsl:call-template name="aside-annotations-panel">
	  <xsl:with-param name="title">Förarbeten som hänvisar till detta</xsl:with-param>
	  <xsl:with-param name="badgecount" select="count($forarbeten)"/>
	  <xsl:with-param name="nodeset" select="ext:node-set($forarbeten-markup)"/>
	  <xsl:with-param name="panelid">top</xsl:with-param>
	  <xsl:with-param name="paneltype">forarbeten</xsl:with-param>
	  <xsl:with-param name="expanded" select="true()"/>
	</xsl:call-template>
      </xsl:if>
    </aside>
  </xsl:template>
  

  <xsl:template name="avgoranden">
    <xsl:param name="avgoranden"/>
      <xsl:for-each select="$avgoranden">
	<xsl:sort select="@uri"/>
	<xsl:variable name="tuned-width">
	  <xsl:call-template name="tune-width">
	    <xsl:with-param name="txt" select="rpubl:referatrubrik"/>
	    <xsl:with-param name="width" select="80"/>
	    <xsl:with-param name="def" select="80"/>
	  </xsl:call-template>
	</xsl:variable>
	<a href="{@rdf:about}"><b><xsl:value-of select="dcterms:identifier"/></b></a>:
	<xsl:choose>
	  <xsl:when test="string-length(rpubl:referatrubrik) > 80">
	    <xsl:value-of select="normalize-space(substring(rpubl:referatrubrik, 1, $tuned-width - 1))" />...
	  </xsl:when>
	  <xsl:otherwise>
	    <xsl:value-of select="rpubl:referatrubrik"/>
	  </xsl:otherwise>
	</xsl:choose>
	<br/>
      </xsl:for-each>
  </xsl:template>

  <!-- remove spans which only purpose is to contain RDFa data -->
  <xsl:template match="xhtml:span[@property and @content and not(text())]"/>
  
  <!-- default template: translate everything from whatever namespace
       it's in (usually the XHTML1.1 NS) into the default namespace
       NOTE: It removes any attributes not accounted for otherwise
       -->
  <xsl:template match="*">
    <xsl:element name="{local-name(.)}"><xsl:apply-templates select="node()"/></xsl:element>
  </xsl:template>

  <!-- toc handling (do nothing) -->
  <xsl:template match="@*|node()" mode="toc"/>
  
</xsl:stylesheet>

