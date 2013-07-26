<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:dct="http://purl.org/dc/terms/"
		xmlns:sparql='http://www.w3.org/2005/sparql-results#'
		xmlns:str="http://exslt.org/strings">
  <xsl:import href="uri.xsl"/>
  <xsl:variable name="dokumenturi" select="//*/@about"/>
  <xsl:output method="xml" encoding="utf-8"/>
  <xsl:template match="*[@typeof='rinfo:Paragraf']">
    <xsl:copy>
      <xsl:apply-templates select="@*|node()"/>
      <xsl:variable name="paragrafuri" select="concat($dokumenturi,'#', @id)"/>
      <xsl:variable name="query">
PREFIX dct:&lt;http://purl.org/dc/terms/&gt;

SELECT DISTINCT ?source WHERE 
{
  {
    ?source dct:references &lt;<xsl:value-of select="$paragrafuri"/>&gt;
  } 
  UNION
  { 
    ?source dct:references ?target . 
    ?target dct:isPartOf &lt;<xsl:value-of select="$paragrafuri"/>&gt;
  } 
  UNION
  { 
    ?source dct:references ?target .
    ?target dct:isPartOf ?container . 
    ?container dct:isPartOf &lt;<xsl:value-of select="$paragrafuri"/>&gt;
  }
}
ORDER BY ?source
      </xsl:variable>
      <xsl:variable name="references-url">http://localhost/openrdf-sesame/repositories/lagen.nu?query=<xsl:value-of select="$query"/></xsl:variable>
      <!--
      <xsl:message>Query: <xsl:value-of select="$query"/></xsl:message>
      <xsl:message>Fetching <xsl:value-of select="str:encode-uri($references-url, false())"/></xsl:message>
      -->
      <xsl:variable name="results" select="document(str:encode-uri($references-url,false()))/sparql:sparql/sparql:results/sparql:result"/>
      <xsl:if test="$results">
	<ul class="backlinks">
	  <xsl:for-each select="$results">
	    <xsl:variable name="url">
	      <xsl:call-template name="localurl">
		<xsl:with-param name="uri" select="sparql:binding/sparql:uri"/>
	      </xsl:call-template>
	    </xsl:variable>
	    <li><a href="https://lagen.nu{$url}"><xsl:value-of select="substring-after(sparql:binding/sparql:uri,'/publ/sfs/')"/></a></li>
	  </xsl:for-each>
	</ul>
      </xsl:if>
    </xsl:copy>
  </xsl:template>

  <xsl:template match="*[@class='contentinfo']"/>

  <xsl:template match="@*|node()">
    <xsl:copy>
      <xsl:apply-templates select="@*|node()"/>
    </xsl:copy>
  </xsl:template>
  
</xsl:stylesheet>