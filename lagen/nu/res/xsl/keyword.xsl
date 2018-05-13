<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rpubl="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xmlns:ext="http://exslt.org/common"
		exclude-result-prefixes="xhtml rdf">

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
	<xsl:choose><xsl:when test="count($myannotations/rdf:Description/dcterms:description/xhtml:div) > 0">
	  <xsl:apply-templates select="$myannotations/rdf:Description/dcterms:description/xhtml:div"/>
	</xsl:when><xsl:otherwise><p class="alert alert-warning">Beskrivning saknas!</p></xsl:otherwise>
	</xsl:choose>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
  </xsl:template>
  <xsl:param name="dyntoc" select="false()"/>
  <xsl:param name="fixedtoc" select="true()"/>
  <xsl:param name="content-under-pagetitle" select="true()"/>
      

  <xsl:template name="aside-annotations">
    <xsl:param name="uri"/>
    <xsl:variable name="links" select="$myannotations/rdf:Description/rdfs:seeAlso/*"/>
    <xsl:message>Found <xsl:value-of select="count($links)"/> link</xsl:message>
    <xsl:variable name="legaldefinitioner" select="$myannotations/rdf:Description/rinfoex:isDefinedBy/*"/>
    <xsl:variable name="rattsfall" select="$myannotations/rdf:Description/dcterms:subject/rdf:Description"/>
    <xsl:variable name="rattsfall-markup">
      <ul>
	<xsl:for-each select="$rattsfall">
	  <li><xhtml:a href="{@rdf:about}"><b><xsl:value-of select="dcterms:identifier"/></b></xhtml:a>:
	  <xsl:value-of select="dcterms:description"/></li>
	</xsl:for-each>
      </ul>
    </xsl:variable>
    <xsl:variable name="legaldefinitioner-markup">
      <ul>
	<xsl:for-each select="$legaldefinitioner">
	  <li><xhtml:a href="{@rdf:about}"><xsl:value-of select="rdfs:label"/></xhtml:a></li>
	</xsl:for-each>
      </ul>
    </xsl:variable>
    <xsl:variable name="links-markup">
      <ul>
	<xsl:for-each select="$links">
	  <xsl:variable name="href" select="@rdf:about"/>
	  <li><xhtml:a href="{@rdf:about}"><xsl:value-of select="rdfs:label"/></xhtml:a></li>
	</xsl:for-each>
      </ul>
    </xsl:variable>

    <aside class="panel-group col-sm-5" role="tablist" id="panel-top"
	   aria-multiselectable="true">
      <xsl:if test="$links">
	<xsl:call-template name="aside-annotations-panel">
	  <xsl:with-param name="title">Länkar</xsl:with-param>
	  <!-- <xsl:with-param name="badgecount" select="count($rattsfall)"/> -->
	  <xsl:with-param name="nodeset" select="ext:node-set($links-markup)"/>
	  <xsl:with-param name="panelid">top</xsl:with-param>
	  <xsl:with-param name="paneltype">links</xsl:with-param>
	  <xsl:with-param name="expanded" select="true()"/>
	</xsl:call-template>
      </xsl:if>

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
  -->
  <xsl:template match="*">
    <xsl:element name="{local-name()}">
      <xsl:for-each select="@*">
        <xsl:attribute name="{local-name()}">
          <xsl:value-of select="."/>
        </xsl:attribute>
      </xsl:for-each>
      <xsl:apply-templates/>
    </xsl:element>
  </xsl:template>

  <!-- toc handling (do nothing) -->
  <xsl:template match="@*|node()" mode="toc"/>
  
</xsl:stylesheet>

