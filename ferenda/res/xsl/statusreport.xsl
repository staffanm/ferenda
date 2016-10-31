<xsl:stylesheet version="1.0"
		xmlns="http://www.w3.org/1999/xhtml"
		xmlns:atom="http://www.w3.org/2005/Atom"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		exclude-result-prefixes="xhtml rdf atom">

  <!-- assume a statusreport.xml like this:
  <status>
    <repo alias="propregeringen">
      <action id="parse">
        <basefile id="2013/14:40" success="true" duration="4.2532" time="2015-05-26 12:33:32"/>
        <basefile id="2013/14:41" success="true" duration="4.2532">
          <warnings>Warning text here...</warnings>
        </basefile>
        <basefile id="2013/14:42" success="true" duration="4.2532">
          <warnings>Warning text here...</warnings>
          <error>InvalidTreeError: xyz</error>
          <traceback>File "foo.py" line 123 ...</traceback>      
        </basefile>
      </action>
    </repo>
  </status>
  -->

  <xsl:import href="uri.xsl"/>
  <xsl:include href="base.xsl"/>

  <xsl:template name="headtitle">Status report | <xsl:value-of select="$configuration/sitename"/></xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>
  <xsl:template name="bodyclass">statusreport</xsl:template>
  <xsl:template name="pagetitle">
    <h1>Status report</h1>
    <div class="control-panel">
      <button onclick="$('div.alert-success').toggle()">show/hide successes</button>
      <button onclick="$('div.alert-warning').toggle()">show/hide warnings</button>
      <button onclick="$('div.alert-danger').toggle()">show/hide errors</button>
    </div>
  </xsl:template>
  <xsl:param name="dyntoc" select="true()"/>
  <xsl:param name="fixedtoc" select="true()"/>
  <xsl:param name="content-under-pagetitle" select="false()"/>

  <xsl:template match="/">
      <html>
          <xsl:call-template name="htmlhead"/>
          <xsl:call-template name="htmlbody"/>
      </html>
  </xsl:template>


  <xsl:template match="repo">
    <h2><xsl:value-of select="@alias"/></h2>
    <div class="basefiles">
      <xsl:apply-templates/>
    </div>
    <p><xsl:value-of select="count(basefile)"/> processed, 
    <xsl:value-of select="count(basefile[action/@success='False'])"/> failed,
    <xsl:value-of select="count(basefile[action/warnings])"/> had warnings</p>
  </xsl:template>

  <xsl:template match="action">
    <xsl:variable name="alerttype">
      <xsl:choose>
	<xsl:when test="@success='True' and ./warnings">alert-warning</xsl:when>
	<xsl:when test="@success='True'">alert-success</xsl:when>
	<xsl:when test="@success='False'">alert-danger</xsl:when>
      </xsl:choose>
    </xsl:variable> 
    <xsl:variable name="tooltip">
      <xsl:choose>
	<xsl:when test="@success='True' and ./warnings">
	<xsl:value-of select="./warnings"/></xsl:when>
	<xsl:when test="@success='False'">
	  <xsl:value-of select="./error"/>
-------------------
<xsl:value-of select="./traceback"/>
	</xsl:when>
      </xsl:choose>
    </xsl:variable> 
    <p class="alert {$alerttype}" title="{$tooltip}">
      <xsl:value-of select="@id"/>
    </p><br/>
      
  </xsl:template>

  <xsl:template match="basefile">
    <xsl:variable name="alerttype">
      <xsl:choose>
	<xsl:when test="action[@success='False']">alert-danger</xsl:when>
	<xsl:when test="action[@success='True'] and action/warnings">alert-warning</xsl:when>
	<xsl:when test="action[@success='True']">alert-success</xsl:when>
      </xsl:choose>
    </xsl:variable> 
    <div class="basefile alert {$alerttype}">
      <b><xsl:value-of select="@id"/></b><br/>
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="repo" mode="toc"/>

</xsl:stylesheet>
