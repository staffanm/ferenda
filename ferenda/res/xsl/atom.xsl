<?xml version="1.0" encoding="utf-8"?>
<!--
Note: this template expects Atom 1.0, outputs HTML5
-->
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

  <xsl:import href="uri.xsl"/>
  <xsl:include href="base.xsl"/>

  <!-- FIXME: this reimplements logic from base.xsl (xhtml:head and
       xhtml:body templates) -->
  <xsl:template match="atom:feed">
    <head>
      <meta charset="utf-8" />
      <meta http-equiv="X-UA-Compatible" content="IE=edge,chrome=1"/>
      <title>FIXME: TITLE GOES HERE</title>
      <meta name="viewport" content="width=device-width" />
      <xsl:copy-of select="$configuration/stylesheets/*"/>
      <xsl:copy-of select="$configuration/javascripts/*"/>
    </head>
    <body>
      <xsl:comment>[if lt IE 7]>
      <p class="chromeframe">You are using an <strong>outdated</strong> browser. Please <a href="http://browsehappy.com/">upgrade your browser</a> or <a href="http://www.google.com/chromeframe/?redirect=true">activate Google Chrome Frame</a> to improve your experience.</p>
        &lt;![endif]</xsl:comment>
        <div class="header-container">
	  <header class="wrapper clearfix">
	    <h1 class="title"><xsl:value-of select="$configuration/sitename"/></h1>
	    <h2 class="title"><xsl:value-of select="$configuration/sitedescription"/></h2>
	    <form>
	      <input type="search"/>
	    </form>
	    <xsl:copy-of select="$configuration/tabs/*"/>
	  </header>
	</div>
	<div class="main-container">
	  <div class="main wrapper clearfix">
	    <nav id="toc">
	      <ul>
		<li>FIXME: TOC/navbar go here</li>
	      </ul>
	    </nav>
	    <article class="clearfix">
	      <h1><xsl:value-of select="atom:title"/></h1>
	      <xsl:apply-templates/>
	    </article>
	  </div>
	</div>
	<div class="footer-container">
	  <footer>
	    <p>This is a footer|Legal info|Disclaimer|Think of the trees!</p>
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

  <xsl:template match="atom:entry">
    <section>
      <h1><xsl:value-of select="atom:title"/></h1>
      <p>
	<xsl:value-of select="atom:summary"/>
      </p>
    </section>
  </xsl:template>

  <xsl:template match="atom:id|atom:title|atom:updated|atom:author|atom:link"/>
  
</xsl:stylesheet>