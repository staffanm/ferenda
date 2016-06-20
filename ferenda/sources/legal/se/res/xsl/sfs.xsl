<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rpubl="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xmlns:ext="http://exslt.org/common"
		exclude-result-prefixes="xhtml rdf rpubl ext">

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
    <xsl:message>pagetitle: documenturi is <xsl:value-of select="$documenturi"/></xsl:message>
    <xsl:variable name="rattsfall" select="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/rpubl:isLagrumFor/rdf:Description"/>
    <xsl:variable name="kommentar" select="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/dcterms:description/xhtml:div/*"/>
    <xsl:variable name="forfattningskommentar" select="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/rinfoex:forfattningskommentar/xhtml:div/*"/>
    <div class="row">
      <section id="top" class="col-sm-7">
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
      <xsl:if test="$kommentar or $rattsfall">
	<xsl:variable name="expanded" select="'true'"/>
	<div class="panel-group col-sm-5" role="tablist" id="panel-top" aria-multiselectable="true">
	  <xsl:if test="$kommentar">
	    <xsl:call-template name="aside-annotations-panel">
	      <xsl:with-param name="title">Kommentar</xsl:with-param>
	      <xsl:with-param name="badgecount"/>
	      <xsl:with-param name="nodeset" select="$kommentar"/>
	      <xsl:with-param name="panelid">top</xsl:with-param>
	      <xsl:with-param name="paneltype">k</xsl:with-param>
	      <xsl:with-param name="expanded" select="$expanded"/>
	    </xsl:call-template>
	  </xsl:if>
	  <xsl:if test="$rattsfall">
	    <xsl:call-template name="aside-annotations-panel">
	      <xsl:with-param name="title">Rättsfall</xsl:with-param>
	      <xsl:with-param name="badgecount" select="count($rattsfall)"/>
	      <xsl:with-param name="nodeset" select="$rattsfall"/>
	      <xsl:with-param name="panelid">top</xsl:with-param>
	      <xsl:with-param name="paneltype">r</xsl:with-param>
	    </xsl:call-template>
	  </xsl:if>
	</div>
      </xsl:if>
    </div>
  </xsl:template>
  <xsl:param name="dyntoc" select="true()"/>

  <xsl:template name="docmetadata">
    <dl id="refs-dokument" class="dl-horizontal">
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
      <dd><xsl:value-of select="substring(//xhtml:meta[@property='rinfoex:senastHamtad']/@content, 1, 10)"/></dd>
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
    <div class="row">
      <div>
	<xsl:if test="@id">
	  <xsl:attribute name="id"><xsl:value-of select="@id"/></xsl:attribute>
	  <xsl:attribute name="about"><xsl:value-of select="//html/@about"/>#<xsl:value-of select="@id"/></xsl:attribute>
	</xsl:if>
	<xsl:if test="@class">
	  <xsl:attribute name="class"><xsl:value-of select="@class"/></xsl:attribute>
	</xsl:if>
	<xsl:apply-templates/>
      </div>
    </div>
  </xsl:template>

 
  <xsl:template match="xhtml:div[@typeof='rpubl:Kapitel']">
    <div class="row" about="{//html/@about}#{@id}">
      <section id="{@id}" class="col-sm-7 kapitelrubrik">
	<xsl:apply-templates select="*[1]"/>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
    <xsl:apply-templates select="*[position()>1]"/>
  </xsl:template>

  <xsl:template match="xhtml:div[@typeof='rpubl:Paragraf']">
    <div class="row" about="{//html/@about}#{@id}">
      <section id="{@id}" class="col-sm-7">
	<xsl:apply-templates mode="in-paragraf"/>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
  </xsl:template>


  <!-- this should only match elements w/o @about -->
  <xsl:template match="xhtml:h2[not(@about)]|xhtml:h3[not(@about)]">
    <div class="row">
      <section id="{@id}" class="col-sm-7">
	<xsl:element name="{local-name(.)}"><xsl:apply-templates/></xsl:element>
      </section>
    </div>
  </xsl:template>

  <xsl:template name="aside-annotations">
    <xsl:param name="uri"/>
    <!-- plocka fram referenser kring/till denna paragraf -->
    <xsl:variable name="rattsfall" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isLagrumFor/rdf:Description"/>
    <xsl:variable name="inbound" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/dcterms:references"/>
    <xsl:variable name="kommentar" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/dcterms:description/xhtml:div/*"/>
    <xsl:variable name="forfattningskommentar" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rinfoex:forfattningskommentar/xhtml:div/*"/>
    <xsl:variable name="inford" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isEnactedBy"/>
    <xsl:variable name="andrad" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isChangedBy"/>
    <xsl:variable name="upphavd" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isRemovedBy"/>
    <xsl:variable name="panelid" select="substring-after($uri, '#')"/>
    <xsl:variable name="expanded" select="'true'"/>
    <xsl:if test="$kommentar or $forfattningskommentar or $rattsfall or $inbound or $inford or $andrad or $upphavd">
      <div class="panel-group col-sm-5" role="tablist" id="panel-{$panelid}" aria-multiselectable="true">
	<xsl:if test="$kommentar">
	  <xsl:call-template name="aside-annotations-panel">
	    <xsl:with-param name="title">Kommentar</xsl:with-param>
	    <xsl:with-param name="badgecount"/>
	    <xsl:with-param name="nodeset" select="$kommentar"/>
	    <xsl:with-param name="panelid" select="$panelid"/>
	    <xsl:with-param name="paneltype">k</xsl:with-param>
	    <xsl:with-param name="expanded" select="$expanded"/>
	  </xsl:call-template>
	</xsl:if>
	<xsl:if test="$forfattningskommentar">
	  <xsl:call-template name="aside-annotations-panel">
	    <xsl:with-param name="title">Författningskommentar</xsl:with-param>
	    <xsl:with-param name="badgecount"/>
	    <xsl:with-param name="nodeset" select="$forfattningskommentar"/>
	    <xsl:with-param name="panelid" select="$panelid"/>
	    <xsl:with-param name="paneltype">f</xsl:with-param>
	    <xsl:with-param name="expanded" select="$expanded"/>
	  </xsl:call-template>
	</xsl:if>
	<xsl:if test="$rattsfall">
	  <xsl:call-template name="aside-annotations-panel">
	    <xsl:with-param name="title">Rättsfall</xsl:with-param>
	    <xsl:with-param name="badgecount" select="count($rattsfall)"/>
	    <xsl:with-param name="nodeset" select="$rattsfall"/>
	    <xsl:with-param name="panelid" select="$panelid"/>
	    <xsl:with-param name="paneltype">r</xsl:with-param>
	  </xsl:call-template>
	</xsl:if>
	<xsl:if test="$inbound">
	  <xsl:call-template name="aside-annotations-panel">
	    <xsl:with-param name="title">Lagrumshänvisningar hit</xsl:with-param>
	    <xsl:with-param name="badgecount" select="count($inbound/rdf:Description)"/>
	    <xsl:with-param name="nodeset" select="$inbound"/>
	    <xsl:with-param name="panelid" select="$panelid"/>
	    <xsl:with-param name="paneltype">l</xsl:with-param>
	  </xsl:call-template>
	</xsl:if>
	<xsl:if test="$inford or $andrad or $upphavd">
	  <xsl:variable name="andringar">
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
	  </xsl:variable>
	  <xsl:call-template name="aside-annotations-panel">
	    <xsl:with-param name="title">Ändringar</xsl:with-param>
	    <xsl:with-param name="badgecount" select="count($inford) + count($andrad) + count(upphavd)"/>
	    <xsl:with-param name="panelid" select="$panelid"/>
	    <xsl:with-param name="paneltype">a</xsl:with-param>
	    <xsl:with-param name="nodeset" select="ext:node-set($andringar)"/>
	  </xsl:call-template>
	</xsl:if>
      </div>
    </xsl:if>
  </xsl:template>

  <xsl:template name="aside-annotations-panel">
    <xsl:param name="title"/>
    <xsl:param name="badgecount"/>
    <xsl:param name="nodeset"/>
    <xsl:param name="paneltype"/>
    <xsl:param name="panelid"/>
    <xsl:param name="expanded" select="'false'"/>
    <xsl:variable name="expanded-class"><xsl:if test="$expanded = 'true'">in</xsl:if></xsl:variable>
    <xsl:message><xsl:value-of select="$paneltype"/>-<xsl:value-of select="$panelid"/>: expanded-class='<xsl:value-of select="$expanded-class"/>'</xsl:message>
    <div class="panel panel-default">
      <div class="panel-heading" role="tab" id="heading-{$paneltype}-{$panelid}">
	<h4 class="panel-title">
        <a role="button" data-toggle="collapse" data-parent="#panel-{$panelid}" href="#collapse-{$paneltype}-{$panelid}" aria-expanded="{$expanded}" aria-controls="collapse-{$paneltype}-{$panelid}">
	  <xsl:value-of select="$title"/>
	  <xsl:if test="$badgecount">
	    <span class="badge pull-right"><xsl:value-of select="$badgecount"/></span>
	  </xsl:if>
        </a>
      </h4>
    </div>
    <div id="collapse-{$paneltype}-{$panelid}" class="panel-collapse collapse {$expanded-class}" role="tabpanel" aria-labelledby="heading-{$paneltype}-{$panelid}">
      <div class="panel-body">
	<xsl:if test="$nodeset">
	  <xsl:apply-templates select="$nodeset"/>
	</xsl:if>
      </div>
    </div>
  </div>    
  </xsl:template>

  <!-- FIXME: This is identical to the template that matches rpubl:Paragraf, that template should match this one as well. -->
  <xsl:template match="xhtml:p[@typeof='rinfoex:Stycke']">
    <div class="row" about="{//html/@about}#{@id}">
      <section id="{@id}" class="col-sm-7">
	<xsl:apply-templates mode="in-paragraf"/>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
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
      <xsl:if test="xhtml:span[@class='paragrafbeteckning']">
	<a href="#{@id}" class="paragrafbeteckning" title="Permalänk till detta stycke"><xsl:copy-of select="xhtml:span[@class='paragrafbeteckning']"/></a>&#160;
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

  <!--
  <xsl:template match="xhtml:div[@typeof='rpubl:Kapitel']" mode="toc">
    <xsl:message>found a chapter</xsl:message>
    <li class="toc-kapitel"><a href="#{@id}"><xsl:value-of select="xhtml:h1"/></a>
    <xsl:if test="xhtml:h2|xhtml:h3">
      <ul>
	<xsl:apply-templates mode="toc"/>
      </ul>
    </xsl:if>
    </li>
  </xsl:template>
  
  <xsl:template match="xhtml:h2" mode="toc">
    <xsl:message>found a h2</xsl:message>
    <li class="toc-rubrik"><a href="#{@id}"><xsl:value-of select="."/></a></li>
  </xsl:template>

  <xsl:template match="xhtml:h3" mode="toc">
    <xsl:message>found a h3</xsl:message>
    <li class="toc-underrubrik"><a href="#{@id}"><xsl:value-of select="."/></a></li>
  </xsl:template>
  -->


  <!-- toc handling (do nothing) -->
  <xsl:template match="@*|node()" mode="toc">
    <xsl:message>mode doc do nothing (<xsl:value-of select="name(.)"/> @about=<xsl:value-of select="@about"/>)</xsl:message>
  </xsl:template>

</xsl:stylesheet>
