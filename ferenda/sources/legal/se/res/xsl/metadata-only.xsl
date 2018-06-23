<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <xsl:template name="metadata-only">
    <xsl:param name="repo"/><!-- must be main repo, ie not a subrepo, and match the key in options.py -->
    <xsl:param name="subrepo"/><!-- we might need this, otherwise we'll have to try to download basefile with all subrepos -->
    <xsl:param name="basefile"/>
    <div class="metadata-only">
      <h2>Dokumenttext saknas</h2>
      <p>Detta dokument har bedömts ha begränsad juridisk betydelse, så dess innehåll har inte tagits med här. Du kan hitta originaldokumentet från dess källa genom länken till höger.</p>
      <p>Om du tycker att dokumentet är relevant kan ange varför, och klicka på nedanstående knapp för att importera det till tjänsten (OBS: Detta kan ta flera minuter för stora dokument).</p>
      <form action="/devel/change-parse-options" method="POST">
	<input type="hidden" id="repo" value="{$repo}"/> 
	<input type="hidden" id="subrepo" value="{$subrepo}"/> 
	<input type="hidden" id="basefile" value="{$basefile}"/>
	<input type="hidden" id="value" value="default"/>
	<div class="form-group">
	  <input type="text" id="reason" class="form-control" placeholder="Beskriv varför dokumentet bör tas med..."/>
	</div>
	<input type="submit" class="btn btn-default" value="Importera dokument"/>
      </form>
    </div>
  </xsl:template>
</xsl:stylesheet>

