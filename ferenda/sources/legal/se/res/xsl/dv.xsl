<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rpubl="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xmlns:bibo="http://purl.org/ontology/bibo/"
		xmlns:ext="http://exslt.org/common"
		exclude-result-prefixes="xhtml rdf">

  <xsl:import href="annotations-panel.xsl"/>
  <xsl:include href="base.xsl"/>

  <!-- Implementations of templates called by base.xsl -->
  <xsl:template name="headtitle"><xsl:value-of select="//xhtml:meta[@property='dcterms:identifier']/@content"/> | <xsl:value-of select="$configuration/sitename"/></xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>
  <xsl:template name="bodyclass">dv</xsl:template>

  <xsl:template name="pagetitle">
    <div class="section-wrapper toplevel">
      <section class="col-sm-7">
	<h1><xsl:value-of select="//xhtml:meta[@property='dcterms:identifier']/@content"/></h1>
	<p class="lead"><xsl:value-of select="//xhtml:meta[@property='rpubl:referatrubrik']/@content"/></p>
	<xsl:apply-templates/>
      </section>
      <xsl:call-template name="aside-annotations">
	<xsl:with-param name="uri" select="@about"/>
      </xsl:call-template>
    </div>
  </xsl:template>
  <xsl:param name="dyntoc" select="true()"/>
  <xsl:param name="fixedtoc" select="true()"/>
  <xsl:param name="content-under-pagetitle" select="true()"/>

  <xsl:template name="aside-annotations">
    <xsl:param name="uri"/>
    <xsl:variable name="domuri" select="//xhtml:link[@rel='rpubl:referatAvDomstolsavgorande']/@href"/>
    <xsl:variable name="metadata">
      <dl class="dl-horizontal">
	<dt>Domstol</dt>

	<dd><xsl:value-of select="substring-after(//xhtml:link[@rel='dcterms:publisher' and @about=$domuri]/@href, '/2008/')"/></dd>
	<dt>Avgörandedatum</dt>
	<dd><xsl:value-of select="//xhtml:meta[@property='rpubl:avgorandedatum' and @about=$domuri]/@content"/></dd>
	<dt>Målnummer</dt>
	<dd><xsl:value-of select="//xhtml:meta[@property='rpubl:malnummer' and @about=$domuri]/@content"/></dd>
	<xsl:if test="//xhtml:link[@rel='rpubl:lagrum' and @about=$domuri]">
	  <dt>Lagrum</dt>
	  <xsl:for-each select="//xhtml:link[@rel='rpubl:lagrum' and @about=$domuri]">
	    <dd><a href="{@href}"><xsl:value-of select="substring-after(@href,'https://lagen.nu/')"/></a></dd>
	  </xsl:for-each>
	</xsl:if>
	<xsl:if test="//xhtml:link[@rel='rpubl:rattsfallshanvisning']">
	  <dt>Rättsfall</dt>
	  <xsl:for-each select="//xhtml:link[@rel='rpubl:rattsfallshanvisning']">
	    <dd><a href="{@href}"><xsl:value-of select="substring-after(@href, '/rf/')"/></a></dd>
	  </xsl:for-each>
	</xsl:if>
	<xsl:if test="count(//xhtml:meta[@property='dcterms:relation']) > 0">
	  <dt>Litteratur <xsl:value-of select="count(//xhtml:meta[@property='dcterms:relation'])"/></dt>
	  <xsl:for-each select="//xhtml:meta[@property='dcterms:relation']">
	    <dd><xsl:value-of select="."/></dd>
	  </xsl:for-each>
	</xsl:if>
	<xsl:if test="//xhtml:link[@about=$domuri and @rel='dcterms:subject']">
	  <dt>Sökord</dt>
	  <xsl:for-each select="//xhtml:link[@about=$domuri and @rel='dcterms:subject']">
	    <dd><a href="{@href}"><xsl:value-of select="substring-after(@href, '/begrepp/')"/></a></dd>
	  </xsl:for-each>
	</xsl:if>
	<dt>Källa</dt>
	<dd><a href="http://www.rattsinfosok.dom.se/lagrummet/index.jsp">Domstolsverket</a></dd>
      </dl>
    </xsl:variable>
    <xsl:variable name="rattsfall" select="$annotations/resource[a/rpubl:Rattsfallsreferat]"/>
    <xsl:variable name="forarbeten" select="$annotations/resource[a/rpubl:Proposition]"/>
    <xsl:variable name="rattsfall-markup">
      <xsl:for-each select="$rattsfall">
	<!-- FIXME: tune width of rpubl:rattsfallsreferat -->
	<li><a href="{@uri}"><b><xsl:value-of select="dcterms:identifier"/></b>:</a> <xsl:value-of select="rpubl:referatrubrik"/></li>
      </xsl:for-each>
    </xsl:variable>

    <xsl:variable name="forarbeten-markup">
      <xsl:for-each select="$forarbeten">
	<li><b><xsl:value-of select="dcterms:identifier"/></b>: <xsl:value-of select="dcterms:title"/>
	<xsl:for-each select="bibo:chapter">
	  <a href="{@uri}#{.}"><xsl:value-of select="."/></a>
	</xsl:for-each>
	</li>
      </xsl:for-each>
    </xsl:variable>

    <aside class="panel-group col-sm-5" role="tablist" id="panel-top" aria-multiselectable="true">
      <xsl:call-template name="aside-annotations-panel">
	<xsl:with-param name="title">Metadata</xsl:with-param>
	<xsl:with-param name="badgecount"/>
	<xsl:with-param name="panelid">top</xsl:with-param>
	<xsl:with-param name="paneltype">metadata</xsl:with-param>
	<xsl:with-param name="expanded" select="true()"/>
	<xsl:with-param name="nodeset" select="ext:node-set($metadata)"/>
      </xsl:call-template>

      <xsl:if test="$rattsfall">
	<xsl:call-template name="aside-annotations-panel">
	  <xsl:with-param name="title">Rättsfall som hänvisar till detta</xsl:with-param>
	  <xsl:with-param name="badgecount" select="count($rattsfall)"/>
	  <xsl:with-param name="nodeset" select="ext:node-set($rattsfall-markup)"/>
	  <xsl:with-param name="panelid">top</xsl:with-param>
	  <xsl:with-param name="paneltype">rattsfall</xsl:with-param>
	  <xsl:with-param name="expanded" select="true()"/>
	</xsl:call-template>
      </xsl:if>

      <xsl:if test="$forarbeten">
	<xsl:call-template name="aside-annotations-panel">
	  <xsl:with-param name="title">Förarbeten som hänvisar till detta</xsl:with-param>
	  <xsl:with-param name="badgecount" select="count($forarbeten)"/>
	  <xsl:with-param name="nodeset" select="ext:node-set($forarbeten-markup)"/>
	  <xsl:with-param name="panelid">top</xsl:with-param>
	  <xsl:with-param name="paneltype">forarbeten</xsl:with-param>
	  <xsl:with-param name="expanded" select="true()"/>
	</xsl:call-template>
      </xsl:if>
    </aside>
  </xsl:template>
  
  <!-- FIXME: this template is copied from sfs.xsl, and should
       probably be in a lib that dv.xsl, sfs.xsl and lnkeyword.xsl can
       share. -->
  <xsl:template name="rattsfall">
    <xsl:param name="rattsfall"/>
      <xsl:for-each select="$rattsfall">
	<xsl:sort select="@uri"/>
	<xsl:variable name="tuned-width">
	  <xsl:call-template name="tune-width">
	    <xsl:with-param name="txt" select="rpubl:referatrubrik"/>
	    <xsl:with-param name="width" select="80"/>
	    <xsl:with-param name="def" select="80"/>
	  </xsl:call-template>
	</xsl:variable>
	<xsl:variable name="localurl"><xsl:call-template name="localurl"><xsl:with-param name="uri" select="@rdf:about"/></xsl:call-template></xsl:variable>
	<a href="{$localurl}"><b><xsl:value-of select="dcterms:identifier"/></b></a>:
	<xsl:choose>
	  <xsl:when test="string-length(rpubl:referatrubrik) > 80">
	    <xsl:value-of select="normalize-space(substring(rpubl:referatrubrik, 1, $tuned-width - 1))" />...
	  </xsl:when>
	  <xsl:otherwise>
	    <xsl:value-of select="rpubl:referatrubrik"/>
	  </xsl:otherwise>
	</xsl:choose>
	<br/>
      </xsl:for-each>
  </xsl:template>


  <xsl:template match="xhtml:div[@class='delmal']">
    <div>
      <h1><xsl:value-of select="@content"/></h1>
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='instans']">
    <div>
      <xsl:if test="@content">
	<h2><xsl:value-of select="@content"/></h2>
      </xsl:if>
    <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='dom']">
    <xsl:apply-templates/>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='domskal']">
    <div>
      <h3>Domskäl</h3>
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='domslut']">
    <div>
      <h3>Domskäl</h3>
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='skiljaktig']">
    <div>
      <h3>Skiljaktig</h3>
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='betankande']">
    <div>
      <h3>Betänkande</h3>
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='tillagg']">
    <div>
      <h3>Tillägg</h3>
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="xhtml:div[@class='endmeta']">
    <div>
      <!-- <h3>Metadata</h3> -->
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="xhtml:h1">
    <h4><xsl:value-of select="."/></h4>
  </xsl:template>
  
  <!-- last resort -->
  <xsl:template match="xhtml:div">
    <h1>THIS SHOULDN'T HAPPEN: <xsl:value-of select="@class"/></h1>
      <section>
	<xsl:apply-templates/>
      </section>
  </xsl:template>

  <!-- remove spans which only purpose is to contain RDFa data -->
  <xsl:template match="xhtml:span[@property and @content and not(text())]"/>
  
  <!-- default template: translate everything from whatever namespace
       it's in (usually the XHTML1.1 NS) into the default namespace
       -->
  <xsl:template match="*">
    <xsl:message>Adding element <xsl:value-of select="local-name()"/></xsl:message>
    <!-- remove element prefix -->
    <xsl:element name="{local-name()}">
      <!-- process attributes -->
      <xsl:for-each select="@*">
	<xsl:message>....Adding attribute <xsl:value-of select="local-name()"/></xsl:message>
        <!-- remove attribute prefix -->
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

