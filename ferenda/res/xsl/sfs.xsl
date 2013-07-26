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
  <xsl:import href="accordion.xsl"/>
  <xsl:import href="tune-width.xsl"/>
  <xsl:include href="base.xsl"/>
  
  <xsl:variable name="dokumenturi" select="/xht2:html/@xml:base"/>

  <xsl:variable name="docmetadata">
    <dl id="refs-dokument">
      <dt>Departement</dt>
      <dd rel="dct:creator" resource="{//xht2:dd[@rel='dct:creator']/@href}"><xsl:value-of select="//xht2:dd[@rel='dct:creator']"/></dd>
      <dt>Utfärdad</dt>
      <dd property="rinfo:utfardandedatum" datatype="xsd:date"><xsl:value-of select="//xht2:dd[@property='rinfo:utfardandedatum']"/></dd>
      <dt>Ändring införd</dt>
      <dd rel="rinfo:konsolideringsunderlag" href="{//xht2:dd[@rel='rinfo:konsolideringsunderlag']/@href}"><xsl:value-of select="//xht2:dd[@rel='rinfo:konsolideringsunderlag']"/></dd>
      <xsl:if test="//xht2:dd[@property='rinfoex:tidsbegransad']">
	<dt>Tidsbegränsad</dt>
	<dd property="rinfoex:tidsbegransad"><xsl:value-of select="//xht2:dd[@property='rinfoex:tidsbegransad']"/></dd>
      </xsl:if>
      <dt>Källa</dt>
      <dd rel="dct:publisher" resource="http://lagen.nu/org/2008/regeringskansliet"><a href="http://62.95.69.15/cgi-bin/thw?%24%7BHTML%7D=sfst_lst&amp;%24%7BOOHTML%7D=sfst_dok&amp;%24%7BSNHTML%7D=sfst_err&amp;%24%7BBASE%7D=SFST&amp;%24%7BTRIPSHOW%7D=format%3DTHW&amp;BET={//xht2:dd[@property='rinfo:fsNummer']}">Regeringskansliets rättsdatabaser</a></dd>
      <dt>Senast hämtad</dt>
      <dd property="rinfoex:senastHamtad" datatype="xsd:date"><xsl:value-of select="//xht2:meta[@property='rinfoex:senastHamtad']/@content"/></dd>
      <xsl:if test="//xht2:dd[@property='rdfs:comment']">
	<dt>Övrigt</dt>
	<dd property="rdfs:comment"><xsl:value-of select="//xht2:dd[@property='rdfs:comment']"/></dd>
      </xsl:if>
    </dl>
  </xsl:variable>
  
  <!-- Implementationer av templates som anropas från base.xsl -->
  <xsl:template name="headtitle">
    <xsl:value-of select="//xht2:title"/>
    <xsl:if test="//xht2:meta[@property='dct:alternate']/@content">
      (<xsl:value-of select="//xht2:meta[@property='dct:alternate']/@content"/>)
    </xsl:if> | Lagen.nu
  </xsl:template>

  <xsl:template name="metarobots"/>

  <xsl:template name="linkalternate">
    <link rel="alternate" type="text/plain" title="Plain text">
      <xsl:attribute name="href">/<xsl:value-of select="//xht2:meta[@property='rinfo:fsNummer']/@content"/>.txt</xsl:attribute>
    </link>
    <link rel="alternate" type="application/xml" title="XHTML2">
      <xsl:attribute name="href">/<xsl:value-of select="//xht2:meta[@property='rinfo:fsNummer']/@content"/>.xht2</xsl:attribute>
    </link>
  </xsl:template>

  <xsl:template name="headmetadata"/>

  <xsl:template match="xht2:dl[@role='contentinfo']"/>
  
  <xsl:template match="xht2:section[@role='main']">
    <xsl:variable name="rattsfall" select="$annotations/rdf:Description[@rdf:about=$dokumenturi]/rinfo:isLagrumFor/rdf:Description"/>
    <xsl:variable name="kommentar" select="$annotations/rdf:Description[@rdf:about=$dokumenturi]/dct:description/xht2:div/*"/>
    <div class="konsolideradtext">
      <table>
	<tr>
	  <td width="50%">
	    <h1 property="dct:title"><xsl:value-of select="//xht2:h[@property = 'dct:title']"/></h1>
	    <xsl:copy-of select="$docmetadata"/>
	    
	    <xsl:if test="../xht2:dl[@role='contentinfo']/xht2:dd[@rel='rinfoex:upphavdAv']">
	      <div class="ui-state-error">
		<span class="ui-icon ui-icon-alert" style="float: left;margin-right:.3em;"/>
		OBS: Författningen har upphävts/ska upphävas <xsl:value-of
		select="../xht2:dl[@role='contentinfo']/xht2:dd[@property='rinfoex:upphavandedatum']"/>
		genom SFS <xsl:value-of
		select="../xht2:dl[@role='contentinfo']/xht2:dd[@rel='rinfoex:upphavdAv']"/>
	      </div>
	    </xsl:if>

	    <xsl:if test="../xht2:dl[@role='contentinfo']/xht2:dd[@property='rinfoex:patchdescription']">
	      <div class="ui-state-highlight">
		<span class="ui-icon ui-icon-info" style="float: left;margin-right:.3em;"/>
		Texten har ändrats jämfört med ursprungsmaterialet:
		<xsl:value-of
		    select="../xht2:dl[@role='contentinfo']/xht2:dd[@property='rinfoex:patchdescription']"/>
	      </div>
	    </xsl:if>
	    
	  </td>
	  <td class="aux">
	    <xsl:if test="$kommentar or $rattsfall">
	      <div class="ui-accordion">
		<xsl:if test="$kommentar">
		  <xsl:call-template name="accordionbox">
		    <xsl:with-param name="heading">Kommentar</xsl:with-param>
		    <xsl:with-param name="contents">
		      <p class="ui-state-highlight">
			<span class="ui-icon ui-icon-info" style="float: left; margin-right: .3em;"></span> 
			Var kommer de här kommentarerna från? <a href="/om/ansvarsfriskrivning.html">Läs mer...</a>
			
		      </p>
		      <xsl:apply-templates select="$kommentar"/>
		      <p class="ui-state-highlight" style="padding:2pt; margin:0">
			Hittar du något fel i lagkommentaren? Du får gärna <a href="/w/index.php?title=Diskussion:SFS/{//xht2:dd[@property='rinfo:fsNummer']}&amp;action=edit&amp;section=new&amp;preloadtitle=Felrapport&amp;editintro=Lagen.nu:Editintro/Felrapport">skriva en felrapport</a>.
		      </p>
		      
		    </xsl:with-param>
		    <xsl:with-param name="first" select="true()"/>
		  </xsl:call-template>
		</xsl:if>
		<xsl:if test="$rattsfall">
		  <xsl:call-template name="accordionbox">
		    <xsl:with-param name="heading">Rättsfall (<xsl:value-of select="count($rattsfall)"/>)</xsl:with-param>
		    <xsl:with-param name="contents">
		      <xsl:call-template name="rattsfall">
			<xsl:with-param name="rattsfall" select="$rattsfall"/>
		      </xsl:call-template>
		    </xsl:with-param>
		    <xsl:with-param name="first" select="not($kommentar)"/>
		  </xsl:call-template>
		</xsl:if>
	      </div>
	    </xsl:if>
	  </td>
	</tr>
	<xsl:apply-templates/>
      </table>
    </div>
  </xsl:template>

  <xsl:template match="xht2:h">
    <xsl:choose>
      <xsl:when test="@property = 'dct:title'"/><!-- main title is handled in another template -->
      <!--
      <xsl:when test="../@typeof = 'rinfo:Bilaga'">
	<h2>
	  <xsl:attribute name="id"><xsl:value-of select="../@id" /></xsl:attribute>
	  <xsl:value-of select="."/>
	</h2>
      </xsl:when>
      -->
      <xsl:when test="@class = 'underrubrik'">
	<tr class="heading-table-row">
	  <td>
	    <h3><xsl:for-each select="@*">
	      <xsl:attribute name="{name()}"><xsl:value-of select="." /></xsl:attribute>
	    </xsl:for-each><xsl:value-of select="."/></h3>
	  </td>
	  <td></td>
	</tr>
      </xsl:when>
      <xsl:otherwise>
	<tr>
	  <td>
	    <h2><xsl:for-each select="@*">
	      <xsl:attribute name="{name()}"><xsl:value-of select="." /></xsl:attribute>
	    </xsl:for-each><xsl:value-of select="."/></h2>
	  </td>
	  <td></td>
	</tr>
      </xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <xsl:template match="xht2:a|a">
    <xsl:call-template name="link"/>
  </xsl:template>

  <xsl:template match="xht2:section">
    <tr>
      <td>
	<xsl:if test="@id">
	  <xsl:attribute name="id"><xsl:value-of select="@id"/></xsl:attribute>
	  <xsl:attribute name="about"><xsl:value-of select="//xht2:html/@about"/>#<xsl:value-of select="@id"/></xsl:attribute>
	</xsl:if>
	<xsl:if test="@class">
	  <xsl:attribute name="class"><xsl:value-of select="@class"/></xsl:attribute>
	</xsl:if>
	<xsl:apply-templates/>
      </td>
      <td></td>
    </tr>
  </xsl:template>

  <xsl:template match="xht2:h[@class='kapitelrubrik']">
    <tr>
      <td>
	<h2><xsl:for-each select="@*">
	  <xsl:attribute name="{name()}"><xsl:value-of select="." /></xsl:attribute>
	</xsl:for-each><xsl:value-of select="."/></h2>
      </td>
      <td class="aux" id="refs-{../@id}">
	<xsl:variable name="kapiteluri" select="concat($dokumenturi,'#', ../@id)"/>
	<xsl:variable name="kommentar" select="$annotations/rdf:Description[@rdf:about=$kapiteluri]/dct:description/xht2:div/*"/>
	<xsl:if test="$kommentar">
	  <div class="ui-accordion">
	    <xsl:call-template name="accordionbox">
	      <xsl:with-param name="heading">Kommentar<a style="display:inline" title="Var kommer  de här kommentarerna från? Läs mer..." href="/om/ansvarsfriskrivning.html"><span class="ui-icon ui-icon-info" style="right: 0.5em; left: auto;"></span></a></xsl:with-param>
	      <xsl:with-param name="contents">
		<xsl:apply-templates select="$kommentar"/>
	      </xsl:with-param>
	      <xsl:with-param name="first" select="true()"/>
	    </xsl:call-template>
	  </div>
	</xsl:if>
      </td>
    </tr>
  </xsl:template>

  
  <xsl:template match="xht2:section[@typeof='rinfo:Paragraf']">
    <!-- plocka fram referenser kring/till denna paragraf -->
    <xsl:variable name="paragrafuri" select="concat($dokumenturi,'#', @id)"/>
    <xsl:variable name="rattsfall" select="$annotations/rdf:Description[@rdf:about=$paragrafuri]/rinfo:isLagrumFor/rdf:Description"/>
    <xsl:variable name="inbound" select="$annotations/rdf:Description[@rdf:about=$paragrafuri]/dct:references"/>
    <xsl:variable name="kommentar" select="$annotations/rdf:Description[@rdf:about=$paragrafuri]/dct:description/xht2:div/*"/>
    <xsl:variable name="inford" select="$annotations/rdf:Description[@rdf:about=$paragrafuri]/rinfo:isEnactedBy"/>
    <xsl:variable name="andrad" select="$annotations/rdf:Description[@rdf:about=$paragrafuri]/rinfo:isChangedBy"/>
    <xsl:variable name="upphavd" select="$annotations/rdf:Description[@rdf:about=$paragrafuri]/rinfo:isRemovedBy"/>
    <tr>
      <td class="paragraf" id="{@id}" about="{//xht2:html/@about}#{@id}">
	<xsl:apply-templates mode="in-paragraf"/>
      </td>
      <td id="refs-{@id}" class="aux">
	<xsl:if test="$kommentar or $rattsfall or $inbound or $inford or $andrad or $upphavd">
	  <div class="ui-accordion">
	    <!-- KOMMENTARER -->
	    <xsl:if test="$kommentar">
	      <xsl:call-template name="accordionbox">
		<xsl:with-param name="heading">Kommentar<a style="display:inline" title="Var kommer  de här kommentarerna från? Läs mer..." href="/om/ansvarsfriskrivning.html"><span class="ui-icon ui-icon-info" style="right: 0.5em; left: auto;"></span></a></xsl:with-param>
		<xsl:with-param name="contents"><xsl:apply-templates select="$kommentar"/></xsl:with-param>
		<xsl:with-param name="first" select="true()"/>
	      </xsl:call-template>
	    </xsl:if>
	    
	    <!-- RÄTTSFALL -->
	    <xsl:if test="$rattsfall">
	      <xsl:call-template name="accordionbox">
		<xsl:with-param name="heading">Rättsfall (<xsl:value-of select="count($rattsfall)"/>)</xsl:with-param>
		<xsl:with-param name="contents">
		  <xsl:call-template name="rattsfall">
		    <xsl:with-param name="rattsfall" select="$rattsfall"/>
		  </xsl:call-template>
		</xsl:with-param>
		<xsl:with-param name="first" select="not($kommentar)"/>
	      </xsl:call-template>
	    </xsl:if>
	    
	    <!-- LAGRUMSHÄNVISNINGAR -->
	    <xsl:if test="$inbound">
	      <xsl:call-template name="accordionbox">
		<xsl:with-param name="heading">Lagrumshänvisningar hit (<xsl:value-of select="count($inbound/rdf:Description)"/>)</xsl:with-param>
		<xsl:with-param name="contents">
		  <xsl:call-template name="inbound">
		    <xsl:with-param name="inbound" select="$inbound"/>
		  </xsl:call-template>
		</xsl:with-param>
		<xsl:with-param name="first" select="not($kommentar or $rattsfall)"/>
	      </xsl:call-template>
	    </xsl:if>
	    
	    <!-- ÄNDRINGAR -->
	    <xsl:if test="$inford or $andrad or $upphavd">
	      <xsl:call-template name="accordionbox">
		<xsl:with-param name="heading">Ändringar/Förarbeten (<xsl:value-of select="count($inford)+count($andrad)+count($upphavd)"/>)</xsl:with-param>
		<xsl:with-param name="contents">
		  <xsl:call-template name="andringsnoteringar">
		    <xsl:with-param name="typ" select="'Införd'"/>
		    <xsl:with-param name="andringar" select="$inford"/>
		  </xsl:call-template>
		  <xsl:call-template name="andringsnoteringar">
		    <xsl:with-param name="typ" select="'Ändrad'"/>
		    <xsl:with-param name="andringar" select="$andrad"/>
		  </xsl:call-template>
		  <xsl:call-template name="andringsnoteringar">
		    <xsl:with-param name="typ" select="'Upphävd'"/>
		    <xsl:with-param name="andringar" select="$upphavd"/>
		  </xsl:call-template>
		</xsl:with-param>
		<xsl:with-param name="first" select="not($kommentar or $rattsfall or $inbound)"/>
	      </xsl:call-template>
	    </xsl:if>
	  </div>
	</xsl:if>
      </td>
    </tr>
  </xsl:template>

  <xsl:template match="xht2:p[@typeof='rinfo:Stycke']">
    <tr>
      <td><xsl:apply-templates mode="in-paragraf"/></td>
      <td><!-- here goes the boxes for commentary etc, but that's not supported for standalone Stycke nodes yet --></td>
    </tr>
  </xsl:template>


  <xsl:template match="xht2:p[@typeof='rinfo:Stycke']" mode="in-paragraf">
    <xsl:variable name="marker">
      <xsl:choose>
	<xsl:when test="substring-after(@id,'S') = '1'"><xsl:if
	test="substring-after(@id,'K')">K<xsl:value-of
	select="substring-before(substring-after(@id,'K'),'P')"/></xsl:if></xsl:when>
	<xsl:otherwise>S<xsl:value-of select="substring-after(@id,'S')"/></xsl:otherwise>
      </xsl:choose>
    </xsl:variable>
    <p id="{@id}" about="{//xht2:html/@about}#{@id}">
      <xsl:if test="$marker != ''">
	<a href="#{@id}" title="Permalänk till detta stycke"><img class="platsmarkor" src="img/{$marker}.png"/></a>
      </xsl:if>
      <xsl:if test="xht2:span[@class='paragrafbeteckning']">
	<a href="#{@id}" class="paragrafbeteckning" title="Permalänk till detta stycke"><xsl:copy-of select="xht2:span[@class='paragrafbeteckning']"/></a>
      </xsl:if>
      <xsl:apply-templates/>
    </p>
  </xsl:template>
  
  <xsl:template name="andringsnoteringar">
    <xsl:param name="typ"/>
    <xsl:param name="andringar"/>
    <xsl:if test="$andringar">
      <xsl:value-of select="$typ"/>: SFS
      <xsl:for-each select="$andringar">
	<a href="#L{concat(substring-before(rinfo:fsNummer,':'),'-',substring-after(rinfo:fsNummer,':'))}"><xsl:value-of select="rinfo:fsNummer"/></a><xsl:if test="position()!= last()">, </xsl:if>
      </xsl:for-each>
      <br/>
    </xsl:if>
  </xsl:template>

  <xsl:template name="rattsfall">
    <xsl:param name="rattsfall"/>
      <xsl:for-each select="$rattsfall">
	<xsl:sort select="@rdf:about"/>
	<xsl:variable name="tuned-width">
	  <xsl:call-template name="tune-width">
	    <xsl:with-param name="txt" select="dct:description"/>
	    <xsl:with-param name="width" select="80"/>
	    <xsl:with-param name="def" select="80"/>
	  </xsl:call-template>
	</xsl:variable>
	<xsl:variable name="localurl"><xsl:call-template name="localurl"><xsl:with-param name="uri" select="@rdf:about"/></xsl:call-template></xsl:variable>
	<a href="{$localurl}"><b><xsl:value-of select="dct:identifier"/></b></a>:
	<xsl:choose>
	  <xsl:when test="string-length(dct:description) > 80">
	    <xsl:value-of select="normalize-space(substring(dct:description, 1, $tuned-width - 1))" />...
	  </xsl:when>
	  <xsl:otherwise>
	    <xsl:value-of select="dct:description"/>
	  </xsl:otherwise>
	</xsl:choose>
	<br/>
      </xsl:for-each>
  </xsl:template>

  <xsl:template name="inbound">
    <xsl:param name="inbound"/>
    <ul class="lagrumslista">
      <xsl:for-each select="$inbound">
	<li>
	  <xsl:for-each select="rdf:Description">
	    <xsl:if test="./dct:identifier != ''">
	      <xsl:variable name="localurl"><xsl:call-template name="localurl"><xsl:with-param name="uri" select="@rdf:about"/></xsl:call-template></xsl:variable>
	      <a href="{$localurl}"><xsl:value-of select="dct:identifier"/></a><xsl:if test="position()!=last()">, </xsl:if>
	    </xsl:if>
	  </xsl:for-each>
	</li>
      </xsl:for-each>
    </ul>
  </xsl:template>

  <xsl:template match="xht2:section[@role='secondary']">
    <div class="andringar"><xsl:apply-templates/></div>
  </xsl:template>

  <xsl:template match="xht2:section[@role='secondary']/xht2:section">
    <xsl:variable name="year" select="substring-before(xht2:dl/xht2:dd[@property='rinfo:fsNummer'],':')"/>
    <xsl:variable name="nr" select="substring-after(xht2:dl/xht2:dd[@property='rinfo:fsNummer'],':')"/>
    <div class="andring" id="{concat(substring-before(@id,':'),'-',substring-after(@id,':'))}" about="{@about}">
      <!-- titel eller sfsnummer, om ingen titel finns -->
      <h2><xsl:choose>
	<xsl:when test="xht2:dl/xht2:dd[@property='dct:title']">
	  <xsl:value-of select="xht2:dl/xht2:dd[@property='dct:title']"/>
	</xsl:when>
	<xsl:otherwise>
	  <xsl:value-of select="xht2:dl/xht2:dd[@property='rinfo:fsNummer']"/>
	</xsl:otherwise>
      </xsl:choose></h2>
      <xsl:if test="(number($year) > 1998) or (number($year) = 1998 and number($nr) >= 306)">

	<p><a href="http://62.95.69.3/SFSdoc/{substring($year,3,2)}/{substring($year,3,2)}{format-number($nr,'0000')}.PDF">Officiell version (PDF)</a></p>
      </xsl:if>
      <xsl:apply-templates mode="in-paragraf"/>
    </div>
  </xsl:template>


  <!-- emit nothing - this is already handled above -->
  <xsl:template match="xht2:span[@class='paragrafbeteckning']"/>
  
  <!-- FIXME: in order to be valid xhtml1, we must remove unordered
       lists from within paragraphs, and place them after the
       paragraph. This turns out to be tricky in XSLT, the following
       is a non-working attempt -->
  <!--
  <xsl:template match="xht2:p">
    <p>
      <xsl:if test="@id">
	<xsl:attribute name="id"><xsl:value-of select="@id"/></xsl:attribute>
      </xsl:if>
      <xsl:for-each select="text()|*">
	<xsl:if test="not(name()='ul')">
	  <xsl:element name="XX{name()}">
	    <xsl:apply-templates select="text()|*"/>
	  </xsl:element>
	</xsl:if>
	<xsl:if test="not(name(node()[1]))">
	  TXT:<xsl:value-of select="."/>END
	</xsl:if>
      </xsl:for-each>
    </p>
    <xsl:if test="ul">
      <xsl:apply-templates select="ul"/>
    </xsl:if>
  </xsl:template>
  -->
  
  <!-- defaultregler: översätt allt från xht2 till xht1-namespace, men inga ändringar i övrigt
  -->
  <xsl:template match="*">
    <xsl:element name="{name()}">
      <xsl:apply-templates select="@*|node()"/>
    </xsl:element>
  </xsl:template>
  <xsl:template match="@*">
    <xsl:copy><xsl:apply-templates/></xsl:copy>
  </xsl:template>


  <xsl:template match="xht2:a|a" mode="in-paragraf">
    <xsl:call-template name="link"/>
  </xsl:template>
  <xsl:template match="*" mode="in-paragraf">
    <xsl:element name="{name()}">
      <xsl:apply-templates select="@*|node()" mode="in-paragraf"/>
    </xsl:element>
  </xsl:template>
  <xsl:template match="@*" mode="in-paragraf">
    <xsl:copy><xsl:apply-templates/></xsl:copy>
  </xsl:template>

  
  <!-- TABLE OF CONTENTS (TOC) HANDLING -->
  <xsl:template match="h[@property = 'dct:title']" mode="toc">
    <xsl:call-template name="toc"/>
  </xsl:template>


  <xsl:template name="toc">
    <ul id="toc">
      <li><h2>Innehållsförteckning</h2>
      <ul>
	<xsl:apply-templates select="//xht2:section[@role='main']" mode="toc"/>
      </ul>
      </li>
    </ul>
  </xsl:template>

  <xsl:template match="xht2:section[@typeof='rinfo:Avdelning']" mode="toc">
    <li class="toc-avdelning"><a href="#{@id}"><xsl:value-of select="xht2:h[@class='avdelningsrubrik']"/>: <xsl:value-of select="xht2:h[@class='avdelningsunderrubrik']"/></a>
    <ul><xsl:apply-templates mode="toc"/></ul>
    </li>
  </xsl:template>

  <xsl:template match="xht2:section[@typeof='rinfo:Kapitel']" mode="toc">
    <li class="toc-kapitel"><a href="#{@id}"><xsl:value-of select="xht2:h[@class='kapitelrubrik']"/></a>
    <xsl:if test="xht2:h[@id]">
      <ul><xsl:apply-templates mode="toc"/></ul>
    </xsl:if>
    </li>
  </xsl:template>

  <xsl:template match="xht2:h[@property='dct:title']" mode="toc">
    <!--<li>Not emitting title</li>-->
  </xsl:template>

  <xsl:template match="xht2:h[@class='kapitelrubrik']" mode="toc">
    <!--<li>Not emitting kapitelrubrik</li>-->
  </xsl:template>
  <xsl:template match="xht2:h[@class='avdelningsrubrik']" mode="toc">
    <!--<li>Not emitting kapitelrubrik</li>-->
  </xsl:template>
  <xsl:template match="xht2:h[@class='avdelningsunderrubrik']" mode="toc">
    <!--<li>Not emitting kapitelrubrik</li>-->
  </xsl:template>
  
  <xsl:template match="xht2:h" mode="toc">
    <li class="toc-rubrik"><a href="#{@id}"><xsl:value-of select="."/></a>
    <!-- for proper handling of underrubriker
	 select ../xht2:h,
         loop until this headline is found (identify by id),
         then output a li for each xht2:h[@class='underrubrik']
	 until a regular headline is found
    -->
    </li>
  </xsl:template>

  <xsl:template match="xht2:h[@class='underrubrik']" mode="toc">
    <li class="toc-underrubrik"><a href="#{@id}"><xsl:value-of select="."/></a></li>
  </xsl:template>

  <xsl:template match="xht2:section[@typeof='rinfo:Bilaga']" mode="toc">
    <li class="toc-bilaga"><a href="#{@id}"><xsl:value-of select="xht2:h"/></a></li>
  </xsl:template>

  <!-- filter the rest -->
  <xsl:template match="xht2:dl[@role='contentinfo']" mode="toc">
    <!-- emit nothing -->
  </xsl:template>
  <xsl:template match="xht2:section[@role='secondary']" mode="toc">
    <!-- emit nothing -->
  </xsl:template>
  <xsl:template match="xht2:p" mode="toc">
    <!-- emit nothing -->
  </xsl:template>
  <xsl:template match="xht2:span" mode="toc">
    <!-- emit nothing -->
  </xsl:template>
  <xsl:template match="xht2:section[@class='upphavd']" mode="toc">
    <!-- emit nothing -->
  </xsl:template>


</xsl:stylesheet>
