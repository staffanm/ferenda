<xsl:stylesheet version="1.0"
		xmlns="http://www.w3.org/1999/xhtml"
		xmlns:atom="http://www.w3.org/2005/Atom"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:date="http://exslt.org/dates-and-times" 
		extension-element-prefixes="date"
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

  <xsl:include href="base.xsl"/>

  <xsl:template name="headtitle">Status report | <xsl:value-of select="$configuration/sitename"/></xsl:template>
  <xsl:template name="metarobots"/>
  <xsl:template name="linkalternate"/>
  <xsl:template name="headmetadata"/>
  <xsl:template name="bodyclass">statusreport</xsl:template>
  <xsl:template name="pagetitle">
    <xsl:variable name="total" select="count(//basefile)"/>
    <xsl:variable name="failed" select="count(//basefile[action/@success='False'])"/>
    <xsl:variable name="warnings" select="count(//basefile[action/warnings])"/>
    <h1>Status report
    <small><xsl:value-of select="date:date-time()"/>-

      <!--
      <xsl:value-of select="date:year()"/>-
      <xsl:value-of select="date:month-in-year()"/>-
      <xsl:value-of select="date:day-in-month()"/> | 
      <xsl:value-of select="date:hour-in-day()"/>:
      <xsl:value-of select="date:minute-in-hour()"/>:
      <xsl:value-of select="date:second-in-minute()"/>
      -->
    </small>
    </h1>
    <h2 data-toc-skip="true">
      <xsl:value-of select="$total"/> total documents
    </h2>
    <p>
      <small>
	<xsl:value-of select="round(($failed div $total) * 100)"/> % failed,
	<xsl:value-of select="round(($warnings div $total) * 100)"/> % warnings
      </small>
    </p>
    <div class="control-panel">
      <!-- <button onclick="$('div.alert-success').toggle()">show/hide successes</button> -->
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
    <xsl:variable name="total" select="count(basefile)"/>
    <xsl:variable name="failed" select="count(basefile[action/@success='False'])"/>
    <xsl:variable name="warnings" select="count(basefile[action/warnings])"/>
    <xsl:variable name="duration" select="sum(basefile/action/@duration)"/>
    <h2>
      <xsl:value-of select="@alias"/>
    </h2>
    <p>
    <small>
	<xsl:value-of select="round(($failed div $total) * 100)"/> % failed,
	<xsl:value-of select="round(($warnings div $total) * 100)"/> % warnings,
	<xsl:value-of select="round($duration * 100 div $total) div 100"/> s avg parse time
    </small>
    </p>
    <div class="basefiles">
      <xsl:apply-templates/>
    </div>
    <p><xsl:value-of select="$total"/> processed, 
    <xsl:value-of select="$failed"/> failed,
    <xsl:value-of select="$warnings"/> had warnings</p>
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
    <xsl:if test="action[@success='False'] or action/warnings">
      <xsl:variable name="alerttype">
	<xsl:choose>
	  <xsl:when test="action[@success='False']">alert-danger</xsl:when>
	  <xsl:when test="action[@success='True'] and action/warnings">alert-warning</xsl:when>
	  <xsl:when test="action[@success='True']">alert-success</xsl:when>
	</xsl:choose>
      </xsl:variable> 
      <div class="basefile alert {$alerttype}">
	<!-- can't happen now that we don't render any alert-success divs
	    <xsl:if test="$alerttype = 'alert-success'"><xsl:attribute name="style">display: none;</xsl:attribute></xsl:if>
	-->
	<b><xsl:value-of select="@id"/></b><br/>
	<xsl:apply-templates/>
      </div>
    </xsl:if>
  </xsl:template>

  <xsl:template match="repo" mode="toc"/>

</xsl:stylesheet>
