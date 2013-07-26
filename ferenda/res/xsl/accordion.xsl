<?xml version="1.0" encoding="ISO-8859-1"?>
<xsl:stylesheet version="1.0"
		xmlns="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform">

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
</xsl:stylesheet>