<?xml version="1.0" encoding="utf-8"?>
<!--
This should be able to handle any well-structured large document, but is only
really tested with direktiv, utredningar (SOU/Ds) and propositioner.
-->
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:prov="http://www.w3.org/ns/prov#"
		xmlns:bibo="http://purl.org/ontology/bibo/"
		xmlns:rpubl="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xmlns:ext="http://exslt.org/common"
		xmlns:str="http://exslt.org/strings"
		extension-element-prefixes="str"
		exclude-result-prefixes="xhtml rdf rdfs prov bibo rpubl ext">
  <xsl:include href="base.xsl"/>
  <xsl:include href="metadata-only.xsl"/>

  <!-- Implementations of templates called by base.xsl -->
  <xsl:template name="headtitle"><xsl:value-of select="//xhtml:title"/> | <xsl:value-of select="$configuration/sitename"/></xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>
  <xsl:template name="bodyclass">forarbete</xsl:template>
  <xsl:template name="pagetitle">
    <div class="row toplevel">
      <section class="col-sm-8">
	<p style="font-size: 24pt;"><xsl:value-of select="../xhtml:head/xhtml:meta[@property='dcterms:identifier']/@content"/></p>
	<p style="font-size: 20pt;"><xsl:value-of select="../xhtml:head/xhtml:title"/></p>
      </section>
      <aside class="source col-sm-4">
	<xsl:variable name="docuri" select="@about"/>
	<xsl:variable name="derivedfrom" select="//xhtml:head/xhtml:link[@rel='prov:wasDerivedFrom']/@href"/>
	<xsl:variable name="alternateof" select="//xhtml:head/xhtml:link[@rel='prov:alternateOf']/@href"/>
	<xsl:if test="$alternateof">
	<div class="panel-group">
	  <div class="panel panel-default">
	    <div class="panel-heading">
	      Källor
	    </div>
	    <div class="panel-body">
	      <xsl:choose>
		<xsl:when test="//xhtml:head/xhtml:meta[@property='olo:index']">
		  <!-- original documents are more than one file -->
		  Originaldokument:
		  <ul>
		    <xsl:for-each select="//xhtml:head/xhtml:meta[@property='olo:index']">
		      <xsl:sort select="@content"/>
		      <xsl:variable name="derivedfrom-part" select="@about"/>
		      <li><a href="{$derivedfrom-part}"><xsl:value-of select="//xhtml:head/xhtml:meta[@property='rdfs:label' and @about=$derivedfrom-part]/@content"/></a></li>
		    </xsl:for-each>
		  </ul>
		  <a href="{$alternateof}">Källa</a> <!-- the original uri, always just one -->
		</xsl:when>
		<xsl:otherwise>
		  <!-- original document is a single file -->
		  Originaldokument: <a href="{$derivedfrom}"><xsl:value-of select="//xhtml:head/xhtml:meta[@about=$derivedfrom]/@content"/></a>, <a href="{$alternateof}">Källa</a>
		</xsl:otherwise>
	      </xsl:choose>
	    </div>
	  </div>
	</div>
	</xsl:if>
      </aside>
    </div>
  </xsl:template>
  <xsl:param name="dyntoc" select="false()"/>
  <xsl:param name="fixedtoc" select="true()"/>
  <xsl:param name="content-under-pagetitle" select="false()"/>


  <!-- Headings shouldn't be expressed with <h*> tags, but rather with
       RDFa attribs in <div class="section"> element. However,
       DirTrips still generates h1 headings, so we can't just ignore
       these. -->
  <!-- <xsl:template match="xhtml:h1|xhtml:h2"/> -->

  <xsl:template name="aside-annotations">
    <xsl:param name="uri"/>
    <xsl:param name="elem">div</xsl:param>
    <xsl:param name="class">col-sm-4</xsl:param>
    <xsl:if test="$annotations/resource[@uri=$uri]">
      <!-- <div class="col-sm-4">-->
      <xsl:element name="{$elem}"><xsl:attribute name="class"><xsl:value-of select="$class"/></xsl:attribute>
      <div class="panel-group">
	<div class="panel panel-default">
	  <div class="panel-heading">
	    <h4 class="panel-title">Hänvisningar till <xsl:value-of select="substring-after($uri,'#')"/></h4>
	  </div>
	  <div class="panel-body">
	    <xsl:call-template name="render-referers">
	      <xsl:with-param name="uri" select="$uri"/>
	    </xsl:call-template>
	  </div>
	</div>
      </div>
      </xsl:element>
    </xsl:if>
  </xsl:template>

  <xsl:key name="referers-by-document" match="resource" use="dcterms:isPartOf/@ref"/>

  <xsl:template name="render-referers">
    <xsl:param name="uri"/>
    <xsl:variable name="resources">
      <xsl:for-each select="$annotations/resource[@uri=$uri]/dcterms:isReferencedBy">
	<xsl:sort select="@ref"/>
	<xsl:variable name="refuri" select="@ref"/>
	<dcterms:isReferencedBy ref="{$refuri}">
	  <xsl:copy-of select="$annotations/resource[@uri=$refuri]"/>
	</dcterms:isReferencedBy>
      </xsl:for-each>
    </xsl:variable>
    <xsl:variable name="resroot" select="$annotations/resource[@uri=$uri]"/>
    <ul>
      <xsl:for-each select="ext:node-set($resources)/dcterms:isReferencedBy/resource[count(. | key('referers-by-document', dcterms:isPartOf/@ref)[1]) = 1]">
	<xsl:variable name="current-grouping-key" 
                      select="dcterms:isPartOf/@ref"/>
	<xsl:variable name="current-group" 
                      select="key('referers-by-document', $current-grouping-key)"/>
	<xsl:variable name="rootresource" select="$annotations/resource[@uri=$current-grouping-key]"/>
	<li>
	  <xsl:choose>
	    <xsl:when test="$rootresource/a/rpubl:Utredningsbetankande or $rootresource/a/rpubl:Proposition">
	      <b title="{$rootresource/dcterms:title}"><xsl:value-of select="$rootresource/dcterms:identifier"/>:</b>
	      <xsl:call-template name="render-doc-referers">
		<xsl:with-param name="current-group" select="$current-group"/>
	      </xsl:call-template>
	    </xsl:when>
	    <xsl:otherwise>
	      <!-- If the current-grouping-key isn't identical to the
	           root resource, it's probably just a section in a
	           referat. In both cases, we should just link the root
	           resource. -->
	      <a href="{$rootresource/@uri}"><xsl:value-of select="$rootresource/dcterms:identifier"/></a>
	    </xsl:otherwise>
	  </xsl:choose>
	</li>
      </xsl:for-each>
    </ul>
  </xsl:template>

  <xsl:template name="render-doc-referers">
    <xsl:param name="current-group"/>
    Avsnitt <xsl:for-each select="$current-group"><xsl:sort select="@uri"/>
    <a href="{@uri}" title="{dcterms:title}"><xsl:value-of select="rdfs:label|bibo:chapter|dcterms:title"/></a><xsl:if test="position() != last()">, </xsl:if></xsl:for-each>
  </xsl:template>

  <!-- This matches all top-level elements that are contained in a
       section div. This is typically preamble stuff -->
  <xsl:template match="xhtml:body/*[self::xhtml:p or self::xhtml:span]" priority="10">
    <div class="row toplevel">
      <section class="col-sm-8">
	<xsl:if test="local-name() = 'span'"><xsl:call-template name="sidbrytning"/></xsl:if><xsl:if test="local-name() != 'span'"><xsl:element name="{local-name()}"><xsl:apply-templates/></xsl:element></xsl:if>
      </section>
    </div>
  </xsl:template>
  
  <xsl:template match="xhtml:body/xhtml:div">
    <!-- this might be used for sections that aren't referencable
         entiries in their own right, but still containers of other
         things, ie the Protokollsutdrag structure of older
         propositions (c.f. prop 1990/91:172) -->
    <div class="row toplevel">
      <section id="{substring-after(@about,'#')}" class="col-sm-8">
	<xsl:if test="@content">
	  <h2><xsl:value-of select="@content"/></h2>
	</xsl:if>
	<!-- FIXME: We try to avoid including referencable
	     sub-entities here, since they need to be wrapped in a
	     div.row, and we can't nest those. 
	-->
	<xsl:apply-templates select="*[not(@about)]"/>
      </section>
      <!--
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="../@about"/>
	</xsl:call-template>
	-->
    </div>
    <xsl:apply-templates select="*[@about]"/>
  </xsl:template>
    
  <!-- everything that has an @about attribute, i.e. _is_ something
       (with a URI) gets a <section> with an <aside> for inbound links etc -->
  <xsl:template match="xhtml:div[@about and (@class='section' or @class='preamblesection' or @class='unorderedsection')]">
    <div class="row" about="{@about}"><!-- needed? -->
      <section id="{substring-after(@about,'#')}" class="col-sm-8">
	<xsl:variable name="sectionheading"><xsl:if test="xhtml:span/@content"><xsl:value-of select="xhtml:span/@content"/>. </xsl:if><xsl:value-of select="@content"/></xsl:variable>
	<xsl:if test="count(ancestor::*) = 2">
	    <h2><xsl:value-of select="$sectionheading"/></h2>
	</xsl:if>
	<xsl:if test="count(ancestor::*) = 3">
	  <h3><xsl:value-of select="$sectionheading"/></h3>
	</xsl:if>
	<xsl:if test="count(ancestor::*) = 4">
	  <h4><xsl:value-of select="$sectionheading"/></h4>
	</xsl:if>
       <xsl:apply-templates select="*[not(@about and @class!='forfattningskommentar')]"/>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
    <!--
    We handle all @about sections afterwards the rest to flatten out sections, ie from
    a structure like:

    4
      4.1
        4.1.1
        4.1.2

    we produce
         
    4
    4.1
    4.1.1
    4.1.2


   This only works when a @about sections only have other @about
   sections as direct descendents, or it has no @about sections as
   direct descendents. For forfattningskommentar subsections, this
   does not hold.
    -->
         	 
    <xsl:apply-templates select="xhtml:div[@about and @class!='forfattningskommentar']"/>
  </xsl:template>


  <xsl:template match="xhtml:div[@about and @class='forfattningskommentar']">
    <xsl:if test="string-length(@content) > 0">
      <h3><xsl:value-of select="@content"/></h3>
    </xsl:if>
    <div class="forfattningskommentar" id="{substring-after(@about, '#')}">
      <xsl:for-each select="*">
	<xsl:apply-templates/>
	<xsl:if test="@class='sidbrytning'">
	  [<xsl:value-of select="local-name(.)"/>]
	  <xsl:call-template name="sidbrytning"/>
	</xsl:if>
      </xsl:for-each>
      <!-- <xsl:apply-templates select="xhtml:div/xhtml:div/*"/> -->
    </div>
  </xsl:template>

  <!-- remove prop{rubrik,huvudrubrik} as they are duplicates of what occurs in pagetitle -->
  <xsl:template match="xhtml:h1[@class='prophuvudrubrik' or @class='proprubrik']"/>
  
  <!-- remove spans which only purpose is to contain RDFa data -->
  <xsl:template match="xhtml:span[@property and @content and not(text())]"/>

  <xsl:template match="xhtml:div[@about]" mode="toc"/>

  <xsl:template match="xhtml:span[@class='sidbrytning']" name="sidbrytning">
    <div class="sida" id="{@id}">
      <!-- Nav tabs -->
      <ul class="nav nav-tabs">
	<li class="active"><a href="#{@id}-text" class="view-text">Sida <xsl:value-of select="substring(@id,4)"/></a></li>
	<li><a href="#{@id}-img" class="view-img"><span class="glyphicon glyphicon-picture">&#160;</span>Original</a></li>
      </ul>
      <a href="{@src}" class="facsimile"><img data-src="{@src}"/></a>
    </div>
    <xsl:variable name="uri"><xsl:value-of select="//@about"/>#<xsl:value-of select="@id"/></xsl:variable>
    <xsl:call-template name="aside-annotations">
      <xsl:with-param name="uri" select="$uri"/>
      <xsl:with-param name="elem">aside</xsl:with-param>
      <xsl:with-param name="class">sidannotering</xsl:with-param>
    </xsl:call-template>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='metadata-only']">
    <xsl:variable name="about" select="//xhtml:body/@about"/>
    <xsl:variable name="generator" select="//xhtml:meta[@property='prov:wasGeneratedBy']/@content"/>
    <xsl:variable name="repo" select="str:tokenize($about, '/')[3]"/>
    <xsl:variable name="subrepo" select="translate(str:tokenize($generator, '.')[last()], 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')"/>
    <!-- FIXME: this gives incorrect results for uris like http://lagen.nu/prop/2017/18:42 -->
    <!-- <xsl:variable name="basefile" select="str:tokenize($about, '/')[last()]"/> -->
    <xsl:variable name="basefile" select="substring-after($about, concat('/', $repo, '/'))"/>
    <!--
    <p>
      about: <xsl:value-of select="$about"/><br/>
      repo: <xsl:value-of select="$repo"/><br/>
      subrepo: <xsl:value-of select="$subrepo"/><br/>
      basefile: <xsl:value-of select="$basefile"/><br/>
      </p>
    -->
    <div class="row">
      <section class="col-sm-8">
	<xsl:call-template name="metadata-only">
	  <xsl:with-param name="repo" select="$repo"/>
	  <xsl:with-param name="subrepo" select="$subrepo"/>
	  <xsl:with-param name="basefile" select="$basefile"/>
	</xsl:call-template>
      </section>
    </div>
  </xsl:template>
  
  <!-- remove these empty elements (often <i/> or <span/> tags) -->
  <xsl:template match="xhtml:span|xhtml:i[not(string())]">
  </xsl:template>
  <!-- default template: translate everything from whatever namespace
       it's in (usually the XHTML1.1 NS) into the default namespace
       -->
  <xsl:template match="*">
    <xsl:element name="{name()}">
      <xsl:apply-templates select="@*|node()"/>
    </xsl:element>
  </xsl:template>
  <!-- remove attributes left behind by pdfreader that we have no clear use for -->
  <xsl:template match="@style"/>
  <xsl:template match="@class"/>
  <xsl:template match="@*">
    <xsl:copy><xsl:apply-templates/></xsl:copy>
  </xsl:template>



  <!-- TABLE OF CONTENTS (TOC) HANDLING -->
  <xsl:template match="xhtml:div[@typeof='bibo:DocumentPart']" mode="toc">
    <xsl:variable name="label">
      <xsl:if test="@class = 'appendix'">
	Bilaga
      </xsl:if>
      <xsl:if test="xhtml:span[@property='bibo:chapter']">
	<xsl:value-of select="xhtml:span[@property='bibo:chapter']/@content"/>. 
      </xsl:if>
      <xsl:value-of select="@content"/>
    </xsl:variable>
    <li><a href="#{substring-after(@about, '#')}"><xsl:value-of select="$label"/></a>
    <xsl:if test="xhtml:div[@typeof='bibo:DocumentPart']">
      <ul class="nav">
	<xsl:apply-templates mode="toc"/>
      </ul>
    </xsl:if>
    </li>
  </xsl:template>
  
  <!-- otherwise do nothing -->
  <xsl:template match="@*|node()" mode="toc"/>
  
</xsl:stylesheet>
