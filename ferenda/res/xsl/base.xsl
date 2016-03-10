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
  <xsl:strip-space elements="*"/>
  <xsl:param name="value"/>
  <xsl:param name="annotationfile"/>
  <xsl:variable name="annotations" select="document($annotationfile)/graph"/>
  <xsl:param name="configurationfile"/>
  <xsl:variable name="configuration" select="document($configurationfile)/configuration"/>
  <xsl:include href="nav-search-form.xsl"/>
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
    <html>
    <xsl:apply-templates/>
    </html>
  </xsl:template>
  
  
  <xsl:template match="xhtml:head">
    <head>
      <meta charset="utf-8" />
      <meta http-equiv="X-UA-Compatible" content="IE=edge"/>
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title><xsl:call-template name="headtitle"/></title>

      <link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.6/css/bootstrap.min.css" integrity="sha384-1q8mTJOASx8j1Au+a5WDVnPi2lkFfwwEAa8hDDdjZlpLegxhjVME1fgjWPGmkzs7" crossorigin="anonymous"/>
      <link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.6/css/bootstrap-theme.min.css" integrity="sha384-fLW2N01lMqjakBkx3l/M9EahuwpSfeNvV63J5ezn3uZzapT0u7EYsXMjQV+0En5r" crossorigin="anonymous"/>
      <!-- HTML5 shim and Respond.js for IE8 support of HTML5 elements and media queries -->
      <!-- WARNING: Respond.js doesn't work if you view the page via file:// -->
      <!--[if lt IE 9]>
	<script src="https://oss.maxcdn.com/html5shiv/3.7.2/html5shiv.min.js"></script>
	<script src="https://oss.maxcdn.com/respond/1.4.2/respond.min.js"></script>
      <![endif]-->      
      <xsl:copy-of select="$configuration/stylesheets/*"/>
      <!-- xhtml files can have custom stylesheets (ie pdf page backgrounds), just copy these -->
      <xsl:for-each select="xhtml:link[@rel='stylesheet']">
	<link rel="stylesheet" href="{@href}"/>
      </xsl:for-each>
      
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
      <nav class="navbar navbar-default">
	<div class="container-fluid">
	  <div class="navbar-header">
	    <button type="button" class="navbar-toggle collapsed" data-toggle="collapse" data-target="#bs-example-navbar-collapse-1" aria-expanded="false">
              <span class="sr-only">Toggle navigation</span>
	      <!-- this is a hamburger menu... -->
              <span class="icon-bar"></span>
              <span class="icon-bar"></span>
              <span class="icon-bar"></span>
	    </button>
	    <a class="navbar-brand" href="{$configuration/url}"><xsl:value-of select="$configuration/sitename"/></a>
	  </div>
	  <div class="collapse navbar-collapse" id="bs-example-navbar-collapse-1">
	    <ul class="nav navbar-nav">
	      <xsl:call-template name="decorate-nav"/>
	    </ul>
	    <xsl:if test="$configuration/search">
	      <xsl:call-template name="nav-search-form"/>
	    </xsl:if>
	  </div><!-- /.navbar-collapse -->
	</div><!-- /.container-fluid -->
      </nav>
      <div class="row">
	<nav id="toc" class="col-sm-3">
	  <ul>
	    <xsl:apply-templates mode="toc"/>
	  </ul>
	</nav>
	<article class="col-sm-9">
	  <xsl:call-template name="pagetitle"/>
	  <xsl:apply-templates/>
	  <!-- Main document text: header, sections (possibly nested) and footer goes here -->
	</article>
      </div>
      <div class="footer-container">
	<footer>
	  <nav>
	    <xsl:copy-of select="$configuration/footerlinks/*"/>
	  </nav>
	</footer>
      </div>
      <script src="https://ajax.googleapis.com/ajax/libs/jquery/1.11.3/jquery.min.js">&#160;</script>
      <script src="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.6/js/bootstrap.min.js" integrity="sha384-0mSbJDEHialfmuBBQP6A4Qrprq5OVfW37PRR3j5ELqxss1yVqOtnepnHVP9aJ7xS" crossorigin="anonymous">&#160;</script>
      <xsl:copy-of select="$configuration/javascripts/*"/>
    </body>
  </xsl:template>

  <!-- NOTE: while resources.py makes it possible to define nested
       navbars, currently the lagen.nu site doesn't support it,
       therefore subnavs are dropped. Also bootstraps dropdown system
       for navbars makes it impossible to click the top-level element,
       which we probably want. Maybe we could redefine resources.py to
       not output subelements if the topelement has a href? In that
       case, this template needs to add a lot of attributes like
       class="dropdown-toggle" and also some extra span element. -->

  <xsl:template name="decorate-nav">
    <xsl:for-each select="$configuration/tabs/*">
      <li><a href="{./a/@href}"><xsl:value-of select="./a"/></a></li>
    </xsl:for-each>
  </xsl:template>
  
</xsl:stylesheet>
