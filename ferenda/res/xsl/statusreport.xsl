<xsl:stylesheet version="1.0"
		xmlns="http://www.w3.org/1999/xhtml"
		xmlns:atom="http://www.w3.org/2005/Atom"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xml:space="preserve"
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
  <xsl:template match="/">
      <html>
          <xsl:call-template name="htmlhead"/>
          <xsl:call-template name="htmlbody"/>
      </html>
  </xsl:template>


  <xsl:template match="repo">
      <h2><xsl:value-of select="@alias"/></h2>
      <xsl:apply-templates/>
  </xsl:template>

  <xsl:template matcH="action">
      <h3><xsl:value-of select="@action"/></h3>
      <p><xsl:count select="basefile[@success='true']"/> OK, 
         <xsl:count select="basefile[@success='false']"/> failed, </p>
      <div class="basefiles"> <!-- css can hook into this to select a good display mode -->
         <xsl:apply-templates/>
      </div>
  </xsl:template>

  <xsl:template match="basefile">
    <xsl:variable name="alerttype">
      <xsl:choose>
        <xsl:when test="@success='true' and ./warnings">alert-warnings</xsl:when>
        <xsl:when test="@success='true'">alert-success</xsl:when>
        <xsl:when test="@success='false'">alert-error</xsl:when>          
      <xsl:choose>
    </Xsl:variable> 
    <p class="alert {$alerttype}"><xsl:value-of select="@id"/>
      <!-- FIXME: add errormsg, warnings, traceback here somehow -->
    </p>
  </xsl:template>
</xsl:stylesheet>
