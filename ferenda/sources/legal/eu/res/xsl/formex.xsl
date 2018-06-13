<?xml version="1.0" encoding="utf-8"?>
<!--
Note: this template expects Formex version 4 (http://formex.publications.europa.eu/formex-4/formex-4.htm), outputs XHTML+RDFa 
-->
<xsl:stylesheet version="1.0"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
		xmlns:dcterms="http://purl.org/dc/terms/"
		xmlns:owl="http://www.w3.org/2002/07/owl#"
		xmlns:prov="http://www.w3.org/ns/prov#"
		xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
		xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
		xmlns:rinfoex="http://lagen.nu/terms#"
		xmlns:rpubl="http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"
		xmlns:xsd="http://www.w3.org/2001/XMLSchema#"
		xmlns="http://www.w3.org/1999/xhtml">
  <xsl:output method="xml" encoding="utf-8" />
  <xsl:param name="about"/>
  <xsl:param name="rdftype"/>
  <xsl:output indent="yes"/>
  <xsl:strip-space elements="*"/>

  <xsl:template match="/">
    <html xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
	  xsi:schemaLocation="http://www.w3.org/1999/xhtml http://www.w3.org/MarkUp/SCHEMA/xhtml-rdfa-2.xsd"
	  version="XHTML+RDFa 1.1" xml:lang="sv">
      <head about="{$about}">
	<!-- fixme: we shouldn't have to use nbsp between the content
	     of <P> tags, but if we use a regular space it gets
	     removed in the transform process FOR SOME REASON -->
	<title property="dcterms:title"><xsl:for-each select="*/TITLE/TI/P"><xsl:value-of select="."/>&#160;</xsl:for-each></title>
	<link rel="rdf:type" href="{$rdftype}"/>
      </head>
      <body about="{$about}">
	<xsl:apply-templates/>
      </body>
    </html>
  </xsl:template>

  <xsl:template match="ACT">
    <xsl:apply-templates/>
  </xsl:template>

  <xsl:template match="TITLE">
    <xsl:for-each select="TI/P">
      <xsl:apply-templates/>&#160;
    </xsl:for-each>
  </xsl:template>

  <xsl:template match="HT">
    <span class="ht">
      <xsl:value-of select="."/>
    </span>
  </xsl:template>

  <xsl:template match="P">
    <p>
      <xsl:value-of select="."/>
    </p>
  </xsl:template>

  <xsl:template match="DATE">
    <abbr class="date" title="{@ISO}">
      <xsl:value-of select="."/>
    </abbr>
  </xsl:template>
  
  <xsl:template match="BIB.INSTANCE">
    <!-- this metadata element only contains stuff about the issue and
         page number of the OJ issue this thing was published in,
         which is of no interest to us right now -->
    <xsl:comment>bib.instance metadata removed</xsl:comment>
  </xsl:template>

  <xsl:template match="PREAMBLE">
    <div class="preamble">
      <h2><xsl:value-of select="PREAMBLE.INIT"/></h2>
      <xsl:for-each select="GR.VISA/VISA">
	<p><xsl:apply-templates/></p>
      </xsl:for-each>
      <p class="init"><xsl:value-of select="GR.CONSID/GR.CONSID.INIT"/></p>
      <xsl:apply-templates select="GR.CONSID/CONSID"/>
      <p class="final"><xsl:value-of select="PREAMBLE.FINAL"/></p>
    </div>
  </xsl:template>

  <xsl:template match="NOTE[@TYPE='FOOTNOTE']">
    <span class="footnote" id="{@NOTE.ID}">
      <xsl:for-each select="P"><xsl:apply-templates/></xsl:for-each>
    </span>
  </xsl:template>

  <xsl:template match="REF.DOC.OJ">
    <!-- we could format this to a <a href="..."> but the element only
         contains refs to the OJ issue/page, which we don't use as
         identifiers for now -->
    <span class="oj-ref"><xsl:apply-templates/></span>
  </xsl:template>

  <xsl:template match="QUOT.START|QUOT.END"><xsl:value-of disable-output-escaping="yes" select="concat('&amp;#x',@CODE,';')"/></xsl:template>

  
  <xsl:template match="CONSID">
    <!-- eg: sv: "skäl" -->
    <p class="consid" about="{$about}#{substring-before(substring-after(NP/NO.P, '('), ')')}" property="cdm:consid-no" content="{NP/NO.P}"><xsl:apply-templates select="NP/TXT"/></p>
  </xsl:template>

  <xsl:template match="TXT">
    <xsl:apply-templates/>
  </xsl:template>

  <xsl:template match="ENACTING.TERMS">
    <div class="enacting-terms">
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="DIVISION">
    <div typeof="bibo:DocumentPart" about="{$about}#D{count(preceding-sibling::DIVISION)+1}" property="dcterms:title" content="{TITLE/TI/P/HT} // {TITLE/STI/P/HT}">
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="DIVISION/DIVISION">
    <div typeof="bibo:DocumentPart" about="{$about}#D{count(../preceding-sibling::DIVISION)+1}-{count(preceding-sibling::DIVISION)+1}" property="dcterms:title" content="{TITLE/TI/P/HT} // {TITLE/STI/P/HT}">
      <xsl:apply-templates/>
    </div>
  </xsl:template>
  
  <xsl:template match="ARTICLE">
    <div typeof="cdm:article_legal" about="{$about}#{@IDENTIFIER}" property="dcterms:identifier" content="{TI.ART}">
      <span rel="dcterms:identifier"><xsl:value-of select="STI.ART"/></span>
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <!-- these two already handled in the ARTICE template -->
  <xsl:template match="TI.ART"/>
  <xsl:template match="STI.ART"/>

  <xsl:template match="PARAG">
    <div typeof="cdm:paragraph_legal" about="{$about}#{@IDENTIFIER}">
      <span class="no-parag"><xsl:value-of select="NO.PARAG"/></span>
      <xsl:for-each select="ALINEA">
	<xsl:apply-templates/>
      </xsl:for-each>
    </div>
  </xsl:template>

  <xsl:template match="ALINEA">
    <!-- alinea can either contain text directly (in which case we'd
         like to wrap it in a <p>) or contain one or probably more <P>
         tags (which we'd like to convert to lower-case <p>) -->
    <xsl:if test="P">
      <xsl:apply-templates/>
    </xsl:if>
    <xsl:if test="not(P)">
      <p><xsl:apply-templates/></p>
    </xsl:if>
  </xsl:template>

  <xsl:template match="LIST[@TYPE='alpha']">
    <ol type='a'>
      <xsl:apply-templates/>
    </ol>
  </xsl:template>

  <xsl:template match="LIST[@TYPE='ARAB']">
    <ol type='1'>
      <xsl:apply-templates/>
    </ol>
  </xsl:template>

  <xsl:template match="LIST[@TYPE='DASH']">
    <ul class="dash">
      <xsl:apply-templates/>
    </ul>
  </xsl:template>

  <xsl:template match="ITEM">
    <!-- fixme: find better rdf predicate -->
    <li property="rinfoex:punkt" content="{NP/NO.P}"><xsl:apply-templates select="NP/TXT"/></li>
  </xsl:template>

  <xsl:template match="FT"><span class="ft"><xsl:apply-templates/></span></xsl:template>

  <xsl:template match="FINAL">
    <div class="final">
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="SIGNATURE">
    <div class="signature">
      <xsl:apply-templates/>
    </div>
  </xsl:template>

  <xsl:template match="PL.DATE">
    <xsl:apply-templates/>
  </xsl:template>

  <xsl:template match="SIGNATORY">
    <xsl:apply-templates/>
  </xsl:template>

  <xsl:template match="text()" priority="2"><xsl:value-of select="."/></xsl:template>

  <xsl:template match="processing-instruction('PAGE')"/>
  
  <xsl:template match="@*|node()"> <xsl:copy> <xsl:apply-templates
  select="@*|node()"/> </xsl:copy> </xsl:template> </xsl:stylesheet>
