<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:xht2="http://www.w3.org/2002/06/xhtml2/"
		xmlns:dct="http://purl.org/dc/terms/"
		exclude-result-prefixes="xht2 dct">

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
    <xsl:choose>
      <xsl:when test="@property = 'dct:title'">
	<h1><xsl:value-of select="."/></h1>
      </xsl:when>
      <xsl:when test="@class = 'underrubrik'">
	<h3><xsl:for-each select="@*">
	    <xsl:attribute name="{name()}"><xsl:value-of select="." /></xsl:attribute>
	  </xsl:for-each><xsl:value-of select="."/></h3>
      </xsl:when>
      <xsl:otherwise>
	<h2><xsl:for-each select="@*">
	    <xsl:attribute name="{name()}"><xsl:value-of select="." /></xsl:attribute>
	  </xsl:for-each><xsl:value-of select="."/></h2>
      </xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <xsl:template match="xht2:section">
    <div>
      <xsl:for-each select="@*">
	<xsl:attribute name="{name()}"><xsl:value-of select="." /></xsl:attribute>
      </xsl:for-each>
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="xht2:a">
    <xsl:call-template name="link">
      <!-- <xsl:with-param name="decorate" select="true()"/> -->
      <xsl:with-param name="validate" select="false()"/>
    </xsl:call-template>
  </xsl:template>

  <xsl:template match="xht2:*[@role='main']">
    <!-- strip the actual @role='main' container --> 
    <xsl:apply-templates/> 
  </xsl:template>

  <xsl:template match="xht2:*[@role='navigation']">
    <!-- emit nothing -->
  </xsl:template>
  <xsl:template match="xht2:*[@role='note']">
    <!-- emit nothing -->
  </xsl:template>
  
  <!-- defaultregel: kopierar alla element från xht2 till
       default-namespacet -->
  <xsl:template match="xht2:*">
    <xsl:element name="{name()}">
      <xsl:apply-templates select="@*|node()"/>
    </xsl:element>
  </xsl:template>


  <xsl:template match="comment()">
    <xsl:copy/>
  </xsl:template>

  <xsl:template match="@*">
    <xsl:copy><xsl:apply-templates/></xsl:copy>
  </xsl:template>

  <!--
  <xsl:template match="xht2:*">
    <xsl:copy><xsl:apply-templates/></xsl:copy>
  </xsl:template>
  -->
  <!-- refs mode -->

  <xsl:template match="*|@*" mode="refs">
    <xsl:if test="@role='note'">
      <xsl:apply-templates mode="trans-ns"/>
    </xsl:if>
  </xsl:template>

  <!-- kommentarer mode -->
  <xsl:template match="xht2:div" mode="toc">
    <xsl:if test="@role='navigation'">
      <xsl:apply-templates mode="trans-ns"/>
    </xsl:if>
  </xsl:template>

  <xsl:template match="xht2:*" mode="toc">
    <!-- emit nothing -->
  </xsl:template>

  <xsl:template match="xht2:h" mode="trans-ns">
    <h2><xsl:apply-templates select="@*|node()"/></h2>
  </xsl:template>
  
  <!-- generic namespace translation -->
  <xsl:template match="xht2:*" mode="trans-ns">
    <xsl:element name="{name()}">
      <xsl:apply-templates select="@*|node()"/>
    </xsl:element>
  </xsl:template>
  
  
</xsl:stylesheet>

