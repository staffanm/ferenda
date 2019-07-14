<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <xsl:template name="nav-search-form">
    <form id="search" class="navbar-form" role="search" action="{$configuration/search/endpoint}">
      <input type="search" class="form-control typeahead" placeholder="Sök..." name="q"></input>
      <input type="submit" value="Sök" class="invisible"/>
      <!-- we can't hide the submit button (display:none) because that
           makes Enter not submit the search on some platforms
           (including ios) -->
      <div class="input-group-btn">
        <button class="btn btn-default" type="button"><i class="glyphicon glyphicon-search">&#8288;</i></button>
      </div>
    </form>
  </xsl:template>
</xsl:stylesheet>
