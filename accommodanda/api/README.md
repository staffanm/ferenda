# lagen.nu API — utvecklarguide

Ett läsbart REST/OpenAPI-gränssnitt över hela det parsade rättskällekorpuset
(författningar, svenska rättsfall, Europadomstolens praxis, förarbeten,
myndighetsföreskrifter, JO/JK/ARN-avgöranden, EU-rätt, Europarådets fördrag,
kommentarer och begrepp). Det
ersätter den gamla pipelinens RDF-/Fuseki-publicering.

API:t exponerar tre saker:

- **fulltextsökning** (via OpenSearch), ned på paragraf-/artikelnivå,
- **citeringsgrafen** — vilka dokument som hänvisar till ett dokument eller en
  enskild paragraf (lagen.nu:s signaturfunktion), och tvärtom,
- **dokumentens metadata och fullständiga parsade innehåll**.

All data är *härledd och återskapningsbar* ur artefakterna på disk — API:t är
aldrig en sanningskälla, bara en läsvy.

> Vill du veta *varför* arkitekturen ser ut så här? Se
> [`../../REWRITE.md`](../../REWRITE.md) §6. Den här filen handlar bara om hur
> man använder API:t.

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

Träffarna är hela dokument, rankade på relevans kombinerat med antalet
inkommande citeringar (`inbound_count`) — så en välträffad, ofta hänvisad lag
slår en lika välträffad men obskyr. Varje träff innehåller även de matchande
paragraferna/artiklarna med markerad text (`fragments`). Sökfrågan matchar
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
      "fragments": [
        {
          "uri": "https://lagen.nu/1962:700#K3P1",
          "pinpoint": "K3P1",
          "highlight": ["Den som <em>uppsåt</em>ligen berövar annan livet …"]
        }
      ]
    }
  ]
}
```

`url` är dokumentets publika sökväg (`layout.page_url`); lägg på
`#<pinpoint>` för att djuplänka direkt till paragrafen.

> Returnerar `/api/v1/search` ett fel om OpenSearch inte är igång eller indexet
> inte är byggt. Kör `lagen all index` och kontrollera `OPENSEARCH_URL`.

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

`total` är antalet matchande dokument *före* paginering, så du kan stega igenom
hela mängden. Sorteringen är på URI (stabil).

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
först. Ordningen är total och oberoende av bygget, så `offset` är stabilt mellan
ombyggnader. `limit` är 10 000 rader (och taket); `total` och `by_source` avser
hela svaret, inte den returnerade sidan.

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
      "date": "2026-07-15"
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
`/hanvisningar/`-utforskaren ritar. `direction=in|out|both` väljer sida,
`groups=` filtrerar på flödesgrupp, `limit` sätter topplistans längd.

En fragment-URI (`…#K4P7`, `…#A6`) svarar för den enheten ensam och lägger
till `internal`: hela dokumentets interna paragrafgraf på enhetsnivå
(§/artikel), med läsbara etiketter ("4 kap. 7 §").

```sh
curl -G http://127.0.0.1:8001/api/v1/graph \
     --data-urlencode "uri=https://lagen.nu/ext/coe/005#A6" \
     --data-urlencode "groups=Rättsfall,Förarbeten"
```

```json
{
  "uri": "https://lagen.nu/ext/coe/005#A6",
  "root": "https://lagen.nu/ext/coe/005",
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

En genererad sida omrenderad för papper: A4, löpande sidhuvud, sidfot av
formen "3 (12)" (sida/antal sidor), PDF-bokmärken — samma `style.css`-
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
| `download` | bool (standard `false`) | servera som bifogad fil (nedladdning) i stället för inline (visning) |

```sh
curl -G http://127.0.0.1:8001/api/v1/pdf \
     --data-urlencode "path=/1998:204" --data-urlencode "toc=true" \
     --data-urlencode "kontext=forarbete,dv" -o forvaltningslagen.pdf
```

### `GET /api/v1/sources` — källor och antal

```sh
curl http://127.0.0.1:8001/api/v1/sources
```

```json
[
  {"source": "avg", "documents": 6256},
  {"source": "begrepp", "documents": 564},
  {"source": "coe", "documents": 233},
  {"source": "dv", "documents": 17103},
  {"source": "eurlex", "documents": 69290},
  {"source": "forarbete", "documents": 15237},
  {"source": "foreskrift", "documents": 1218},
  {"source": "hudoc", "documents": 21661},
  {"source": "kommentar", "documents": 212},
  {"source": "sfs", "documents": 11184}
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
| `get_document` | ett dokuments metadata + fullständiga parsade klartext (hela, eller en enskild `pinpoint` som `K3P1`) |
| `fetch` | samma text, men hämtad på ett `id` från `search` (`…/1962:700#K3P1`) i stället för URI + pinpoint var för sig — se *Sök/hämta-kontraktet* nedan |
| `list_documents` | räknar upp dokument (id + lättviktig metadata) filtrerade på källa/typ — corpus-indexet, inte fulltextsökning |
| `get_incoming_citations` | vilka dokument som citerar denna URI/paragraf **och allt som ligger i den** (citeringsgrafen inåt — lagen.nu:s signaturfunktion); svarar med `total` + `by_source` för hela mängden och en sida rader i sidans egen ordning (rättsfall först), filtrerbart på `source` (vem som citerar) och `scope` (`tree`/`exact` — vad frågan gäller) |
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
  träffen — en paragrafdjup match id:as med sitt fragment
  (`https://lagen.nu/1962:700#K3P1`), inte med hela balken, så en `fetch` läser
  paragrafen och inte 300 sidor. Övriga fält (`fragments`, `inbound_count`,
  `source`/`kind`) ligger kvar orörda för alla andra värdar.
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

---

## Felkoder

| Kod | Betyder |
|---|---|
| `404` | dokumentet finns inte i katalogen |
| `422` | obligatorisk parameter saknas eller är ogiltig (FastAPI-validering) |
| `503` | katalogen är inte byggd — kör `lagen all relate` |

Söker du och får ett fel från OpenSearch: kontrollera att klustret är igång,
att `OPENSEARCH_URL` stämmer och att `lagen all index` har körts.
