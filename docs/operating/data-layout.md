# Data layout

The pipelines read large data trees that live under `site/data/` (not all
committed):

```
site/data/downloaded/sfs/                     # SFS raw (beta JSON + legacy sfst/sfsr HTML)
site/data/downloaded/sfs/pdf/                 # mirrored official SFS PDFs (1998–; the graphic-crop source)
site/data/artifact/sfs/                       # parsed JSON artifacts (+ .versions.json sidecars)
site/data/{downloaded,artifact}/sfs/archive/  # superseded consolidations, raw + parsed (a raw JSON with a `_reconstructed` key is ferenda's own reconstruction, not a download)
site/data/downloaded/eurlex/                  # CELLAR harvest: {year}/{celex}/notice.ttl + content per language
site/data/downloaded/eurlex/*/*/.versions/    # consolidated wordings (CONSLEG), one dated dir per version
site/data/downloaded/eurlex/*/*/.versions/*/.no-content  # dated: CELLAR had no swe/eng text for this wording (re-asked when stale)
site/data/artifact/eurlex/                    # parsed JSON artifacts (+ .versions.json sidecars)
site/data/artifact/eurlex/archive/            # superseded consolidations, parsed (lydelse pages)
site/data/downloaded/dom/                     # DV new-API harvest (per court)
site/data/downloaded/dv/                      # DV legacy feed (.doc/.docx)
site/data/artifact/dom/identity-index.json    # canonical case -> source records
site/data/artifact/dom/casenumbers.json       # case number -> held decisions (read by lib/malnummer at parse time, not a recipe input; `lagen dv casenumbers`)
site/data/downloaded/avg/{jo,jk,arn,imy,kkv}/ # per-decision records (+ jo/arn PDFs, jk landing html)
site/data/downloaded/avg/imy/dok/             # IMY decision PDFs, by asset name (shared between decisions)
site/data/downloaded/avg/kkv/dok/             # KKV decision documents, by diarium file name (pdf/htm/docx)
site/data/downloaded/rs/{fk,migr,kfm,imy,fi,kkv}/  # per-ställningstagande records + their PDFs
site/data/downloaded/rs/skv/                  # per-ställningstagande records + the pages that ARE the documents
site/data/downloaded/hudoc/                   # HUDOC metadata JSON + converted full-text HTML
site/data/downloaded/coe/                     # Treaty Office records + official English texts
site/data/downloaded/icrc/                    # ICRC JSON:API treaty envelopes (metadata + authentic text, no PDF)
site/data/downloaded/untc/                    # MTDSG status pages (metadata + participation) + the depositary's authentic text (.text.html / .pdf)
site/data/downloaded/icc/                     # ICC Legal Tools records (metadata) + decision PDFs
site/data/downloaded/icj/                     # ICJ index rows (metadata) + I.C.J. Reports decision PDFs
site/data/downloaded/forarbete/<type>/<year>/ # regeringen.se harvest + frozen-import records (prop/sou/ds/pm/dir/fm/skr/so/lr), year-segmented (pm buckets under `_`)
site/data/downloaded/forarbete/bet/<year>/    # data.riksdagen.se harvest (utskottsbetänkanden; record json + PDF, no HTML landing page)
site/data/downloaded/forarbete/rskr/<year>/   # data.riksdagen.se harvest (riksdagsskrivelser; record json + HTML body, no PDF)
site/data/ocr/forarbete/<type>/<year>/        # optional re-OCR sidecar PDFs (win over frozen scans)
site/data/downloaded/remisser/<typ>/<id-slug>.json  # regeringen.se remiss ärende record (Remiss json), keyed on the referred document, not the ärende-page slug
site/data/downloaded/remisser/<typ>/<id-slug>/       # its per-organisation answer PDFs (beside the record)
site/data/artifact/stats/statistik.json       # the 54 corpus measurements (no downloaded/ half — the corpus is the input)
site/data/artifact/stats/archive/statistik-<date>.json  # one dated snapshot per compute run, kept indefinitely
```

Historical corpora use the ordinary `site/data/downloaded/` tree and the same
record format as live-harvested documents. They need no separate mount.

