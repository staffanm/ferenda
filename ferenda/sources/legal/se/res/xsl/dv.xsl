<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rpubl="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xml:space="preserve"
		exclude-result-prefixes="xhtml rdf">

  <xsl:import href="uri.xsl"/>
  <xsl:include href="base.xsl"/>

  <!-- Implementations of templates called by base.xsl -->
  <xsl:template name="headtitle"><xsl:value-of select="//xhtml:meta/@dcterms:identifier"/> | <xsl:value-of select="$configuration/sitename"/></xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>
  <xsl:template name="bodyclass">dv</xsl:template>
  <xsl:template name="pagetitle">

    <div class="section-wrapper toplevel">
      <section>
	<h1><xsl:value-of select="//xhtml:meta[@property='dcterms:identifier']/@content"/></h1>
	<h2><xsl:value-of select="//xhtml:meta[@property='rpubl:referatrubrik']/@content"/></h2>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
    
  </xsl:template>
      

  <xsl:template match="xhtml:a">
    <xsl:call-template name="link"/>
  </xsl:template>

  <xsl:template name="aside-annotations">
    <xsl:param name="uri"/>
    <xsl:variable name="domuri" select="//xhtml:link[@rel='rpubl:referatAvDomstolsavgorande']/@href"/>
    <aside class="metadata">
      <h2>Metadata</h2>
      <dl>
	<dt>Domstol</dt>
	<dd><xsl:value-of select="//xhtml:link[@rel='dcterms:publisher' and @about=$domuri]/@href"/></dd>
	<dt>Avgörandedatum</dt>
	<dd><xsl:value-of select="//xhtml:meta[@property='rpubl:avgorandedatum' and @about=$domuri]/@content"/></dd>
	<dt>Målnummer</dt>
	<dd><xsl:value-of select="//xhtml:meta[@property='rpubl:malnummer' and @about=$domuri]/@content"/></dd>
	<xsl:if test="//xhtml:link[@rel='rpubl:lagrum' and @about=$domuri]">
	  <dt>Lagrum</dt>
	  <xsl:for-each select="//xhtml:link[@rel='rpubl:lagrum' and @about=$domuri]">
	    <dd><xsl:apply-templates select="@href"/></dd>
	  </xsl:for-each>
	</xsl:if>
	<xsl:if test="//xhtml:link[@rel='rpubl:rattsfallshanvisning']">
	  <dt>Rättsfall</dt>
	  <xsl:for-each select="//xhtml:link[@rel='rpubl:rattsfallshanvisning']">
	    <dd><xsl:apply-templates select="."/></dd>
	  </xsl:for-each>
	</xsl:if>
	<xsl:if test="//xhtml:meta[@property='dcterms:relation']">
	  <dt>Litteratur</dt>
	  <xsl:for-each select="//xhtml:meta[@property='dcterms:relation']">
	    <dd><xsl:value-of select="."/></dd>
	  </xsl:for-each>
	</xsl:if>
	<xsl:if test="//xhtml:link[@about=$domuri and @rel='dcterms:subject']">
	  <dt>Sökord</dt>
	  <xsl:for-each select="//xhtml:link[@about=$domuri and @rel='dcterms:subject']">
	    <dd><a href="@href"><xsl:value-of select="@href"/></a></dd>
	  </xsl:for-each>
	</xsl:if>
	<dt>Källa</dt>
	<dd><a href="http://www.rattsinfosok.dom.se/lagrummet/index.jsp">Domstolsverket</a></dd>
      </dl>
    </aside>

    <aside class="annotations rattsfall">
      <h2>Rättsfall som hänvisar till detta</h2>
      <xsl:for-each select="$annotations/resource/dcterms:references[@ref=$uri]">
	<li>Data for <xsl:value-of select="../@uri"/> goes here</li>
      </xsl:for-each>
    </aside>
    
    <xsl:variable name="rattsfall" select="$annotations/resource[a/rpubl:Rattsfallsreferat]"/>
    <xsl:if test="$rattsfall">
      <aside class="annotations rattsfall">
	<h2>Rättsfall (<xsl:value-of select="count($rattsfall)"/>)</h2>
	<xsl:call-template name="rattsfall">
	  <xsl:with-param name="rattsfall" select="$rattsfall"/>
	</xsl:call-template>
      </aside>
    </xsl:if>
  </xsl:template>
  
  <!-- FIXME: this template is copied from sfs.xsl, and should probably be in a lib that dv.xsl, sfs.xsl and lnkeyword.xsl can share. -->
  <xsl:template name="rattsfall">
    <xsl:param name="rattsfall"/>
      <xsl:for-each select="$rattsfall">
	<xsl:sort select="@uri"/>
	<xsl:variable name="tuned-width">
	  <xsl:call-template name="tune-width">
	    <xsl:with-param name="txt" select="rpubl:referatrubrik"/>
	    <xsl:with-param name="width" select="80"/>
	    <xsl:with-param name="def" select="80"/>
	  </xsl:call-template>
	</xsl:variable>
	<xsl:variable name="localurl"><xsl:call-template name="localurl"><xsl:with-param name="uri" select="@rdf:about"/></xsl:call-template></xsl:variable>
	<a href="{$localurl}"><b><xsl:value-of select="dcterms:identifier"/></b></a>:
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


  <xsl:template match="xhtml:body/xhtml:div">
    <h1><xsl:value-of select="@class"/></h1>
      <section>
	<xsl:apply-templates/>
      </section>
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

