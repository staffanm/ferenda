# SAVFS — Statens arbetsgivarverks författningssamling

The AGVFS scope now reads the six SAVFS regulations on Arbetsgivarverket's
second register page. Each document keeps its printed SAVFS designation.
The series registry names AGVFS as its successor, so browse lists SAVFS
documents under Arbetsgivarverket without changing their document URLs.

The official register lists SAVFS 1990:3, 1984:11, 1984:6, 1983:11,
1983:5, and 1979:4. SAVFS 1986:11 announces a split into two series;
the harvester excludes that announcement.

The official 1990:3 and 1983:5 PDFs both print their listed SAVFS designation.
They contain government förordningar published in this agency series.
The 1990:3 PDF, its landing page, and the second register page are frozen
fixtures under `test/files/foreskrift/`. The tests cover discovery, storage,
citations, canonical document URLs, and browse succession.

`lagen foreskrift download agvfs` includes both AGVFS and SAVFS by default.
A first harvest reaches all listed records. For an existing corpus, use
`lagen foreskrift download agvfs --deep` once to fetch older gaps beyond the
incremental watermark. This shared backfill option skips documents already held.
The change does not reconstruct the complete historical SAVFS collection.
It adds the six regulations that the official register currently publishes.
The existing AGVFS subseries-number collision remains outside this change.

Sources checked 2026-10-17:

- https://www.arbetsgivarverket.se/avtal-och-skrifter?category=32&page=2
- https://www.arbetsgivarverket.se/globalassets/arbetsgivarverket/avtal-och-skrifter/agvfs/savfs-1990-3a1-forordning-om-utbetalning-av-personskadeersattning.pdf
- https://www.arbetsgivarverket.se/globalassets/arbetsgivarverket/avtal-och-skrifter/agvfs/savfs-1983-5-forordning-om-halsoundersokning-infor-anstallning-i-utrikesforvaltningen.pdf
