# lawreview — known gaps

The vertical harvests nine journals (`journals.py`). These Swedish legal
journals are known to exist and to publish open material, but are not
harvested. Each is parked, not broken: nothing in the built scope depends on
them, and adding one is the same shape as the nine — a `Journal` entry, one
walker module, tests against trimmed fixtures.

## Excluded on purpose

- **Sjörättsbiblioteket** — not harvested, excluded at the user's direction.

## Parked (exist, not yet built)

- **NST** — Nordisk socialrättslig tidskrift (nordisksocialrattslig.se).
- **Sc.St.L.** — Scandinavian Studies in Law (scandinavianlaw.se). Its
  articles are open on the LAWPUB platform and therefore reachable from the
  `lawpub` scope; a dedicated walker is parked.
- **SNEF** — parked, no walker.
- **ERT** — Europarättslig tidskrift (ert.se).
- **JT open-access subsets** — the open-access slices of the Juristtidningen
  hands; the paid articles are not reachable and are left out.

A journal moves off this list only with its walker, a `Journal` registry
entry, and hermetic tests; the nine above are the working set.

## Partial on purpose

- **Lov & Data** (lod.lovdata.no) — only the volumes Lovdata publishes as
  per-article web pages are harvested (2022 and later). The 2018–2021
  volumes, and every print volume back to 1984, exist only as full-issue
  PDFs; the walk reads their year pages and takes nothing off them, so a
  volume Lovdata republishes as pages joins on its own.

## Upstream gaps (the journal owes them to its readers)

- **EU och arbetsrätt: four dead item links.** The journal's issue pages
  set four cards to item addresses that answer 404, and the journal has not
  mended the links. An item's page is its document, so a card to one of
  these pages contributes nothing the walk can store: `euar.py` keeps the
  four addresses in `DEAD_ITEM_URLS`, writes no record for a card to one of
  them, and the run stays clean around it. An item that the journal brings
  back to life is added on the next run once the address is dropped from
  the set:

  - `…/artiklar/eu-domstolen-svarar-islands-landsretturregler-om-kollektiva-uppsagningar-galler-ocksanar-arbetsgivaren-vill-andra-arbetsvillkoren/`
  - `…/artiklar/nationella-domstolar-ska-avgora-ominhyrning-rimligen-kan-ses-som-temporar/`
  - `…/artiklar/tco-anmaler-sverige-for-indirekt-konsdiskriminering/`
  - `…/artiklar/uppforandekoder-och-social-markning-bor-bli-mer-enhetliga/`

- **EU och arbetsrätt: pre-2005 issue pages offline.** The journal has
  taken its oldest issue pages offline; they 404 at their addresses while
  the index still lists them. The walk records each dead page as a skip in
  the run's output, and the skip keeps the store dirty until the walk runs
  clean to the end.