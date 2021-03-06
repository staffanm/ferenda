<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:xsd="http://www.w3.org/2001/XMLSchema#"
		exclude-result-prefixes="xhtml rdf dcterms xsd"
		>
  <!-- I removed xml:space="preserve" from the above attributes, as it
       caused toc.xsl to not properly pretty-print the navbar
       (instead, everything got smushed. But it might have some
       adverse side effect -->

  <xsl:strip-space elements="*"/>
  <xsl:param name="value"/>
  <xsl:param name="annotationfile"/>
  <xsl:variable name="annotations" select="document($annotationfile)/graph"/>
  <xsl:param name="configurationfile"/>
  <xsl:variable name="configuration" select="document($configurationfile)/configuration"/>
  <xsl:include href="nav-search-form.xsl"/>
  <xsl:include href="analytics-tracker.xsl"/>
  <xsl:output method="html"
	      doctype-system="about:legacy-compat"
	      omit-xml-declaration="yes"
	      encoding='utf-8'
	      indent="yes"/>

  <xsl:template match="xhtml:html">
    <html>
    <xsl:apply-templates/>
    </html>
  </xsl:template>
  
  <xsl:template name="htmlhead">
    <head>
      <meta charset="utf-8" />
      <meta http-equiv="X-UA-Compatible" content="IE=edge"/>
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title><xsl:call-template name="headtitle"/></title>
      <link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/css/bootstrap.min.css" integrity="sha384-BVYiiSIFeK1dGmJRAkycuHAHRg32OmUcww7on3RYdg4Va+PmSTsz/K68vbdEjh4u" crossorigin="anonymous"/>
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
      <xsl:call-template name="analytics-tracker"/>
    </head>
  </xsl:template>

  <xsl:template match="xhtml:head">
    <xsl:call-template name="htmlhead"/>
  </xsl:template>



  <xsl:template name="htmlbody">
    <xsl:variable name="bodyclass"><xsl:call-template name="bodyclass"/></xsl:variable>
    <body class="{$bodyclass}" data-spy="scroll" data-target="#toc">
      <nav class="navbar navbar-default">
	<div class="container-fluid">
	  <div class="navbar-header">
	    <button type="button" class="navbar-toggle collapsed" data-toggle="collapse" data-target="#bs-example-navbar-collapse-1" aria-expanded="false">
              <span class="sr-only">Toggle navigation</span>
	      <!-- this is a hamburger menu... -->
              <span class="icon-bar">&#8204;</span>
              <span class="icon-bar">&#8204;</span>
              <span class="icon-bar">&#8204;</span>
	    </button>
	    <xsl:if test="$configuration/search">
	      <xsl:call-template name="nav-search-form"/>
	    </xsl:if>
	    <a class="navbar-brand" href="{$configuration/url}"><xsl:value-of select="$configuration/sitename"/></a>
	  </div>
	  <div class="collapse navbar-collapse" id="bs-example-navbar-collapse-1">
	    <ul class="nav navbar-nav">
	      <xsl:call-template name="decorate-nav"/>
	    </ul>
	  </div><!-- /.navbar-collapse -->
	</div><!-- /.container-fluid -->
      </nav>
      <div class="row row-offcanvas row-offcanvas-left">
	  
	<div class="col-sm-3 sidebar-offcanvas" id="sidebar">
	  <div class="slidebutton" data-toggle="offcanvas">
	    <div class="inner-slidebutton">
	      <span class="glyphicon glyphicon-option-vertical">&#8288;</span>
	    </div>
	  </div>
	  <!-- note: importing stylesheet MUST define
	       <xsl:param name="dyntoc" select="false()"/>
	       (or true()). Same for fixedtoc -->
	  <xsl:choose>
	    <xsl:when test="$fixedtoc">
	      <nav id="toc" data-spy="affix" data-toggle="toc" data-offset-top="70">
		<xsl:if test="not($dyntoc)">
		  <ul class="nav">
		    <xsl:apply-templates mode="toc"/>
		  </ul>
		</xsl:if>
	      </nav>
	    </xsl:when>
	    <xsl:otherwise>
	      <nav id="toc">
		<ul class="nav">
		  <xsl:apply-templates mode="toc"/>
		</ul>
	      </nav>
	    </xsl:otherwise>
	  </xsl:choose>
	</div>
	<article class="col-sm-9">
	  <xsl:call-template name="pagetitle"/>
	  <xsl:choose><xsl:when test="not($content-under-pagetitle)">
	    <xsl:apply-templates/>
	  </xsl:when>
	  </xsl:choose>
	  <!-- Main document text: header, sections (possibly nested) goes here -->
	</article>
	<footer>
	  <nav>
	    <xsl:copy-of select="$configuration/footerlinks/*"/>
	  </nav>
	</footer>
      </div>
      <script src="https://ajax.googleapis.com/ajax/libs/jquery/1.12.4/jquery.min.js">&#160;</script>
      <script src="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/js/bootstrap.min.js" integrity="sha384-Tc5IQib027qvyjSMfHjOMaLkfuWVxZxUPnCJA7l2mCWNIpG9mGCD8wGNIcPD7Txa" crossorigin="anonymous">&#160;</script>
      <script src="https://cdn.rawgit.com/twitter/typeahead.js/v0.11.1/dist/typeahead.bundle.min.js">&#160;</script>
      <xsl:if test="$dyntoc">
	<script src="https://cdn.rawgit.com/afeld/bootstrap-toc/v0.3.0/dist/bootstrap-toc.min.js">&#160;</script> 
      </xsl:if>
      <xsl:copy-of select="$configuration/javascripts/*"/>
    </body>
  </xsl:template>

  <xsl:template match="xhtml:body">
    <xsl:call-template name="htmlbody"/>
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
