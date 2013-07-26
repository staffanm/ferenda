<?xml version="1.0" encoding="utf-8"?>
<!-- den här XSL-filen är ganska atypisk - kolla hellre på DV.xsl -->
<xsl:stylesheet version="1.0"
		xmlns="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
		xmlns:xht2="http://www.w3.org/2002/06/xhtml2/"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xmlns:dct="http://purl.org/dc/terms/"
		xmlns:str="http://exslt.org/strings"
		exclude-result-prefixes="xht2 dct rdf">

  <xsl:import href="uri.xsl"/>
  <xsl:import href="accordion.xsl"/>
  <xsl:include href="base.xsl"/>

  <xsl:variable name="dokumenturi" select="/xht2:html/@xml:base"/>

  <!-- Implementationer av templates som anropas från base.xsl -->
  <xsl:template name="headtitle">
    <xsl:value-of select="//xht2:title"/> - om begreppet | Lagen.nu
  </xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>

  <xsl:template match="xht2:h[@property='dct:title']">
    <xsl:variable name="wikidesc" select="$annotations/rdf:Description/dct:description/xht2:div/*"/>
    <xsl:variable name="legaldefs" select="$annotations/rdf:Description/rinfoex:isDefinedBy/*"/>
    <xsl:variable name="rattsfall" select="$annotations/rdf:Description/dct:subject/rdf:Description"/>
    <xsl:variable name="wikipedia" select="//xht2:p[@class='wikibox']"/>

    <xsl:comment>
      <xsl:variable name="ws" select="'&#x20;&#xD;&#xA;&#x9;'"/>
      Score: <xsl:value-of select="count(str:tokenize(string($annotations/rdf:Description/dct:description/xht2:div), $ws)) + count($rattsfall) + 5*count($legaldefs)+5*count($wikipedia)"/>
      Word Count: <xsl:value-of select="count(str:tokenize(string($annotations/rdf:Description/dct:description/xht2:div), $ws))"/>
      Rattsfall: <xsl:value-of select="count($rattsfall)"/>
      Legaldefs: <xsl:value-of select="count($legaldefs)"/>
      Wikipedia: <xsl:value-of select="count($wikipedia)"/>
      
    </xsl:comment>
    
    <table>
      <tr>
	<td width="50%">
	  <h1 property="dct:title"><xsl:value-of select="."/></h1>
	  <xsl:if test="$wikidesc">
	    <p class="ui-state-highlight">
	      <span class="ui-icon ui-icon-info" style="float: left; margin-right: .3em;"></span> 
	      Var kommer den här beskrivningen från? <a href="/om/ansvarsfriskrivning.html">Läs mer...</a>.
	    </p>

	    <xsl:apply-templates select="$wikidesc"/>

	    <p class="ui-state-highlight" style="padding:2pt;">
	      Hittar du något fel i ovanstående? Du får gärna <a href="/w/index.php?title=Diskussion:{.}&amp;action=edit&amp;section=new&amp;preloadtitle=Felrapport&amp;editintro=Lagen.nu:Editintro/Felrapport">skriva en felrapport</a>.
	    </p>
	  </xsl:if>
	  <xsl:if test="not($wikidesc)">
	    <p class="ui-state-highlight" style="padding:2pt;">
	      Ingen har skrivit en beskrivning av "<xsl:value-of select="."/>" än. Vill du göra det? <a href="/w/index.php?title=Diskussion:{.}&amp;action=edit&amp;section=new&amp;preloadtitle=Förslag&amp;editintro=Lagen.nu:Editintro/Förslag">Skriv gärna ett förslag!</a>
	    </p>
	  </xsl:if>
	</td>
	<td class="aux">
	  <div class="ui-accordion">
	    <xsl:if test="$wikipedia">
	      <xsl:call-template name="accordionbox">
		<xsl:with-param name="heading">Externa länkar</xsl:with-param>
		<xsl:with-param name="contents" select="$wikipedia"/>
	      </xsl:call-template>
	    </xsl:if>
	    
	    <xsl:if test="$legaldefs">
	      <xsl:call-template name="accordionbox">
		<xsl:with-param name="heading">Legaldefinitioner (<xsl:value-of select="count($legaldefs)"/>)</xsl:with-param>
		<xsl:with-param name="contents">
		  <xsl:for-each select="$legaldefs">
		    <xsl:sort select="@rdf:about"/>
		    <xsl:variable name="localurl"><xsl:call-template name="localurl"><xsl:with-param name="uri" select="@rdf:about"/></xsl:call-template></xsl:variable>
		    <a href="{$localurl}"><xsl:value-of select="rdfs:label"/></a><br/>
		  </xsl:for-each>
		</xsl:with-param>
	      </xsl:call-template>
	    </xsl:if>
	    
	    <xsl:if test="$rattsfall">
	      <xsl:call-template name="accordionbox">
		<xsl:with-param name="heading">Rättsfall med detta begrepp (<xsl:value-of select="count($rattsfall)"/>)</xsl:with-param>
		<xsl:with-param name="contents">
		  <xsl:call-template name="rattsfall">
		    <xsl:with-param name="rattsfall" select="$rattsfall"/>
		  </xsl:call-template>
		</xsl:with-param>
	      </xsl:call-template>
	    </xsl:if>

	  </div>
	</td>
      </tr>
    </table>
  </xsl:template>

  <xsl:template match="xht2:p[@class='wikibox']"/>

  <xsl:template match="xht2:a|a">
    <xsl:call-template name="link">
    </xsl:call-template>
  </xsl:template>

  <!-- defaultregel: kopierar alla element från xht2 till
       default-namespacet -->
  <xsl:template match="xht2:*|*">
    <xsl:element name="{name()}">
      <xsl:apply-templates select="@*|node()"/>
    </xsl:element>
  </xsl:template>

  <xsl:template match="@*">
    <xsl:copy><xsl:apply-templates/></xsl:copy>
  </xsl:template>

  <xsl:template name="rattsfall">
    <xsl:param name="rattsfall"/>
      <xsl:for-each select="$rattsfall">
	<xsl:sort select="@rdf:about"/>
	<xsl:variable name="tuned-width">
	  <xsl:call-template name="tune-width">
	    <xsl:with-param name="txt" select="dct:description"/>
	    <xsl:with-param name="width" select="200"/>
	    <xsl:with-param name="def" select="200"/>
	  </xsl:call-template>
	</xsl:variable>
	<xsl:variable name="localurl"><xsl:call-template name="localurl"><xsl:with-param name="uri" select="@rdf:about"/></xsl:call-template></xsl:variable>
	<a href="{$localurl}"><b><xsl:value-of select="dct:identifier"/></b></a>:
	<xsl:choose>
	  <xsl:when test="string-length(dct:description) > 200">
	    <xsl:value-of select="normalize-space(substring(dct:description, 1, $tuned-width - 1))" />...
	  </xsl:when>
	  <xsl:otherwise>
	    <xsl:value-of select="dct:description"/>
	  </xsl:otherwise>
	</xsl:choose>
	<br/>
      </xsl:for-each>
  </xsl:template>

  <xsl:template match="*|@*" mode="toc">
    <!-- emit nothing -->
  </xsl:template>

</xsl:stylesheet>

