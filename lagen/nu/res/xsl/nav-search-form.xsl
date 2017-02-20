<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <xsl:template name="nav-search-form">
    <form class="navbar-form navbar-right" role="search" action="{$configuration/search/endpoint}">
      <div class="form-group">
	<input type="text" name="q" class="form-control typeahead" placeholder="Sök"/>
	<!-- this is needed to get Enter to submit the form, now that we use typeahead.js
	     https://github.com/twitter/typeahead.js/issues/255#issuecomment-21954768
	-->
	<input type="submit" value="Sök" class="invisible hidden"/>
      </div>
    </form>
  </xsl:template>
</xsl:stylesheet>
