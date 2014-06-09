<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:xsd="http://www.w3.org/2001/XMLSchema#"
		xmlns:rinfo="http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xml:space="preserve"
		exclude-result-prefixes="xhtml rdf dcterms xsd rinfo rinfoex"
		>
  <xsl:param name="value"/>
  <xsl:param name="annotationfile"/>
  <xsl:variable name="annotations" select="document($annotationfile)/graph"/>
  <xsl:param name="configurationfile"/>
  <xsl:variable name="configuration" select="document($configurationfile)/configuration"/>
  <xsl:output method="html"
	      omit-xml-declaration="yes"
	      encoding='utf-8'
	      indent="yes"/>




  <xsl:template match="/">
    <!-- this is a ugly workaround required to get the proper html5
         doctype *and* the pre-rootnode conditional IE comments needed
         for the h5bp template. Strip the <remove-this-tag> start and end
         tags as a postprocessing step. -->
    <remove-this-tag>
      <xsl:apply-templates/>
    </remove-this-tag>
  </xsl:template>
  
  <xsl:template match="xhtml:html">
    <!-- <xsl:text disable-output-escaping='yes'>&lt;!DOCTYPE html></xsl:text>     -->
    <xsl:comment>[if lt IE 7]>      &lt;html class="no-js lt-ie9 lt-ie8 lt-ie7"> &lt;![endif]</xsl:comment>
    <xsl:comment>[if IE 7]>         &lt;html class="no-js lt-ie9 lt-ie8"> &lt;![endif]</xsl:comment>
    <xsl:comment>[if IE 8]>         &lt;html class="no-js lt-ie9"> &lt;![endif]</xsl:comment>
    <xsl:comment>[if gt IE 8]>&lt;!</xsl:comment> <html class="no-js"> <xsl:comment>&lt;![endif]</xsl:comment>
    <xsl:apply-templates/>
    </html>
  </xsl:template>
  
  
  <xsl:template match="xhtml:head">
    <head>
      <meta charset="utf-8" />
      <meta http-equiv="X-UA-Compatible" content="IE=edge,chrome=1"/>
      <title><xsl:call-template name="headtitle"/></title>
      <meta name="viewport" content="width=device-width" />
      <xsl:copy-of select="$configuration/stylesheets/*"/>
      <!-- xhtml files can have custom stylesheets (ie pdf page backgrounds), just copy these -->
      <xsl:for-each select="xhtml:link[@rel='stylesheet']">
	<link rel="stylesheet" href="{@href}"/>
      </xsl:for-each>
      
      <xsl:copy-of select="$configuration/javascripts/*"/>
      <xsl:call-template name="metarobots"/>
      <xsl:call-template name="linkalternate"/>
      <xsl:call-template name="headmetadata"/>

      <!-- replace this with data from resources.xml
      <link rel="apple-touch-icon-precomposed" sizes="144x144" href="../assets/ico/apple-touch-icon-144-precomposed.png"/>
      <link rel="apple-touch-icon-precomposed" sizes="114x114" href="../assets/ico/apple-touch-icon-114-precomposed.png"/>
      <link rel="apple-touch-icon-precomposed" sizes="72x72" href="../assets/ico/apple-touch-icon-72-precomposed.png"/>
      <link rel="apple-touch-icon-precomposed" href="../assets/ico/apple-touch-icon-57-precomposed.png"/>
      <link rel="shortcut icon" href="../assets/ico/favicon.png"/>
      -->
    </head>
  </xsl:template>

  <xsl:template match="xhtml:body">
    <xsl:variable name="bodyclass"><xsl:call-template name="bodyclass"/></xsl:variable>
    <body class="{$bodyclass}">
      <xsl:comment>[if lt IE 7]>
      <p class="chromeframe">You are using an <strong>outdated</strong> browser. Please <a href="http://browsehappy.com/">upgrade your browser</a> or <a href="http://www.google.com/chromeframe/?redirect=true">activate Google Chrome Frame</a> to improve your experience.</p>
        &lt;![endif]</xsl:comment>
        <div class="header-container">
	  <header class="wrapper clearfix">
	    <h1 class="title"><a href="{$configuration/url}"><xsl:value-of select="$configuration/sitename"/></a></h1>
	    <h2 class="title"><xsl:value-of select="$configuration/sitedescription"/></h2>
	    <xsl:copy-of select="$configuration/search/*"/>
	    <xsl:copy-of select="$configuration/tabs/*"/>
	  </header>
	</div>
	<div class="main-container">
	  <div class="main wrapper clearfix">
	    <nav id="toc">
	      <xsl:copy-of select="$configuration/tocbutton/*"/>
	      <ul>
		<xsl:apply-templates mode="toc"/>
	      </ul>
	    </nav>
	    <article class="clearfix">
	      <xsl:call-template name="pagetitle"/>
	      <xsl:apply-templates/>
	      <!-- Main document text: header, sections (possibly nested) and footer goes here -->
	    </article>
	  </div>
	</div>
	<div class="footer-container">
	  <footer>
	    <xsl:copy-of select="$configuration/footerlinks/*"/>
	  </footer>
	</div>

      <xsl:if test="$configuration/ganalytics-siteid">
	<script>
	  var _gaq=[['_setAccount','<xsl:value-of select="$configuration/ganalytics-siteid"/>'],['_trackPageview']];
	  (function(d,t){var g=d.createElement(t),s=d.getElementsByTagName(t)[0];
	  g.src=('https:'==location.protocol?'//ssl':'//www')+'.google-analytics.com/ga.js';
	  s.parentNode.insertBefore(g,s)}(document,'script'));
	</script>
      </xsl:if>
    </body>
  </xsl:template>
  
</xsl:stylesheet>
