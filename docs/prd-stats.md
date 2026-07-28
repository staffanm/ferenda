# PRD: `stats` — 54 mätvärden om korpuset

En idékatalog för en `stats`-källa som räknar fram roliga och intressanta
siffror om lagen.nu-korpuset och lägger dem på en sida (`/statistik`).

Varje post nedan har **var siffran kommer ifrån** och en **status**:

- **✔ mätt** — jag har kört frågan mot den nuvarande korpusen och siffran
  i posten är den faktiska. Den är alltså bevisligen räknebar.
- **○ räknebar** — datat finns, frågan är skriven men inte körd (oftast för
  att den kräver en genomsökning av hela artefaktträdet).
- **⚠ kräver ändring** — datat finns inte där en `stats`-källa får läsa det;
  se "Vad som saknas" nedan.

Alla mätta siffror är från korpusen som den såg ut 2026-07-27.

---

## Hur källan ser ut

> **Status: byggd.** `accommodanda/stats/` (`model`·`scan`·`compute`·`charts`
> ·`render`) renderar `/statistik`; se REWRITE.md §7k.
>
> Sidan har **54 mätvärden**, inte 51: fyra nya tillkom under bygget (längsta
> EU-artikeln, lagar med äldst kvarvarande text, vilken dag lagar träder i
> kraft, avgöranden per år) och post 36 delades i två. **Två av katalogens
> poster är ännu inte byggda: 45 (namngivna rättsfall) och 48
> (bemyndigandekedjan).** Numreringen på sidan följer sidans egen ordning och
> är därför förskjuten mot numren här.
>
> Två avvikelser från skissen i övrigt: det blev *två* verb i stället för ett,
> och `note`-fältet på varje mätvärde blev obligatoriskt där populationen är
> smalare än rubriken låter påskina.

`stats` passar som en vanlig vertikal, men **utan download och parse**: den har
inget att ladda ner och inga egna dokument att parsa.

```
lagen stats compute    # mät korpusen -> artifact/stats/statistik.json (minuter)
lagen stats generate   # rendera den artefakten till /statistik
```

Uppdelningen i två verb är vad som gör siffrorna diffbara: mätningen är dyr och
körs sällan, sidan är en ren projektion av en artefakt och kan renderas om när
som helst. `compute` är medvetet *inte* inkrementell — varje mätvärde är ett
faktum om hela korpusen, så det finns ingen delmängd att uppdatera för sig.

- **Läser:** `catalog.sqlite` (dokument, länkar, `genomforande`,
  `fk_kommentar`, `correspondence`) och artefaktträdet under
  `artifact/`.
- **Skriver:** en artefakt, `artifact/stats/statistik.json`, som innehåller
  alla mätvärdena med sina siffror — plus en `generated`-tidsstämpel och
  vilken korpusversion de räknades på.
- **Renderar:** en sida ur den artefakten, precis som `site`-källan gör med
  sina handskrivna sidor.

Att låta artefakten vara mellanledet (i stället för att räkna i renderaren)
är inte bara konvention: det gör siffrorna diffbara mellan bygg, så man kan
se vad som faktiskt ändrades i korpusen sedan förra körningen. Och det gör
det billigt att lägga en `/api/v1/stats`-endpoint ovanpå senare.

**Beroendeordning:** `stats compute` måste köra *efter* `relate` (den läser
katalogen), och `stats generate` *före* den vanliga `generate` om sidan ska med
i sitemap och sökindex.

**Kostnad:** de tunga posterna är de som kräver en genomsökning av alla
42 399 SFS-artefakter (~4 min med `ProcessPoolExecutor`) och alla 97 179
förarbetesartefakter. Resten är SQL mot katalogen och tar sekunder. Men
större delen av den genomsökningen borde inte behöva finnas alls — se
"Vad relate borde lägga till" sist i dokumentet.

---

## Vad som saknas i dagens data

Fem saker att veta innan man börjar:

1. **Ändringsförfattningar är inte dokument i katalogen.** SFS 1985:518 finns
   inte i `documents` — den finns bara som ett element i `amendments`-listan
   i den konsoliderade lagens artefakt. Alla ändringsstatistik måste alltså
   läsa artefakter, inte katalogen. Det fungerar, men det är genomsökningen
   som kostar. (Se förslag R1 sist.)

2. **Ändringsförfattningarnas rubriker finns bara i `downloaded/`.** Den
   fantastiska rubriken *"Lag (2025:191) om ändring i lagen (2022:201) om
   ändring i lagen (2021:110) om …"* står i `downloaded/sfs/2025/191.json`
   under `andringsforfattningar[].rubrik`, men följer inte med ut i
   artefakten. Post 11 nedan är därför märkt ⚠.

3. **Tabellceller räknas inte som text i en naiv genomsökning.** En
   definitionsparagraf vars hela innehåll är en tabell ("*I denna lag
   betyder*" + tabell) mäter 19 tecken om man bara läser `text`-runs. Det
   förgiftar "kortaste paragrafen" direkt. Måttet måste läsa tabellcellerna
   också — och de ligger *två* nivåer ner: en `tabell` har `rad`-barn, en `rad`
   har `cells`, och varje cell är själv en run-lista (`["text", {...}]`), inte
   en sträng. Att läsa `cells` som en lista av strängar ger tyst noll tecken,
   vilket är precis det felet `scan._runs_text` först gjorde: 3,6 % av all
   SFS-kroppstext (2,4 miljoner tecken i ett urval om 8 000 lagar) föll bort,
   koncentrerat till de ~10 % av lagarna som har tabeller.

4. **Sidnummer i förarbeten är opålitliga för OCR:ade dokument.** Största
   sidnumret i SOU 1996:165 läses som 9005. Använd teckenantal som mått på
   "tjocklek" och sidnummer bara för dokument där `ocr` är falskt.

5. **Ändringsförfattningar har inget utfärdandedatum någonstans.** Registret i
   artefakten anger `rpubl:ikrafttradandedatum` men bär
   `rpubl:utfardandedatum` på 11 av 50 948 poster, och nedladdningsträdets
   `andringsforfattningar[]` har bara `ikraftDateTime` plus en
   `publiceradDateTime` som är en databasstämpel från 2017 — inte aktens
   utfärdande. Varje mått på *varsel* (post 25) är därför ett mått på
   grundförfattningar, som bär båda datumen i sina egna `properties`.

Fyra punkter till fanns när det här skrevs och är **åtgärdade**: EU-korpuset var
fullt av fantomartiklar och löpska artiklar. Se avsnittet sist.

---

## A. Lagbokens storlek och form (1–9)

**1. De längsta lagarna** ✔
Kroppstext i tecken, per konsoliderad lag.
> Inkomstskattelag (1999:1229) 871 587 tecken · Socialförsäkringsbalk
> (2010:110) 729 172 · Skollag (2010:800) 588 997 · Aktiebolagslag
> (2005:551) 542 628 · Skatteförfarandelag (2011:1244) 498 113

**2. De kortaste lagarna** ✔
Samma mått, andra änden. Toppen är nästan komisk.
> Kungl. Maj:ts Reglemente (1919:878) för statens pensionsanstalt: **38
> tecken**. Förordning (1994:1283) om lönegarantiregister: 42.
> 101 av de 11 210 SFS-artefakterna har ingen extraherad kroppstext alls —
> vilket i sig är en användbar kvalitetssiffra.

**3. Flest paragrafer och flest kapitel** ✔
> Socialförsäkringsbalken: 2 076 §§ i 125 kapitel. Inkomstskattelagen:
> 1 814 §§ i 79 kapitel. Rättegångsbalken: 943 §§ i 59 kapitel.

**4. Längsta och kortaste paragrafen** ✔
Per paragraf, med provenance-markörer ("*Lag (2011:590).*") bortrensade.
Korpuset har **196 503 paragrafer**, varav **104 527 i gällande rätt**.
Medianparagrafen är **304 tecken** lång, medelvärdet 409 — fördelningen är
kraftigt högersvansad.

*Längst genom tiderna:*
> **23 § kommunalskattelagen (1928:370) — 55 313 tecken.** Den paragrafen är
> ensam längre än **97,5 % av alla svenska författningar** i sin helhet.
> Kommunalskattelagen tar dessutom sju av de tio översta platserna.

*Längst i gällande rätt:*
> 2 § förordningen (1983:858) om dubbelbeskattningsavtal mellan Sverige och
> Bangladesh — **26 412 tecken**. Den är egentligen ett helt skatteavtal
> instoppat i en paragraf, så den mest *paragrafliknande* rekordhållaren är
> **2 kap. 1 § lagen (2015:1016) om resolution, 11 757 tecken**.

*Kortast i gällande rätt:*
> **25 kap. 7 § brottsbalken: "Böter tillfaller staten." — 24 tecken.**
> Delad förstaplats med 31 § bilskrotningsförordningen: "Avfall får inte
> brännas." Kortare mätvärden än så är alla artefakter av något annat:
> ombeteckningsstubbar ("Ny beteckning 2 §."), upphävda paragrafer som bara
> har kvar sin provenance-rad, eller definitionsparagrafer vars innehåll är
> en tabell.

*EU-artiklarna* ○ — samma mått finns för `article`-noder i eurlex-korpuset
(**216 342 artiklar, median 502 tecken**). Båda ytterlägena visade sig vara
parse-fel, inte kuriosa, och fyra av dem är nu fixade (se avsnittet sist):
den korta änden var fantomartiklar, den långa var artiklar som svalde
signaturblock, bilagor och citerade akter. Efter fixen faller 77 av 129
löpska artiklar under 20 000 tecken och det värsta fallet går från 286 000
till 206 000.

Kvar i den långa änden finns ett fall som inte är ett parse-fel utan en
egenskap hos källan: **ett CELEX-dokument kan innehålla flera instrument**.
Anslutningsakten 12003T/TXT har 86 artiklar numrerade 1, 2, 3, sedan 1, 2, 3,
4, 5 … — fördraget, anslutningsakten och de akter bilagorna återger i sin
helhet, allt i ett dokument. Där är artikelrubrikerna äkta; det som saknas är
att de hör till olika instrument. Se förslag R5.

**5. Fördelningen av lagars längd** ○
Histogram över alla 11 210 gällande författningar. Poängen är formen: den
är extremt sned — några få jättar och en lång svans av
tvåparagrafsförordningar.

**6. Längsta och kortaste rubriken** ✔
Beteckningen ska **inte** räknas med — "Ellag (1997:857)" är en rubrik på
fem tecken, inte sexton.
> Kortast: **Ellag (1997:857) — 5 tecken.** Sedan ett kluster på 6:
> Sjölag, Väglag, Vallag, Tullag, Köplag. Och på 7: Datalag, Gruvlag,
> Passlag, Namnlag, Skollag, Jaktlag, Postlag.
> Längst: **Kungörelse (1962:502) om tillämpning av en mellan Sverige samt
> Storbritannien och Nordirland … träffad överenskommelse — 385 tecken**
> utan beteckning.

Två fällor: sex SFS-rubriker inleds med renderingsmarkörer
(`/Rubriken upphör att gälla U:2027-01-01 …`) som måste bort först, och
katalogens `short_title` ger 6 tecken för Ellagen i stället för 5 eftersom
den byter till det etablerade namnformen ("Ellagen") för namngivna lagar.
Använd rå rubrik minus beteckning minus markörer.

**7. Lagar med eget namn** ○
Andelen författningar med en `dcterms:alternate` (BrB, RB, ABL, IL …) och
listan över dem. Roligt som "vilka lagar är kända nog att ha ett smeknamn".

**8. Författningar per departement** ✔
> Finansdepartementet 2 518 · Justitiedepartementet 1 836 ·
> Socialdepartementet 1 254 · Utbildningsdepartementet 824 ·
> Landsbygds- och infrastrukturdepartementet 556

**9. Hela svensk författningssamling i siffror** ○
Totalt antal tecken, ord, paragrafer och kapitel i allt som gäller — och
den oundvikliga följdfrågan: *hur lång tid tar det att läsa hela svensk
författningssamling högt?* (~200 ord/minut ger ett tal man kan sätta på en
sida.)

---

## B. Förändring och omsättning (10–19)

**10. De mest ändrade lagarna** ✔
Antal ändringsförfattningar i registret.
> Kommunalskattelag (1928:370) **570** · Lag (1962:381) om allmän försäkring
> 419 · Offentlighets- och sekretesslag (2009:400) 415 · Rättegångsbalk
> (1942:740) 404 · Brottsbalk (1962:700) 364

Notera att de tre översta är upphävda — en delad topplista "gällande" vs
"genom tiderna" är rimlig.

**11. Djupaste kedjan av "ändring i lagen om ändring i lagen om …"** ⚠✔
Den som frågas om oftast. Rekordet är **åtta led**:
> **Lag (2025:191) om ändring i lagen (2022:201) om ändring i lagen
> (2021:110) om ändring i lagen (2019:543) om ändring i lagen (2018:1799) om
> ändring i lagen (2018:546) om ändring i lagen (2017:1149) om ändring i
> lagen (2014:798) om ändring i lagen (2013:561) om förvaltare av
> alternativa investeringsfonder**

Med tvillingen SFS 2025:190 på samma djup. Fördelningen är ett fint
stapeldiagram i sig:

| djup | antal |
|---|---|
| 2 | 1 435 |
| 3 | 148 |
| 4 | 35 |
| 5 | 9 |
| 6 | 6 |
| 7 | 2 |
| 8 | 2 |

Kräver antingen att ändringsrubriken kommer med i artefakten, eller att
ändringsförfattningarna blir riktiga katalograder (förslag R1) — då blir
kedjedjupet en rekursiv SQL-fråga över `rpubl:andrar` i stället för en
regex över rubriktext, vilket också är mycket mer trovärdigt.

**12. Ändringsförfattningen som rör flest lagar samtidigt** ✔
> **SFS 1993:1646 ändrade 161 lagar på en gång** — förordningen om
> ikraftträdande av vissa lagar och förordningar med anledning av Sveriges
> tillträde till EES-avtalet. Sedan SFS 1993:1278 (55), SFS 1994:103 (50),
> SFS 1999:1230 (37).

**13. Lagar som aldrig har ändrats** ✔
> **2 111** av 11 210 författningar har aldrig fått en enda ändring
> (gällande och upphävda tillsammans). De äldsta är från 1850-talet, t.ex.
> Förordning (1851:55 s.4) angående sättet för uppsägning av förbindelser,
> för vilka flera är ansvariga.

**14. Ändringar per år** ✔
Tidsserie över alla ikraftträdda ändringar. Nivån ligger stadigt kring
1 000–1 800 per år.
> 2018: 1 792 · 2022: 1 571 · 2025: 1 284 · 2026: 1 882
> Totalt **75 817** distinkta ändringsförfattningar i korpusen.

**15. Snabbast ändrade lag** ✔
Kortaste tiden mellan ikraftträdande och första ändringens ikraftträdande.
> Bottennoteringen är **1 dygn**, delad av ett tiotal författningar —
> bl.a. Förordning (2007:1202) med instruktion för Socialstyrelsen (i kraft
> 2008-01-01, ändrad 2008-01-02) och Mervärdesskattelag (2023:200).

**16. Lagtextens medelålder** ✔ *(byggd)*
För varje lag: hur gammal är texten som faktiskt gäller idag, viktat per
paragraf via `rpubl:inforsI`/`rpubl:ersatter`? Ger både "mest renoverade lag"
och "lag med äldst kvarvarande text".
> 374 författningar går att mäta så; deras medeltext är från 2012. Äldst
> kvarvarande text: medbestämmandelagen (1977), medeltext 1988,6. Mest
> renoverade: väglagen (1972) och tandvårdslagen (1986), båda 29 års förnyelse.

Populationen är gällande författningar med minst 20 paragrafer där registret
namnger de berörda paragraferna för minst 90 % av de daterade ändringarna —
utan det kravet läses en lag vars register är tyst om *vad* ändringarna rörde
som helt oförändrad.

**17. Den mest ändrade enskilda paragrafen** ○
Räkna `rpubl:inforsI`-träffar per ankare över hela korpusen. Vem vinner
mellan 3 kap. 1 § BrB och någon undanskymd skatteparagraf?

**18. Vad ändringar faktiskt gör, över tid** ○
Fördelningen av `rpubl:andrar`-texten i kategorier (`ändr.` / `upph.` /
`ny` / `omtryck` / `nuvarande … betecknas`) som en tidsserie. Visar om
lagstiftaren har gått från att ändra till att skriva om.

**19. Tidsmaskinens djup** ✔
Antal historiska konsoliderade versioner per lag — hur många olika lydelser
lagen.nu kan visa.
> Offentlighets- och sekretesslag (2009:400): **218 versioner**.
> Brottsbalken: 103. Inkomstskattelagen: 100.
> Totalt **31 189** historiska versioner mot 11 210 gällande lagar.

---

## C. Tid och livslängd (20–27)

**20. Äldsta lagar som fortfarande gäller** ✔
> **Byggningabalken och Handelsbalken (1736:0123)** — 290 år gamla och
> fortfarande i kraft. Sedan Successionsordningen (1810:0926) och
> Lag (1845:50 s.1) om handel med lösören som köparen låter i säljarens vård
> kvarbliva.

**21. Längst levande upphävda lagar** ✔
> **Kyrkolag (1686:0903)** gällde i **306 år** innan den upphävdes 1993.
> Ridderskapets och adelns privilegier (1723): 279 år, upphävda 2003.
> Förordning (1772:1104) angående sabbatens firande: 217 år.

**22. Kortast levande lagar** ✔
> **Förordning (2018:2023) om skiktgränser för statlig inkomstskatt för
> beskattningsåret 2019 — 14 dagar.** Beslutad 6 december 2018, upphävd 20
> december 2018.

**23. Upphävanden per år** ✔
Tidsserie. Nivån ligger på 60–120 per år.
> 2018: 118 · 2019: 101 · 2022: 70 · 2025: 66 · 2026: 79

**24. Överlevnadskurva** ○
Av alla författningar utfärdade år *Y*, hur stor andel gäller fortfarande?
En kurva från 1900 till idag. Det här är den mest intressanta serien i hela
listan — den ger en riktig "halveringstid för svensk lag".
> Grunddata finns: 5 309 av 11 211 SFS-poster är fortfarande i kraft.

**25. Hur långt varsel får en ny lag?** ⚠✔ *(byggd, men bara för
grundförfattningar)*
Fördelningen av tiden mellan `rpubl:utfardandedatum` och
`rpubl:ikrafttradandedatum`. **Inte** för ändringar: se punkt 5 i "Vad som
saknas" — ändringsregistret anger inget utfärdandedatum, så posten mäter de
7 870 grundförfattningar som bär båda datumen.
> Median 36 dygn; en fjärdedel får 24 dygn eller mindre. Tyngdpunkten ligger
> på 31–60 dygn (3 166 författningar). Längst varsel: lagen om ansvar och
> ersättning vid radiologiska olyckor, 4 202 dygn.

**26. Framtiden som redan är skriven** ✔
Ändringar som är beslutade men ännu inte i kraft, per framtida år.
> 2027: 153 · 2028: 53 · 2029: 15 · 2030: 44 · 2031: 6 —
> och enstaka ändringar daterade **2032, 2035, 2036 och 2037**.

**27. Nya författningar per decennium** ✔
Bland dem som fortfarande gäller — visar hur snabbt äldre lager tunnas ut.
> 1970-talet 1 162 · 1980-talet 2 066 · 1990-talet 2 429 · 2000-talet 1 801
> · 2010-talet 1 541 · 2020-talet 966

---

## D. Hänvisningsgrafen (28–35)

**28. Korpuset i siffror** ✔
> **11 483 996 hänvisningar** mellan **247 451** dokument. Av dem har
> **6 073 586** ett sidnummer i det citerande dokumentet.

**29. Mest hänvisade dokument** ✔
> Rättegångsbalken **200 209** inkommande · Brottsbalken 199 661 ·
> Miljöbalken 172 969 · Regeringsformen 142 976 · Utlänningslagen 100 545 ·
> EUF-fördraget 92 369

**30. Mest hänvisade enskilda paragraf** ✔
> **6 § räntelagen (1975:635) — 9 467 hänvisningar.** Dröjsmålsräntan är
> alltså den mest omtalade paragrafen i hela svensk rätt.
> Sedan 8 kap. 7 § regeringsformen (9 296) och artikel 267 FEUF (8 656).

**31. Dokument med flest utgående hänvisningar** ✔
> SOU 2020:63 *Barnkonventionen och svensk rätt* — **14 233 hänvisningar i
> ett enda dokument.** Sedan SOU 2024:98 (12 955) och prop. 1994/95:19 om
> Sveriges medlemskap i EU (12 551).

**32. Hänvisningsmatrisen mellan källor** ✔
Vem citerar vem, som en värmekarta.
> förarbeten → SFS 4 155 603 · EU-rätt → EU-rätt 2 789 459 ·
> förarbeten → EU-rätt 1 404 396 · SFS → SFS 437 982 · domar → SFS 323 463
> · Europadomstolen → Europakonventionen 27 195

**33. Föräldralösa lagar** ✔
Gällande författningar som ingenting i hela korpuset hänvisar till.
> **971 stycken** — 18 % av allt som gäller.

**34. Den mest självrefererande lagen** ✔
> Inkomstskattelagen hänvisar till sig själv **5 896 gånger**;
> socialförsäkringsbalken 5 148. (EU-förordningar slår båda: tullkodexens
> genomförandeförordning har 8 257 interna hänvisningar.)

**35. Mest omtalade begrepp** ✔
Begrepp rankade på inkommande länkar.
> Långsam handläggning 685 · Skadestånd 588 · Förundersökning 392 ·
> Brottspåföljd 360 · Rättegångskostnad 349 · Konkurs 338 · Sekretess 320

---

## E. Förarbeten (36–41)

**36. Förarbeten per år och typ** ✔
> **97 179** förarbeten totalt: 28 279 propositioner, 24 803 betänkanden,
> 20 028 riksdagsskrivelser, 8 626 SOU:er, 5 194 kommittédirektiv,
> 1 446 Ds. Årstakten ligger kring 1 200–1 650 dokument.

**37. Tjockaste utredningen** ○ (delvis ✔)
Teckenantal per SOU. Ur ett stickprov på 400:
> SOU 2020:83 *Havet och människan* — 3,07 miljoner tecken.
> SOU 2026:15 *Marken, vattnet, tankarna* — 2,56 miljoner.
Kräver en full genomsökning för att bli en riktig topplista.

**38. Propositionen som ändrade flest lagar** ✔
> **Prop. 2013/14:110 — 198 lagar** ändrade genom 201
> ändringsförfattningar. Sedan **prop. 2018/19:162** *En ny beteckning för
> kommuner på regional nivå* (162 lagar — varje lag som sa "landsting"
> behövde skrivas om) och **prop. 2010/11:166** *Följdändringar med
> anledning av införandet av skatteförfarandelagen* (143).

Värt att notera: prop. 2013/14:110 finns bara som stubbe i korpusen — vi
har den som citerad referens men inte som hämtat dokument. En topplista av
den här sorten kommer att innehålla dokument vi saknar texten till, och
sidan bör visa det i stället för att dölja det.

**39. Volymen författningskommentar** ✔
> **59 152 stycken** kommentar till enskilda paragrafer, hämtade ur
> propositionerna. Mest kommenterade lag: skollagen (2010:800) med 1 918
> kommentarer, sedan inkomstskattelagen (1 330) och utlänningslagen (1 273).

**40. Från förslag till lag** ✔ *(byggd)*
Tiden från propositionens datum till ändringens ikraftträdande. Både
medianen och rekorden i båda riktningar.
> Median 133 dygn över 13 136 spårbara par. Kortast: prop. 2020/21:79 →
> SFS 2021:5 och 2021:7 på 6 dygn (pandemilagstiftningen). Längst:
> prop. 2005/06:4 → SFS 2023:168, 6 410 dygn.

Kräver att ändringen har både ikraftträdandedatum och en
propositionshänvisning, och att propositionen har ett dagsexakt datum i
katalogen: 5 106 av de 8 822 daterade propositionerna ligger på 12-31 eller
01-01, vilket är ett årtal stämplat som datum och skulle lägga ett halvår fel
på varje gammal proposition.

**41. Paragrafmotsvarigheter** ✔
De spårade "motsvarar/överförd från"-relationerna mellan gammal och ny
lagtext — hur mycket av en ny lag som egentligen är gammal.
> **6 126** motsvarigheter: 5 802 "helt", 264 "delvis", 60
> ombeteckningar.

---

## F. Rättspraxis (42–46)

**42. Avgöranden per domstol** ✔
> RÅ 6 229 · NJA 4 599 · RH 3 638 · AD 2 492 · HFD 1 799 · MÖD 1 429 ·
> MIG 533 · Marknadsdomstolen 346 · Patent- och marknadsöverdomstolen 208.
> Totalt **23 733** avgöranden, 1981–2026.

**43. Mest hänvisade rättsfall** ✔
> **Juniavgörandet (NJA 2013 s. 502) — 361 hänvisningar.** Sedan
> Finanschefen på ICS (NJA 2005 s. 462) 342, Den felaktiga
> läkarundersökningen (NJA 2007 s. 584) 230, Kezban (NJA 2013 s. 842) 227.

**44. Vilka lagrum domstolarna citerar mest** ○
De 323 463 hänvisningarna från domar till SFS, aggregerade per paragraf —
och uppdelat per domstol, så man ser HD:s och HFD:s helt olika kartor.

**45. Namngivna rättsfall** ○
Andelen avgöranden som har fått ett namn (`lib/casenaming`), över tid.
Namngivningen är en relativt ny HD-praxis och kurvan borde synas tydligt.

**46. Längsta och kortaste avgörandet** ○
Teckenantal per avgörande. Både "vilken dom är en bok" och "vilket referat
är tre meningar".

---

## G. Myndighetsföreskrifter, remisser och omvärlden (47–51)

**47. Föreskrifter per myndighet** ✔
> **12 939** föreskrifter ur **101** författningssamlingar.
> Skolverket 3 183 · Transportstyrelsen 1 115 · Jordbruksverket 803 ·
> Socialstyrelsen 582 · Skatteverket 577 · Riksarkivet 447

**48. Bemyndigandekedjan** ○
Vilken lag har bemyndigat flest myndighetsföreskrifter? Datat finns som
9 027 `rpubl:bemyndigande`-länkar — en fin bild av var i lagstiftningen
den delegerade normgivningen faktiskt sitter.

**49. Direktiven som satt djupast spår i svensk rätt** ✔
> Mervärdesskattedirektivet (2006/112/EG) genomförs i **520** svenska
> paragrafer. Sedan LOU-direktivet (2014/24/EU) 312, LUF-direktivet
> (2014/25/EU) 287, kapitaltäckningsdirektivet (2013/36/EU) 259.
> Motsatt riktning: **mervärdesskattelagen (2023:200) genomför 688 EU-artiklar**
> — flest av alla svenska lagar.

**50. Europakonventionen i svensk rätt** ✔
Vilka artiklar som faktiskt används.
> Artikel 6 (rättvis rättegång) **5 582** hänvisningar · artikel 8
> (privatliv) 4 339 · artikel 6.1 4 213 · artikel 3 (tortyrförbudet) 2 507.
> Och: **77 av Europadomstolens 7 059 domar i korpuset rör Sverige.**

**51. Remissvaren** ✔
**14 735 remissvar** på **302 remitterade ärenden**.
> Flest svar: **SOU 2024:29 *Goda möjligheter till ökat välstånd* — 189
> remissinstanser.** Sedan SOU 2025:6 (182) och SOU 2024:98 *En ny samordnad
> miljöbedömnings- och tillståndsprövningsprocess* (176).
> Flitigaste remissinstanser: **Regelrådet 148 yttranden** ·
> Polismyndigheten 118 · Skatteverket 117 · Statskontoret 112 ·
> Sveriges advokatsamfund 110 · Justitiekanslern 105

Räknat direkt ur artefaktträdet — remisserna ligger ännu inte i katalogens
`documents`-tabell, så den här posten läser filer i stället för SQL.

---

## En mätpunkt som *inte* går att bygga

Värd att nämna eftersom den ser lockande ut: **"EU-drivna ändringar över
tid"**. Nedladdningsposten för varje ändringsförfattning har både
`celexnummer` och en `eUdirektiv`-flagga, så serien går att räkna. Men den
ljuger:

| år | flaggade som EU-drivna |
|---|---|
| 2000–2012 | **0 av 14 300** |
| 2013 | 51 av 964 |
| 2016 | 137 av 1 209 |
| 2026 | 139 av 1 714 |

Regeringskansliet började helt enkelt registrera fältet 2013. En kurva av
det här skulle påstå att EU-rätten inte påverkade svensk lagstiftning före
2013, vilket är uppenbart falskt. Bara 2 158 av 67 439 ändringsförfattningar
har över huvud taget ett celexnummer.

Vill man mäta EU-påverkan är `rpubl:genomforDirektiv`-länkarna (13 692
stycken) och `genomforande`-tabellen rätt källa — de kommer ur
propositionstexten, inte ur ett registreringsfält.

---

## Vad `relate` borde lägga till

Det här är den intressantaste frågan i hela övningen. Nyckelobservationen:

> **`relate` läser redan varje artefakts hela kropp.** `index_artifact()`
> går igenom hela dokumentträdet för att plocka ut hänvisningar. Varje
> skalär man kan räkna under den vandringen är i princip gratis — medan
> samma siffra i `stats` kostar en ny genomsökning av 42 399 filer.

Fyra förslag, i fallande ordning av nytta per krona:

### R1. Ändringsförfattningar som dokumentrader

Precis som du säger: de *är* dokument. De trycks i SFS, har en egen
beteckning, ett eget utfärdandedatum, en egen rubrik och egna förarbeten —
och i de flesta fall finns de som PDF hos utgivaren.

Idag finns 67 439 distinkta ändringsförfattningar i `downloaded/` (59 949
med rubrik) och 75 817 sedda över artefaktträdet, men **noll** rader i
`documents`. Det gör att SFS 1985:518 är osynlig för sökningen, för
inbound-panelerna, för API:et och för all statistik.

Som katalograder skulle de bära:

| kolumn | värde |
|---|---|
| `uri` | `https://lagen.nu/1985:518` |
| `kind` | `andringsforfattning` |
| `label` / `title` | `SFS 1985:518` / hela ändringsrubriken |
| `date` | utfärdandedatum |
| `publisher` / `creator` | Regeringskansliet / departementet |

plus länkar: `rpubl:andrar` → grundförfattningen (och, för en kedjeakt,
till den ändringsförfattning den ändrar), `rpubl:forarbete` → propositionen,
`rpubl:genomforDirektiv` → direktivet.

Vad det köper:

- **Kedjedjupet (post 11) blir en rekursiv fråga över `rpubl:andrar`** i
  stället för en regex över rubriktext. Både mer trovärdigt och oberoende
  av att rubriken följer en viss formulering.
- **Post 10, 12, 14, 15, 18, 26 och 38 blir SQL** i stället för en
  artefaktgenomsökning. Det är sju av de mätvärden som idag driver hela
  kostnaden i `stats`.
- Utanför statistiken: ändringsförfattningar blir sökbara och länkbara, och
  "vilka ändringar hör till prop. X" blir en join.

Kostnad: ~67 000 dokumentrader och kanske 200 000 länkar. Mot dagens
247 451 dokument och 11,5 miljoner länkar är det försumbart.

**En arkitektonisk detalj:** `catalog.rebuild()` synkroniserar rader
per artefaktsökväg (`have` är keyad på `path`, 1:1 med ett dokument). En
ändringsförfattning har ingen egen artefaktfil — dess innehåll bor inne i
grundförfattningens artefakt. Den ska alltså **inte** skrivas av
`index_artifact()`, utan av en efterpass i samma stil som
`synthesize_concepts()`, som redan äger de sökvägslösa raderna. Mönstret
finns, det behöver bara användas.

Det som *inte* följer med: en ändringsförfattnings egen brödtext. Vi har
övergångsbestämmelserna (de ligger i grundförfattningens artefakt) men inte
själva ändringsanvisningarna som eget dokument. Vill man ha det behövs en
riktig hämtning av PDF:erna — ett eget projekt, och inget statistiken
behöver.

### R2. Per-dokument-mått direkt i `documents`

Fem heltalskolumner, fyllda under den vandring `relate` ändå gör:

```
chars, words, n_paragrafer, n_kapitel, max_page
```

Det ensamt gör post 1, 2, 3, 5, 9, 37 och 46 till `ORDER BY`-frågor, och
tar bort behovet av en full artefaktgenomsökning i `stats` nästan helt.
Kostnaden i `relate` är en handfull additioner per nod.

Bonus: `chars` blir ett gratis kvalitetslarm. De 101 SFS-artefakterna med
noll kroppstext skulle synas i en `ops`-vy i stället för att upptäckas av
en statistikkörning.

### R3. En `nodes`-tabell

```sql
CREATE TABLE nodes (
    uri      TEXT NOT NULL,   -- dokumentets uri
    anchor   TEXT NOT NULL,   -- K25P7
    type     TEXT NOT NULL,   -- paragraf | kapitel | article | ...
    ordinal  TEXT,            -- "7"
    chars    INTEGER,         -- textinnehåll, tabellceller inräknade
    page     INTEGER          -- där dokumentet har sidor
);
```

Storleksordning: ~196 000 SFS-paragrafer + ~216 000 EU-artiklar + noderna i
förarbeten och domar. Stort, men en bråkdel av `links`.

Det är precis vad **post 4** (den nya mätpunkten) behöver, och dessutom
post 17 (mest ändrade paragrafen) och 44 (vilka lagrum domstolarna citerar).

Men den viktigaste nyttan ligger utanför statistiken: idag kan katalogen
säga att något pekar på `1975:635#P6`, men inte vad det ankaret *heter*
eller *innehåller*. En `nodes`-tabell låter inbound-panelen skriva
"6 § räntelagen" med rätt beteckning utan att öppna artefakten — och den
skulle göra länkupplösningen validerbar: en `to_uri` vars fragment saknar
motsvarande `nodes`-rad är en trasig hänvisning, och den siffran vill vi ha
i `ops`.

### R4. Rena namnformer

`documents.title` bär idag både beteckningen ("Ellag (1997:857)") och, för
sex dokument, renderingsmarkörer (`/Rubriken upphör att gälla U:2027-01-01
…`). `short_title` är nästan rätt men byter till den etablerade namnformen
("Ellagen") för namngivna lagar, vilket gör den olämplig som mått på
rubriklängd.

Ett `clean_title`-fält — rubrik minus beteckning minus markörer, ingen
namnsubstitution — löser post 6 och är samtidigt det fält en sorterad
lagförteckning egentligen vill sortera på.

### R5. Flagga dokument som innehåller flera instrument

Det här är svaret på frågan "kan ändringsakters artiklar märkas upp och
räknas bort från *längsta artikel*".

**Att flagga ändringsakter är fel verktyg.** Av de 187 artiklarna över 20 000
tecken sa bara 34 "om ändring/amending" i titeln. Att utesluta dem hade
lämnat 153 kvar och samtidigt kastat bort äkta ändringsakter med normala
artiklar. Måttet var inte fel för att ändringsakter är speciella — det var
fel för att parsern satte ihop artiklarna fel, och fyra buggar senare är 77
av 129 fall borta utan att någon metadata behövdes.

**Men resten är en äkta egenskap hos källan, och där är en flagga rätt.** Ett
CELEX-dokument är inte alltid *en* akt:

- Anslutningsakten 12003T/TXT är fördraget **plus** anslutningsakten **plus**
  de akter bilagorna återger i sin helhet — 86 artiklar numrerade 1, 2, 3,
  sedan 1, 2, 3, 4, 5 …
- Beslutet 31998D0490 (Crédit Lyonnais) citerar ett tidigare besluts
  artiklar i sina skäl: numreringen går 2, 3, 4, 5, **1, 2, 3**.

Signalen är densamma i båda: **artikelnumreringen startar om**. Det är en
strukturell egenskap som `nest` ser gratis medan den bygger trädet, och den
hör hemma som en dokumentnivåflagga i artefakten och i `documents`:

```
multi_instrument INTEGER   -- artikelnumreringen startar om: dokumentet bär
                           -- fler än ett instrument (anslutningsakter,
                           -- beslut som citerar tidigare beslut)
```

Före fixarna hade 911 av 63 836 dokument (1,4 %) icke-stigande numrering; båda
de värsta kvarvarande fallen ovan är bland dem. Flaggan är alltså precis nog
för att statistiksidan skall kunna säga *"längsta artikel, bland akter som
bär ett enda instrument"* — och ärlig nog att kunna säga hur många den
uteslöt.

Två saker till hör ihop med den här posten:

- **Redovisa medianen, inte bara rekordet.** Medianen (502 tecken för EU,
  304 för SFS) är okänslig för exakt den här sortens fel. Ett mått som bara
  visar max är ett mått som visar den värsta parse-buggen.
- **Ändringsaktflaggan är ändå värd att ha** — inte för det här måttet, utan
  för att "hur stor andel av EU-rätten är ändringsakter" är en egen
  intressant siffra, och titeln bär signalen tillförlitligt.

---

## Om jag skulle bygga tio först

De som är både roligast och redan helt räknebara:

11 (kedjedjupet), 22 (14-dagarslagen), 20+21 (äldst i kraft / längst levande),
12 (161 lagar på en gång), 4 ("Böter tillfaller staten" vs 55 000 tecken),
30 (räntelagens 6 §), 31 (SOU 2020:63 med 14 233 hänvisningar),
43 (Juniavgörandet), 26 (lagar som gäller från 2037), 33 (971 föräldralösa
lagar).

Ingen av dem kräver kodändring — men R1 och R2 skulle göra ungefär hälften
av hela listan billigare att bygga och lättare att lita på.

## Fyra parse-fel i EU-korpuset (åtgärdade)

Att bara *mäta* korpuset hittade fyra riktiga buggar — värt att skriva ner,
eftersom det är hela argumentet för att bygga sidan.

Alla fyra sitter i den klasslösa legacy-HTML:en (pre-Formex, "Avis juridique
important"), där `parse_html` måste gissa strukturen ur texten i stället för
att läsa den ur taggar. Gemensam nämnare: **en textrad som *börjar* med ett
strukturord togs för en rubrik**, eller så saknades den rubrik som skulle ha
avslutat något.

### 1. Fantomartiklar ur ändringsakters prosa

**Symptomet:** 1 671 EU-artiklar under 30 tecken, koncentrerade till
1970- och 80-talen.

**Orsaken:** i den klasslösa legacy-HTML:en (pre-Formex, "Avis juridique
important") gissar `parse_html` strukturen ur texten. Testet var att en rad
på högst 60 tecken som *börjar* med "Artikel N" är en artikelrubrik. Men en
**ändringsakt** skriver hela sin brödtext i den formen:

> Artikel 9.2 skall ersättas med följande:
> Artikel 10.1. b skall ersättas med följande:
> Artikel 8 skall utgå.

Varje sådan mening blev en artikelrubrik — numrerad efter den akt som
*ändrades*, inte akten man läste. Direktiv 69/60/EEG har åtta artiklar, men
korpuset visade elva i ordningen 1, 2, 3, 4, 5, **9**, 6, **10**, 7, **15**,
8. Och eftersom fantomen la sig mellan den riktiga artikeln och dess
brödtext blev artikel 5, 6 och 7 *tomma* — allt innehåll hamnade under en
artikel som inte finns.

**Fixen:** en egen `Vocab.article_heading` i `eurlex/lang.py` som skiljer en
rubrik från prosa på vad som följer numret. En punktprecisering ("9.2") eller
en gemen fortsättning ("skall utgå") är en mening; en rubrik står ensam eller
följs av versal, siffra, citattecken eller tankstreck. Den lösa `article`
finns kvar för tabellcellsmarkörer, där cellen redan är strukturell.

`parse_pdf` hade samma rad kopierad och använder nu samma definition.

**Effekt**, mätt genom att parsa om de 260 berörda dokumenten från deras
källfiler:

| | före | efter |
|---|---|---|
| artiklar | 6 763 | 5 793 |
| fantomartiklar | 958 | 0 |
| tomma artiklar | 592 | 48 |

De 48 kvarvarande tomma är äkta — upphävda artiklar vars text är borta.

### 2. Latinska ordningstal i ankaret

`article_num` läste bara *en* bokstav efter numret, så Pariskonventionens
"Artikel 6ter" och "Artikel 6sexies" fick ankarna `#6t` och `#6s` — som inte
pekar på någonting. Nu `[a-z]*`, så hela suffixet följer med.

### 3. Den flerspråkiga bilagerubriken

Den pre-2000 OJ:n tryckte bilagerubriken en gång för *varje* språkupplaga, på
en rad:

> ANEXO I - BILAG I - ANHANG I - ΠΑΡΑΡΤΗΜΑ I - ANNEX I - ANNEXE I - ALLEGATO I
> - BIJLAGE I - ANEXO I - LIITE I - BILAGA I

`voc.heading` letar efter *dokumentets eget* språk först i raden, och en
svensk akt vars rad börjar på spanska matchade ingenting. Bilagan förblev
brödtext och aktens sista artikel svalde den.

Testet är radens *form*: tre eller fler ' - '-separerade segment som alla är
ett bilageord med eventuellt numeral. Att kräva att *varje* segment
kvalificerar är det som håller vanlig prosa ute — en mening som nämner
"bilaga" tre gånger har bara ett segment. (Ett lösare ordräkningstest
flaggade 1 418 sådana meningar mot 244 äkta rubriker.) Raden förekommer också
med separatorerna borta, orden ihopskrivna
("ANEXOBILAGANHANG…"), och den formen känns igen på samma sätt.

Rubriktexten blir läsarens eget språksegment ("BILAGA I"), utom för de rader
som trycktes innan svenskan var OJ-språk — där finns inget svenskt segment,
och hela raden får stå.

### 4. Signaturblocket och domar utan artiklar

Två varianter av samma sak: artikeln stängdes aldrig.

- **Signaturen.** `structure.nest` stänger en öppen artikel på ett
  `signature`-block — men den klasslösa HTML:en märker inget som `signatory`,
  så "Utfärdat i Bryssel den 14 juli 1986." förblev ett vanligt stycke.
  Aktens sista artikel fortsatte svälja signatur, fotnoter och samtliga
  bilagor (31986L0465:s artikel 3: 193 710 tecken över 6 143 stycken). Nu är
  slutfrasen en del av språkvokabulären, som ingressformeln redan var.
- **Domarna.** En dom återger den överklagade aktens artiklar, och en citerad
  "Artikel 4" öppnade en behållare som svalde resten av domen (61989TJ0068:
  280 353 tecken). En dom har inga egna artiklar — nu härleds inga för
  `CASELAW`-doctyper.

### Effekt

Mätt genom att parsa om de berörda dokumenten från deras källfiler:

| | före | efter |
|---|---|---|
| fantomartiklar (kort ände) | 958 | 0 |
| tomma artiklar | 592 | 48 |
| akter med artikel > 20 000 tecken | 129 | 52 |
| största artikel | 285 617 | 205 552 |

De 48 kvarvarande tomma är äkta — upphävda artiklar vars text är borta.

Alla fyra är låsta med regressionstester i `test/test_eurlex_html.py`.
Hela sviten (2 050 tester) går igenom.

**Kvar att göra:** fixarna ändrar `EURLEX_CODE`, så nästa `lagen eurlex parse`
parsar om hela korpuset (63 836 dokument) av sig själv. Tills det körts har
artefakterna kvar felen, och siffrorna i post 4 ovan är de gamla.

**Ett femte fel, hittat men inte åtgärdat:** `voc.heading` har exakt samma
över­matchning som artikelrubriken hade — "Bilaga I skall ändras." (22 tecken,
börjar med ett strukturord) blir en rubrik. Samma diskriminator som
`article_heading` använder skulle lösa det, men det är en femte ändring i
samma parser och bör mätas för sig.

## Där siffrorna kommer ifrån

Alla ✔-siffror är körda mot `site/data/catalog.sqlite` och
`site/data/artifact/` 2026-07-27. De tre genomsökningarna:

- **SFS-artefakterna** (42 399 filer): per lag teckenantal, antal
  paragrafer/kapitel/stycken, och hela `amendments`-listan platt utlagd
  (ikraftträdande, omfattning, förarbeten, `inforsI`). ~4 min över alla
  kärnor.
- **SFS-artefakterna igen, per paragraf** (196 503 noder) för post 4,
  korsad med katalogens `expired`-kolumn för uppdelningen gällande/upphävd.
- **`downloaded/sfs/`** (14 021 filer): `andringsforfattningar[].rubrik` för
  kedjeräkningen i post 11, och `eUdirektiv`/`celexnummer` för avsnittet om
  mätpunkten som inte går att bygga.

Resten är enskilda SQL-frågor mot katalogen.
