# paraTEXT

paraTEXT is a reader for Swedish law: search, browse and read statutes,
preparatory works, case law, EU law and international law, with the
citations to each provision beside the text. It is a static one-page app.
The browser talks straight to lagen.nu's open API, and the app logs every
request it makes in the "API-trafik" card, with a link to the raw JSON
answer.

It is the reading sibling of
[paraGRAF](https://github.com/staffanm/para-graf), which draws the citation
graph. The two share one design — hard ink borders, offset block shadows,
flat hues, Archivo Black / Space Grotesk / IBM Plex Mono — and link to each
other: every document in paraTEXT opens in paraGRAF with one click.

## What it does

- **Search** (`/api/v1/search`). A citation-shaped query — `avtalslagen 36
  §`, `BrB 3:1`, `GDPR art 32` — resolves to the exact provision, which the
  result list pins first and marks. The source, document-kind and year
  facets narrow the answer; each bucket's count is computed against the
  other filters, so the numbers stay usable for widening.
- **Browse** (`/api/v1/browse`). One leaf bucket at a time, with the
  navigator beside it: statutes by initial, case law by court and year, EU
  law by act type. A listing states its own paging.
- **Read** (`/api/v1/document`). The parsed artifact is drawn as a reading
  text: headings, paragraph designations, lists, tables, and every citation
  as a link. Each §, article or numbered paragraph hangs its numeral in the
  gutter with its own permalink.
- **Context** (`/api/v1/document/inbound`). One call per document gives every
  provision its citation count, printed as a badge in the gutter. Selecting a
  provision asks the exact question for it and groups the answer by source,
  case law first. The card follows the reading as the reader scrolls.
- **Identity cards** (`/api/v1/card`). Hovering any citation shows the
  target's own words without leaving the page.
- **Versions and diffs** (`/api/v1/document/versions`, `/document/diff`). A
  statute's earlier consolidations are a dropdown; choosing one shows what
  changed.

The whole view lives in the URL hash, so every screen is a link:
`#/1975:635#P6` is 6 § räntelagen with its context open,
`#/sok?q=uppsägning&source=dv` is a filtered search,
`#/bladdra/dv/nja/2024` is a browse bucket.

## Layout

- `site/index.html` — the page: masthead with search, the reading column,
  the contents card, the context card, the API-traffic card.
- `site/app.js` — routing, the three screens, the artifact-to-HTML walk, and
  the logged API client.
- `site/style.css` — the design.
- `docker-compose.yml` — nginx serving `site/` on port 8097.

The API origin sits in one place: the `data-api` attribute in
`site/index.html`. The `data-graph` attribute beside it names the paraGRAF
instance the "Utforska grafen" buttons open.

## Run locally

    python3 -m http.server 8097 -d site

The app then reads the live API at https://lagen.nu, which serves CORS to
any origin.

## The artifact walk

`app.js` draws the document from the artifact JSON the API returns. It
dispatches on each node's `type` only, never on which source the artifact
came from — the same contract `ferenda/lib/mdtext.py` keeps for markdown. A
node type the walk does not know renders through the generic rule: its runs
as a paragraph, then its children. A new source therefore degrades to
readable prose rather than to nothing.

Every renderable text value is a list of inline runs — a plain string, or a
link dict `{predicate, uri, text}`. Those link dicts are the citation graph,
and they become the links the reader follows and hovers.
