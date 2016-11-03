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
		xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		exclude-result-prefixes="xhtml rdf rdfs prov">

  <xsl:import href="uri.xsl"/>
  <xsl:include href="base.xsl"/>

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
	      Originaldokument: <a href="{$derivedfrom}"><xsl:value-of select="//xhtml:head/xhtml:meta[@about=$derivedfrom]/@content"/></a>, <a href="{$alternateof}">Källa</a>
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
  
  <xsl:template match="xhtml:a">
    <xsl:call-template name="link"/>
  </xsl:template>

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
	    <ul>
	    <xsl:for-each select="$annotations/resource[@uri=$uri]/dcterms:isReferencedBy">
	      <xsl:variable name="referencing" select="@ref"/>
	      <xsl:variable name="label">
		<xsl:variable name="referrer" select="$annotations/resource[@uri=$referencing]"/>
		<xsl:choose>
		  <xsl:when test="$referrer">
		    <xsl:if test="$referrer/dcterms:isPartOf"><xsl:value-of select="$annotations/resource[@uri=$referrer/dcterms:isPartOf/@ref]/dcterms:identifier"/>: </xsl:if>
		    <xsl:choose>
		      <xsl:when test="$referrer/rdfs:label">
			<xsl:value-of select="$referrer/rdfs:label"/>
		      </xsl:when>
		      <xsl:when test="$referrer/dcterms:identifier">
			<xsl:value-of select="$referrer/dcterms:identifier"/>
		      </xsl:when>
		      <xsl:when test="$referrer/bibo:chapter">
			avsnitt <xsl:value-of select="$referrer/bibo:chapter"/>
		      </xsl:when>
		      <xsl:when test="$referrer/dcterms:creator">
			(<xsl:value-of select="$referrer/dcterms:creator"/>)
		      </xsl:when>
		      <xsl:otherwise>
			<xsl:value-of select="substring-after($referencing,'#')"/>
		      </xsl:otherwise>
		    </xsl:choose>
		  </xsl:when>
		  <xsl:otherwise>
		    <!-- the base case: we have no useful labels for for the referring doc, so just show the URI -->
		    <xsl:value-of select="@ref"/>
		  </xsl:otherwise>
	      </xsl:choose></xsl:variable>
	      <li><a href="{@ref}"><xsl:value-of select="$label"/></a></li>
	    </xsl:for-each>
	    </ul>
	  </div>
	</div>
      </div>
      </xsl:element>
    </xsl:if>
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
      <section id="{translate(substring-after(@about,'#'),'.','-')}" class="col-sm-8">
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
      <section id="{translate(substring-after(@about,'#'),'.','-')}" class="col-sm-8">
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
    <div class="forfattningskommentar" id="{translate(substring-after(@about, '#'),'.','-')}">
       <xsl:apply-templates select="xhtml:div/xhtml:div/*"/>
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
      <img data-src="{@src}" class="facsimile"/>
      <!--
      <p class="sidbrytning"><i>Sida <xsl:value-of select="substring(@id,4)"/></i>
      <button type="button" class="view-facsimile pull-left">
 	<span>Visa faksimil</span>
 	<span style="display: none">Visa text</span>
      </button>
      </p>
      -->
    </div>
    <xsl:variable name="uri"><xsl:value-of select="//@about"/>#<xsl:value-of select="@id"/></xsl:variable>
    <xsl:call-template name="aside-annotations">
      <xsl:with-param name="uri" select="$uri"/>
      <xsl:with-param name="elem">aside</xsl:with-param>
      <xsl:with-param name="class">sidannotering</xsl:with-param>
    </xsl:call-template>
  </xsl:template>


  <!-- remove these empty elements (often <i/> or <span/> tags) -->
  <xsl:template match="xhtml:span|xhtml:i[not(text())]">
  </xsl:template>
  <!-- default template: translate everything from whatever namespace
       it's in (usually the XHTML1.1 NS) into the default namespace
       NOTE: It removes any attributes not accounted for otherwise
       -->
  <xsl:template match="*">
    <xsl:element name="{local-name(.)}"><xsl:apply-templates select="node()"/></xsl:element>
  </xsl:template>

  <xsl:template match="@*">
    <xsl:attribute name="{local-name(.)}"><xsl:apply-templates select="@*"/></xsl:attribute>
  </xsl:template>

  <!-- alternatively: identity transform (keep source namespace) -->
  <!--
  <xsl:template match="@*|node()">
    <xsl:copy>
      <xsl:apply-templates select="@*|node()"/>
    </xsl:copy>
  </xsl:template>
  --> 


  <!-- TABLE OF CONTENTS (TOC) HANDLING -->
  <xsl:template match="xhtml:div[@typeof='bibo:DocumentPart']" mode="toc">
    <xsl:message>found a top-level document part</xsl:message>
    <xsl:variable name="label">
      <xsl:if test="@class = 'appendix'">
	Bilaga
      </xsl:if>
      <xsl:if test="xhtml:span[@property='bibo:chapter']">
	<xsl:value-of select="xhtml:span[@property='bibo:chapter']/@content"/>. 
      </xsl:if>
      <xsl:value-of select="@content"/>
    </xsl:variable>
    <li><a href="#{translate(substring-after(@about, '#'), '.', '-')}"><xsl:value-of select="$label"/></a>
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
