Betatestning av nya versionen av lagen.nu
=========================================

Du som läser det här har fått en inbjudan att testa den kommande
versionen av lagen.nu.

Utåt sett har det inte hänt mycket med tjänsten sedan
dess. Uppdaterade lagar och nya rättsfall har strömmat in, men i
övrigt har den sett ut och funkat likadant hela tiden. Den största
förändringen var kanske när den anpassades 2015 så att den är
någotsånär användbar från en mobiltelefon eller surfplatta.

Men bakom kulisserna har jag jobbat på i några år med att göra en
nästa version. Pga heltidsarbete och småbarnsförälderliv har det tagit
sin tid. Men nu finns den tillgänglig i en första testversion.

Testversionen finns på http://ferenda.lagen.nu/. Länken är inte
hemlig, men jag ser helst att den inte sprids just nu. Det finns även
en beskrivning av vad som är `nytt jämfört med dagens tjänst <nytt>`_.

Du får gärna testa runt tjänsten. Jag är särskilt intresserade av det
allmänna intryck. Är det enkelt/snabbt att hitta det du vill, när du
vet vad du är ute efter? Är det bekvämt att läsa och hoppa mellan
olika dokument? Är nya funktioner, som sökmotorn, begripliga?

Det finns många buggar och skavanker, till allra största delen pga att
koden för att automatiskt hantera förarbeten och andra nya
informationstyper inte klarar den ordentliga variation som finns i
alla nytillkomna dokument. Mycket känner jag till (och fixar i ordning
efter hur mycket de irriterar mig mest) men vill gärna veta om fel du
hittar.

Feedback kan ges antingen direkt i mail till mig
(staffan.malmgren@gmail.com) eller (särskilt om det rör direkta buggar
i funktioner) på https://github.com/staffanm/lagen.nu/issues (skapa
ett konto och använd knappen "New issue"). Om du är intresserad av
diskussion kring utvecklingen finns en utskickslista man kan gå med i
på https://groups.google.com/forum/#!aboutgroup/lagennu-utveckling.

Tack för hjälpen!

/Staffan


P.S: För den tekniskt intresserade är ramverket bakom släppt som öppen
källkod, vilket kan användas för att bygga andra webbtjänster som
hanterar liknande informationssamlingar, fast kanske på helt andra
områden (se http://ferenda.readthedocs.io/en/stable/ och
https://github.com/staffanm/ferenda/ ). Det finns även ett REST-API
till vilket man kan ställa frågor om vilka dokument som finns inom
vissa kriterier, och få svar i JSON-format. Det är mestadels
odokumenterat, men följer i stort API:t för det offentliga
rättsinformationssystemet (se
http://dev.lagrummet.se/dokumentation/#intro-vidareutnyttjare )
