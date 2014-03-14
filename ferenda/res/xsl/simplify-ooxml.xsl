<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
		xmlns:exslt="http://exslt.org/common"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		exclude-result-prefixes="exslt">
  <xsl:output method="xml" encoding="utf-8"/>


  <!-- keep: w:t, w:tr, w:tc, w:p, w:pPr IF IF it contains w:rPr/(w:b|w:i) -->
  <!-- remove: everything else -->
  <!-- attributes: remove everything -->
  <xsl:template match="body|w:document|w:body|w:tbl|w:t|w:tr|w:tc|w:r[w:t]|w:p|w:pPr[w:rPr/w:b|w:i]|w:rPr[w:b|w:i]|w:b|w:i">
    <xsl:copy>
      <xsl:apply-templates select="@*|node()"/>
    </xsl:copy>
  </xsl:template>

  
  <xsl:template match="text()">
    <xsl:value-of select="."/>
  </xsl:template>

  <!-- default element template: remove -->
  <xsl:template match="*"/>
      <!--
	<xsl:comment>remove tag <xsl:value-of select="name()"/></xsl:comment>
      </xsl:when>
      -->

  <!-- default attribute template: remove -->
  <xsl:template match="@*"/>
</xsl:stylesheet>
