Hur ser det ut bakom kulisserna?
================================

*NB: Nedanståend är inaktuellt!*

Lagen.nu består av ca 15000 html-sidor med lagtexter och
rättsfall. För att kunna hantera en sån stor informationsmängd på
mycket begränsad tid är nästan allt automatiserat.

Lagtexter hämtas från Regeringskansliets webbserver. Den oformatterade och
ostrukturerade textmassan som finns där konverteras till en
strukturerad XML-version (XHTML2 med RDFa).

På samma sätt hämtas rättsfall från Domstolsverkets FTP-server,
och konverteras, först från Word-format till en formatterad, men
semantiskt ostrukturerad HTML (genom Microsoft Words "Spara som
HTML"-funktion), och sedan till en strukturerad XML-version (även
här XHTML2 med RDFa).

I nästa steg sammanställs all metadata från samtliga dokument i
en stor s.k. RDF-graf. Denna används sedan tillsammans med en
uppsättning XSLT-stylesheets för att skapa XHTML1.0-sidor, som är
redo att visas direkt i en webbläsare.

I ett avslutande steg skapas sedan innehållsförteckningar över
samtliga dokument i tjänsten, samt sidor som listar nytillkomna
och uppdaterade dokument.

Webbsidorna görs sedan tillgängliga genom en vanlig Apache 2-server,
som med hjälp av mod_rewrite översätter de enkla URL:erna (som
`https://lagen.nu/1960:729`) till de faktiska filerna (som
`sfs/generated/1960/729.html`).

Koden är skriven i python, med vissa delar i XSLT. Den finns
tillgänglig via subversion från
`http://svn.lagen.nu/svnroot, och mer information finns på
`utvecklingswikin <http://trac.lagen.nu/>`_.

Mer bakgrundsinformation finns i "lagen.nu"-kategorin på min `blogg
<http://blog.tomtebo.org/tag/lagennu/>`_.
