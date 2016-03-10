<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <xsl:template name="nav-search-form">
    <form class="navbar-form navbar-right" role="search" action="{$configuration/search/endpoint}">
      <div class="form-group">
	<input type="text" class="form-control" placeholder="Search"/>
      </div>
      <button type="submit" class="btn btn-default">Submit</button>
    </form>
  </xsl:template>
</xsl:stylesheet>
