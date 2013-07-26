<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dct="http://purl.org/dc/terms/">
  <xsl:param name="uri"/>

  <xsl:template match="/">
    <rdf:RDF>
      <xsl:apply-templates select="rdf:RDF/rdf:Description[starts-with(@rdf:about,$uri)]"/>
    </rdf:RDF>
  </xsl:template>

  <xsl:template match="@*|node()">
    <xsl:copy>
      <xsl:apply-templates select="@*|node()"/>
    </xsl:copy>
  </xsl:template>

</xsl:stylesheet>
  

