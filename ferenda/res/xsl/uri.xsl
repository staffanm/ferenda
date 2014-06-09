<?xml version="1.0" encoding="ISO-8859-1"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:skos="http://www.w3.org/2008/05/skos#"
		xmlns:str="http://exslt.org/strings"
		extension-element-prefixes="str">

  <xsl:import href="tune-width.xsl"/>
  <!-- this is a standalone template for formatting links that use
       rinfo-standard URI:s (mapping those abstract uris to concrete
       uris used by lagen.nu -->
  <xsl:template name="link">
    <xsl:param name="decorate"/>
    <xsl:param name="validate" select="true()"/>
  	<xsl:variable name="terms"></xsl:variable>
  	<xsl:variable name="lagkommentar"></xsl:variable>
    <xsl:variable name="uri" select="@href"/>
    <xsl:variable name="localurl">
      <xsl:call-template name="localurl">
	<xsl:with-param name="uri" select="$uri"/>
	<xsl:with-param name="validate" select="$validate"/>
      </xsl:call-template>
    </xsl:variable>
    <xsl:choose>
      <xsl:when test="contains($uri, '#L')">
	<span class="andringsnot" rel="dcterms:references" resource="{$uri}"><xsl:apply-templates/></span>
      </xsl:when>
      <xsl:when test="$localurl = ''">
	<xsl:value-of select="."/>
      </xsl:when>
      <xsl:otherwise>
	<xsl:variable name="rawtitle">
	  <xsl:choose>
	    <xsl:when test="substring($uri, 0, 25) = 'http://lagen.nu/concept/'"><xsl:value-of select="$terms//skos:Concept[@rdf:about=$uri]/dcterms:description"/></xsl:when>
	      <!-- FIXME: on large documents this increases processing
		   time exponentially. wonder if there's a faster XSLT way
		   to get a node with a particular ID? A better way would
		   be to get the information dynamically through an AJAX
		   call. As an added bonus, that would work with cross-law
		   links. But it requires that this information can be
		   dynamically queried, maybe if we store the actual
		   content of the texts in Sesame?  
	    <xsl:otherwise>
	      <xsl:value-of select="normalize-space(//*[@id=substring-after($uri,'#')])"/>
	    </xsl:otherwise>
	    -->
	  </xsl:choose>
	</xsl:variable>


	<xsl:variable name="title">
	  <xsl:choose>
	    <xsl:when test="string-length($rawtitle) > 150">
              <xsl:variable name="tuned-width">
                <xsl:call-template name="tune-width">
                  <xsl:with-param name="txt" select="$rawtitle"/>
                  <xsl:with-param name="width" select="150"/>
                  <xsl:with-param name="def" select="150"/>
                </xsl:call-template>
              </xsl:variable>
	      <xsl:value-of select="substring($rawtitle, 1, $tuned-width - 1)" />...</xsl:when>
	    <xsl:otherwise>
	      <xsl:value-of select="$rawtitle"/>
	    </xsl:otherwise>
	  </xsl:choose>
	</xsl:variable>

	<a href="{$localurl}"><xsl:if test="$title != ''"><xsl:attribute name="title"><xsl:value-of select="$title"/></xsl:attribute></xsl:if><xsl:if test="@class"><xsl:attribute name="class"><xsl:value-of select="@class"/></xsl:attribute></xsl:if><xsl:if test="@rel"><xsl:attribute name="rel"><xsl:value-of select="@rel"/></xsl:attribute></xsl:if><xsl:apply-templates/></a>
	<xsl:if test="$decorate">
	  <xsl:choose>
	    <xsl:when test="substring($uri, 0, 25) = 'http://lagen.nu/concept/'">
	      <xsl:if test="string-length($rawtitle) > 0">
		<img src="/img/comment.png" alt="Kommentar" title="Det här begreppet finns beskrivet"/>
	      </xsl:if>
	    </xsl:when>
	    <xsl:when test="contains($uri,'/publ/sfs')">
	      <xsl:if test="$lagkommentar//*[@rdf:about=$uri]">
		<img src="/img/comment.png" alt="Kommentar" title="Den här lagen är kommenterad"/>
	      </xsl:if>
	    </xsl:when>
	    <xsl:otherwise>
	    </xsl:otherwise>
	  </xsl:choose>
	</xsl:if>

      </xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <!-- maps an idealized rinfo resource URI to an actual retrievable
       URI, either at lagen.nu or somewhere else -->
  <xsl:template name="localurl">
    <xsl:param name="uri"/>
    <xsl:param name="validate"/>
  	<xsl:variable name="terms"></xsl:variable>
  	<xsl:variable name="rattsfall"></xsl:variable>
    <xsl:choose>
      <xsl:when test="substring($uri, 0, 25) = 'http://lagen.nu/concept/'">
	<xsl:choose>
	  <xsl:when test="$validate">
	    <xsl:if test="$terms//skos:Concept[@rdf:about=$uri]">/begrepp/<xsl:value-of select="substring-after($uri, '/concept/')"/></xsl:if>
	  </xsl:when>
	  <xsl:otherwise>/begrepp/<xsl:value-of select="substring-after($uri, '/concept/')"/></xsl:otherwise>
	</xsl:choose>
      </xsl:when>
      <xsl:when test="substring($uri, 0, 26) != 'http://rinfo.lagrummet.se'">
	<xsl:value-of select="$uri"/>
      </xsl:when>
      <xsl:when test="contains($uri,'/publ/sfs')">
	<xsl:value-of select="substring-after($uri, '/publ/sfs')"/>
      </xsl:when>
      <xsl:when test="contains($uri,'/publ/arn')">
	/arn<xsl:value-of select="substring-after($uri, '/publ/arn')"/>
      </xsl:when>
      <xsl:when test="contains($uri,'/publ/dir')">/dir<xsl:value-of select="substring-after($uri, '/publ/dir')"/></xsl:when>
      <xsl:when test="contains($uri,'/publ/rattsfall')">
	<!-- FIXME: this gets pretty slow when handling 100+ links in a document (DV.py/Indexpages) -->
	<xsl:choose>
	  <xsl:when test="$validate">
	    <xsl:if test="$rattsfall//rinfo:Rattsfallsreferat[@rdf:about=$uri]">/dom<xsl:value-of select="substring-after($uri, '/publ/rattsfall')"/></xsl:if>
	  </xsl:when>
	  <xsl:otherwise>/dom<xsl:value-of select="substring-after($uri, '/publ/rattsfall')"/></xsl:otherwise>
	</xsl:choose>
      </xsl:when>
      <xsl:when test="contains($uri,'/publ/prop')">
	<xsl:variable name="year" select="substring(substring-after($uri, '/publ/prop/'),1,4)"/>
	<xsl:variable name="nr" select="substring-after(substring-after($uri, '/publ/prop/'), ':')"/>
	<xsl:variable name="int">
	  <xsl:choose>
	    <!-- brutet år, exv 1975/76:24 -->
	    <xsl:when test="contains(substring-after($uri, '/publ/prop/'),'/')">
	      <xsl:value-of select="number($year)-1400"/>
	    </xsl:when>
	    <!-- kalenderår, exv 1975:6 -->
	    <xsl:otherwise>
	      <xsl:value-of select="number($year)-1401"/>
	    </xsl:otherwise>
	  </xsl:choose>
	</xsl:variable>
	<xsl:variable name="base36year">
	  <xsl:call-template name="base36">
	    <xsl:with-param name="int" select="$int"/>
	  </xsl:call-template>
	</xsl:variable>
	<xsl:if test="number($year) > 1970">http://www.riksdagen.se/Webbnav/index.aspx?nid=37&amp;dok_id=<xsl:value-of select="$base36year"/>03<xsl:value-of select="$nr"/></xsl:if>
      </xsl:when>
      <!--       <xsl:when test="contains($uri,'/ext/celex')">http://eurlex.nu/doc/<xsl:value-of select="substring-after($uri, '/ext/celex/')"/> -->
      <xsl:when test="contains($uri,'/ext/celex')">http://eur-lex.europa.eu/LexUriServ/LexUriServ.do?uri=CELEX:<xsl:value-of select="substring-after($uri, '/ext/celex/')"/>:SV:HTML
      </xsl:when>
      <xsl:when test="contains($uri,'/ext/bet/')">
	<xsl:variable name="year" select="substring(substring-after($uri, '/ext/bet/'),1,4)"/>
	<xsl:variable name="nr" select="substring-after(substring-after($uri, '/ext/bet/'), ':')"/>
	<xsl:variable name="int">
	  <xsl:value-of select="number($year)-1400"/>
	</xsl:variable>
	<xsl:variable name="base36year">
	  <xsl:call-template name="base36">
	    <xsl:with-param name="int" select="$int"/>
	  </xsl:call-template>
	</xsl:variable>
	<xsl:if test="number($year) > 1990">http://www.riksdagen.se/Webbnav/index.aspx?nid=37&amp;dok_id=<xsl:value-of select="$base36year"/>01<xsl:value-of select="$nr"/></xsl:if>
      </xsl:when>
      <xsl:when test="contains($uri,'/ext/celex')">http://eurlex.nu/doc/<xsl:value-of select="substring-after($uri, '/ext/celex/')"/>
      </xsl:when>
    </xsl:choose>            
  </xsl:template>

  <!-- converts an int to base36 -->
  <xsl:template name="base36">
    <xsl:param name="int"/>
    <xsl:variable name="digits">0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ</xsl:variable><xsl:value-of select="substring($digits,floor($int div 36) + 1,1)"/><xsl:value-of select="substring($digits, ($int mod 36 + 1),1)"/></xsl:template>
  
</xsl:stylesheet>
