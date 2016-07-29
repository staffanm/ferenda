<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/2002/06/xhtml2/"
      xmlns:xi="http://www.w3.org/2001/XInclude"
      xml:lang="sv">
  <head>
    <title>Hur ser det ut bakom kulisserna?</title>
  </head>
  <body>
    <div role="main">
    <h1>Hur ser det ut bakom kulisserna?</h1>

    <p>Lagen.nu består av ca 15000 html-sidor med lagtexter och
    rättsfall. För att kunna hantera en sån stor informationsmängd på
    mycket begränsad tid är nästan allt automatiserat.</p>

    <p>Lagtexter hämtas från Regeringskansliets webbserver. Den oformatterade och
    ostrukturerade textmassan som finns där konverteras till en
    strukturerad XML-version (XHTML2 med RDFa).</p>

    <p>På samma sätt hämtas rättsfall från Domstolsverkets FTP-server,
    och konverteras, först från Word-format till en formatterad, men
    semantiskt ostrukturerad HTML (genom Microsoft Words "Spara som
    HTML"-funktion), och sedan till en strukturerad XML-version (även
    här XHTML2 med RDFa).</p>

    <p>I nästa steg sammanställs all metadata från samtliga dokument i
    en stor s.k. RDF-graf. Denna används sedan tillsammans med en
    uppsättning XSLT-stylesheets för att skapa XHTML1.0-sidor, som är
    redo att visas direkt i en webbläsare.</p>

    <p>I ett avslutande steg skapas sedan innehållsförteckningar över
    samtliga dokument i tjänsten, samt sidor som listar nytillkomna
    och uppdaterade dokument.</p>

    <p>Webbsidorna görs sedan tillgängliga genom en vanlig Apache
    2-server, som med hjälp av mod_rewrite översätter de enkla
    URL:erna (som <samp>https://lagen.nu/1960:729</samp>) till de
    faktiska filerna (som
    <samp>sfs/generated/1960/729.html</samp>)</p>.

    <p>Koden är skriven i python, med vissa delar i XSLT. Den finns
    tillgänglig via subversion från
    <samp>http://svn.lagen.nu/svnroot</samp>, och mer information
    finns på <a href="http://trac.lagen.nu/">utvecklingswikin</a>.</p>

    <p>Mer bakgrundsinformation finns i "lagen.nu"-kategorin på min<a
    href="http://blog.tomtebo.org/tag/lagennu/">blogg</a>.</p>
      
    </div>
  </body>
</html>
