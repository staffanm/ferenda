<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                xmlns:grddl="http://www.w3.org/2003/g/data-view#"
		xmlns:dcterms="http://purl.org/dc/terms/"
                exclude-result-prefixes="rdf grddl">

    <!--
        Last modified: 2010-03-28
        Copyright: Niklas Lindström <lindstream@gmail.com>
        License: BSD-style
    -->
    <xsl:template name="_description">
        <doas:XSLTStylesheet rdf:about="http://purl.org/oort/impl/xslt/grit/rdfxml-grit.xslt"
                             xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                             xmlns:dcterms="http://purl.org/dc/terms/"
                             xmlns:foaf="http://xmlns.com/foaf/0.1/"
                             xmlns:doas="http://purl.org/net/ns/doas#">
            <dcterms:title>Grit XSLT</dcterms:title>
            <dcterms:description xml:lang="en">Transforms RDF/XML to Grit (Grokkable RDF Is Transformable).</dcterms:description>
            <dcterms:created rdf:datatype="http://www.w3.org/2001/XMLSchema#date"
                         >2009-12-08</dcterms:created>
            <dcterms:modified rdf:datatype="http://www.w3.org/2001/XMLSchema#date"
                        >2010-03-28</dcterms:modified>
            <dcterms:license rdf:resource="http://usefulinc.com/doap/licenses/bsd"/>
            <foaf:primaryTopic rdf:resource="http://purl.org/oort/def/2009/grit"/>
            <dcterms:creator>
                <foaf:Person rdf:about="http://purl.org/NET/dust/foaf#self">
                    <foaf:name>Niklas Lindström</foaf:name>
                    <foaf:mbox rdf:resource="mailto:lindstream@gmail.com"/>
                </foaf:Person>
            </dcterms:creator>
            <dcterms:format>application/xslt+xml</dcterms:format>
        </doas:XSLTStylesheet>
    </xsl:template>

    <!-- TODO:

    * Design Issues:
        - evaluate the usefulness of current solution for datatyped literals (@fmt)

    * Less important:

        - @xml:base: to resolve about, resource and ID against
            - currently no or uniform use of relative uri:s is assumed
            - and crude special-casing of "#..." is done in final uri values
            - see also <http://xsltsl.sourceforge.net/>

        - @rdf:ID: normalize with @rdf:about use ("concat($base, '#', @rdf:ID)")
            - currently assumes uses of ID are isolated

        - interpreted rdf:li, @rdf:_* (rdf:Seq, rdf:Bag, rdf:Alt)..
        - handle non-sugared rdf:List..

    * Improve:
        - topresources algorithm.. (about and nodeID via //*, *[not(...)] top-level bnodes)
        - optional support for $lang-filter (remove all lang literals with different lang)

    -->

    <xsl:param name="base" select="/*/@xml:base[position()=1]"/>
    <xsl:param name="lang-filter"/>
    <xsl:param name="grddl-ref">
        <!-- <xsl:text>http://purl.org/oort/impl/xslt/grit/grit-grddl.xslt</xsl:text> -->
    </xsl:param>

    <xsl:variable name="all-namespaces" select="//*/namespace::*"/>

    <xsl:key name="about" match="//*[@rdf:about]" use="@rdf:about"/>
    <xsl:key name="bnode" match="//*[@rdf:nodeID and *]" use="@rdf:nodeID"/>
    <xsl:key name="bnoderef" match="//*[@rdf:nodeID and not(*)]" use="@rdf:nodeID"/>

    <xsl:template match="/" name="grit">
        <graph>
            <xsl:if test="$grddl-ref">
                <xsl:attribute name="grddl:transformation">
                    <xsl:value-of select="$grddl-ref"/>
                </xsl:attribute>
            </xsl:if>
            <xsl:copy-of select="$all-namespaces"/>
            <xsl:copy-of select="$base"/>
            <xsl:call-template name="topresources">
                <xsl:with-param name="descriptions" select="rdf:RDF/* | *[not(self::rdf:RDF)]"/>
                <xsl:with-param name="atroot" select="true()"/>
            </xsl:call-template>
        </graph>
    </xsl:template>

    <xsl:template name="topresources">
        <xsl:param name="atroot" select="false()"/>
        <xsl:param name="descriptions"/>
        <xsl:for-each select="$descriptions">
	    <xsl:sort select="@rdf:about"/>
            <xsl:choose>
                <!--
                <xsl:when test="self::rdf:Description[not(*)] and
                          not(../@rdf:parseType='Collection')"/>
                -->
                <xsl:when test="self::rdf:Description[not(*)]"/>

                <xsl:when test="@rdf:about">
                    <xsl:variable name="aboutthis" select="key('about', @rdf:about)"/>
                    <!--
                    <xsl:if test="generate-id() = generate-id($aboutthis[1])">
                    -->
                    <xsl:if test="generate-id() = generate-id($aboutthis[1])
                          or $aboutthis[1]/../@rdf:parseType='Collection'">
                        <resource>
                            <xsl:attribute name="uri">
                                <xsl:call-template name="normalize-uri">
                                    <xsl:with-param name="uri" select="@rdf:about"/>
                                </xsl:call-template>
                            </xsl:attribute>
                            <xsl:for-each select="$aboutthis">
                                <xsl:call-template name="resourcebody"/>
                            </xsl:for-each>
                        </resource>
                    </xsl:if>
                </xsl:when>

                <xsl:when test="@rdf:ID">
                    <resource>
                        <xsl:attribute name="uri">
                            <xsl:call-template name="normalize-uri">
                                <xsl:with-param name="uri" select="concat('#', @rdf:ID)"/>
                            </xsl:call-template>
                        </xsl:attribute>
                        <xsl:for-each select=".">
                            <xsl:call-template name="resourcebody"/>
                        </xsl:for-each>
                    </resource>
                </xsl:when>

                <xsl:otherwise>
                    <xsl:variable name="all-nodeids" select="key('bnode', @rdf:nodeID)"/>
                    <xsl:variable name="ref-count" select="count(key('bnoderef', @rdf:nodeID))"/>
                    <xsl:variable name="true-blank" select="$atroot and not(@rdf:nodeID)"/>
                    <xsl:variable name="first-named-bnode"
                                  select="@rdf:nodeID and generate-id() =
                                                generate-id($all-nodeids[1])"/>
                    <xsl:variable name="first-spread-bnode" select="
                            $first-named-bnode and not($atroot and
                                    (../*//*[@rdf:nodeID = current()/@rdf:nodeID])
                                    )"/>
                    <xsl:variable name="selfref"
                                  select=".//*[@rdf:nodeID = current()/@rdf:nodeID]"/>
                    <xsl:if test="$true-blank or ($first-named-bnode and $selfref) or
                             ($first-spread-bnode and
                                (($ref-count = 0 and $atroot) or $ref-count > 1))">
                        <resource>
                            <xsl:if test="$ref-count > 0">
                                <xsl:attribute name="uri">
                                    <xsl:text>_:</xsl:text>
                                    <xsl:value-of select="@rdf:nodeID"/>
                                </xsl:attribute>
                            </xsl:if>
                            <xsl:for-each select=". | $all-nodeids">
                                <xsl:call-template name="resourcebody"/>
                            </xsl:for-each>
                        </resource>
                    </xsl:if>
                </xsl:otherwise>
            </xsl:choose>

            <xsl:call-template name="topresources">
                <xsl:with-param name="descriptions"
                                select="*[not(@rdf:parseType='Literal')]/*[@rdf:about | *]"/>
            </xsl:call-template>

        </xsl:for-each>
    </xsl:template>

    <xsl:template name="resourcebody">
        <xsl:variable name="elemtype" select="self::*[not(self::rdf:Description)]"/>
        <xsl:apply-templates mode="type" select="$elemtype | rdf:type"/>
        <xsl:apply-templates mode="property" select="@*|*">
	  <xsl:sort select="local-name()"/>
	</xsl:apply-templates>
    </xsl:template>

    <xsl:template mode="property" match="*|@*">
        <xsl:element namespace="{namespace-uri(.)}" name="{name(.)}">
            <xsl:choose>
                <xsl:when test="@rdf:parseType='Collection'">
                    <xsl:for-each select="*">
                        <li>
                            <xsl:choose>
                                <xsl:when test="@rdf:about">
                                    <xsl:attribute name="ref">
                                        <xsl:call-template name="normalize-uri">
                                            <xsl:with-param name="uri" select="@rdf:about"/>
                                        </xsl:call-template>
                                    </xsl:attribute>
                                </xsl:when>
                                <xsl:otherwise>
                                    <xsl:call-template name="resourcebody"/>
                                </xsl:otherwise>
                            </xsl:choose>
                        </li>
                    </xsl:for-each>
                </xsl:when>
                <xsl:when test="*/@rdf:about">
                    <xsl:attribute name="ref">
                        <xsl:call-template name="normalize-uri">
                            <xsl:with-param name="uri" select="*/@rdf:about"/>
                        </xsl:call-template>
                    </xsl:attribute>
                </xsl:when>
                <xsl:when test="@rdf:resource">
                    <xsl:attribute name="ref">
                        <xsl:call-template name="normalize-uri">
                            <xsl:with-param name="uri" select="@rdf:resource"/>
                        </xsl:call-template>
                    </xsl:attribute>
                </xsl:when>
                <xsl:when test="@rdf:nodeID">
                    <xsl:call-template name="output-bnode-in-property"/>
                </xsl:when>
                <xsl:when test="@rdf:parseType='Resource'">
                    <xsl:apply-templates mode="type" select="rdf:type"/>
                    <xsl:apply-templates mode="property" select="*|@*"/>
                </xsl:when>
                <xsl:when test="@rdf:parseType='Literal'">
                    <xsl:attribute name="fmt">xml</xsl:attribute>
                    <xsl:copy-of select="node()"/>
                </xsl:when>
                <xsl:when test="@rdf:datatype">
                    <xsl:attribute name="fmt">datatype</xsl:attribute>
                    <xsl:call-template name="element-from-uri">
                        <xsl:with-param name="uri" select="@rdf:datatype"/>
                        <xsl:with-param name="body">
                            <xsl:value-of select="."/>
                        </xsl:with-param>
                    </xsl:call-template>
                </xsl:when>
                <xsl:when test="key('bnode', */@rdf:nodeID)">
                    <xsl:for-each select="key('bnode', */@rdf:nodeID)">
                        <xsl:call-template name="output-bnode-in-property"/>
                    </xsl:for-each>
                </xsl:when>
                <xsl:when test="*[not(@rdf:about)]">
                    <xsl:for-each select="*[not(@rdf:about)]">
                        <xsl:call-template name="resourcebody"/>
                    </xsl:for-each>
                </xsl:when>
                <!-- plain/language literals -->
                <xsl:otherwise>
                    <xsl:variable name="lang" select="(ancestor-or-self::*/@xml:lang)[last()]"/>
                    <xsl:if test="$lang and $lang != ''">
                        <xsl:attribute name="xml:lang"><xsl:value-of select="$lang"/></xsl:attribute>
                    </xsl:if>
                    <xsl:apply-templates/>
                </xsl:otherwise>
            </xsl:choose>
        </xsl:element>
    </xsl:template>

    <xsl:template name="output-bnode-in-property">
        <xsl:variable name="ref-count" select="count(key('bnoderef', @rdf:nodeID))"/>
        <xsl:variable name="thisNode" select="."/>
        <xsl:variable name="selfref"
                      select="ancestor::*[@rdf:nodeID = current()/@rdf:nodeID]"/>
        <xsl:choose>
            <xsl:when test="$ref-count &lt; 2 and not($selfref)">
                <xsl:for-each select="key('bnode', @rdf:nodeID)">
                    <xsl:if test=". != $thisNode">
                        <xsl:call-template name="resourcebody"/>
                    </xsl:if>
                </xsl:for-each>
            </xsl:when>
            <xsl:otherwise>
                <xsl:attribute name="ref">
                    <xsl:text>_:</xsl:text>
                    <xsl:value-of select="@rdf:nodeID"/>
                </xsl:attribute>
            </xsl:otherwise>
        </xsl:choose>
    </xsl:template>

    <xsl:template mode="property" match="@rdf:about | @rdf:resource | @rdf:nodeID | @rdf:ID |
                  @rdf:parseType | @rdf:datatype"/>

    <xsl:template mode="property" match="rdf:type"></xsl:template>

    <xsl:template mode="type" match="*">
        <!--<a uri="{concat(namespace-uri(.), local-name(.))}">-->
        <a><xsl:copy/></a>
    </xsl:template>

    <xsl:template mode="type" match="rdf:type">
        <a><!-- uri="{@rdf:resource}">-->
            <xsl:call-template name="element-from-uri">
                <xsl:with-param name="uri" select="@rdf:resource"/>
            </xsl:call-template>
        </a>
    </xsl:template>

    <xsl:template name="element-from-uri">
        <xsl:param name="uri"/>
        <xsl:param name="body"/>
        <xsl:variable name="ns">
            <xsl:call-template name="get-ns"><xsl:with-param name="uri"
                               select="$uri"/></xsl:call-template>
        </xsl:variable>
        <xsl:variable name="pfx">
            <xsl:call-template name="get-pfx"><xsl:with-param name="ns"
                               select="$ns"/></xsl:call-template>
        </xsl:variable>
        <xsl:variable name="leaf" select="substring-after($uri, $ns)"/>
        <xsl:variable name="name" select="concat($pfx, $leaf)"/>
        <xsl:element namespace="{$ns}" name="{$name}">
            <xsl:copy-of select="$body"/>
        </xsl:element>
    </xsl:template>

    <xsl:template name="get-ns">
        <xsl:param name="uri"/>
        <xsl:choose>
            <xsl:when test="contains($uri, '#')">
                <xsl:value-of select="concat(substring-before($uri, '#'), '#')"/>
            </xsl:when>
            <xsl:otherwise>
                <xsl:variable name="last-index">
                    <xsl:call-template name="last-index-of">
                        <xsl:with-param name="string" select="$uri" />
                        <xsl:with-param name="token">
                            <xsl:choose>
                                <xsl:when test="contains($uri, '/')">/</xsl:when>
                                <xsl:otherwise>:</xsl:otherwise>
                            </xsl:choose>
                        </xsl:with-param>
                    </xsl:call-template>
                </xsl:variable>
                <xsl:value-of select="substring($uri, 1, number($last-index))"/>
            </xsl:otherwise>
        </xsl:choose>
    </xsl:template>

    <xsl:template name="get-pfx">
        <xsl:param name="ns"/>
        <xsl:param name="sep" select="':'"/>
        <xsl:variable name="pfx">
            <xsl:value-of select="local-name(ancestor-or-self::*/namespace::*[.=$ns][position()=1])"/>
        </xsl:variable>
        <xsl:choose>
            <xsl:when test="$pfx != ''">
                <xsl:value-of select="concat($pfx, ':')"/>
            </xsl:when>
            <xsl:otherwise></xsl:otherwise>
        </xsl:choose>
    </xsl:template>

    <xsl:template name="last-index-of">
        <xsl:param name="string"/>
        <xsl:param name="token"/>
        <xsl:param name="index" select="0"/>
        <xsl:choose>
            <xsl:when test="contains($string, $token)">
                <xsl:call-template name="last-index-of">
                    <xsl:with-param name="string"
                                    select="substring-after($string, $token)"/>
                    <xsl:with-param name="token" select="$token" />
                    <xsl:with-param name="index"
                                    select="$index +
                                    string-length(substring-before($string, $token)) +
                                    string-length($token)"/>
                </xsl:call-template>
            </xsl:when>
            <xsl:otherwise><xsl:value-of select="$index"/></xsl:otherwise>
        </xsl:choose>
    </xsl:template>

    <xsl:template name="normalize-uri">
        <!-- very incomplete (see TODO list above) -->
        <xsl:param name="uri"/>
        <xsl:choose>
            <xsl:when test="$uri = ''">
                <xsl:value-of select="$base"/>
            </xsl:when>
            <xsl:when test="starts-with($uri, '#')">
                <xsl:choose>
                    <xsl:when test="contains($base, '#')">
                        <xsl:value-of select="substring-before($base, '#')"/>
                    </xsl:when>
                    <xsl:otherwise>
                        <xsl:value-of select="$base"/>
                    </xsl:otherwise>
                </xsl:choose>
                <xsl:value-of select="$uri"/>
            </xsl:when>
            <!-- overly careful "is relative" check -->
            <xsl:when test="not(contains($uri, ':')) and not(contains($uri, '/'))">
                <xsl:value-of select="concat($base, $uri)"/>
            </xsl:when>
            <xsl:otherwise>
                <xsl:value-of select="$uri"/>
            </xsl:otherwise>
        </xsl:choose>
    </xsl:template>

</xsl:stylesheet>
