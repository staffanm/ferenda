<?xml version="1.0" encoding="utf-8"?>
<!--
Note: this template expects Atom 1.0, outputs HTML5
-->
<xsl:stylesheet version="1.0"
		xmlns="http://www.w3.org/1999/xhtml"
		xmlns:atom="http://www.w3.org/2005/Atom"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		exclude-result-prefixes="xhtml rdf atom">

  <xsl:include href="base.xsl"/>
  <xsl:param name="dyntoc" select="false()"/>
  <xsl:param name="fixedtoc" select="true()"/>
  <xsl:param name="content-under-pagetitle" select="false()"/>
  <xsl:template name="pagetitle">
    <h1><xsl:value-of select="atom:title"/></h1>
  </xsl:template>
  <xsl:template name="bodyclass">feed</xsl:template>
  <xsl:template name="headmetadata"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="metarobots"/>
  <xsl:template name="headtitle"><xsl:value-of select="atom:title"/> (<xsl:value-of select="name()"/>)</xsl:template>

  <xsl:template match="atom:feed">
    <html>
      <xsl:call-template name="htmlhead"/>
      <xsl:call-template name="htmlbody"/>
    </html>
  </xsl:template>

  <xsl:template name="convert-linebreaks">
    <xsl:param name="plaintext" select="."/>
    <xsl:choose><xsl:when test="not(contains($plaintext, '&#xA;'))">
	<xsl:value-of select="$plaintext"/>
      </xsl:when>
      <xsl:otherwise>
	<xsl:value-of select="substring-before($plaintext, '&#xA;')"/>
	<br />
	<xsl:call-template name="convert-linebreaks">
          <xsl:with-param name="plaintext" select="substring-after($plaintext, '&#xA;')"/>
	</xsl:call-template>
      </xsl:otherwise>
    </xsl:choose>
  </xsl:template>  

  <xsl:template match="atom:entry">
    <section>
      <a href="{atom:id}"><h2><xsl:value-of select="atom:title"/></h2></a>
      <!-- just include the date, not the time, in the human-readable version -->
      <small><xsl:value-of select="substring(atom:published,1,10)"/></small>
	<xsl:choose>
	  <xsl:when test="atom:summary/@type='html'">
	    <!-- summary is HTML fragment. Output it as-is -->
	    <xsl:value-of select="atom:summary" disable-output-escaping="yes"/>
	  </xsl:when>
	  <xsl:otherwise>
	    <!-- summary is a plaintext tag. Convert linebreaks to <br/>
		 tags when displaying in HTML -->
	    <p>
	      <xsl:call-template name="convert-linebreaks">
		<xsl:with-param name="plaintext" select="atom:summary"/>
	      </xsl:call-template>
	    </p>
	  </xsl:otherwise>
	</xsl:choose>
    </section>
  </xsl:template>

  <xsl:template match="atom:*" mode="toc">
    <!--<li><xsl:value-of select="atom:title"/></li> -->
  </xsl:template>

  <xsl:template match="atom:id|atom:title|atom:updated|atom:author|atom:link"/>
  
</xsl:stylesheet>
