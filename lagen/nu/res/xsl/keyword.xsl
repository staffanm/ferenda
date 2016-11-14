<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xmlns:ext="http://exslt.org/common"
		xml:space="preserve"
		exclude-result-prefixes="xhtml rdf">

  <xsl:import href="uri.xsl"/>
  <xsl:import href="annotations-panel.xsl"/>
  <xsl:include href="base.xsl"/>
  <!-- NOTE: this annotation file does not use Grit syntax (yet) -->
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
      <section class="col-sm-7">
	<xsl:apply-templates select="$myannotations/rdf:Description/dcterms:description/xhtml:div"/>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
  </xsl:template>
  <xsl:param name="dyntoc" select="false()"/>
  <xsl:param name="fixedtoc" select="true()"/>
  <xsl:param name="content-under-pagetitle" select="true()"/>
      

  <xsl:template match="xhtml:a">
    <xsl:call-template name="link"/>
  </xsl:template>

  <xsl:template name="aside-annotations">
    <xsl:param name="uri"/>
    <xsl:variable name="legaldefinitioner" select="$myannotations/rdf:Description/rinfoex:isDefinedBy/*"/>
    <xsl:variable name="rattsfall" select="$myannotations/rdf:Description/dcterms:subject/rdf:Description"/>
    <xsl:variable name="rattsfall-markup">
      <ul>
	<xsl:for-each select="$rattsfall">
	  <li><a href="{@rdf:about}"><b><xsl:value-of select="dcterms:identifier"/></b></a>:
	  <xsl:value-of select="dcterms:description"/></li>
	</xsl:for-each>
      </ul>
    </xsl:variable>
    <xsl:variable name="legaldefinitioner-markup">
      <ul>
	<xsl:for-each select="$legaldefinitioner">
	  <li><a href="{@rdf:about}"><xsl:value-of select="rdfs:label"/></a></li>
	</xsl:for-each>
      </ul>
    </xsl:variable>

    <aside class="panel-group col-sm-5" role="tablist" id="panel-top"
	   aria-multiselectable="true">
      <xsl:if test="$rattsfall">
	<xsl:call-template name="aside-annotations-panel">
	  <xsl:with-param name="title">Rättsfall</xsl:with-param>
	  <xsl:with-param name="badgecount" select="count($rattsfall)"/>
	  <xsl:with-param name="nodeset" select="ext:node-set($rattsfall-markup)"/>
	  <xsl:with-param name="panelid">top</xsl:with-param>
	  <xsl:with-param name="paneltype">rattsfall</xsl:with-param>
	  <xsl:with-param name="expanded" select="true()"/>
	</xsl:call-template>
      </xsl:if>

      <xsl:if test="$legaldefinitioner">
	<xsl:call-template name="aside-annotations-panel">
	  <xsl:with-param name="title">Legaldefinitioner</xsl:with-param>
	  <xsl:with-param name="badgecount" select="count($rattsfall)"/>
	  <xsl:with-param name="nodeset" select="ext:node-set($legaldefinitioner-markup)"/>
	  <xsl:with-param name="panelid">top</xsl:with-param>
	  <xsl:with-param name="paneltype">legaldefinitioner</xsl:with-param>
	  <xsl:with-param name="expanded" select="true()"/>
	</xsl:call-template>
      </xsl:if>
    </aside>
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

