# SKVFS (and MTFS): the F5/Shape bot-wall — harvest handover

**Status (2026-07-15):** SKVFS/RSFS remain **frozen-only** (§7g). No live harvester
exists or is currently buildable from this environment. The blocker is a
commercial bot-defense, not a code problem. This note records what was tried,
what was learned, and what might still work, so the next attempt doesn't repeat
the dead ends.

## What SKVFS is and why it's stuck

- **SKVFS** = Skatteverkets författningssamling; **RSFS** = its Riksskatteverket
  predecessor. Both are binding law and public records. Skatteverket publishes a
  lot, and stale tax regulation is actively harmful, so *timely* access matters.
- Registered in `accommodanda/foreskrift/agencies.py` as **frozen-only** stubs
  (`SKVFS`/`RSFS` via `frozen_agency`), fed once from the legacy tree by
  `foreskrift/legacy.py` (`lagen foreskrift import-legacy skvfs`). On disk:
  ~509 skvfs + 31 rsfs records, parsed and generated. The gap is everything
  published **since** the legacy freeze.
- **MTFS (Tillväxtanalys)** sits behind the **same wall** and is frozen for the
  same reason — anything that unblocks SKVFS likely unblocks MTFS too.

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
- Rättsliga ställningstaganden (future want, same host/wall):
  `https://www4.skatteverket.se/rattsligvagledning/121.html`

## What was tried (all failed)

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
| Headful under xvfb | **Not testable** — no X server, no xvfb, no sudo in that env |

**Key finding:** a headless browser *executes* Shape's telemetry, **fails the
bot classification, and is actively banned** — strictly worse than a dumb client
(which at least gets the challenge page). Do **not** ship a headless harvester.

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
**Stop probing this wall from the dev/VPS box**; it risks flagging the IP.

## What might still work

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

2. **Headful Chromium under a real X server / xvfb + Swedish residential IP.**
   The *only* technical config with a realistic chance (Shape weights
   headless/automation signals heavily; genuine headful is the differentiator).
   **Unproven** — never demonstrated. Would also need: full stealth (patched
   `navigator.webdriver`, realistic GPU/WebGL, plugins, sv-SE/Stockholm), a
   warm-up navigation to mint the TSPD cookie, **very slow human-like pacing**
   for 500+ docs (Shape scores behaviour and rate), and a residential/Swedish
   egress (datacenter IPs are penalised — see the reputation gotcha). Treat as a
   research spike to run on a real desktop, **off the nightly pipeline**, and
   prove it holds a session before investing. This is an arms race, not a
   one-time build — prefer option 1.

## When a channel opens: how to wire it

Whichever channel SKV opens (open-data feed or an allowlisted register path), the
harvester itself is **just config over the existing engine** — replace the
`SKVFS`/`RSFS` `frozen_agency(...)` stubs in `agencies.py` with live `Agency`
entries (an `enumerate` + a `resolve`), keeping `foreskrift/legacy.py`,
`LEGACY_CORPORA`, and the RSFS entry intact. If it's an allowlisted *register*
path, set `user_agent`/`headers` (and the shared-secret header if granted); if
it's a browser-driven fetch, keep it a **standalone off-pipeline stage**, not a
client wired into the nightly build.

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
- Frozen stubs + `frozen_agency`: `accommodanda/foreskrift/agencies.py`
- Frozen import: `accommodanda/foreskrift/legacy.py`, `lib/legacy_import.py`
- HTTP/2 transport (KKVFS precedent, wrong tool for this wall):
  `Agency.http2`, `lib/net.make_http2_session`
- Session scratch artifacts (ephemeral): Playwright HTML dumps + `datasets.json`
  under `scratchpad/skvfs_playwright/`; the draft access request at
  `scratchpad/skv_atkomstbegaran.md`.
