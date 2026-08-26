# EU-rättslig praxis i paragrafmarginalen — status, arkitektur och plan

*Handover 2026-07-24. Målbild: på varje svensk lagparagraf som genomför en
direktivartikel ska läsaren se EU-domstolens praxis om just den artikeln.
Syratestet är upphandlingsrätten: LOU/LUF/LUK-paragraferna mot 2014 års
direktiv och deras föregångare (2004/18, 2004/17, 92/50 …), där den mesta
praxisen finns.*

## Nuläge (klart och verifierat)

Kedjan fungerar ände till ände på riktiga data:

- **LOU 13 kap. 1 §** (uteslutning på grund av brott, genomför artikel 57 i
  2014/24) visar C-41/18 Meca, C-124/17 Vossloh Laeis och C-590/24.
- **LOU 3 kap. 12 §** (intern upphandling, genomför artikel 12) visar
  C-285/18 Irgita, C-719/20, C-332/20, C-253/18, C-856/24.

Bakom det ligger dagens arbete:

1. **Genomförande-referenserna finns för hela korpusen.** SFSR-datat (fältet
   CELEX-nummer för äldre författningar, direktivhänvisningen i
   Förarbeten-raden för nyare) identifierade 763 propositioner 1994/95– som
   genomför EU-rättsakter; 361 var körbara (parsad prop + minst ett parsat
   direktiv + kandidater i författningskommentaren) och samtliga har nu ett
   `.ann`-lager i annstore (WIKI_ROOT/ann/forarbete/prop/), genererade med
   gemma-4-31B (96 via Berget innan bytet, resten lokalt). Plus prop
   2015/16:195 (nya LOU/LUF/LUK, fyra volymer, 837 FK-kandidater) som krävde
   tre parserfixar (se nedan). Totalt **364 lager**; efter relate ligger
   **7 959 paragraf↔artikel-referenser** i katalogen (mekaniska extraktionen
   gav 372), varav 419 på LOU och 141 med styckepinpoint.

2. **Kvaliteten är mätt.** Ett fable-adjudikerat facit för 2015/16:195
   (823 referenser, `2015-16-195.ann.golden`) gav den regenererade
   195-körningen **precision 0,997 / recall 0,999**. Den första körningen
   låg på 0,80/0,76 — nästan hela felet (163 av 166 falska träffar) var
   *rätt påstående bundet till fel direktiv*, orsakat av alias-bindningsbuggen
   nedan, inte av modellen.

3. **Fem verkliga buggar hittades och fixades under körningen**, alla låsta
   med regressionstest:
   - eurlex `lang.article_num`: ändelseankrat regex gav artikelnoder utan
     nummer i gamla txt_te-HTML-formatet ("Artikel 1 Räckvidd") — sex
     direktiv (bl.a. IPRED 2004/48 och rörlighetsdirektivet 2004/38) hade
     tomma artikelinventarier.
   - forarbete `parse`: flervolymspropositioner lästes bara till volym 1
     (`pdfs[0]`); nu konkateneras alla volymer (195:s FK låg i del 2).
   - forarbete `kommentar.fk_span`: FK-kapitlet klipptes efter fyra block
     när LOU 1 kap. 1 §:s citerade lagtext räknade upp lagens egna bilagor
     ("Bilaga 1 – …"); bilagemarginalian avslutar nu kapitlet först när den
     upprepas på nästa block.
   - forarbete `kommentar.resolve_directives` (alias-bindningen): (a) en
     uppräkningsmening som definierar flera direktiv band varje alias till
     meningens *första* direktiv; (b) en senare tillfällig parentes kunde
     skriva över en korrekt tidig definition; (c) "Direktiv X har ersatts av
     [formell citering] (alias)" band till det ersatta. Ny regel: spannet
     öppnar efter föregående alias, bindningen utgår från *första formella
     citeringen* ("Europaparlamentets och rådets direktiv …") i spannet,
     första definitionen vinner. 18 flerdirektivspropositioner vars
     kataloketiketter ändrades är regenererade.
   - `prop_implements`: fragmentbärande direktiv-URI:er i mekaniska
     referenser undgick ersättningen av det författade lagret
     (`directive_base`-normalisering).

4. **Styckepinpoints ("sfs": "S1" / "S3N2") går hela vägen.** LLM-svaret får
   ett valfritt fält i SFS-elementid-syntax; formkontroll vid författandet
   (felformat ignoreras, referensen består), existenskontroll mot den
   publicerade lagens myntade element-id:n vid relate (S5 på en
   tvåstyckesparagraf ignoreras — förlåtande per design), lagras i
   `genomforande.sfs_pinpoint` och skrivs ut som citatprosa ("första
   stycket genomför …"). Modellen använder fältet glest (54 av 828 på 195)
   — promptjustering återstår.

5. **Praxisrutan är byggd** (uppgift 2 nedan) och testad mot fixtur och
   verklig katalog.

Ej committat: alla kodändringar ligger i arbetsträdet (ferenda), alla
`.ann`/`.ann.golden`-lager i lagen-wiki.

## Arkitektur

Kedjan består av fyra lager, vart och ett självständigt användbart:

```
LOU 13 kap. 1 §  ──genomforande-tabellen──▶  32014L0024#57
                     (sfs_uri, sfs_anchor,        ▲
                      directive, article,         │ generiska inbound-länkar
                      sfs_pinpoint, prop_uri)     │ (links-tabellen)
                                                  │
                                    62018CJ0041 (C-41/18 Meca)
                                    dom, parsad i eurlex-vertikalen,
                                    citaten typade av citeringsmotorn
```

1. **Källan till paragraf↔artikel:** författningskommentarens uttryckliga
   genomförandepåståenden, lästa av `forarbete ai-genomforande` (LLM-passet)
   till ett `.ann`-lager per prop; `genomforande.resolve` föredrar det
   författade lagret framför den mekaniska extraktionen per täckt direktiv
   och pinnar referenserna till SFS-ankare vid relate.
2. **Domarna:** eurlex-korpusen håller 14 227 EU-domstolsdomar (6CJ) +
   11 183 förslag till avgörande (6CC) + 5 992 tribunaldomar (6TJ), parsade
   med samma struktur- och citeringsmaskineri som direktiven — en doms
   "artikel 12 i direktiv 2014/24" är redan en typad referens till
   `…/celex/32014L0024#12` i links-tabellen (2 769 artikelnivålänkar in i
   2014/24 från 107 domar).
3. **Sammanfogningen:** `catalog.caselaw_anchored(con, sfs_uri)` tilldelar
   hela lagens praxis i ett svep: varje doms citering (t.ex. `#57.4`) hamnar
   hos den paragraf vars genomförande-pinpoint täcker den djupast; vid lika
   djup vinner direkt genomförande före ärvt, sedan första paragrafen i
   lagens ordning; en citering utan täckande anspråk faller tillbaka på
   artikelfamiljens första paragraf. Domar är sektor 6, dokumentkod CJ/TJ/FJ
   — förslag till avgörande och beslut räknas inte som praxis.
4. **Visningen:** `render.eu_caselaw_margin` i paragrafens kontextpanel —
   "EU-domstolens praxis", nyast först, max 5 uppfällda med "+N till", dedup
   när en dom citerar flera artiklar, "(om artikel N)" som attribution.

**Det avgörande designvalet:** praxisrutan hämtar sin tilldelning via
`caselaw_anchored`, aldrig direkt ur genomforande-tabellen. Det är sömmen
där direktivsläktskapet (uppgift 3) kopplas in utan att röra visningssidan.

## Uppgift 3 — direktivsläktskapet: klart (2026-07-24)

Problemet var: dagens ruta ser bara domar som citerar 2014 års direktiv. Den
klassiska praxisen citerar 2004/18, 2004/17, 92/50, 93/36–38 — och de fanns
inte i korpusen alls, eftersom sektor 3-beståndet packades upp ur EUR-Lex
bulkdump, som *endast innehåller gällande rättsakter*. Inget eget filter —
upphävda direktiv fanns aldrig i indatat.

**Hela kedjan är nu byggd och mätt.** 238 av de 310 LOU-paragrafer som har
ett genomförandepåstående visar nu äldre EU-praxis de inte hade.

### Vad som gjordes

1. **Hämtningen: inte omhämtning av allt, utan citeringsstyrd påfyllning.**
   Korpusen vet själv vad den saknar: `links`-tabellen pekar på 17 078
   sektor-3-akter utan artefakt, med 882 855 hänvisningar. Ny
   `catalog.dangling_targets` + `lagen eurlex backfill [<sektor>] [--limit N]`
   laddar dem mest-citerade först (de 500 översta bär 76 % av alla hängande
   hänvisningar). Att köra om hela sektor 3 via SPARQL hade hämtat hundratusen
   akter för att nå desamma. Upphandlingslinjen (32004L0018, 32004L0017,
   31992L0050, 31993L0036–0038, 31971L0305, 31977L0062, 31989L0440) är hämtad
   och parsad.
2. **Två riktiga nedladdnings-/parserbuggar hittades på vägen** (båda låsta
   med regressionstest):
   - `download`: en akt publicerad över flera EUT-filer exponerar *ett item
     per del*, och inget item är dokumentet. 32004L0018 låg på disk som sin
     "BILAGA I" (14 kB, noll artiklar); EU:s rättighetsstadga som sin
     innehållsförteckning. Flerdelad Formex hämtas nu som hel manifestation i
     en zip — precis den `.fmx4.zip`-bunt bulkimporten redan producerar.
   - `parse`: en `GENERAL`-rot lägger akten i `CONTENTS` (2004/18) eller ännu
     ett steg ned i `GR.SEQ` (stadgan); `ENACTING.TERMS` ensamt gav noll
     artiklar. 2004/18 har nu sina 84 artiklar, stadgan sina 54.
   - dessutom: `_emit_table` behåller nu *inre* tomma celler, eftersom en
     jämförelsetabells kolumn är det som säger vilken akt värdet hör till.
3. **Korrespondenslagret** `ferenda/eurlex/correspond.py` +
   körs av `eurlex parse` — mekaniskt, inget LLM, och eftersom det är aktens
   egen strukturerade data hamnar resultatet i aktens **artefakt**
   (`correspondence`-nyckeln), inte i ett författat lager. Tabellen
   hittas på sin **rubrikrad**, inte på bilagerubriken (bara ~20 % av dem
   sitter under en rubrik som heter *Jämförelsetabell*; 2014/24:s heter
   `BILAGA XV`). **Orienteringen varierar** — omvänd är normen (424 av 456
   tabeller har den upphävda akten i kolumn 1) — så självkolumnen hittas på
   ordval, med alla åtta formuleringar korpusen använder. **Gamla sidans
   länkar är fel**: citeringsmotorn löser "Artikel 12" i vilken cell som helst
   mot den akt som parsas, så artikelnummer läses ur celltexten.
4. **Sömmen** `catalog.directive_correspondence` + `predecessor_atoms`
   (pinpoint-medveten där tabellen är det, annars artikelnivå);
   `caselaw_anchored` bär `(akt, citerad pinpoint, transponerad artikel,
   hopp)` och praxisrutan sätter ut släktskapshoppet: "(om artikel 15 i
   92/50/EEG, motsvarar artikel 79)".

### De öppna frågorna, besvarade

- **Transitivitetens djup:** `catalog.LINEAGE_DEPTH = 3` (höjt från 2 den
  2026-07-26, efter denna handover): upphandlingskedjan går 2014/24 →
  2004/18 → 92/50 & 93/36–38 → 71/305 & 77/62 — tredje generationen är den
  Dundalk och SIAC Construction citerar, och vandringen följer bara de
  jämförelsetabeller omarbetningarna själva publicerat.
- **Volymen:** reell men hanterbar. Art. 2 (definitionerna) i 2014/24 når 13
  förfäder i två hopp; rutans tak på 5 domar plus "+N till" bär det.
- **Splittrade/sammanslagna artiklar:** tabellen anger dem som flera mål i
  samma cell, och uppräkningsläsaren tar dem alla; "—" ger inget par.

### Vad som *inte* går mekaniskt

- **Kodifierad praxis.** 2014/24 art. 12 (intern upphandling) står som "—" i
  bilaga XV — den hade ingen föregångarartikel, Teckal *var* källan. Teckal
  når alltså inte LOU 3 kap. 12 § via jämförelsetabellen, och kan aldrig göra
  det. Grannparagraferna får däremot äldre praxis (art. 11 → 2004/18 art. 18).
  Beslutat: den relationen hör hemma i ett **handkurerat kommentarslager** —
  `commentary/sfs/2016/1145.md` i lagen-wiki, med frontmatter
  `annotates: 2016:1145` och en `## 3 kap. 12 §`-rubrik som landar på
  `K3P12`-ankaret. Relationen *är* en redaktionell bedömning, inte något
  källorna påstår, så den ska stå där och inte härledas. (Ej skriven ännu.)
- **93/36, 93/37 och 2004/17.** De *har* jämförelsetabeller, men deras enda
  manifestation är HTML från före 2003 där tabellen är en ren platshållare
  (`>Plats för tabell>`) — 42 akter i korpusen är i det läget. Kedjan stannar
  därför vid 2004/18 för LOU, och LUF får ett hopp. Tabellerna finns i PDF:en;
  PDF-tabellextraktion är nästa steg om det behövs.
- 12 akter har hela tabellen kollapsad till ett enda `paragraph`-block utan
  cellgränser (bl.a. 32004L0037, 32026L1194).

### Nästa steg

- Kör om hela eurlex-parsen (`lagen eurlex parse --force`) så
  `_emit_table`-fixen slår igenom; först då är flerkolumnstabellerna
  (2010/75, 2009/138, 2006/112 …) rätt uppradade.
- Lagren byggs numera av `lagen eurlex parse` självt: 386 akter (122 direktiv,
  264 förordningar) har en läsbar tabell, ~27 000 artikelpar, och de kommer med
  när korpusen parsas om. Inget separat kommando kvar.
- **Påfyllningen är körd (2026-07-25).** Hela sektor 3: 17 076 citerade men
  saknade akter, varav **12 120 hämtade** och **4 956 utan svensk eller engelsk
  manifestation** (29 % — akter från tiden före medlemskapet som aldrig
  översatts; de lämnar inget på disk och maskeras inte av en tom notice).
  Korpusen gick från 53 499 till 63 902 basefiles. Kontrollerat efteråt: av de
  22 414 innehållsfiler körningen skrev var **noll** en underhållssida —
  CELLAR svarade "Web Site Under Maintenance" med HTTP 200 vid ett tillfälle,
  men det träffade SPARQL-endpointen, där `lib.net.request` läser JSON och
  därför fångade det och gjorde om anropet.

  Kvar att köra: `lagen eurlex parse` (12 117 nya + 51 785 inaktuella — hela
  beståndet är inaktuellt eftersom parsern ändrats) och sedan `relate`.

  Påfyllningen är en **fixpunktsiteration, inte en engångskörning**: varje
  generation som hämtas och parsas bidrar med sina *egna* utgående
  hänvisningar, så önskelistan växer tillbaka efter varje varv. Den är däremot
  begränsad av "något i korpusen har någon gång citerat detta" — ett direktiv
  från 1971 som ingen dom nämner hamnar aldrig på listan. Kör
  backfill → parse → relate → backfill tills antalet nya mål planar ut.

## Kvarstående småsaker

- **20 kap. LOU (avtalsspärr/överprövning) — diagnosen ovan var fel.**
  Det är inte en katalogfråga: att lägga till 89/665 och 92/13 i en omkörning
  av prop. 2015/16:195 ger nästan ingenting. Samtliga 21 FK-poster för 20 kap.
  inleds "Paragrafen motsvarar 16 kap. N § LOU" och hänvisar vidare
  ("Författningskommentarerna till denna paragraf finns i prop. 2009/10:180");
  bara en av de 21 nämner ett direktiv alls, och då som ett resonemang om vad
  "avtal" betyder i artikel 1, inte som ett genomförandepåstående. 20 kap. är
  inte ny rätt — rättsmedelsdirektiven genomfördes i *gamla* LOU (2007:1091)
  16 kap. genom prop. 2009/10:180, och 2016 års omarbetning flyttade kapitlet
  och numrerade om det.

  Läget i data (kontrollerat 2026-07-24):
  - Steg 1 är **redan gjort**: `.ann`-lagret för prop. 2009/10:180 finns
    (gemma-4-31b-it, samma korpuskörning som de övriga 364) och dess kanter är
    pinnade — 2007:1091 har 32 genomföranderader, bl.a. `K16P17` → 89/665
    art. 2, 4, 5 och `K17P2` → 2007/66 art. 2; LUF-tvillingen 2007:1092 har 5.
  - Steg 2 återstår: det finns inget `.corr`-lager för 2016:1145 alls.
    `lagen sfs ai-correspond 2016:1145 prop/2015-16-195 2007:1091` har ovanligt
    bra indata, eftersom varje FK-post uttryckligen säger vilken gammal
    paragraf den motsvarar.
  - Steg 3 återstår som kod: `caselaw_anchored` läser bara
    `genomforande`-tabellen. SFS-korrespondenslagret matar den *svenska*
    rättsfallsrälsen (`corresponding_cases_margin`), aldrig EU-rälsen — så
    påståendena skulle bli kvar på `2007:1091#K16P17`. Samma sömvidgning som
    direktivsläktskapet, fast i den andra dimensionen (svenska paragrafer
    bakåt i stället för EU-akter bakåt).

  Utbytet är dock magert: bara 16 kap. 17 § (och 17 kap. 2 §) fick en kant, av
  20 kap:s 21 paragrafer, och bakom de faktiskt pinnade artiklarna ligger
  **21 domar**. Skälet är att prop. 2009/10:180 är en *ändringsproposition* —
  den kommenterar bara de paragrafer den ändrade. Resten av kapitlet har sin FK
  i original-LOU:s prop. 2006/07:128, och **den propositionen ger 0 FK-poster
  och 0 `implements`** i den parsade artefakten (2009/10:180 ger 113, 2015/16:195
  ger 1 033). Den verkliga flaskhalsen för 20 kap. är alltså FK-extraktionen för
  2006/07:128, inte korrespondenskedjan. (Notera också att FK-indexet för
  2015/16:195 stannar vid kapitel 14 trots att 20 kap:s 21 poster finns i
  artefakten — kapitelattributionen där är värd en titt.)
- Tre facit från 2025/26-sviten är misstänkta mot dagens bredare katalog
  (262 binder till massflyktsdirektivet där FK avser nya mottagande-
  direktivet; 202 har en habitat-mot-vattendirektiv-fråga; 108 små
  pinpointdiffar) — omadjudikering kräver höjd månadsgräns för
  Claude-subagenter.
- Promptjustering så gemma anger styckepinpointen oftare (idag 54/828 där
  FK:n uttrycker fler), och därefter eventuellt en korpusomkörning för
  styckepinpoints + de fixade kataloketiketterna i äldre lager.
- LLM-svar som underkänns två gånger dumpas nu till
  `site/data/llm-debug/rejected-*.json`; `llm.json_values` räddar svar
  med efterföljande extra JSON/prosa ("Extra data"-fallet).
