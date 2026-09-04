# lagen.nu API — utvecklarguide

Ett läsbart REST/OpenAPI-gränssnitt över hela det parsade rättskällekorpuset
(författningar, svenska rättsfall, Europadomstolens praxis, förarbeten,
myndighetsföreskrifter, JO/JK/ARN-avgöranden, EU-rätt, Europarådets fördrag,
kommentarer och begrepp).

API:t exponerar tre saker:

- **fulltextsökning** (via OpenSearch), ned på paragraf-/artikelnivå,
- **citeringsgrafen** — vilka dokument som hänvisar till ett dokument eller en
  enskild paragraf (lagen.nu:s signaturfunktion), och tvärtom,
- **dokumentens metadata och fullständiga parsade innehåll**.

All data är *härledd och återskapningsbar* ur artefakterna på disk — API:t är
aldrig en sanningskälla, bara en läsvy.

---

## Förutsättningar

| Funktion | Kräver |
|---|---|
| metadata, dokument, citeringsgraf, dumpar | en byggd **katalog** (`lagen all relate`) |
| `/api/v1/search` | dessutom en igång **OpenSearch** + ett byggt index (`lagen all index`) |

De katalogberoende endpointerna fungerar alltså utan OpenSearch. Bara sökningen
behöver klustret.

```sh
uv sync                      # installerar fastapi, uvicorn, opensearch-py m.m.
lagen all relate             # bygger site/data/catalog.sqlite ur artefakterna
lagen all index              # (valfritt) bygger fulltextindexet i OpenSearch
```

Ange var OpenSearch-klustret finns. Antingen i `config.yml`:

```yaml
opensearch_url: http://localhost:9200
```

…eller via miljövariabeln `OPENSEARCH_URL`, som har företräde och är behändig
för tillfälliga byten:

```sh
export OPENSEARCH_URL=http://localhost:9200
```

Är ingetdera satt används `http://localhost:9200`. Du behöver ett eget
OpenSearch-kluster. Det enklaste är projektets `docker-compose.yml` — en
single-node-OpenSearch 2.x med säkerheten avstängd, på just
`http://localhost:9200`:

```sh
docker compose up -d
```

På WSL2: startar den inte, höj `vm.max_map_count`
(`sudo sysctl -w vm.max_map_count=262144`).

---

## Starta servern

En enda process serverar både den statiska webbplatsen och API:t (samma origin):

```sh
lagen all serve              # webbplats + API på http://127.0.0.1:8000/
lagen all serve --port 9000  # annan port
```

API:t svarar under `/api/v1/*`; allt annat är de genererade sidorna. Eftersom
sidorna och API:t delar origin anropar ⌘K-sökningen API:t med relativa URL:er –
ingen separat API-server, ingen konfigurerbar API-bas som kan bli inaktuell.

Interaktiv dokumentation genereras automatiskt:

- **Swagger UI:** <http://127.0.0.1:8000/docs>
- **OpenAPI-schema (JSON):** <http://127.0.0.1:8000/openapi.json>

Allt nedan är `GET`. Svaren är JSON. API:t är skrivskyddat och CORS-öppet (det
är publik, läsbar data), så det kan anropas direkt från en webbläsare på en
annan origin.

---

## De två API:erna

Processen svarar för två olika publiker, och de ligger i **var sitt
sökvägsutrymme och var sitt schema** (`api/internal.py`):

| | publikt | internt |
|---|---|---|
| bas | `/api/v1` | `/internal-api/v1` |
| schema | `/openapi.json`, `/docs` | `/internal-api/openapi.json`, `/internal-api/docs` |
| vem | vem som helst, varifrån som helst | webbplatsens egen JS och redaktörsverktygen |
| metoder | bara `GET` | `GET` och `POST` |
| origin | alla (CORS `*`) | bara samma origin |
| stabilitet | ett löfte — resten av den här filen | ändras när gränssnittet ändras |

Det interna API:t bär inloggningen (`/auth/*`), de tre redigerarna
(`/edit/*`, `/patch/*`, `/graphics/*`) och PDF-exportens bakgrundsjobb
(`/pdf/*`). Ops-panelen `/ops` behåller sin egen sökväg men har samma två
grindar: utanför det publika schemat och bara samma origin.

**Samma origin gäller allt internt, inte bara skrivningarna.** Grinden
(`auth.same_origin`) avvisar en begäran vars `Sec-Fetch-Site` säger att en
annan sida gjorde den, eller vars `Origin` inte är vår egen — med `403`. En
anropare utan de huvudena (curl, testklienten, den interna klient som
`generate` kör) släpps igenom; grinden stoppar webbläsare på främmande
sidor, den är ingen autentisering. Det gör `require_editor` fortfarande.
CORS räcker inte: det hindrar bara en främmande sida från att *läsa* svaret
på en `GET`, och halva den interna ytan är `GET`.

Resten av den här filen handlar om det publika API:t.

---

## Om dokument-URI:er

Ett dokument identifieras av sin publika lagen.nu-URI, t.ex.
`https://lagen.nu/1962:700` (brottsbalken) eller
`https://lagen.nu/1962:700#K3P1` (3 kap. 1 §). Dessa URI:er är *oförändrade*
från den gamla pipelinen och fungerar som nyckel överallt — i API:t, i
dump-filerna och som `_id` i OpenSearch.

Eftersom en URI innehåller `:` och `/` skickas den alltid som **query-parameter
`uri`**, aldrig som en del av sökvägen. Med `curl`, URL-koda den:

```sh
curl -G http://127.0.0.1:8001/api/v1/document \
     --data-urlencode "uri=https://lagen.nu/1962:700"
```

---

## Endpoints

### `GET /api/v1/search` — fulltextsökning

| Parameter | Typ | Förklaring |
|---|---|---|
| `q` | sträng (obligatorisk) | sökfrågan |
| `source` | sträng | begränsa till en källa: `sfs`, `dv`, `hudoc`, `forarbete`, `foreskrift`, `eurlex`, `coe`, `avg`, `kommentar`, `begrepp` |
| `kind` | sträng | begränsa till en dokumenttyp (`law`, `case`, `prop`, `directive`, …) |
| `year` | fyrsiffrigt år | begränsa till dokumentets publicerings-/avgörandeår |
| `limit` | heltal 1–100 (standard 10) | antal träffar |
| `offset` | heltal (standard 0) | paginering, begränsad till 9900 |
| `cursor` | sträng | ogenomskinlig kursor från föregående svars `next_cursor`, för djup paginering bortom `offset`-taket (ömsesidigt uteslutande med `offset`) |
| `sort` | sträng | `relevance` (standard) eller `citations` — det senare ordnar träffarna efter deras eget `inbound_count` i stället för relevans; en `cursor` är bunden till den ordning som skapade den |

Träffarna är hela dokument, rankade på relevans kombinerat med antalet
inkommande citeringar (`inbound_count`) — så en välträffad, ofta hänvisad lag
slår en lika välträffad men obskyr. En träff pekar alltid ut sitt **dokument**:
`highlight` är dokumentets eget utdrag och `url` dess adress.

Två fält kan peka djupare, och de betyder olika saker:

| Fält | Betydelse | Länken |
|---|---|---|
| `pin` | den bestämmelse som en hänvisningsformad fråga löste ut ("avtalslagen 36 §") — själva svaret | `url` + `#` + `pin.pinpoint` |
| `fragments` | de ställen i dokumentet där sökorden står, högst tre, dubbletter borttagna | dokumentet; styckena visas *under* träffen |

Bara `pin` flyttar träffens länk. Ett `fragments`-stycke är stödinformation:
ordet kan stå i ett dokument av skäl som inte är det läsaren frågade efter —
"dataförordningen" står i artikel 47 i EU:s dataförordning därför att den
artikeln ändrar en annan förordning genom att citera titeln. Sökfrågan matchar
även ordprefix (`upphovsr` hittar `upphovsrätt`), och svaret bär `facets`
(räknade `source`/`kind`/`year`-hinkar över hela träffmängden, inte bara den
returnerade sidan) som driver facettfältet på webbplatsens fullständiga
sökresultatsida (`/sok`, `render.render_search_page` + `fullsearch.js`).
Svaret bär även `next_cursor` — icke-null så länge fler sidor finns — att
skicka som nästa anrops `cursor`.

```sh
curl -G http://127.0.0.1:8001/api/v1/search \
     --data-urlencode "q=uppsåt mord" --data-urlencode "source=sfs"
```

```json
{
  "query": "uppsåt mord",
  "total": 1,
  "results": [
    {
      "uri": "https://lagen.nu/1962:700",
      "url": "/1962:700",
      "identifier": "SFS 1962:700",
      "title": "Brottsbalk (1962:700)",
      "source": "sfs",
      "kind": "law",
      "score": 9.1,
      "inbound_count": 5153,
      "highlight": ["… den som <em>uppsåtligen</em> …"],
      "pin": null,
      "fragments": [
        {
          "uri": "https://lagen.nu/1962:700#K3P1",
          "pinpoint": "K3P1",
          "label": "3 kap. 1 §",
          "highlight": ["Den som <em>uppsåt</em>ligen berövar annan livet …"]
        }
      ]
    }
  ]
}
```

`url` är dokumentets publika sökväg (`layout.page_url`); lägg på
`#<pinpoint>` för att djuplänka direkt till paragrafen. `label` namnger stället:
pinpointen skriven som en läsare citerar den ("6 kap. 3 §", "artikel 47"), följd
av den rubrik dokumentet självt sätter över den när det finns en ("artikel 32 -
Säkerhet i samband med behandlingen") — eller enbart den rubriken, både för
ankare utan hänvisningsgrammatik (ett förarbetes `sec745` → "3.6 Sökbegrepp och
direktåtkomst") och när rubriken redan inleds med sin egen beteckning (en
fördragsartikels "Article 6 - Right to a fair trial"). `null` när ankaret har
ingendera (en punkt hos EDPB).

En träff bär också `abbr`: den förkortning dokumentets rubrik eller citernamn
har ("GDPR", "EKMR"), annars `null`. Det är namnraden för en träffrad som lägger
sin andra rad på `pin` — visa `display` när `abbr` är `null`.

> Returnerar `/api/v1/search` ett fel om OpenSearch inte är igång eller indexet
> inte är byggt. Kör `lagen all index` och kontrollera `OPENSEARCH_URL`.

### `GET /api/v1/resolve` — slå upp en hänvisning, utan fulltextsökning

Samma resolver som `/api/v1/search` lägger först som `pin` — här ensam, utan
den fulltextsökning som en hänvisningsformad fråga annars också triggar (och
som svarar med många lösa träffar när frågan tolkas ord för ord, t.ex.
`C-199/24` mot `24` och `199` var för sig). Använd detta anropet när bara den
utlösta träffen behövs.

| Parameter | Typ | Förklaring |
|---|---|---|
| `q` | sträng (obligatorisk) | hänvisningen: ett lagnamn/förkortning + paragraf ("avtalslagen 36 §", "BrB 12:1"), en EU-akt + artikel/skäl ("GDPR artikel 32"), ett EU-domstolsmål ("C-199/24"), en fördragsartikel ("EKMR 6") eller ett vedertaget rättsfallsnamn ("Instagrambilden") |
| `source` | sträng | begränsa till en källa |
| `kind` | sträng | begränsa till en dokumenttyp inom källan |

Svaret har samma träffform som `/api/v1/search` (`query` + `results`). Kräver
**inte** OpenSearch, bara ett byggt corpus-index (`lagen all relate`).

```sh
curl -G http://127.0.0.1:8001/api/v1/resolve --data-urlencode "q=C-199/24"
```

```json
{
  "query": "C-199/24",
  "results": [
    {
      "uri": "https://lagen.nu/celex/62024CJ0199",
      "url": "/celex/62024CJ0199",
      "identifier": "62024CJ0199",
      "title": "C-199/24",
      "source": "eurlex",
      "kind": "judgment",
      "score": null,
      "inbound_count": 10,
      "highlight": [],
      "pin": null,
      "fragments": []
    }
  ]
}
```

### `GET /api/v1/documents` — lista dokument-id:n (corpus-index)

Räknar upp dokument filtrerade på källa/typ — **inte** fulltextsökning (det är
`/search`, som kräver `q`). Det här är indexet du använder för att hitta vilka
URI:er som finns, och sedan slå upp var och en med `/document`. Returnerar
id + lättviktig metadata, **inte** det fullständiga innehållet.

| Parameter | Typ | Förklaring |
|---|---|---|
| `source` | sträng | filtrera på källa (`sfs`, `dv`, `hudoc`, `forarbete`, `foreskrift`, `eurlex`, `coe`, `avg`, `kommentar`, `begrepp`) |
| `kind` | sträng | filtrera på dokumenttyp (`law`, `case`, `prop`, `directive`, …) |
| `limit` | heltal 1–1000 (standard 100) | sidstorlek |
| `offset` | heltal (standard 0) | paginering |
| `include_expired` | boolean (standard `false`) | ta med upphävda dokument |

`total` är antalet matchande dokument *före* paginering, så du kan stega igenom
hela mängden. Sorteringen är på URI (stabil).

Upphävda dokument utelämnas — en upphävd författning, en EU-rättsakt som inte
längre gäller, ett återkallat ställningstagande. Listan visar alltså gällande
rätt, på samma sätt som browse-sidorna och `/search`. Ett dokument vars
upphävande ännu inte trätt i kraft räknas som gällande och listas. Dokumentet
finns kvar: `/document` hämtar det på sin URI och `/document/inbound`
respektive `/graph` når det genom hänvisningsgrafen. `include_expired=true`
tar med dem i listan.

```sh
curl -G http://127.0.0.1:8001/api/v1/documents \
     --data-urlencode "source=sfs" --data-urlencode "limit=2"
```

```json
{
  "total": 11184,
  "limit": 2,
  "offset": 0,
  "documents": [
    {
      "uri": "https://lagen.nu/1772:1104",
      "source": "sfs",
      "kind": "law",
      "label": "SFS 1772:1104",
      "title": "Kungörelse (1772:1104) angående …",
      "source_url": null,
      "updated": "2026-06-19T08:44:55+00:00"
    }
  ]
}
```

- `updated` är artefaktens senaste byggtid (filens mtime) — alltid satt.
- `source_url` är utgivarens sida ("Källa") *där den finns* i artefakten;
  fältet indexeras i katalogen vid `relate` (precis som `title`), så det fylls
  i för dokument vars artefakt bär en `source_url`. Vill du garanterat ha den
  färska källan för ett enskilt dokument, läs `/document` — den hämtas live ur
  artefakten där.

### `GET /api/v1/document` — ett dokuments metadata + innehåll

```sh
curl -G http://127.0.0.1:8001/api/v1/document \
     --data-urlencode "uri=https://lagen.nu/1962:700"
```

```json
{
  "uri": "https://lagen.nu/1962:700",
  "source": "sfs",
  "kind": "law",
  "label": "SFS 1962:700",
  "title": "Brottsbalk (1962:700)",
  "source_url": "https://beta.rkrattsbaser.gov.se/sfs/item?bet=1962%3A700&tab=forfattningstext",
  "inbound_count": 5153,
  "artifact": { "uri": "https://lagen.nu/1962:700", "structure": [ … ] }
}
```

- `inbound_count` är antalet citeringar till dokumentet *som helhet* (till någon
  av dess paragrafer eller dess egen URI), exklusive självcitering.
- `source_url` är den auktoritativa källan hos utgivaren ("Källa").
- `artifact` är hela den parsade artefakten: `structure`/`body` med inbäddade
  citeringar (löpande text som en lista av textsträngar och länkobjekt
  `{uri, predicate, text}`).

Okänd URI ger `404`. Saknas katalogen helt ges `503` (kör `lagen all relate`).

### `GET /api/v1/document/inbound` — vilka som hänvisar hit

Signaturfunktionen som data: alla *andra* dokument som citerar den angivna
URI:n — en post per (citerande dokument, citerande ställe, citerad bestämmelse).
Självcitering exkluderas.

`scope=tree` (standard) svarar för URI:n **och allt som ligger i den**: på en lag
alltså varje citering av varje paragraf, vilket är vad som krävs för att spegla
lagen.nu:s egna sidor (brottsbalken, mätt 2026-08-07: 40 696 gånger som balk och
162 909 gånger om man räknar dess 2 844 citerade bestämmelser). `scope=exact` ger den
snäva frågan — bara de rader som namnger URI:n själv. Ange en fragment-URI för
att fråga på paragrafnivå; `tree` täcker då paragrafens stycken och punkter.

`source` filtrerar på vem som citerar — `source=dv` ger bara rättsfallen. De två
filtren är oberoende axlar: `scope` avgränsar vad frågan gäller, `source` vem
som räknas. `total` räknas efter båda filtren (det är vad `offset` bläddrar i);
`by_source` räknas för hela scopet *före* `source`-filtret, så svaret ändå
visar vad de andra källorna håller.

Ordningen är densamma som i sidans kontextspalt — rättsfall först för en lag,
sedan myndighetsavgöranden, sedan lagrumshänvisningar — så att första sidan är
representativ i stället för att styras av vilket källnamn som råkar sortera
först. Den ordningen är total och oberoende av bygget, så `offset` är stabilt
mellan ombyggnader (`sort=citations` ändrar det — se nedan). `limit` är 10 000 rader (och taket); `total` och `by_source` avser
hela svaret, inte den returnerade sidan.

**Vilka av dem väger tyngst — `inbound_count` och `sort=citations`.** Varje rad
bär det *citerande* dokumentets egen citeringssiffra: samma tal och samma namn
som `/search` och `/document` svarar med, så svaret går att rangordna utan ett
anrop per rad. `sort=citations` ordnar hela scopet efter den, störst först;
`sort=rail` (förvalt) behåller ordningen ovan.

`sort=citations` är den enda ordningen där `offset` **inte** är stabil mellan
ombyggnader: siffran räknas om vid varje bygge, så en rad kan flytta sig mellan
sidor när samlingen växer. Lika tal faller tillbaka på kontextspaltens ordning,
som är stabil. Ta första sidan, eller bläddra klart i ett svep.

Det är frågan "vilka är de viktigaste rättsfallen om den här bestämmelsen", och
den vill ha `source=dv` med sig:

```sh
curl -G http://127.0.0.1:8001/api/v1/document/inbound \
     --data-urlencode "uri=https://lagen.nu/1915:218#P36" \
     -d source=dv -d sort=citations -d limit=5
```

```
  32  Den kollektiva hemförsäkringen (NJA 1987 s. 394)
  29  NJA 1992 s. 66
  27  AD 1998 nr 80
  26  AD 1994 nr 122
  23  AD 1998 nr 97
```

Två saker att veta först. Siffran mäter hur ofta något citeras, vilket
samvarierar med auktoritet men inte är samma sak: den gynnar ett gammalt
avgörande framför ett färskt, och den räknar bara det som finns i samlingen.
Och raderna är oreducerade (se nedan), så ett flitigt citerande dokument
återkommer: med `sort=citations` över avtalslagen 36 § *utan* källfilter rymmer
en sida på 50 rader 14 skilda dokument, eftersom en proposition citerar samma
paragraf från många ställen. Med `source=dv` rymmer den 48, och med förvalt
`sort=rail` rymmer den 46 oavsett. Slå ihop på `uri` om det är dokument
och inte citeringar som ska rangordnas.

`sort=citations` räknar hela scopet före sidindelningen, inte bara sidan — 893
citerande källor och 13 ms för avtalslagen 36 §, 11 693 och 578 ms för hela
brottsbalken. Förvalet räknar bara sidan, alltså som mest `limit` källor, vilket
kostar 8 ms på en sida med 10 000 rader ur brottsbalken. Bådadera är en liten
del av anropet: endpointen läser hela dokumentets citeringsfil innan den
sidindelar, vilket är 260 ms för brottsbalken och 1,85 s för EKMR. Frågan går på
ett täckande index, alltså indexläsningar och inte tabelläsningar. (Mätt
2026-08-21 på en varm utvecklingsdisk; produktionsvärden är inte mätta.)

Mängden är **oreducerad**: sidan slår ihop ett dokuments upprepade citeringar
till en rad och döljer heldokumentscitat som ersätts av en pinpoint — båda är
presentation. Filtrera på `predicate` för de typade relationerna
(`rpubl:bemyndigande`, `rpubl:andrar`, `rpubl:upphaver`) och på `source` för
lagen.nu:s egen kommentar. Citatets ordalydelse ingår inte — den tillhör det
citerande dokumentet, och `/document/outbound` på den URI:n har den.

```sh
curl -G http://127.0.0.1:8001/api/v1/document/inbound \
     --data-urlencode "uri=https://lagen.nu/1975:635#P6"
```

```json
{
  "uri": "https://lagen.nu/1975:635#P6",
  "scope": "tree",
  "sort": "rail",
  "total": 3924,
  "limit": 10000,
  "offset": 0,
  "by_source": {"dv": 2767, "forarbete": 874, "avg": 164, "sfs": 116,
                "foreskrift": 2, "begrepp": 1},
  "citations": [
    {
      "uri": "https://lagen.nu/dom/mmd/F8748-25/2026-07-15",
      "target": "https://lagen.nu/1975:635#P6",
      "anchor": null,
      "page": 1,
      "predicate": "dcterms:references",
      "label": "F 8748-25",
      "title": "F 8748-25",
      "source": "dv",
      "kind": "case",
      "date": "2026-07-15",
      "inbound_count": 0
    }
  ]
}
```

Svaret läses ur en per-dokument-fil som bygget skriver, inte ur en direktfråga
mot katalogen: på produktionsdisken tar heldokumentsfrågan minuter av spridda
läsningar.

### `GET /api/v1/document/outbound` — vad ett dokument hänvisar till

Spegelvänt: alla citeringar dokumentet *gör*. Mål som ännu inte finns i
corpuset kommer tillbaka med `hosted: false` och utan `label`/`title`.

```sh
curl -G http://127.0.0.1:8001/api/v1/document/outbound \
     --data-urlencode "uri=https://lagen.nu/2018:585"
```

```json
[
  {
    "uri": "https://lagen.nu/1962:700#K3P1",
    "anchor": "P1",
    "predicate": "dcterms:references",
    "text": "3 kap. 1 § brottsbalken",
    "label": "SFS 1962:700",
    "title": "Brottsbalk (1962:700)",
    "source": "sfs",
    "hosted": true
  }
]
```

### `GET /api/v1/graph` — ett dokuments grannskap i grafen, ritfärdigt

Samma fakta som `inbound`/`outbound`, men aggregerat per granndokument (en
rad per granne med länkantal) och grupperat med samma flödesgrupper som
statistiksidans flödesdiagram (`lib/facets.flow_group`). Det är vad
paraGRAF-utforskaren (https://para-graf.tomtebo.org) ritar.
`direction=in|out|both` väljer sida, `groups=` filtrerar på flödesgrupp,
`limit` sätter topplistans längd. `sort=citations` rangordnar grannarna
efter hur citerade de själva är (radens `inbound_count`) i stället för
antalet länkar till mittpunkten, och `grouplimit` sätter ett tak per
flödesgrupp — bredd i stället för en dominerande källtyp. `depth` (1–3)
svarar med en djupare omgivning i ett enda anrop: `limit` blir en budget
över hela vyn (60/40 på djup 2, 50/30/20 på 3), de yttre ringarna kommer i
`expansion.nodes` och `expansion.edges` listar varje hänvisning mellan de
returnerade dokumenten. Djup > 1 svarar 503 medan grafen laddas.

`source_url` är dokumentets sida hos utgivaren -- för en källa som
sajten inte renderar (tidskriftsartiklar) är det länken att öppna.

En fragment-URI (`…#K4P7`, `…#A6`) svarar för den enheten ensam och lägger
till `internal`: hela dokumentets interna paragrafgraf på enhetsnivå
(§/artikel), med läsbara etiketter ("4 kap. 7 §"). `internal=true` lägger
till samma graf även i svaret för en dokument-URI.

### `GET /api/v1/card` — ett dokuments identitetskort

Enradssvaret för det enda objekt läsaren valt eller hovrar: citerande namn
(`citation`), `short_id`, `title`, sidans adress (`url`, samt `source_url`
för källor sajten inte renderar), `inbound_count` och `snippet` --
texten på platsen själv. För ett dokument är det dess inledande text (ett
rättsfalls sammanfattning, en författnings 1 § med beteckning, en
EU-rättsakts första skäl; null tills relate stämplat dokumentet). För en
**fragment-URI** är det bestämmelsens egen text under sitt lagrum ("1 kap.
5 § Konungen eller drottning som enligt successionsordningen ..."), vilket
kostar en artefaktläsning. `uri` tar båda formerna sajten skriver: URI:n
(`https://lagen.nu/1962:700#K3P1`) eller sidadressen för samma plats
(`/1962:700#K3P1`) -- det en webbläsare har i en href och inte kan bilda
URI:n av (en EU-rättsakt serveras på `/celex/<id>` och identifieras som
`celex/<id>`). Sajtens länk-popovers använder anropet för varje mål
utanför sidan läsaren är på, i stället för att hämta den sidan. Grafsvaret
bär avsiktligt inte dessa fält: av 300 grannar väljs en, och detta är
anropet för den.

### `GET /api/v1/path` — kortaste kedjan av hänvisningar mellan två dokument

"Six degrees"-vandringen: en kortaste kedja av hänvisningar som binder ihop
`from` och `to`, på dokumentnivå. `direction=out|in|both` säger vilka länkar
ett steg får följa — med `both` kan ett steg gå åt båda hållen, och varje
stegs `forward` säger åt vilket håll det gick; `links` är antalet hänvisningar
som bär steget. `groups=` filtrerar *mellanliggande* dokument på flödesgrupp
(ändpunkterna är alltid tillåtna). `distance` är null när ingen kedja finns.
`paths=N` (1--5) ber om fler vägar: den kortaste är fortfarande `path`, de
övriga kommer som `alternatives` (`{distance, path}`), näst kortast först.
Färre kommer tillbaka när grafen inte håller fler slingfria kedjor.

```sh
curl -G http://127.0.0.1:8001/api/v1/graph \
     --data-urlencode "uri=https://lagen.nu/coe/005#A6" \
     --data-urlencode "groups=Rättsfall,Förarbeten"
```

```json
{
  "uri": "https://lagen.nu/coe/005#A6",
  "root": "https://lagen.nu/coe/005",
  "anchor": "A6",
  "unit": "A6",
  "pinpoint": "artikel 6",
  "label": "ETS No. 005",
  "title": "Convention for the Protection of Human Rights and Fundamental Freedoms",
  "source": "coe",
  "kind": "treaty",
  "group": "Konventioner",
  "inbound": {
    "total_links": 4350,
    "total_docs": 1634,
    "unresolved": 0,
    "top": [
      {
        "uri": "https://lagen.nu/lr/2002/administrativa-avgifter-pa-skatte-och-tullomradet-fi2002-112",
        "label": "Administrativa avgifter på skatte- och tullområdet, Fi2002/1122",
        "title": "Administrativa avgifter på skatte- och tullområdet, Fi2002/1122",
        "descriptive": "Administrativa avgifter på skatte- och tullområdet, Fi2002/1122",
        "source": "forarbete",
        "kind": "lr",
        "group": "Förarbeten",
        "n": 34
      }
    ]
  },
  "outbound": {"total_links": 0, "total_docs": 0, "unresolved": 0, "top": []},
  "internal": {
    "nodes": [{"anchor": "A6", "label": "artikel 6", "n": 0}],
    "edges": [],
    "truncated": 0
  }
}
```

### `GET /api/v1/pdf` — dokumentet som PDF

En genererad sida omrenderad för papper: A4, löpande sidhuvud, sidnummer i
det yttre hörnet och PDF-bokmärken — samma `style.css`-
utskriftsblock som webbläsarens egen Skriv ut, plus det sidbrytningslager
(`@page`, `string-set`, `target-counter()`) webbläsare inte implementerar.
Renderas av WeasyPrint (`api/pdf.py`); understilar och bilder hämtas i
processen, aldrig över nätet. Resultatet cachas på disk
(`cache/pdfexport/`, LRU, 2 GiB tak) med sidans *innehåll* och stilmallens
text i nyckeln — en stor balk tar över en minut första gången, sedan
millisekunder. En sida som ändrats (ny lydelse, ändrad patchfil, ny
stilmall) träffar aldrig en gammal post; en kopiering av identiska bytes
behåller cacheträffen.

| Parameter | Typ | Förklaring |
|---|---|---|
| `path` | sträng (obligatorisk) | sidans publika sökväg, t.ex. `/1998:204` eller `/prop/2020/21:22` |
| `toc` | bool (standard `false`) | lägg till sidans egen innehållsförteckning med utlästa sidnummer |
| `kontext` | sträng | kommaseparerad lista kontextslag att skriva ut under varje paragraf/artikel (rälsens sektionsnamn, t.ex. `kommentar,dv,forarbete`), eller `alla`; standard: ingen kontext |
| `andringar` | bool (standard `true`) | ta med SFS-registret Ändringar och övergångsbestämmelser, utan skärmens fillänkar |
| `kolumner` | heltal `1` eller `2` | använd normal spegelvänd layout eller kompakt tvåspaltslayout; två spalter tar inte med kontext |
| `download` | bool (standard `false`) | servera som bifogad fil (nedladdning) i stället för inline (visning) |

```sh
curl -G http://127.0.0.1:8001/api/v1/pdf \
     --data-urlencode "path=/1998:204" --data-urlencode "toc=true" \
     --data-urlencode "kontext=forarbete,dv" -o forvaltningslagen.pdf
```

### `/samling` — flera dokument i en PDF

`/samling` bygger en författningssamling utan konto eller serverlagrad lista.
Webbläsaren sparar utkastet i `localStorage`. Bokmärkeslänken bär ett
kompakt, versionsmärkt representation efter `#`. Fragmentet skickas inte till
servern. Samlingen kan exporteras och importeras som JSON när länken blir
opraktiskt lång.

Redigeraren kan ordna högst 1 000 dokument. Varje dokument kan starta direkt,
på nästa sida eller på nästa högersida. Den kan också utesluta SFS-
ändringar, en EU-preambel eller delar av ett dokument. Flera separata avsnitt
kan väljas ur samma proposition. Globala val styr en eller två spalter och
vilka kontextslag som ska finnas med.

Ett valfritt omslag bär titel, undertitel och genereringsdatum. Omslagets
baksida är tom. Den tryckta innehållsförteckningen listar endast dokument.
PDF-panelen kan dessutom visa dokumentens interna rubriker.

Servern tar emot hela manifestet först när webbläsaren inspekterar eller skapar
samlingen:

- `POST /internal-api/v1/pdf/samling/inspektera` läser titlar, tillgängliga
  val och avsnitt ur de aktuella genererade sidorna.
- `POST /internal-api/v1/pdf/samling/jobb` startar eller ansluter till en
  cachelagd bakgrundsrendering.
- `GET /internal-api/v1/pdf/jobb/{id}/resultat` hämtar den färdiga filen.

De tre ligger under `/internal-api/v1` — bara webbläsarens egen
samlingsredigerare anropar dem. Se "De två API:erna" ovan.

Alla dokument sätts i en WeasyPrint-körning. Därför kan `direct` använda
plats som finns kvar på den aktuella sidan. Globala sidnummer, TOC-mål och
spegelvända marginaler blir också exakta. Ett kapacitetsprov skapade 5 002
fysiska sidor. Jobbkön tillåter högst åtta unika pågående jobb. Identiska
begäranden delar jobb och cacheträffar tar ingen köplats.

### `GET /api/v1/sources` — källor och antal

```sh
curl http://127.0.0.1:8001/api/v1/sources
```

```json
[
  {"source": "avg", "documents": 8607},
  {"source": "begrepp", "documents": 30393},
  {"source": "coe", "documents": 233},
  {"source": "dv", "documents": 23734},
  {"source": "edpb", "documents": 60},
  {"source": "eurlex", "documents": 64035},
  {"source": "forarbete", "documents": 97215},
  {"source": "foreskrift", "documents": 11252},
  {"source": "hudoc", "documents": 46045},
  {"source": "icc", "documents": 269},
  {"source": "icj", "documents": 255},
  {"source": "icrc", "documents": 111},
  {"source": "kommentar", "documents": 316},
  {"source": "rs", "documents": 2868},
  {"source": "sfs", "documents": 11214},
  {"source": "untc", "documents": 14}
]
```

### `GET /api/v1/dumps` — tillgängliga bulkdumpar

Listar NDJSON-dumparna (se nedan).

```json
[
  {"source": "sfs", "file": "sfs.ndjson.gz", "bytes": 48213344}
]
```

---

## MCP-server (`/mcp`)

Samma läsvy, men som en **MCP-server** (Model Context Protocol) i stället för
REST — så att vilken MCP-kapabel AI-värd som helst (Claude, ChatGPT, …) kan
grunda svar om svensk (och EU-) rätt i det levande corpuset och citera exakt
paragraf/artikel. Servern är **publik och utan inloggning**, precis som REST-API:t
och sidorna — det är offentlig, läsbar data.

Den ligger i *samma* process som allt annat (`lagen all serve`) och nås över
**Streamable HTTP** på:

```
https://ferenda.lagen.nu/mcp
```

Lägg till den URL:en som en anpassad ("custom"/"remote") MCP-server i din
AI-värd. Ingen nyckel, ingen OAuth. Lokalt under utveckling:
`http://127.0.0.1:8000/mcp`.

### Protokollrevision

Endpointen talar **2026-07-28** — revisionen som tog bort protokollets
sessionsbegrepp: ingen `initialize`-handskakning, inget `Mcp-Session-Id`. Varje
anrop är en fristående POST som bär klientens protokollversion och förmågor i
`params._meta`, och värden hämtar serverns förmågor med `server/discover` när
den behöver dem. I praktiken: vilket anrop som helst kan landa på vilken
process som helst, så `/mcp` kan skalas bakom en vanlig round-robin utan
sticky routing eller delat sessionslager.

Samma endpoint svarar fortfarande **2025-11-25 och äldre** — de klienterna
handskakas som förut och förhandlar ned. Värdar uppgraderar i sin egen takt, så
båda vägarna testas mot en och samma uppkopplade server i `test/test_mcp.py`.

Två detaljer att känna till om en proxy hamnar framför appen: 2026-07-28 kräver
att varje POST bär `Mcp-Method` (och `Mcp-Name` för `tools/call`) så att
lastbalanserare kan routa utan att läsa bodyn — SDK:n avvisar med
`-32020` om de saknas eller inte matchar bodyn. Vår nginx-vhost proxar rakt
igenom, så det behövs ingen konfiguration; men en proxy som *strippar* okända
headers tystar hela endpointen. Vidare annonseras `tools/list` och
`server/discover` som cachebara i en timme och delbara mellan användare
(`CACHE_HINTS` i `api/mcp.py`) — verktygstabellen ändras bara vid deploy, även
om corpuset växer varje natt.

### Verktyg (tools)

| Verktyg | Vad |
|---|---|
| `search` | fulltextsökning över hela corpuset, ned på paragraf-/artikelnivå; en citeringsformad fråga ("avtalslagen 36", "GDPR art 32", "Instagrambilden") fäster det exakta målet överst. Degraderar till enbart citeringsträff om OpenSearch är nere |
| `resolve_citation` | slår upp en citering skriven med namn/förkortning → exakt dokument-URI (+ fragment); kräver *inte* OpenSearch |
| `get_document` | ett dokuments metadata + fullständiga text (hela, eller en enskild `pinpoint` som `K3P1`); `format` väljer markdown (förvalt, via `lib/mdtext.py` — samma transform som REST:ens `/document?format=md`) eller rå artefakt-JSON |
| `fetch` | samma text, men hämtad på ett `id` från `search` (`…/1962:700#K3P1`) i stället för URI + pinpoint var för sig — se *Sök/hämta-kontraktet* nedan |
| `list_documents` | räknar upp dokument (id + lättviktig metadata) filtrerade på källa/typ — corpus-indexet, inte fulltextsökning |
| `get_incoming_citations` | vilka dokument som citerar denna URI/paragraf **och allt som ligger i den** (citeringsgrafen inåt — lagen.nu:s signaturfunktion); svarar med `total` + `by_source` för hela mängden och en sida rader, filtrerbart på `source` (vem som citerar) och `scope` (`tree`/`exact` — vad frågan gäller). Varje rad bär det citerande dokumentets eget `inbound_count`, och `sort` väljer ordning: `rail` (förvalt, sidans egen — rättsfall först) eller `citations` (mest citerade källan först — "vilka är de viktigaste rättsfallen om den här bestämmelsen") |
| `get_outgoing_citations` | alla citeringar ett dokument gör (grafen utåt) |
| `list_sources` | corpusets källor och antal — orientering för `source`-filtret |

Verktygen svarar genom `api/reads.py` — samma funktioner som REST-endpointerna
anropar — så en corpus-fakta når MCP och REST genom en kodväg, med samma filter
på båda sidor. Precis som REST behöver bara `search` ett igång OpenSearch; de
katalogberoende verktygen svarar utan klustret. Ett nere kluster är ett synligt
fel på båda sidor (REST: 503, MCP: tool-fel) — aldrig ett tyst mindre svar.

### Sök/hämta-kontraktet

OpenAI:s värdar förväntar sig att en kunskapsserver exponerar just `search` och
`fetch` med en bestämd resultatform: `search` → `{results: [{id, title, url}]}`
och `fetch` → `{id, title, text, url, metadata}`, båda som `structuredContent`
utöver JSON-dubbletten i `content`. Vi *uppfyller* det kontraktet men har inte
antagit det som modell — dess fält är en delmängd av vad corpuset ändå svarar
med, så anpassningen bestod i att namnge fälten, inte i att smalna av något:

- `search` fick nyckeln `id` per träff. Den pekar på **den mest precisa**
  träffen: löste frågan ut en bestämmelse (`pin`) id:as träffen med den
  (`https://lagen.nu/1962:700#K3P1`), inte med hela balken, så en `fetch` läser
  paragrafen och inte 300 sidor. En ren fulltextträff id:as med dokumentet —
  `fragments` säger var orden står, inte att stycket är svaret. Övriga fält
  (`fragments`, `inbound_count`, `source`/`kind`) ligger kvar orörda för alla
  andra värdar.
- `fetch` är ett nytt tunt omslag kring `get_document`. Allt kontraktet saknar
  fält för — källa, typ, myndighetens egen sida, citeringsantal — rider i
  `metadata`.
- Citeringsgrafen (`get_incoming_citations`/`get_outgoing_citations`) och
  `resolve_citation` har ingen motsvarighet i kontraktet och är oförändrade.
  Det är de som är poängen med servern; kontraktet är en projektion av
  läsvyn, inte modellen.

De två verktygen deklarerar `TypedDict`-returer, vilket är vad SDK:n behöver för
att alls sända `structuredContent` — en naken `-> dict` ger varken output-schema
eller strukturerad payload. `structuredContent` och JSON-dubbletten i `content`
ska bära samma nycklar och värden (ordningen skiljer; ett test låser det).

Servern är monterad i `api/app.py` via `api/mcp.py` (`mcp.mount(app)` +
`lifespan`). Eftersom nginx redan proxar *allt* till appen (se
`docker/nginx/ferenda.lagen.nu.conf`) publiceras `/mcp` automatiskt — ingen extra
container, ingen extra port.

MCP SDK:ns DNS-rebinding-skydd är explicit avstängt
(`enable_dns_rebinding_protection=False`) — dess standardinställning tillåter
bara `Host: localhost`, vilket skulle ge `421` på all produktionstrafik som
kommer in via nginx-vhosten. En `_LoggedMCP`-ASGI-wrapper loggar en rad per
JSON-RPC-anrop (klient-IP, metod, verktygsnamn + trunkerade argument) — det är
den enda verktygsnivå-insynen som finns, eftersom uvicorns/nginx access-logg
bara visar `POST /mcp/ 200`.

---

## Bulkdumpar (NDJSON)

För maskinkonsumenter som vill ha hela corpuset i stället för att anropa API:t
dokument för dokument. En gzippad NDJSON-fil per källa, en kompakt
JSON-artefakt per rad — radvis identisk med artefakten på disk. Citeringsgrafen
ligger redan inbäddad i varje artefakt, så varje rad är fristående.

```sh
lagen all dump                 # skriver site/data/dumps/<källa>.ndjson.gz
lagen sfs dump                 # bara en källa
```

Läs en dump:

```sh
# titta på första dokumentet
zcat site/data/dumps/sfs.ndjson.gz | head -1 | jq .

# alla författningstitlar
zcat site/data/dumps/sfs.ndjson.gz | jq -r '.metadata.properties."dcterms:title"'

# ladda i Python
python - <<'PY'
import gzip, json
with gzip.open("site/data/dumps/sfs.ndjson.gz", "rt", encoding="utf-8") as f:
    for line in f:
        doc = json.loads(line)
        print(doc["uri"])
PY
```

---

## Webbplatsens ⌘K-sökning

Den genererade statiska webbplatsen (`lagen all generate`, serverad med
`lagen all serve`) har en ⌘K-sökruta som anropar `/api/v1/search` live. Anropet
är **relativt** (samma origin som sidan serverades från), så det finns ingen
inbakad API-bas som kan peka fel om man byter port eller om en sida ligger kvar i
webbläsarens cache. En sökning på en hänvisning ("avtalslagen 36", "GDPR art 32")
fäster det exakta målet (§/artikel) överst, så Enter går direkt dit.

Rutan söker medan man skriver, men först vid **tre tecken**
(`MIN_QUERY` i `lib/assets/dom.js`, via `lagenDom.tooShort`). Kortare än så
kostar en sökning sekunder utan att svara på något: `lib/search.py` prefixmatchar varje ord, så
"N" går iväg som `N*` och Lucene expanderar det mot hela termordboken innan
något filter tillämpas — 2,6 s och 231 076 träffar, mätt på lagen.nu. Ett
uttryckligt Enter söker ändå, hur kort frågan än är; "EU" och "JO" är riktiga
sökningar. Undantaget är när sidans egen paragraf redan står i listan: då går
Enter dit, som vanligt, och tredje tecknet är vägen vidare. Sökresultatsidan
`/sok` följer samma regel, med sin egen Sök-knapp. Gränsen sitter bara i
webbläsaren — API:t självt tar emot vilken fråga som helst.

---

## Felkoder

| Kod | Betyder |
|---|---|
| `403` | en begäran från en annan origin till `/internal-api/v1` eller `/ops` |
| `404` | dokumentet finns inte i katalogen |
| `422` | obligatorisk parameter saknas eller är ogiltig (FastAPI-validering) |
| `503` | katalogen är inte byggd — kör `lagen all relate` |

Svaret är JSON: `{"detail": …}`. Ett `404` och ett `5xx` — alltså även `503` i
tabellen ovan — bär dessutom `"error_id"`, nyckeln till felloggen: `lagen all
errors <id>` skriver ut vad som spelades in. Nyckeln är `null` om själva
loggen inte gick att skriva; svaret levereras ändå.

De två andra koderna i tabellen, `403` och `422`, har ingen `error_id` alls.
Båda är anroparens eget fel och loggas inte, och ett `422` bär dessutom
FastAPI:s lista över valideringsfel som `detail`, inte en mening.

Söker du och får ett fel från OpenSearch: kontrollera att klustret är igång,
att `OPENSEARCH_URL` stämmer och att `lagen all index` har körts.
