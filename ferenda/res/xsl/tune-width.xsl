<?xml version="1.0" encoding="ISO-8859-1"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
<!-- From Paul Tchistopolski, http://www.dpawson.co.uk/xsl/sect2/N7240.html#d9087e244 -->
<xsl:template name="tune-width">
  <xsl:param name="txt" /> 
  <xsl:param name="width" /> 
  <xsl:param name="def" /> 

  <xsl:choose>
    <xsl:when test="$width = 0">
      <xsl:value-of select="$def" /> 
    </xsl:when>
    <xsl:otherwise>
      <xsl:choose>
	<xsl:when test="substring($txt, $width, 1 ) = ' '">
	  <xsl:value-of select="$width" /> 
	</xsl:when>
	<xsl:otherwise>
	  <xsl:call-template name="tune-width">
	    <xsl:with-param select="$txt" name="txt" /> 
	    <xsl:with-param select="$width - 1" name="width" /> 
	    <xsl:with-param select="$def" name="def" /> 
	  </xsl:call-template>
	</xsl:otherwise>
      </xsl:choose>
    </xsl:otherwise>
  </xsl:choose>
</xsl:template>
</xsl:stylesheet>