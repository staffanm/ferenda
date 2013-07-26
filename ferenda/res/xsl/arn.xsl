<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:xht2="http://www.w3.org/2002/06/xhtml2/"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dct="http://purl.org/dc/terms/"
		xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		exclude-result-prefixes="xht2 rdf">

  <xsl:import href="uri.xsl"/>
  <xsl:include href="base.xsl"/>
  <!-- Implementationer av templates som anropas från base.xsl -->
  <xsl:template name="headtitle">
    <xsl:value-of select="//xht2:title"/> | Lagen.nu
  </xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>
      
  <xsl:template match="xht2:h">
    <h2><xsl:value-of select="."/></h2>
  </xsl:template>

  <xsl:template match="xht2:a">
    <xsl:call-template name="link"/>
  </xsl:template>

  <xsl:template match="xht2:section">
    <div><xsl:apply-templates/></div>
  </xsl:template>

  <xsl:template match="xht2:dl[@role='contentinfo']">
    <!-- plocka ut det gottaste från metadatat -->
    <h1>ARN <xsl:value-of select="xht2:dd[@property='rinfoex:arendenummer']"/></h1>
    <p property="dct:description" class="rattsfallsrubrik"><xsl:value-of select="xht2:dd[@property='dct:description']"/></p>
  </xsl:template>

  <!-- defaultregel: kopierar alla element från xht2 till
       default-namespacet -->
  <xsl:template match="*">
    <xsl:element name="{name()}">
      <xsl:apply-templates select="@*|node()"/>
    </xsl:element>
  </xsl:template>

  <xsl:template match="@*">
    <xsl:copy><xsl:apply-templates/></xsl:copy>
  </xsl:template>

  <!-- refs mode -->
  <xsl:template match="xht2:h" mode="refs">
    <!-- emit nothing -->
  </xsl:template>

  <xsl:template match="xht2:dl[@role='contentinfo']">
    <div class="sidoruta">
      <dl>
	<dt>Beslutsdatum</dt>
	<dd property="rinfo:beslutsdatum"><xsl:value-of select="xht2:dd[@property='rinfo:beslutsdatum']"/></dd>
	<dt>Ärendenummer</dt>
	<dd property="rinfoex:arendenummer"><xsl:value-of select="xht2:dd[@property='rinfoex:arendenummer']"/></dd>
	<dt>Beslut</dt>
	<dd property="rinfoex:beslutsutfall"><xsl:value-of select="xht2:dd[@property='rinfoex:beslutsutfall']"/></dd>
	<dt>Avdelning</dt>
	<dd property="rinfoex:avdelning"><xsl:value-of select="xht2:dd[@property='rinfoex:avdelning']"/></dd>
	<dt>Ärendemening</dt>
	<dd property="dct:subject"><xsl:value-of select="xht2:dd[@property='dct:subject']"/></dd>
	
	<dt>Källa</dt>
	<dd property="dct:publisher" resource="http://lagen.nu/org/2008/allmanna-reklamationsnamnden"><a href="http://www.arn.se/">Allmänna reklamationsnämnden</a></dd>
      </dl>
    </div>
  </xsl:template>

  <xsl:template match="*|@*" mode="toc">
    <!-- emit nothing -->
  </xsl:template>
  
</xsl:stylesheet>

