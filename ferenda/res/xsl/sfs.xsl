<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rpubl="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		exclude-result-prefixes="xhtml rdf rpubl">

  <xsl:import href="uri.xsl"/>
  <xsl:import href="tune-width.xsl"/>
  <xsl:include href="base.xsl"/>
  
  <!-- Implementationer av templates som anropas från base.xsl -->
  <xsl:template name="headtitle">
    <xsl:value-of select="//xhtml:title"/>
    <xsl:if test="//xhtml:meta[@property='dcterms:alternate']/@content">
      (<xsl:value-of select="//xhtml:meta[@property='dcterms:alternate']/@content"/>)
    </xsl:if> | Lagen.nu
  </xsl:template>

  <xsl:template name="metarobots"/>

  <xsl:template name="linkalternate">
    <link rel="alternate" type="text/plain" href="{$documenturi}.txt" title="Plain text"/>
  </xsl:template>

  <xsl:template name="headmetadata"/>

  <xsl:template name="bodyclass">sfs</xsl:template>

  <xsl:variable name="documenturi" select="//xhtml:body/@about"/>
  <xsl:variable name="sfsannotations" select="document($annotationfile)/rdf:RDF"/>

  <xsl:template name="pagetitle">
    <xsl:message>pagetitle <xsl:value-of select="$documenturi"/></xsl:message>
    <xsl:variable name="rattsfall" select="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/rpubl:isLagrumFor/rdf:Description"/>
    <xsl:variable name="kommentar" select="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/dcterms:description/xhtml:div/*"/>
    <div class="section-wrapper toplevel">
      <section id="top">
	<h1><xsl:value-of select="../xhtml:head/xhtml:title"/></h1>
	<xsl:call-template name="docmetadata"/>
	<xsl:if test="../../xhtml:head/xhtml:meta[@rel='rinfoex:upphavdAv']">
	  <div class="ui-state-error">
	    <span class="ui-icon ui-icon-alert" style="float: left;margin-right:.3em;"/>
	    OBS: Författningen har upphävts/ska upphävas <xsl:value-of
	    select="../dl[@role='contentinfo']/dd[@rel='rinfoex:upphavandedatum']"/>
	    genom SFS <xsl:value-of
	    select="../../xhtml:head/meta[@rel='rinfoex:upphavdAv']"/>
	  </div>
	</xsl:if>
	<xsl:if test="//xhtml:meta[@property='rinfoex:patchdescription']/@content">
	  <div class="ui-state-highlight">
	    <span class="ui-icon ui-icon-info" style="float: left;margin-right:.3em;"/>
	    Texten har ändrats jämfört med ursprungsmaterialet:
	    <xsl:value-of
		select="//xhtml:meta[@property='rinfoex:patchdescription']/@content"/>
	  </div>
	</xsl:if>
      </section>
      <xsl:if test="$kommentar">
	<aside class="annotations kommentarer">
	  <h2>Kommentar<a style="display:inline" title="Var kommer  de här kommentarerna från? Läs mer..." href="/om/ansvarsfriskrivning.html"><span class="ui-icon ui-icon-info" style="right: 0.5em; left: auto;"></span></a></h2>
	  <xsl:apply-templates select="$kommentar"/>
	</aside>
      </xsl:if>
      <xsl:if test="$rattsfall">
	<aside class="annotations rattsfall">
	  <h2>Rättsfall (<xsl:value-of select="count($rattsfall)"/>)</h2>
	  <xsl:call-template name="rattsfall">
	    <xsl:with-param name="rattsfall" select="$rattsfall"/>
	  </xsl:call-template>
	</aside>
      </xsl:if>
    </div>
  </xsl:template>

  <xsl:template name="docmetadata">
    <dl id="refs-dokument">
      <dt>Departement</dt>
      <dd><xsl:value-of select="//xhtml:link[@rel='dcterms:creator']/@href"/></dd>
      <dt>Utfärdad</dt>
      <dd><xsl:value-of select="//xhtml:meta[@property='rpubl:utfardandedatum']/@content"/></dd>
      <dt>Ändring införd</dt>
      <dd><xsl:value-of select="//xhtml:meta[@property='dcterms:identifier']/@content"/></dd>
      <xsl:if test="//xhtml:meta[@property='rinfoex:tidsbegransad']/@content">
	<dt>Tidsbegränsad</dt>
	<dd><xsl:value-of select="//xhtml:meta[@property='rinfoex:tidsbegransad']/@content"/></dd>
      </xsl:if>
      <dt>Källa</dt>
      <dd rel="dcterms:publisher" resource="http://lagen.nu/org/2008/regeringskansliet"><a href="http://62.95.69.15/cgi-bin/thw?%24%7BHTML%7D=sfst_lst&amp;%24%7BOOHTML%7D=sfst_dok&amp;%24%7BSNHTML%7D=sfst_err&amp;%24%7BBASE%7D=SFST&amp;%24%7BTRIPSHOW%7D=format%3DTHW&amp;BET={//span[@property='rpubl:arsutgava'][1]}:{//span[@property='rpubl:lopnummer'][1]}">Regeringskansliets rättsdatabaser</a></dd>
      <dt>Senast hämtad</dt>
      <dd><xsl:value-of select="//xhtml:meta[@property='rinfoex:senastHamtad']/@content"/></dd>
      <xsl:if test="//xhtml:meta[@property='rdfs:comment']/@content">
	<dt>Övrigt</dt>
	<dd><xsl:value-of select="//xhtml:meta[@property='rdfs:comment']/@content"/></dd>
      </xsl:if>
    </dl>
  </xsl:template>
  
  <xsl:template match="xhtml:a">
    <xsl:call-template name="link"/>
  </xsl:template>

  <xsl:template match="xhtml:div">
    <tr>
      <td>
	<xsl:if test="@id">
	  <xsl:attribute name="id"><xsl:value-of select="@id"/></xsl:attribute>
	  <xsl:attribute name="about"><xsl:value-of select="//html/@about"/>#<xsl:value-of select="@id"/></xsl:attribute>
	</xsl:if>
	<xsl:if test="@class">
	  <xsl:attribute name="class"><xsl:value-of select="@class"/></xsl:attribute>
	</xsl:if>
	<xsl:apply-templates/>
      </td>
      <td></td>
    </tr>
  </xsl:template>

 
  <xsl:template match="xhtml:div[@typeof='rpubl:Paragraf']">
    <div class="section-wrapper" about="{//html/@about}#{@id}">
      <section id="{@id}">
	<xsl:apply-templates mode="in-paragraf"/>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
  </xsl:template>

  <xsl:template name="aside-annotations">
    <xsl:param name="uri"/>
    <!-- plocka fram referenser kring/till denna paragraf -->
    <xsl:variable name="rattsfall" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isLagrumFor/rdf:Description"/>
    <xsl:variable name="inbound" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/dcterms:references"/>
    <xsl:variable name="kommentar" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/dcterms:description/xhtml:div/*"/>
    <xsl:variable name="inford" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isEnactedBy"/>
    <xsl:variable name="andrad" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isChangedBy"/>
    <xsl:variable name="upphavd" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isRemovedBy"/>

    <xsl:if test="$kommentar">
      <aside class="annotations kommentarer">
	<h2>Kommentar</h2>
	<xsl:apply-templates select="$kommentar"/>
      </aside>
    </xsl:if>

    <xsl:if test="$rattsfall">
      <aside class="annotations rattsfall">
	<h2>Rättsfall (<xsl:value-of select="count($rattsfall)"/>)</h2>
	<xsl:call-template name="rattsfall">
	  <xsl:with-param name="rattsfall" select="$rattsfall"/>
	</xsl:call-template>
      </aside>
    </xsl:if>

    <xsl:if test="$inbound">
      <aside class="annotations lagrumshanvisningar">
	<h2>Lagrumshänvisningar hit (<xsl:value-of select="count($inbound/rdf:Description)"/>)</h2>
	<!-- call the template -->
	<xsl:call-template name="inbound">
	  <xsl:with-param name="inbound" select="$inbound"/>
	</xsl:call-template>
      </aside>
    </xsl:if>

    <xsl:if test="$inford or $andrad or $upphavd">
      <aside class="annotations andringar">
	<h2>Ändringar/Förarbeten (<xsl:value-of select="count($inford)+count($andrad)+count($upphavd)"/>)</h2>
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
      </aside>
    </xsl:if>
  </xsl:template>

  <xsl:template match="xhtml:p[@typeof='rinfoex:Stycke']">
    <tr>
      <td><xsl:apply-templates mode="in-paragraf"/></td>
      <td><!-- here goes the boxes for commentary etc, but that's not supported for standalone Stycke nodes yet --></td>
    </tr>
  </xsl:template>


  <xsl:template match="xhtml:p[@typeof='rinfoex:Stycke']" mode="in-paragraf">
    <xsl:variable name="marker">
      <xsl:choose>
	<xsl:when test="substring-after(@id,'S') = '1'"><xsl:if
	test="substring-after(@id,'K')">K<xsl:value-of
	select="substring-before(substring-after(@id,'K'),'P')"/></xsl:if></xsl:when>
	<xsl:otherwise>S<xsl:value-of select="substring-after(@id,'S')"/></xsl:otherwise>
      </xsl:choose>
    </xsl:variable>
    <p id="{@id}" about="{//html/@about}#{@id}">
      <xsl:if test="$marker != ''">
	<a href="#{@id}" title="Permalänk till detta stycke"><img class="platsmarkor" src="../../../rsrc/img/{$marker}.png"/></a>
      </xsl:if>
      <xsl:if test="span[@class='paragrafbeteckning']">
	<a href="#{@id}" class="paragrafbeteckning" title="Permalänk till detta stycke"><xsl:copy-of select="span[@class='paragrafbeteckning']"/></a>
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
	<a href="#L{concat(substring-before(rpubl:fsNummer,':'),'-',substring-after(rpubl:fsNummer,':'))}"><xsl:value-of select="rpubl:fsNummer"/></a><xsl:if test="position()!= last()">, </xsl:if>
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
	    <xsl:with-param name="txt" select="dcterms:description"/>
	    <xsl:with-param name="width" select="80"/>
	    <xsl:with-param name="def" select="80"/>
	  </xsl:call-template>
	</xsl:variable>
	<xsl:variable name="localurl"><xsl:call-template name="localurl"><xsl:with-param name="uri" select="@rdf:about"/></xsl:call-template></xsl:variable>
	<a href="{$localurl}"><b><xsl:value-of select="dcterms:identifier"/></b></a>:
	<xsl:choose>
	  <xsl:when test="string-length(dcterms:description) > 80">
	    <xsl:value-of select="normalize-space(substring(dcterms:description, 1, $tuned-width - 1))" />...
	  </xsl:when>
	  <xsl:otherwise>
	    <xsl:value-of select="dcterms:description"/>
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
	    <xsl:if test="./dcterms:identifier != ''">
	      <xsl:variable name="localurl"><xsl:call-template name="localurl"><xsl:with-param name="uri" select="@rdf:about"/></xsl:call-template></xsl:variable>
	      <a href="{$localurl}"><xsl:value-of select="dcterms:identifier"/></a><xsl:if test="position()!=last()">, </xsl:if>
	    </xsl:if>
	  </xsl:for-each>
	</li>
      </xsl:for-each>
    </ul>
  </xsl:template>

  <xsl:template name="accordionbox">
    <xsl:param name="heading"/>
    <xsl:param name="contents"/>
    <xsl:param name="first" select="true()"/>
    <xsl:if test="$first">
      <h3 class="ui-accordion-header ui-helper-reset ui-accordion-header-active ui-state-active ui-corner-top">
	<span class="ui-icon ui-icon-triangle-1-s"/><xsl:copy-of select="$heading"/>
      </h3>
      <div class="ui-accordion-content ui-helper-reset ui-accordion-content-active ui-widget-content ui-corner-bottom">
	<xsl:copy-of select="$contents"/>
      </div>
    </xsl:if>
    <xsl:if test="not($first)">
      <h3 class="ui-accordion-header ui-helper-reset ui-state-default ui-corner-top ui-corner-bottom">
	<span class="ui-icon ui-icon-triangle-1-e"/><xsl:copy-of select="$heading"/>
      </h3>
      <div class="ui-accordion-content ui-helper-reset ui-helper-hidden ui-widget-content ui-corner-bottom">
	<xsl:copy-of select="$contents"/>
      </div>
    </xsl:if>
  </xsl:template>
  
  <xsl:template match="xhtml:div[@class='register']">
    <div class="andringar"><xsl:apply-templates/></div>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='registerpost']">
    <xsl:variable name="year" select="substring-before(dl/dd[@property='rpubl:fsNummer'],':')"/>
    <xsl:variable name="nr" select="substring-after(dl/dd[@property='rpubl:fsNummer'],':')"/>
    <div class="andring" id="{concat(substring-before(@id,':'),'-',substring-after(@id,':'))}" about="{@about}">
      <!-- titel eller sfsnummer, om ingen titel finns -->
      <h2><xsl:choose>
	<xsl:when test="dl/dd[@property='dcterms:title']">
	  <xsl:value-of select="dl/dd[@property='dcterms:title']"/>
	</xsl:when>
	<xsl:otherwise>
	  <xsl:value-of select="dl/dd[@property='rpubl:fsNummer']"/>
	</xsl:otherwise>
      </xsl:choose></h2>
      <xsl:if test="(number($year) > 1998) or (number($year) = 1998 and number($nr) >= 306)">

	<p><a href="http://rkrattsdb.gov.se/SFSdoc/{substring($year,3,2)}/{substring($year,3,2)}{format-number($nr,'0000')}.PDF">Officiell version (PDF)</a></p>
      </xsl:if>
      <xsl:apply-templates mode="in-paragraf"/>
    </div>
  </xsl:template>

  <!-- emit nothing - this is already handled above -->
  <xsl:template match="xhtml:span[@class='paragrafbeteckning']"/>
  
  <!-- FIXME: in order to be valid xhtml1, we must remove unordered
       lists from within paragraphs, and place them after the
       paragraph. This turns out to be tricky in XSLT, the following
       is a non-working attempt -->
  <!--
  <xsl:template match="p">
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
  
  <!-- remove spans which only purpose is to contain RDFa data -->
  <xsl:template match="xhtml:span[@property and @content and not(text())]"/>
  <xsl:template match="xhtml:span[@rel and @href and not(text())]"/>

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


  <xsl:template match="a|a" mode="in-paragraf">
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
  
  <xsl:template match="xhtml:h2" mode="toc">
    <li class="toc-rubrik"><a href="#{@id}"><xsl:value-of select="."/></a>
    </li>
  </xsl:template>

  <xsl:template match="xhtml:h3" mode="toc">
    <li class="toc-underrubrik"><a href="#{@id}"><xsl:value-of select="."/></a></li>
  </xsl:template>



  <!-- toc handling (do nothing) -->
  <xsl:template match="@*|node()" mode="toc"/>

</xsl:stylesheet>
