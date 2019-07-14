<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rpubl="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xmlns:ext="http://exslt.org/common"
		exclude-result-prefixes="xhtml rdf rpubl ext rinfoex">

  <xsl:import href="tune-width.xsl"/>
  <xsl:import href="annotations-panel.xsl"/>
  <xsl:include href="base.xsl"/> 
  <xsl:param name="version"/> <!-- set by generate() if asked to generate a specific version -->
  <xsl:param name="expired"/> <!-- set by generate() if asked to generate a expired SFS -->
 
  <!-- Implementationer av templates som anropas från base.xsl -->
  <xsl:template name="headtitle">
    <xsl:value-of select="//xhtml:title"/>
    <xsl:if test="//xhtml:meta[@property='dcterms:alternate']/@content">
      (<xsl:value-of select="//xhtml:meta[@property='dcterms:alternate']/@content"/>)
    </xsl:if> | Lagen.nu
  </xsl:template>

  <xsl:template name="metarobots"/>

  <xsl:template name="linkalternate"><!--
    <link rel="alternate" type="text/plain" href="{$documenturi}.txt" title="Plain text"/>
  --></xsl:template>

  <xsl:template name="headmetadata"/>

  <xsl:template name="bodyclass">sfs</xsl:template>

  <xsl:variable name="documenturi" select="//xhtml:body/@about"/>
  <xsl:variable name="sfsannotations" select="document($annotationfile)/rdf:RDF"/>

  <xsl:template name="pagetitle">
    <xsl:message>pagetitle: documenturi is <xsl:value-of select="$documenturi"/></xsl:message>
    <xsl:variable name="rattsfall" select="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/rpubl:isLagrumFor/rdf:Description"/>
    <xsl:variable name="kommentar" select="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/dcterms:description/xhtml:div/*"/>
    <xsl:variable name="myndfs" select="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/rpubl:isBemyndigandeFor/rdf:Description"/>
    <xsl:variable name="forfattningskommentar" select="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/rinfoex:forfattningskommentar/xhtml:div/*"/>
    <xsl:variable name="label" select="//xhtml:meta[@property='rdfs:label' and not(@about)]/@content"/>
    <xsl:variable name="alternate" select="//xhtml:meta[@property='dcterms:alternate']/@content"/>
    <div class="row">
      <xsl:choose>
	<xsl:when test="$expired">
	  <div class="watermark"><p>Upphävd författning</p></div>
	</xsl:when>
	<xsl:when test="$version">
	  <div class="watermark"><p>Inaktuell version</p></div>
	</xsl:when>
      </xsl:choose>
      <section id="top" class="col-sm-7">
	<h1><xsl:value-of select="../xhtml:head/xhtml:title"/></h1>
	<!--
	<xsl:if test="$version">
	  <h2>Version: <xsl:value-of select="$version"/></h2>
        </xsl:if>
        -->
	<xsl:if test="$label or $alternate">
	  <p class="lead">(<xsl:value-of select="$label"/><xsl:if test="$label and $alternate">, </xsl:if><xsl:value-of select="$alternate"/>)</p>
	</xsl:if>
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
	  <p class="alert alert-warning patchdescription">
	    Texten har ändrats jämfört med ursprungsmaterialet: <xsl:value-of select="//xhtml:meta[@property='rinfoex:patchdescription']/@content"/>
	  </p>
	</xsl:if>
      </section>
      <xsl:if test="$kommentar or $rattsfall or $myndfs">
	<div class="panel-group col-sm-5" role="tablist" id="panel-top" aria-multiselectable="true">
	  <xsl:if test="$kommentar">
	    <xsl:call-template name="aside-annotations-panel">
	      <xsl:with-param name="title">Kommentar</xsl:with-param>
	      <xsl:with-param name="badgecount"/>
	      <xsl:with-param name="nodeset" select="$kommentar"/>
	      <xsl:with-param name="panelid">top</xsl:with-param>
	      <xsl:with-param name="paneltype">k</xsl:with-param>
	      <xsl:with-param name="expanded" select="true()"/>
	    </xsl:call-template>
	  </xsl:if>
	  <xsl:if test="$rattsfall">
	    <xsl:call-template name="aside-annotations-panel">
	      <xsl:with-param name="title">Rättsfall</xsl:with-param>
	      <xsl:with-param name="badgecount" select="count($rattsfall)"/>
	      <xsl:with-param name="nodeset" select="$rattsfall"/>
	      <xsl:with-param name="panelid">top</xsl:with-param>
	      <xsl:with-param name="paneltype">r</xsl:with-param>
	      <xsl:with-param name="expanded" select="not($kommentar)"/>
	    </xsl:call-template>
	  </xsl:if>
	  <xsl:if test="$myndfs">
	    <xsl:variable name="myndfs-markup">
	      <ul>
		<xsl:for-each select="$myndfs">
		  <li><b><a href="{@rdf:about}"><xsl:value-of select="dcterms:identifier"/></a>: </b><xsl:value-of select="dcterms:title"/></li>
		</xsl:for-each>
	      </ul>
	    </xsl:variable>
	    <xsl:call-template name="aside-annotations-panel">
	      <xsl:with-param name="title">Meddelat med bemyndigande i denna författning</xsl:with-param>
	      <xsl:with-param name="badgecount" select="count($myndfs)"/>
	      <xsl:with-param name="nodeset" select="ext:node-set($myndfs-markup)"/>
	      <xsl:with-param name="panelid">top</xsl:with-param>
	      <xsl:with-param name="paneltype">m</xsl:with-param>
	      <xsl:with-param name="expanded" select="not($kommentar or $rattsfall)"/>
	    </xsl:call-template>
	  </xsl:if>
	</div>
      </xsl:if>
    </div>
  </xsl:template>
  <xsl:param name="dyntoc" select="false()"/>
  <xsl:param name="fixedtoc" select="true()"/>
  <xsl:param name="content-under-pagetitle" select="false()"/>

  <xsl:template name="docversions">
    <xsl:variable name="versions" select="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/dcterms:hasVersion/rdf:Description"/>
    <div class="docversions">
      <xsl:if test="$versions">
	<!--
	<ul>
	<xsl:for-each select="$versions">
	  <li><a href="{@rdf:about}"><xsl:value-of select="dcterms:identifier"/> (Ikraft <xsl:value-of select="rpubl:ikrafttradandedatum"/>, <xsl:value-of select="rpubl:forarbete/rdf:Description/dcterms:identifier"/> <xsl:value-of select="rpubl:forarbete/rdf:Description/dcterms:title"/>)</a></li>
	</xsl:for-each>
	</ul>
	-->
	<form action="" method="GET">
	  <input type="hidden" name="diff" value="true"/>
	  <select name="from" id="from" onchange="if (this.options[this.selectedIndex] != 'None') {{ this.form.submit(); }}">
	    <!--<select name="from" id="from" onchange="this.form.submit()">-->
	    <option value="None">Jämför med tidigare lydelser</option>
	    <xsl:for-each select="$versions">
	      <option value="{substring(dcterms:identifier, 5)}"><xsl:value-of select="dcterms:identifier"/>
	      <xsl:if test="rpubl:forarbete"> (<xsl:value-of select="rpubl:forarbete/rdf:Description/dcterms:identifier"/>: <xsl:value-of select="rpubl:forarbete/rdf:Description/dcterms:title"/> <xsl:if test="rpubl:ikrafttradandedatum and rpubl:ikrafttradandedatum != 'None'">, ikraft <xsl:value-of select="rpubl:ikrafttradandedatum"/></xsl:if>)</xsl:if>
	      <!-- fixme: if the version is the first version (x = y in the url .../x/konsolidering/y), state "(ursprunglig lydelse)" -->
	      </option>
	    </xsl:for-each>
	  </select>
	</form>
      </xsl:if>
    </div>
  </xsl:template>
  
  <xsl:template name="docmetadata">
    <xsl:variable name="regpost" select="//xhtml:div[@class='registerpost'][1]"/>
    <dl id="refs-dokument" class="dl-horizontal">
      <dt>Departement</dt>
      <dd><xsl:value-of select="//xhtml:meta[@about=//xhtml:link[@rel='dcterms:creator']/@href]/@content"/></dd>
      <dt>Utfärdad</dt>
      <dd><xsl:value-of select="//xhtml:meta[@property='rpubl:utfardandedatum']/@content"/></dd>
      <dt>Ändring införd</dt>
      <dd><xsl:value-of select="//xhtml:meta[@property='dcterms:identifier']/@content"/>
	<xsl:call-template name="docversions"/>

      </dd>
      <xsl:if test="//xhtml:meta[@property='rpubl:ikrafttradandedatum']/@content">
	<dt>Ikraft</dt>
	<dd><xsl:value-of select="//xhtml:meta[@property='rpubl:ikrafttradandedatum']/@content"/></dd>
      </xsl:if>
      <xsl:if test="//xhtml:meta[@property='rinfoex:tidsbegransad']/@content">
	<dt>Tidsbegränsad</dt>
	<dd><xsl:value-of select="//xhtml:meta[@property='rinfoex:tidsbegransad']/@content"/></dd>
      </xsl:if>
      <xsl:if test="//xhtml:meta[@property='rinfoex:upphavdAv']"> <!-- FIXME: This property should be encoded as link rel="..." ? -->
	<xsl:variable name="upphavdAv" select="//xhtml:meta[@property='rinfoex:upphavdAv']/@content"/>
	<dt>Upphävd</dt>
	<dd><xsl:value-of select="//xhtml:meta[@property='rpubl:upphavandedatum']/@content"/></dd>
	<dt>Upphävd genom</dt>
	<dd><a href="{$upphavdAv}"><xsl:value-of select="//xhtml:div[@about=$upphavdAv]/xhtml:span[@property='dcterms:identifier']/@content"/></a></dd>
      </xsl:if>
      <xsl:if test="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/rinfoex:upphaver/rdf:Description"> 
	<dt>Upphäver</dt>
	<xsl:for-each select="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/rinfoex:upphaver/rdf:Description">
	  <dd><a href="{@rdf:about}"><xsl:value-of select="dcterms:title"/></a></dd>
	</xsl:for-each>
      </xsl:if>
      <dt>Källa</dt>
      <dd rel="dcterms:publisher" resource="http://lagen.nu/org/2008/regeringskansliet"><a href="http://rkrattsbaser.gov.se/sfst?bet={$regpost/xhtml:span[@property='rpubl:arsutgava']/@content}:{$regpost/xhtml:span[@property='rpubl:lopnummer']/@content}">Regeringskansliets rättsdatabaser</a></dd>
      <dt>Senast hämtad</dt>
      <dd><xsl:value-of select="substring(//xhtml:meta[@property='rinfoex:senastHamtad']/@content, 1, 10)"/></dd>
      <xsl:if test="//xhtml:meta[@property='rdfs:comment']/@content">
	<dt>Övrigt</dt>
	<dd><xsl:value-of select="//xhtml:meta[@property='rdfs:comment']/@content"/></dd>
      </xsl:if>
    </dl>
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
    <xsl:variable name="andringsmarkering">
      <xsl:if test="xhtml:span[@rel='rinfoex:upphor']">
	<p class="andringsdatum">/Upphör att gälla U: <xsl:value-of select="xhtml:span[@rel='rinfoex:upphor']/@content"/>/</p>
      </xsl:if>
      <xsl:if test="xhtml:span[@rel='rinfoex:ikrafttrader']">
	<p class="andringsdatum">/Träder i kraft I: <xsl:value-of select="xhtml:span[@rel='rinfoex:ikrafttrader']/@content"/>/</p>
      </xsl:if>
    </xsl:variable>
    <div class="row" about="{//html/@about}#{@id}">
      <section id="{@id}" class="col-sm-7 kapitelrubrik">
	<xsl:copy-of select="$andringsmarkering"/>
	<xsl:apply-templates select="*[1]"/>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
    <xsl:apply-templates select="*[position()>1]"/>
  </xsl:template>

  <xsl:template match="xhtml:div[@typeof='rpubl:Paragraf']">
    <xsl:variable name="andringsmarkering">
      <xsl:if test="xhtml:span[@rel='rinfoex:upphor']">
	<p class="andringsdatum">/Upphör att gälla U: <xsl:value-of select="xhtml:span[@rel='rinfoex:upphor']/@content"/>/</p>
      </xsl:if>
      <xsl:if test="xhtml:span[@rel='rinfoex:ikrafttrader']">
	<p class="andringsdatum">/Träder i kraft I: <xsl:value-of select="xhtml:span[@rel='rinfoex:ikrafttrader']/@content"/>/</p>
      </xsl:if>
    </xsl:variable>

    <xsl:if test="@id">
      <div class="row" about="{//html/@about}#{@id}">
	<section id="{@id}" class="col-sm-7">
	  <xsl:copy-of select="$andringsmarkering"/>
	  <xsl:apply-templates mode="in-paragraf"/>
	</section>
	<xsl:call-template name="aside-annotations">
	  <xsl:with-param name="uri" select="@about"/>
	</xsl:call-template>
      </div>
    </xsl:if>
    <xsl:if test="not(@id) and not($version)">
      <div class="row">
	<section class="col-sm-7 ej-ikraft">
	  <xsl:copy-of select="$andringsmarkering"/>
	  <xsl:apply-templates mode="in-paragraf" select="xhtml:p"/>
	</section>
      </div>
    </xsl:if>
  </xsl:template>



  <!-- this should only match elements w/o @about -->
  <xsl:template match="xhtml:h2[not(@about)]|xhtml:h3[not(@about)]">
    <xsl:variable name="andringsmarkering">
      <xsl:if test="xhtml:span[@rel='rinfoex:upphor']">
	<p class="andringsdatum">/Upphör att gälla U: <xsl:value-of select="xhtml:span[@rel='rinfoex:upphor']/@content"/>/</p>
      </xsl:if>
      <xsl:if test="xhtml:span[@rel='rinfoex:ikrafttrader']">
	<p class="andringsdatum">/Träder i kraft I: <xsl:value-of select="xhtml:span[@rel='rinfoex:ikrafttrader']/@content"/>/</p>
      </xsl:if>
    </xsl:variable>
    <div class="row">
      <xsl:if test="@id">
	<section id="{@id}" class="col-sm-7">
	  <xsl:copy-of select="$andringsmarkering"/>
	  <xsl:element name="{local-name(.)}"><xsl:apply-templates/></xsl:element>
	</section>
      </xsl:if>
      <xsl:if test="not(@id) and not($version)">
	<section class="col-sm-7 ej-ikraft">
	  <xsl:copy-of select="$andringsmarkering"/>
	  <xsl:element name="{local-name(.)}"><xsl:apply-templates/></xsl:element>
	</section>
      </xsl:if>
    </div>
  </xsl:template>

  <xsl:template name="aside-annotations">
    <xsl:param name="uri"/>
    <!-- plocka fram referenser kring/till denna paragraf -->
    <xsl:variable name="rattsfall" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isLagrumFor/rdf:Description"/>
    <xsl:variable name="inbound"   select="$sfsannotations/rdf:Description[@rdf:about=$uri]/dcterms:isReferencedBy"/>
    <xsl:variable name="kommentar" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/dcterms:description/xhtml:div/*"/>
    <xsl:variable name="forfattningskommentar" select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rinfoex:forfattningskommentar/xhtml:div/*"/>
    <xsl:variable name="inford"    select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isEnactedBy"/>
    <xsl:variable name="andrad"    select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isChangedBy"/>
    <xsl:variable name="upphavd"   select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isRemovedBy"/>
    <xsl:variable name="myndfs"    select="$sfsannotations/rdf:Description[@rdf:about=$uri]/rpubl:isBemyndigandeFor/rdf:Description"/>
    <xsl:variable name="panelid"   select="substring-after($uri, '#')"/>
    <xsl:variable name="expanded"  select="'true'"/>
    <xsl:if test="$kommentar or $forfattningskommentar or $rattsfall or $myndfs or $inbound or $inford or $andrad or $upphavd">
      <div class="panel-group col-sm-5" role="tablist" id="panel-{$panelid}" aria-multiselectable="true">
	<xsl:if test="$kommentar">
	  <xsl:call-template name="aside-annotations-panel">
	    <xsl:with-param name="title">Kommentar</xsl:with-param>
	    <xsl:with-param name="badgecount"/>
	    <xsl:with-param name="nodeset" select="$kommentar"/>
	    <xsl:with-param name="panelid" select="$panelid"/>
	    <xsl:with-param name="paneltype">k</xsl:with-param>
	    <xsl:with-param name="expanded" select="true()"/>
	  </xsl:call-template>
	</xsl:if>
	<xsl:if test="$forfattningskommentar">
	  <xsl:call-template name="aside-annotations-panel">
	    <xsl:with-param name="title">Författningskommentar</xsl:with-param>
	    <xsl:with-param name="badgecount"/>
	    <xsl:with-param name="nodeset" select="$forfattningskommentar"/>
	    <xsl:with-param name="panelid" select="$panelid"/>
	    <xsl:with-param name="paneltype">f</xsl:with-param>
	    <xsl:with-param name="expanded" select="not($kommentar)"/>
	  </xsl:call-template>
	</xsl:if>
	<xsl:if test="$rattsfall">
	  <xsl:variable name="rattsfall-markup">
	    <ul>
	      <xsl:for-each select="$rattsfall">
		<li><a href="{@rdf:about}"><b><xsl:value-of select="dcterms:identifier"/></b>:</a> <xsl:value-of select="dcterms:description"/></li>
	      </xsl:for-each>
	    </ul>
	  </xsl:variable>
	  <xsl:call-template name="aside-annotations-panel">
	    <xsl:with-param name="title">Rättsfall</xsl:with-param>
	    <xsl:with-param name="badgecount" select="count($rattsfall)"/>
	    <xsl:with-param name="nodeset" select="ext:node-set($rattsfall-markup)"/>
	    <xsl:with-param name="panelid" select="$panelid"/>
	    <xsl:with-param name="paneltype">r</xsl:with-param>
	    <xsl:with-param name="expanded" select="not($kommentar or $forfattningskommentar)"/>
	  </xsl:call-template>
	</xsl:if>
	<xsl:if test="$myndfs">
	  <xsl:variable name="myndfs-markup">
	    <ul>
	      <xsl:for-each select="$myndfs">
		<li><b><a href="{@rdf:about}"><xsl:value-of select="dcterms:identifier"/></a>: </b><xsl:value-of select="dcterms:title"/></li>
	      </xsl:for-each>
	    </ul>
	  </xsl:variable>
	  <xsl:call-template name="aside-annotations-panel">
	    <xsl:with-param name="title">Meddelat med detta bemyndigande</xsl:with-param>
	    <xsl:with-param name="badgecount" select="count($myndfs)"/>
	    <xsl:with-param name="nodeset" select="ext:node-set($myndfs-markup)"/>
	    <xsl:with-param name="panelid" select="$panelid"/>
	    <xsl:with-param name="paneltype">m</xsl:with-param>
	    <xsl:with-param name="expanded" select="not($kommentar or $forfattningskommentar or $rattsfall)"/>
	  </xsl:call-template>
	</xsl:if>
	<xsl:if test="$inbound">
	  <xsl:variable name="inbound-markup">
	    <ul>
	      <xsl:for-each select="$inbound">
		<li>
		  <xsl:for-each select="rdf:Description">
		    <a href="{@rdf:about}"><xsl:value-of select="dcterms:identifier"/></a>
		    <xsl:if test="position() != last()">, </xsl:if>
		  </xsl:for-each>
		</li>
	      </xsl:for-each>
	    </ul>
	  </xsl:variable>
	  <xsl:call-template name="aside-annotations-panel">
	    <xsl:with-param name="title">Lagrumshänvisningar hit</xsl:with-param>
	    <xsl:with-param name="badgecount" select="count($inbound/rdf:Description)"/>
	    <xsl:with-param name="nodeset" select="ext:node-set($inbound-markup)"/>
	    <xsl:with-param name="panelid" select="$panelid"/>
	    <xsl:with-param name="paneltype">l</xsl:with-param>
	    <xsl:with-param name="expanded" select="not($kommentar or $forfattningskommentar or $myndfs or $rattsfall)"/>
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
	    <xsl:with-param name="badgecount" select="count($inford) + count($andrad) + count($upphavd)"/>
	    <xsl:with-param name="panelid" select="$panelid"/>
	    <xsl:with-param name="paneltype">a</xsl:with-param>
	    <xsl:with-param name="nodeset" select="ext:node-set($andringar)"/>
	    <xsl:with-param name="expanded" select="not($kommentar or $forfattningskommentar or $myndfs or $rattsfall or $inbound)"/>
	  </xsl:call-template>
	</xsl:if>
      </div>
    </xsl:if>
  </xsl:template>


  <!-- FIXME: This is identical to the template that matches rpubl:Paragraf, that template should match this one as well. -->
  <xsl:template match="xhtml:p[@typeof='rinfoex:Stycke']">
    <div class="row" about="{//html/@about}#{@id}">
      <section id="{@id}" class="col-sm-7">
	<xsl:apply-templates/>
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
	<xsl:when test="@id">S<xsl:value-of select="substring-after(@id,'S')"/></xsl:when>
	<xsl:otherwise/>
      </xsl:choose>
    </xsl:variable>
    <p id="{@id}" about="{//html/@about}#{@id}">
      <!-- marker can be empty if the Stycke has no @id (which is the
           case for Stycke in a Paragraf which is not in force -->
      <xsl:if test="$marker != ''">
	<a href="#{@id}" title="Permalänk till detta stycke"><img class="platsmarkor" src="../../../rsrc/img/{$marker}.png" alt="[{$marker}]"/></a>
      </xsl:if>
      <xsl:if test="xhtml:span[@class='paragrafbeteckning']">
	<xsl:choose>
	  <xsl:when test="@id">
	    <a href="#{@id}" class="paragrafbeteckning" title="Permalänk till detta stycke"><xsl:value-of select="xhtml:span[@class='paragrafbeteckning']"/></a>&#160;
	  </xsl:when>
	  <xsl:otherwise>
	    <xsl:value-of select="xhtml:span[@class='paragrafbeteckning']"/>&#160;
	  </xsl:otherwise>
	</xsl:choose>
      </xsl:if>
      <xsl:apply-templates/>
    </p>
  </xsl:template>

  <xsl:template match="xhtml:li[@property='rinfoex:punkt']">
    <li id="{@id}" about="{@about}" data-ordinal="{@content}"><xsl:apply-templates/></li>
  </xsl:template>
  
  <xsl:template match="xhtml:div[@typeof='rinfoex:Bilaga']">
    <xsl:variable name="andringsmarkering">
      <xsl:if test="xhtml:span[@rel='rinfoex:upphor']">
	<p class="andringsdatum">/Upphör att gälla U: <xsl:value-of select="xhtml:span[@rel='rinfoex:upphor']/@content"/>/</p>
      </xsl:if>
      <xsl:if test="xhtml:span[@rel='rinfoex:ikrafttrader']">
	<p class="andringsdatum">/Träder i kraft I: <xsl:value-of select="xhtml:span[@rel='rinfoex:ikrafttrader']/@content"/>/</p>
      </xsl:if>
    </xsl:variable>

    <xsl:if test="@id">
      <div class="row bilaga" about="{//html/@about}#{@id}" id="{@id}">
 	<xsl:apply-templates select="xhtml:h1[1]"/>
	<xsl:copy-of select="$andringsmarkering"/>
	<xsl:apply-templates select="*[not(self::xhtml:h1[1])]"/>
      </div>
    </xsl:if>
    <xsl:if test="not(@id) and not($version)">
      <div class="row bilaga ej-ikraft">
	<xsl:apply-templates select="xhtml:h1[1]"/>
	<xsl:copy-of select="$andringsmarkering"/>
	<xsl:apply-templates select="*[not(self::xhtml:h1[1])]"/>
      </div>
    </xsl:if>
  </xsl:template>

  <xsl:template name="andringsnoteringar">
    <xsl:param name="typ"/>
    <xsl:param name="andringar"/>
    <xsl:if test="$andringar">
      <xsl:value-of select="$typ"/>: SFS 
      <xsl:for-each select="$andringar">
	<a href="#L{concat(substring-before(rpubl:fsNummer,':'),'-',substring-after(rpubl:fsNummer,':'))}"><xsl:value-of select="rpubl:fsNummer"/><xsl:value-of select="rpubl:proposition"/></a><xsl:if test="position()!= last()">, </xsl:if>
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
	<a href="{@rdf:about}"><b><xsl:value-of select="dcterms:identifier"/></b></a>:
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
	      <a href="{@rdf:about}"><xsl:value-of select="dcterms:identifier"/></a><xsl:if test="position()!=last()">, </xsl:if>
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
    <div class="andringar" id="L"><xsl:apply-templates/></div>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='registerpost']">
    <xsl:variable name="year" select="xhtml:span[@property='rpubl:arsutgava']/@content"/>
    <xsl:variable name="nr" select="xhtml:span[@property='rpubl:lopnummer']/@content"/>
    <xsl:variable name="konsurl" select="concat($documenturi, '/konsolidering/', $year, ':', $nr)"/>
    <div class="andring" id="{@id}" about="{@about}">
      <h2><xsl:choose><xsl:when test="@content"><xsl:value-of select="@content"/></xsl:when><xsl:otherwise>Ändring, <xsl:value-of select="xhtml:span[@property='dcterms:identifier']/@content"/></xsl:otherwise></xsl:choose></h2>
      <ul>
      <!-- SFS older than 1998:306 does not exist in PDF anywhere. SFS
           1998:306 to 2018:159 exists in unofficial form at
           rkrattsdb.gov.se. SFS equal to or newer than 2018:160
           exists in official form at svenskforfattningssamling.se -->
      <xsl:if test="((number($year) > 1998) or (number($year) = 1998 and number($nr) >= 306)) and (2018 > number($year)) or (number($year) = 2018 and 160 > number($nr))">
	<li><a href="http://rkrattsdb.gov.se/SFSdoc/{substring($year,3,2)}/{substring($year,3,2)}{format-number($nr,'0000')}.PDF">Tryckt format (PDF)</a></li>
      </xsl:if>
      <xsl:if test="(number($year) > 2018) or (number($year) = 2018 and number($nr) >= 160)">
	<li><a href="https://svenskforfattningssamling.se/doc/{$year}{$nr}.html">Officiell autentisk version</a></li>
      </xsl:if>
      <xsl:if test="$sfsannotations/rdf:Description[@rdf:about=$documenturi]/dcterms:hasVersion/rdf:Description[@rdf:about=$konsurl]">
	<li><a href="{$documenturi}/konsolidering/{$year}:{$nr}">Konsoliderad version med ändringar införda till och med SFS <xsl:value-of select="$year"/>:<xsl:value-of select="$nr"/></a></li>
      </xsl:if>
      </ul>
       <xsl:if test="xhtml:div[@class='overgangsbestammelse']">
	<div class="overgangsbestammelse">
	  <h3>Övergångsbestämmelse</h3> <!-- FIXME: sometimes better labeled as Ikraftträdandebestämmelse -->
	  <xsl:apply-templates select="xhtml:div[@class='overgangsbestammelse']"/>
	</div>
      </xsl:if>
      <!-- since the rest of the data is only available as an RDF
           graph, serialized as naive RDFa, generating good HTML is a
           bit involved -->
      <dl class="dl-horizontal">
	<xsl:if test="xhtml:span[@rel='rpubl:forarbete']">
	  <dt>Förarbeten</dt>
	  <dd>
	    <xsl:for-each select="xhtml:span[@rel='rpubl:forarbete']">
	      <!-- only link propositioner, not utskottsbetänkanden nor riksdagsskrivelser -->
	      <xsl:choose>
		<xsl:when test="xhtml:span[@rel='rdf:type']/@href = 'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#Proposition'">
		  <a href="{@href}"><xsl:value-of select="xhtml:span/@content"/></a>
		</xsl:when>
		<xsl:otherwise><xsl:value-of select="xhtml:span/@content"/></xsl:otherwise></xsl:choose><xsl:if test="position()!= last()">, </xsl:if>
	    </xsl:for-each>
	  </dd>
	</xsl:if>
	<xsl:if test="xhtml:span[@property='rpubl:andrar']">
	  <dt>Omfattning</dt>
	  <dd><xsl:value-of select="xhtml:span[@property='rpubl:andrar']/@content"/></dd>
	</xsl:if>
	<xsl:if test="xhtml:span[@rel='rpubl:genomforDirektiv']">
	  <dt>CELEX-nr</dt>
	  <!-- we'd like to use {xhtml:span[@rel='rpubl:genomforDirektiv']/@href} here, but that points to an internal URL that doesn't redirect -->
	  <dd><a href="https://eur-lex.europa.eu/legal-content/SV/TXT/?uri=CELEX:{xhtml:span[@rel='rpubl:genomforDirektiv']/xhtml:span/@content}"><xsl:value-of select="xhtml:span[@rel='rpubl:genomforDirektiv']/xhtml:span/@content"/></a></dd>
	</xsl:if>
	<xsl:if test="xhtml:span[@property='rpubl:ikrafttradandedatum']">
	  <dt>Ikraftträder</dt>
	  <dd><xsl:value-of select="xhtml:span[@property='rpubl:ikrafttradandedatum']/@content"/></dd>
	</xsl:if>
      </dl>
    </div>
  </xsl:template>

  <!-- emit nothing - this is already handled above -->
  <xsl:template match="xhtml:span[@class='paragrafbeteckning']"/>
  
  <!-- remove spans which only purpose is to contain RDFa data -->
  <xsl:template match="xhtml:span[@property and @content and not(text())]"/>
  <xsl:template match="xhtml:span[@rel and @href and not(text())]"/>
  <xsl:template match="xhtml:span"/>

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

  <!-- getting a nested structure of headings and subheadings within a
       chapter is difficult due to them being on the same nesting, but
       http://stackoverflow.com/a/2165644/2718243 has an answer -->
  <xsl:template match="xhtml:div[@typeof='rinfoex:Avdelning']" mode="toc">
    <xsl:choose>
      <xsl:when test="xhtml:div[@class='underavdelning']"> <!-- 2010:110 and 1942:740 are the only ones that have these elements -->
	<xsl:variable name="chapters" select="xhtml:div/xhtml:div[@typeof='rpubl:Kapitel']"/>
	<xsl:variable name="firstchapter" select="$chapters[1]/@content"/>
	<xsl:variable name="lastchapter" select="$chapters[last()]/@content"/>
	<li><a href="#{@id}"><xsl:value-of select="xhtml:h1/@abbr"/> (kap. <xsl:value-of select="$firstchapter"/><xsl:if test="$firstchapter != $lastchapter">-<xsl:value-of select="$lastchapter"/></xsl:if>)</a>
	<ul class="nav">
	  <xsl:for-each select="xhtml:div[@class='underavdelning']">
	    <xsl:call-template name="toc-chapters"/>
	  </xsl:for-each>
	</ul>
	</li>
      </xsl:when>
      <xsl:otherwise>
	<xsl:call-template name="toc-chapters"/>
      </xsl:otherwise>
    </xsl:choose>
  </xsl:template>
      
  <xsl:template name="toc-chapters">
    <xsl:variable name="chapters" select="xhtml:div[@typeof='rpubl:Kapitel']"/>
    <xsl:variable name="firstchapter" select="$chapters[1]/@content"/>
    <xsl:variable name="lastchapter" select="$chapters[last()]/@content"/>
    <xsl:variable name="label">
      <xsl:choose>
	<xsl:when test="xhtml:h1/@abbr"><xsl:value-of select="xhtml:h1/@abbr"/></xsl:when>
	<xsl:otherwise><xsl:value-of select="xhtml:h1"/></xsl:otherwise>
      </xsl:choose>
    </xsl:variable>
    <li><a href="#{@id}"><xsl:value-of select="$label"/> (kap. <xsl:value-of select="$firstchapter"/><xsl:if test="$firstchapter != $lastchapter">-<xsl:value-of select="$lastchapter"/></xsl:if>)</a>
      <ul class="nav">
	<xsl:apply-templates mode="toc" select="xhtml:div[@typeof='rpubl:Kapitel']"/>
      </ul>
    </li>
  </xsl:template>

  <xsl:template match="xhtml:div[@typeof='rpubl:Kapitel' and @id]" mode="toc">
    <li><a href="#{@id}"><xsl:value-of select="xhtml:h1"/></a>
    <xsl:if test="xhtml:h2|xhtml:h3">
      <ul class="nav">
	<xsl:apply-templates mode="toc"/>
      </ul>
    </xsl:if>
    </li>
  </xsl:template>
  
  <xsl:template match="xhtml:h2" mode="toc">
    <xsl:variable name="this" select="."/>
    <xsl:variable name="subheadings" select="following-sibling::xhtml:h3[preceding-sibling::xhtml:h2[1] = $this][@id]"/>
    <xsl:variable name="subparas" select="following-sibling::xhtml:div[preceding-sibling::xhtml:h2[1] = $this]"/>
    <xsl:variable name="firstpara" select="$subparas[1]/@content"/><!-- select="$subparas[first()]/@content"/> -->
    <xsl:variable name="lastpara" select="$subparas[last()]/@content"/><!-- select="$subparas[last()]/@content"/> -->
    <xsl:variable name="scope"><!-- either '4 §' or '4-6 §§' -->
    <xsl:value-of select="$firstpara"/>&#160;<xsl:if test="$firstpara != $lastpara">- <xsl:value-of select="$lastpara"/> §</xsl:if>§</xsl:variable>
    <xsl:if test="@id">
      <li><a href="#{@id}"><xsl:value-of select="."/> (<xsl:value-of select="$scope"/>)</a>
      <xsl:if test="$subheadings">
	<ul class="nav">
	  <xsl:for-each select="$subheadings">
	    <xsl:if test="@id">
	      <li><a href="#{@id}"><xsl:value-of select="."/></a></li>
	    </xsl:if>
	  </xsl:for-each>
	</ul>
      </xsl:if>
      </li>
    </xsl:if>
  </xsl:template>

  <xsl:template match="xhtml:div[@typeof='rinfoex:Bilaga'][@id]" mode="toc">
    <li><a href="#{@id}"><xsl:value-of select="xhtml:h1"/></a></li>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='register']" mode="toc">
    <li><a href="#L"><xsl:value-of select="xhtml:h1"/></a>
    <ul class="nav">
      <xsl:for-each select="xhtml:div[@class='registerpost']">
	<li><a href="#{@id}"><xsl:value-of select="xhtml:span[@property='dcterms:identifier']/@content"/></a></li>
      </xsl:for-each>
    </ul>
    </li>
  </xsl:template>


  <!-- otherwise do nothing -->
  <xsl:template match="@*|node()" mode="toc"/>

</xsl:stylesheet>
