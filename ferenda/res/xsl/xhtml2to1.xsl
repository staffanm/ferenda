<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns="http://www.w3.org/1999/xhtml"
		xmlns:xht2="http://www.w3.org/2002/06/xhtml2/"
		xmlns:dct="http://purl.org/dc/terms/"
		xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xmlns:exslt="http://exslt.org/common"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform">

  <xsl:output method="xml" encoding="utf-8"/>


<xsl:template match="@*|node()">
   <xsl:copy>
      <xsl:apply-templates select="@*|node()"/>
   </xsl:copy>
</xsl:template>
  <xsl:template match="xht2:html">
    <xsl:element name="{name()}">
      <xsl:attribute name="version">XHTML+RDFa 1.0</xsl:attribute>
      <xsl:variable name="dct-prefix">
	<dct:elem xmlns:dct="http://purl.org/dc/terms/"/>
      </xsl:variable>
      <xsl:variable name="rinfo-prefix">
	<rinfo:elem rinfo:dct="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"/>
      </xsl:variable>
      <xsl:variable name="rinfoex-prefix">
	<rinfoex:elem xmlns:rinfoex="http://lagen.nu/terms#"/>
      </xsl:variable>
      <xsl:copy-of select="exslt:node-set($dct-prefix)/*/namespace::*"/>
      <xsl:copy-of select="exslt:node-set($rinfo-prefix)/*/namespace::*"/>
      <xsl:copy-of select="exslt:node-set($rinfoex-prefix)/*/namespace::*"/>
      <xsl:apply-templates/>
    </xsl:element>
  </xsl:template>

  <xsl:template match="xht2:section">
    <xsl:element name="div">
      <xsl:apply-templates select="@*|node()"/>
    </xsl:element>
  </xsl:template>

  <xsl:template match="xht2:h[@class='underrubrik']">
    <xsl:element name="h2">
      <xsl:apply-templates select="@*|node()"/>
    </xsl:element>
  </xsl:template>

  <xsl:template match="xht2:h">
    <xsl:element name="h1">
      <xsl:apply-templates select="@*|node()"/>
    </xsl:element>
  </xsl:template>

  <xsl:template match="@role">
    <xsl:attribute name="class"><xsl:value-of select="."/></xsl:attribute>
  </xsl:template>
  
	    
  <xsl:template match="*">
    <xsl:element name="{name()}">
      <xsl:apply-templates select="@*|node()"/>
    </xsl:element>
  </xsl:template>
  <xsl:template match="@*">
    <xsl:copy><xsl:apply-templates/></xsl:copy>
  </xsl:template>

</xsl:stylesheet>