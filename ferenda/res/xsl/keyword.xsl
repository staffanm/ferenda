<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xml:space="preserve"
		exclude-result-prefixes="xhtml rdf">

  <xsl:import href="uri.xsl"/>
  <xsl:include href="base.xsl"/>

  <xsl:variable name="myannotations" select="document($annotationfile)/rdf:RDF"/>
  <!-- Implementations of templates called by base.xsl -->
  <xsl:template name="headtitle"><xsl:value-of select="//xhtml:title"/> | <xsl:value-of select="$configuration/sitename"/></xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>
  <xsl:template name="bodyclass">keyword</xsl:template>
  <xsl:template name="pagetitle">
    <h1><xsl:value-of select="../xhtml:head/xhtml:title"/></h1>
    <!--
    because the keyword xhtml files doesn't contain anything, we use
    this point to add all of our content -->
    <div class="section-wrapper toplevel">
      <section>
	<xsl:apply-templates select="$myannotations/rdf:Description/dcterms:description/xhtml:div"/>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
    
  </xsl:template>
      

  <xsl:template match="xhtml:a">
    <xsl:call-template name="link"/>
  </xsl:template>

  <xsl:template name="aside-annotations">
    <xsl:param name="uri"/>
    <xsl:variable name="legaldefs" select="$myannotations/rdf:Description/rinfoex:isDefinedBy/*"/>
    <xsl:variable name="rattsfall" select="$myannotations/rdf:Description/dcterms:subject/rdf:Description"/>
    <xsl:message>aside: <xsl:value-of select="count($legaldefs)"/> legaldefs, <xsl:value-of select="count($rattsfall)"/> legalcases</xsl:message>
    <xsl:if test="$rattsfall">
      <aside class="annotations rattsfall">
	<h2>Rättsfall (<xsl:value-of select="count($rattsfall)"/>)</h2>
	<xsl:call-template name="rattsfall">
	  <xsl:with-param name="rattsfall" select="$rattsfall"/>
	</xsl:call-template>
      </aside>
    </xsl:if>

    <xsl:if test="$legaldefs">
      <aside class="annotations lagrumshanvisningar">
	<h2>Lagrumshänvisningar hit (<xsl:value-of select="count($legaldefs)"/>)</h2>
	<!-- call the template -->
	<xsl:call-template name="inbound">
	  <xsl:with-param name="inbound" select="$legaldefs"/>
	</xsl:call-template>
      </aside>
    </xsl:if>
  </xsl:template>

  <!-- FIXME: these 2 templates are copied from sfs.xsl, and they
       should probably be part of lnkeyword.xsl -->
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
	  <xsl:variable name="localurl"><xsl:call-template name="localurl"><xsl:with-param name="uri" select="@rdf:about"/></xsl:call-template></xsl:variable>
	  <a href="{$localurl}"><xsl:value-of select="rdfs:label"/></a>
	</li>
      </xsl:for-each>
    </ul>
  </xsl:template>



  <xsl:template match="xhtml:body/xhtml:div">
  </xsl:template>


  <!-- remove spans which only purpose is to contain RDFa data -->
  <xsl:template match="xhtml:span[@property and @content and not(text())]"/>
  
  <!-- default template: translate everything from whatever namespace
       it's in (usually the XHTML1.1 NS) into the default namespace
       NOTE: It removes any attributes not accounted for otherwise
       -->
  <xsl:template match="*">
    <xsl:element name="{local-name(.)}"><xsl:apply-templates select="node()"/></xsl:element>
  </xsl:template>

  <!-- toc handling (do nothing) -->
  <xsl:template match="@*|node()" mode="toc"/>
  
</xsl:stylesheet>

