<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0"
		xmlns:xhtml="http://www.w3.org/1999/xhtml"
		xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <xsl:template name="nav-search-form">
    <form id="search" class="navbar-form" role="search" action="{$configuration/search/endpoint}">
      <input type="text" class="form-control typeahead" placeholder="Search" name="q"></input>
      <!-- this is needed to get Enter to submit the form, now that we use typeahead.js
	   https://github.com/twitter/typeahead.js/issues/255#issuecomment-21954768
      -->
      <input type="submit" value="Search" class="invisible hidden"/>
      <div class="input-group-btn">
        <button class="btn btn-default" type="button"><i class="glyphicon glyphicon-search">&#8288;</i></button>
      </div>
    </form>
  </xsl:template>
</xsl:stylesheet>
