# SKVFS (and MTFS): the F5/Shape bot-wall — harvest handover

**Status (2026-07-15):** **live incremental SKVFS and MTFS work** through the
ordinary `lagen foreskrift download {skvfs|mtfs}` sweep. SKVFS layers over the
frozen SKVFS/RSFS baseline (§7g); MTFS has no frozen baseline. Their two
`Agency.browser` flags select a real headful Chrome transport; every other
agency keeps `requests`/HTTP2. F5 still blocks direct HTTP and any browser
instrumented while the challenge runs, so the working transport is
operationally heavier and more fragile than an open-data feed.

## The sources

- **SKVFS** = Skatteverkets författningssamling; **RSFS** = its Riksskatteverket
  predecessor. Both are binding law and public records. Skatteverket publishes a
  lot, and stale tax regulation is actively harmful, so *timely* access matters.
- The frozen import remains the baseline: `foreskrift/legacy.py` (`lagen
  foreskrift import-legacy skvfs`) supplies ~509 SKVFS + 31 RSFS records. The
  live `SKVFS` Agency fills only missing records; its one register also emits
  the closed RSFS predecessor into the `rsfs/` namespace. The separate `RSFS`
  registry entry remains frozen because no second browser sweep is needed.
- **MTFS (Tillväxtanalys)** sits behind the **same wall**. Its Sitevision page
  contains both current and repealed MTFS headings, each followed directly by
  the official PDF. The live Agency now harvests all 16 listed regulations.
  Five older PDF filenames omit “MTFS”, so enumeration deliberately keys off
  the authoritative headings rather than filename patterns.

## The wall

`www.skatteverket.se` **and** the register host lagrummet.se points at,
`https://www4.skatteverket.se/rattsligvagledning/`, both sit behind **F5/Shape**
(now "F5 Distributed Cloud Bot Defense"), the TSPD JavaScript challenge:

- A plain HTTP client that sends a browser UA + `Accept-Language` gets HTTP 200
  but only the **~3–7 kB obfuscated challenge stub** (`window["bobcmn"]`,
  `/TSPD/...?type=11/12` scripts, `re.security.f5aas.com`,
  `<noscript>Please enable JavaScript</noscript>`). Never the register.
- Without `Accept-Language`, or with the honest harvester UA, you get the outer
  **F5 ASM hard-block** (~245–255 B, "Request Rejected", a support ID).
- The valid `TSPD` cookie is minted only by **executing** the challenge JS. The
  short-lived `Set-Cookie` values on the challenge response (`TS...`,
  `Max-Age=30`) are the challenge's own tracking cookies, **not** a solved token
  — reusing them returns the same challenge.

Relevant register URLs:
- SKVFS/RSFS: `https://www4.skatteverket.se/rattsligvagledning/115.html?year=Alla`
- MTFS: `https://www.tillvaxtanalys.se/statistik/tillvaxtanalysforeskrifter.125740.html`
- Rättsliga ställningstaganden (future want, same host/wall):
  `https://www4.skatteverket.se/rattsligvagledning/121.html`

## What was tried

### Plain HTTP clients (`accommodanda/lib/net`)
| Attempt | Result |
| --- | --- |
| `HARVESTER_UA` (honest UA) | ASM hard-block, 245 B |
| `BROWSER_UA` alone | ASM hard-block, 245 B |
| `BROWSER_UA` + `Accept-Language: sv-SE` | 200 — but the TSPD **JS challenge stub**, not the register |
| HTTP/2 via `make_http2_session` (httpx2[http2]) | Same challenge stub |
| Cookie two-step (fetch, reuse `Set-Cookie`, re-fetch) | Same challenge (cookies are not a solved token) |

The `httpx2` HTTP/2 client and `requests` both **cannot run JS**, so they can
never pass the challenge. This is why the engine's `Agency.http2` flag (which
*did* solve KKVFS's Cloudflare-HTTP/2 wall) does **nothing** here — different
wall class entirely.

### Headless browser (Playwright Chromium) — **worse than a dumb client**
Isolated venv, `chromium` + `chromium_headless_shell`. Against the register:

| Config | Result |
| --- | --- |
| Plain headless (`headless_shell`) | **Hard-rejected** — "Request Rejected", 255 B, 0 SKVFS links |
| Stealth headless (UA, sv-SE, Europe/Stockholm TZ, real viewport, `navigator.webdriver` masked) | **Hard-rejected**, identical 255 B |
| Full chromium `--headless=new` + heavy stealth (WebGL vendor spoof, chrome runtime, plugins, hwConcurrency) | **Hard-rejected**; 2nd navigation in same context also rejected |
| Playwright-launched headful Chrome, webdriver masked | **Hard-rejected** — CDP was attached while F5 classified the page |

**Key finding:** a headless browser *executes* Shape's telemetry, **fails the
bot classification, and is actively banned** — strictly worse than a dumb client
(which at least gets the challenge page). Do **not** ship a headless harvester.

### Headful Chrome with delayed Playwright attachment — **works**

The differentiator was not more fingerprint spoofing. It was keeping
Playwright's Chrome DevTools Protocol (CDP) connection absent while F5 ran:

1. Start the system `google-chrome` headfully with a dedicated persistent
   profile and a fixed remote-debugging port, but no DevTools client connected.
2. Ask that ordinary Chrome process to open the protected URL and leave it
   completely uninstrumented for **20 seconds** (18 seconds was the observed
   minimum). Chrome executes the TSPD challenge and reaches the real page.
3. Only then connect Playwright over CDP. This is "attaching": Playwright gets
   handles to the tabs that already exist; it does not relaunch or reload them.
   Read the completed DOM, close that tab, and disconnect Playwright again
   before the next navigation.
4. A PDF is likewise navigated to while detached. Once Chrome has rendered and
   cached it, attach and use CDP `Network.loadNetworkResource` + `IO.read` to
   recover the exact response bytes; assert `Content-Type: application/pdf` and
   `%PDF-` before storing them.

Live proof from this environment (Chrome 149, Playwright 1.61, real `DISPLAY=:0`):

- register: 1,048,875-byte real HTML, 139 rows / 134 unique FS identifiers;
- detail: `SKVFS 2026:7`, 75,553-byte real HTML;
- PDF: 180,063 exact bytes, `%PDF-1.6`;
- integrated `--only skvfs/2026:7` run: ordinary föreskrift record + HTML + PDF,
  reported as one new document.
- full post-freeze SKVFS gap: 34/34 records, SKVFS 2025:4 through 2026:8,
  downloaded in one session with no errors;
- MTFS register: 71,599-byte real HTML with 16 regulation headings/PDFs;
- MTFS 2023:3 PDF: 720,726 exact bytes, `%PDF-1.7`.
- full MTFS sweep: 16/16 records, MTFS 2009:1 through 2023:3, including the
  five older filenames that omit “MTFS”, with no errors.

The register duplicates SKVFS 2021:19, 2021:20 and 2009:6 under multiple
subject/detail ids. Their titles are identical; `skvfs.parse_index` keeps the
first only after asserting that duplicate titles agree. The frozen fixture locks
both this rule and the site's `SKVFS 2026_3` underscore typo.

Solved cookies were not a durable hand-off to `requests`: after Chrome exited,
the protected cookies were empty/expired and a direct PDF request returned the
7,461-byte challenge again. Keep the browser process alive for the whole agency
run.

### Other data channels — none carry SKVFS
- **Rättsliga regler API** (`api.skatteverket.se/regelverk/rattsligaregler/v1`):
  Skatteverket's **Rules-as-Code** *interpretation* of law as machine-readable
  decision trees per `regelområde`. OAuth2-gated (client id/secret). **Not** the
  föreskrift text, no SKVFS-identifier catalogue. Wrong dataset — and presenting
  SKV's computed opinion as "the regulation" would be wrong for a legal-info
  service anyway.
- **Developer portal / open-data catalog:** the portal SPA's dataset list is a
  **public EntryScape DCAT catalog** reachable with no browser and no F5:
  `https://skatteverket.entryscape.net/store/search?type=solr&query=(context:.../store/9)+AND+rdfType:dcat%23Dataset+AND+public:true&limit=100&sort=modified+desc&format=application/json`
  → **110 datasets**, none exposing SKVFS as text/catalogue/PDF. Only rules-ish
  entry is "Rättsliga regelfiler" (the RaC feed above). Everything else is
  statistics/reference data (skattetabeller, riktvärden, traktamenten, …).

### Operational gotcha
Repeated probing degraded the **source IP reputation** — during the Playwright
test the VPS/dev IP began getting immediate ASM rejections even from `curl`.
Do not repeatedly probe or retry this wall from the dev/VPS box; one slow nightly
session is materially safer than bursts of new profiles and failed challenges.

## Operations and the preferred long-term channel

Ranked by realism and durability.

1. **Institutional access (preferred).** Ask Skatteverket for a sustainable
   channel. A draft request (Swedish) was written this session covering
   SKVFS/RSFS + rättsliga ställningstaganden + rättslig vägledning, asking for,
   in order: (a) open data via the existing EntryScape/DCAT-AP-SE catalog;
   (b) an **allowlist exception in the Shape/WAF config** (UA + source IP +/or a
   shared-secret header) — a one-line change for them, a durable feed for us;
   (c) recurring bulk export as fallback. Legal lever: offentlighetsprincipen +
   öppna data-lagen (2022:818) / Open Data Directive 2019/1024. This converts
   "defeat the wall" into "they open a door," which serves both sides (a single
   well-behaved client loads Shape far less than manual exports or adversarial
   traffic). The allowlist path is the one to push for — bulk export doesn't
   scale for continuous updates.

2. **Current nightly posture.** `Agency.browser=True` is configured only for
   SKVFS and MTFS. `foreskrift.harvest` selects `lib.browser.DetachedChrome` for
   those two; all other agencies still select `requests` or `Agency.http2`. The job requires:
   Playwright (a project dependency), system `google-chrome`, and a real X display
   exported as `DISPLAY` to the nightly process. It fails fast if any is absent.
   Each dedicated profile/cache lives under
   `downloaded/foreskrift/{skvfs|mtfs}/.browser-profile/`.

   A no-change run pays one 20-second register navigation per selected browser
   source. The normal SKVFS incremental walk then skips every frozen/live record;
   each genuinely new document adds two protected navigations (~40 seconds:
   detail + PDF). MTFS links directly from its register, so a new document adds
   one protected PDF navigation (~20 seconds). Do not reduce
   `browser_settle=20.0` without a fresh live measurement; attaching at four
   seconds observed the challenge stub, and challenge-time attachment is exactly
   the rejected posture.

## How it is wired

The sources still follow the same configured-by-data engine. `SKVFS` names
`skvfs.enumerate_register` + `skvfs.resolve`; `MTFS` names
`mtfs.enumerate_register` + `mtfs.resolve`; both set `browser=True`.
`foreskrift.harvest` chooses the transport, then hands those callables to the
same `lib.harvest.walk`, watermark, `--only`, `--full`, error ledger and record
layout as every HTTP agency. Browser mechanics are generic in `lib/browser.py`;
source selectors and identity rules stay in `foreskrift/{skvfs,mtfs}.py`.

If Skatteverket supplies an open feed or allowlisted endpoint, the replacement
is therefore small: remove `browser=True` and point the enumerator/resolver at
that channel. Keep `foreskrift/legacy.py`, `LEGACY_CORPORA`, and the RSFS entry.

**Critical interaction — live harvest vs. frozen corpus** (verified in
`lib/harvest.walk` + `harvest.item_key`):
- `is_downloaded = compress.exists(record_path(root, fs, basefile))`, and the
  frozen import writes records at exactly that path → **every frozen basefile
  reads as already-downloaded**.
- A normal live run (`if key.is_downloaded and not full: continue`) therefore
  **fetches only basefiles absent from the frozen corpus** and leaves the frozen
  509+31 records untouched (their `source` marker and in-place `LEGACY_ROOT` PDF
  pointers preserved). This is the correct "frozen stays, new fills the gaps"
  behaviour, and a *first* live run (no watermark yet) still honours the skip.
- **`--full` clobbers frozen records.** It sets `backfill=True` and bypasses the
  `is_downloaded` guard, so the resolvers re-resolve every enumerated basefile
  and overwrite frozen records with freshly-harvested ones — the resolvers write
  plain records and do **not** consult `legacy_import.should_write`. Intended
  "live-wins" direction, but know it before running `--full` on a future live
  SKVFS.

## Pointers
- SKVFS Agency config: `accommodanda/foreskrift/agencies.py`
- SKVFS register/detail semantics: `accommodanda/foreskrift/skvfs.py`
- MTFS register/direct-PDF semantics: `accommodanda/foreskrift/mtfs.py`
- Detached headful transport: `accommodanda/lib/browser.py`
- Frozen import: `accommodanda/foreskrift/legacy.py`, `lib/legacy_import.py`
- HTTP/2 transport (KKVFS precedent, wrong tool for this wall):
  `Agency.http2`, `lib/net.make_http2_session`
- Regression fixtures/tests: `test/files/{skvfs,mtfs}/`,
  `test/test_foreskrift_{skvfs,mtfs}.py`
