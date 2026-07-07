# lagen.nu API — utvecklarguide

Ett läsbart REST/OpenAPI-gränssnitt över hela det parsade rättskällекorpuset
(författningar, rättsfall, förarbeten, myndighetsföreskrifter, JO/JK/ARN-avgöranden,
EU-rätt, kommentarer och begrepp). Det
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
| `source` | sträng | begränsa till en källa: `sfs`, `dv`, `forarbete`, `foreskrift`, `eurlex`, `avg`, `kommentar`, `begrepp` |
| `kind` | sträng | begränsa till en dokumenttyp (`law`, `case`, `prop`, `directive`, …) |
| `limit` | heltal 1–100 (standard 10) | antal träffar |
| `offset` | heltal (standard 0) | paginering |

Träffarna är hela dokument, rankade på relevans kombinerat med antalet
inkommande citeringar (`inbound_count`) — så en välträffad, ofta hänvisad lag
slår en lika välträffad men obskyr. Varje träff innehåller även de matchande
paragraferna/artiklarna med markerad text (`fragments`).

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
      "url": "/sfs/1962_700.html",
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

`url` är den genererade sidans sökväg (via `layout.page_relpath`); lägg på
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
| `source` | sträng | filtrera på källa (`sfs`, `dv`, `forarbete`, `foreskrift`, `eurlex`, `avg`, `kommentar`, `begrepp`) |
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

Signaturfunktionen som data: alla *andra* dokument som citerar exakt den angivna
URI:n — en post per (citerande dokument, pinpoint). Självcitering exkluderas.
Ange en fragment-URI för att fråga på paragrafnivå.

```sh
curl -G http://127.0.0.1:8001/api/v1/document/inbound \
     --data-urlencode "uri=https://lagen.nu/1975:635#P6"
```

```json
[
  {
    "uri": "https://lagen.nu/dom/nja/2009s796",
    "anchor": "domskal",
    "predicate": null,
    "text": null,
    "label": "NJA 2009 s. 796",
    "source": "dv",
    "hosted": true
  }
]
```

(6 § räntelagen har i det fullständiga corpuset ~2 800 citerande dokument.)

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

### `GET /api/v1/sources` — källor och antal

```sh
curl http://127.0.0.1:8001/api/v1/sources
```

```json
[
  {"source": "avg", "documents": 6256},
  {"source": "begrepp", "documents": 564},
  {"source": "dv", "documents": 17103},
  {"source": "eurlex", "documents": 69290},
  {"source": "forarbete", "documents": 15237},
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
