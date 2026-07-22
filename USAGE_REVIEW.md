Usage review of accommodanda

> **Status tracking** (added by Claude while working through this list):
> each item is annotated inline with `[DONE]`, `[POSTPONED — reason]`, or
> `[PARTIAL — note]`. Postponed items are ones that need a new data pipeline,
> a broad cross-source refactor, or visual QA against a running site.

These are observations as a end user of the accommodanda codebase. While the site technically works according to specs (such as they are), the UX cn be improved. All these observations should be transformed into tasks with at clear UX perspective. The main persona to target is the user as a passionate amateur, ie not someone who works in law (although they are welcome too and we make special affordances to them where relevant) but a non-legally trained person with motivation to read the primary sources to understand the law.

UI
------
U1: **[DONE]** the front page and index pages (aka "aggregation views") have the text in a centered view with a pleasant width. When reading a document, the centering goes away and becomes toc (fixed width) + main text (max-width) left aligned, and context rail (fixed width) right aligned, causing a large empty space between text and context on most full screen displays. The main layout shoud always have a max-width and centering for the document or other content. left and right rails (often toc/commentary respectively) should align up to the main text and have a smaller max-width (and handle smaller screens gracefully). Any significant white space should be in the left+right margins.

U2: **[DONE]** the top nav should also be centered in the same way. 

U3: **[DONE]** the top nav should also link Begrepp -> /begrepp/


Content/presentation
--------------------
C1: **[DONE — the frontmatter is eyebrow → h1 → a compact dl.meta directly under the h1 (`_doc_meta`), for every source; the authoritative-source "Källa" link is its last row. The full official title is a "Titel" row in that dl.meta (SFS/EU acts/treaties/föreskrifter), not the h1. SFS drops "Utfärdad", reformulates the identifier row to "Ändring införd t.o.m. SFS N" (omitted when unamended) and brings back "Senast hämtad" (a `.fetched.json` sidecar bumped on every SFS fetch, incl. an unchanged one; read live at generate). EU drops "Typ"/"EUT"/"Mål"; föreskrift drops "Utkom från trycket"; hudoc drops "Dokumenttyp"/"Språk"/"Motpart". dl.meta styled compact — tight rows, small type — so it leads quickly into the text (style.css). The rail's "Om dokumentet" panel now holds only the curated layers (Externa länkar, Kommentar).]** Vertical space is at a premium. The start of any document should be short and lead straight into the legal text. dl.meta can still exist but should be trimmed to essentials, eg not "EUT: L"  I think the source link can always be moved to the right context rail (that way it always have some content when the document is loaded). Maybe dl.meta should move there as well, I'm not sure.

C2: **[DONE — the eyebrow is now the `short_id` (the bare identifier: "SFS 2018:585", "(EU) 2016/679", "C-311/18", "Prop. 2019/20:1", "no. 48786/09", "GK III", "CAT", "ICC-01/14-01/18") and the h1 is the `short_title` (the friendly short name: "Säkerhetsskyddslagen", "dataskyddsförordningen (GDPR)", "Schrems II", "Europakonventionen (EKMR)"), never the same string twice. The four name forms are computed in the new `lib/labels.py` (`document_labels(source, art)` → `short_id`/`short_title`/`official_title`/`descriptive_label`), the discoverable I2 seed — edit `_sfs`/`_eurlex`/`_dv`/… to change one source. Inspect any document's values with `lagen <source> status <basefile>`. Remaining I2 work: route `descriptive_label` into inbound links/listings (I1), and give föreskrifter a captured title (harvest gap).]** I'm not sure about div.eyebrow -- it looks nice but it must have some valuable information, not just the SFS-number (well, it can have it if its removed from the h1) or "EU-förordning" (well, it can stay if the exact same information is removed from dl.meta) 

Inbound links and reference labels
-------------
I1: **[DONE — the inbound "Hänvisat till av" panels now label each citer by its `descriptive_label` (labels.py), stored in a new `catalog.documents.descriptive` column at index time and read by `citer_name`. SFS → "räntelagen" (named short form, no number); rättsfall → "NJA 2019 s. 1021 (Fotbollsmatchen)" / "HFD 2011 ref. 4" / court+mål for pre-referat; myndighetsbeslut → "JO 2024 s. 246" (ämbetsberättelse) else "JO dnr …"; EU → the short label; förarbete keeps its "Prop. …: title" form. CAVEAT: myndighetsföreskrifter show only the FS number ("KVFS 2011:9") until F1 captures the föreskrift title — the detailed "19 § … (KVFS 2011:9) om fängelse" form is blocked on that data gap.]** Whenever a document or a document section presents a "these sources link here" we should strive to use a consistent, short but descriptibe label for the source:
* SFS: "10 § förvaltningslagen" -- omit the SFS number
* Myndighetsföreskrift: "19 § Kriminalvårdens föreskrifter och allmänna råd (KVFS 2011:9) om fängelse" -- use a detalied citing source, FS number and title
* Rättsfall: 
  * "NJA 2019 s. 1021 (Fotbollsmatchen)" -- use official ids + names where available
  * "HFD 2011 ref. 4" -- use the first sentence or ~10 words of the description otherwise
  * "Högsta förvaltningsdomstolen, mål 1889-24" -- use court + casenumber for cases not yet published
* Myndighetsbeslut:
  * "JO 2024 s. 246" -- use the Ämbetsberättelse property if available, "JO <diarienummer>" otherwise 

I2: **[DONE — `lib/labels.py` is the single home: `document_labels(source, art)` returns the `short_id`/`short_title`/`official_title`/`descriptive_label` quartet, one `_<source>` block per vertical (edit `_icc` to change ICC cases, etc.), reading the artifact + curated datasets. All consumers wired: the document header (eyebrow=short_id, h1=short_title, dl.meta Titel=official_title), the inbound panels (descriptive_label), and the browse **listings** — `short_id`/`short_title`/`description` are stamped onto the catalog at relate and rendered per-type (basic entry = bold linked short_id + short_title; SFS keeps its split-title markup; a case reads "namn: sammanfattning"). `lagen <source> status <basefile>` shows the quartet. Browse listings are single-column (multi-column dropped); DV buckets group Domar / Referat / Notiser (headed only when >1 form present), Referat/Notiser in referat-number order, Domar by avgörandedatum. REMAINING: (a) a distinct "short official" form ("Förordning (EU) 2026/1030") is not yet separated from short_id; (b) föreskrift titles (F1).]** Ideally, every source should know how to construct this short but descriptive label in itself though a method, and any consumer should just call that method -- this ensures that specialities like namedacts.json/namedlaws.json are applied consistently throughout.  In addition, each source should have a method to return the long official (normally the full title like "Europaparlamentets och rådets förordning (EU) 2026/1030 av den 29 april 2026 om redovisning av växthusgasutsläpp från transporttjänster (Text av betydelse för EES)") + short official ("Förordning (EU) 2026/1030") names. These three methods (descriptive_label, official_title, short_id) should be implemented for all document types and the method to construct them should be documented and easily tweaked (ie if I want to change the descriptive label for ICC cases, i should just have to look up its descriptive_label method and change it)

I3: **[DONE — a citer whose repeal date has passed (catalog.expired column) is dropped from inbound panels; a future/not-yet-in-force repeal is kept]** Repealed acts (of all kinds eg SFS, myndighetsföreskrifter, EU acts) should not show up as inbound links. An act is not repealed when a new act repealing the old is published, only when the new act enters into force. Not-yet-into force acts may show up as inbound links

Search
------
S1: **[DONE — kept Ctrl+K accelerator; label now shows "Ctrl K" off Mac, "⌘K" on Mac]** The "⌘K" button only makes sense to Mac people. The windows equivalent would maybe be "Sök" with the k underlined and Alt+k being the accellerator (right now its Ctrl-K -- do people undersstand that as a button label?)

S2: **[DONE — refine link is now right of the input; the hit count rolls odometer-style (easeOutCubic, 150 ms, tabular-nums, respects prefers-reduced-motion) via search.js rollNumber]** when doing search-as-you type, the a.search-refine link should be to the right of the input element, not the left. If the numbers could spin up/down (ie when changing avta to avtal, dont just replace 46 690 with 45 358, do a 150ms odometer-style transition between the numbers) that'd be awesome.

S3: **[DONE — acts (sfs/foreskrift/EU acts+treaties) get a flat score-tier boost above other sources; ACT_TIER_BOOST tunable against the live index]** Search should always weigh acts (SFS, myndighetsföreskrifter and EU treaties/acts) higher than other sources. The first hit for "arbetsmiljölag" should be /1977:1160 (arbetsmiljölagen) not the SOU/Prop for the same law

S4: **[DONE — /sok now queries as-you-type (200 ms debounce), URL kept current via replaceState so keystrokes don't spam history; Enter still does an explicit pushState submit]** the advanced search view (https://ferenda.lagen.nu/sok/) should also use search-as-you-type

S5: **[DONE (pre-existing) — /sok already uses full-search-layout: facets in a left aside, results as the main column; matches the acts layout intent]** The layout of the advanced search view should be basically similar to acts, with the search list being the main content and the facets being the left rail.

S6: **[DONE — the repeal date (catalog `expired`) is now indexed (search.MAPPING + doc_actions); query_body excludes acts whose repeal is in force via a query-time `range {expired <= now/d}`. INDEX_FORMAT bumped 3→4, so the next incremental reindex picks it up. Completeness still tracks whatever sources populate the `expired` column, same as browse]** Repealed acts (of all kinds) should not show up in search results.

S7: **[DONE — the same range filter compares to `now` at query time, so a future (not-yet-in-force) repeal date, and a null one, stay in results]** Not-yet-into force acts may show up in search results.

Link previews
-------------
L1: **[DONE]** Link previews should not be used oon index pages (in that context the user already knows where they're about to navigate)

L2: **[DONE]** Link previews to acts or documents as a whole should not display dl.meta, rather only the the title and then straight into document content (the first paragraph or so)

Indexes
-------

X1: **[DONE — browse indexes now render facets in a sticky left rail (link-like, not pills), list as the main column; collapses to a horizontal row on small screens (needs visual QA)]** All indexes currently display nav.facets as pill buttons on top of the list. They should be in the left rail instead, and link-like rather than button-like. The faceting used by /sok/ in aside.full-search-facets is a better model and should be used for indexes also (they should be using the same design tokens)

X2: **[DONE]** The SFS index has a filter input which noone else has. I think it can be removed.


SFS
---
T1: **[DONE — widget compacted to an inline pill; a "SFS X jämfört med aktuell lydelse" status heading now appears under the widget the instant a lydelse is picked (needs visual QA)]** The start of each SFS that has been amended contains a Jämför lydelser widget that takes a lot of vertical space. Try a minimal widget directly underneath the title. And any diff loads so fast that the user might not see anything happening -- add some text like "SFS 2019:345 jämfört med aktuell lydelse" as a h2 beteween h1 and the widget. Or maybe div.diff-note could move up and be placed directly underneath the widget.

T2: **[DONE — drops bet/rskr from the SFSR förarbeten row (which carries only prop/bet/rskr; SOU/Ds/lagrådsremiss aren't in SFSR), so in practice the list is propositions; collapses to 5 + "+N fler"]** The list of förarbeten should only contain prop not bet/rskr and shoud be collapsed to show the five first and then a expandable "+ 8 fler" link

Swedish caselaw
---------------
R1: **[DONE — facets relabelled 'Court name (CODE)'; HFD and RÅ merged into one bucket]** The facets (which shuld be shown lin the left rail, cf above) should be titled "Högsta domstolen (NJA)", "Högsta förvaltningsdomstolen (HFD, tidigare RÅ)" [ie combine these two courts since its the same court having been renamed], "Hovrätterna (RH)" and so on.

R2: **[DONE — (bucketing, earlier) not-yet-published verdicts (`dom/{slug}/{malnr}/{date}`) bucket under their series via facets.VERDICT_BUCKET. (PDF pipeline) a raw verdict has no innehåll HTML, only a PDF attachment named by its målnummer; `dv.parse.parse_pdf_record`/`classify_pdf` now build its body from that PDF via `lib.pdftext` (court header/footer/page-marker boilerplate stripped, HD/HFD numbered reasons kept), wired into `build.dv_parse_run` when innehåll is empty. (dedup) `dv.identity.build_index` folds a raw verdict — an API record with *no referat of its own* — into the referat component that later publishes its målnummer, guarded to one referat component per målnummer + a *matching avgörandedatum* (so a reused number / earlier prövningstillstånd stays apart); validated on the live corpus (17 folds, 0 new cross-court links). `api_member` then prefers the referat member so the fused case renders from the NJA text. (Ursprunglig dom) the fused referat stamps `ursprunglig_dom` (the raw verdict's målnummer + a `/api/v1/dv-verdict` download url), rendered as an "Ursprunglig dom" link on the referat page; the PDF is served from the DV download store by the new `dv-verdict` endpoint. NOTE: needs a `lagen dv reindex` + `lagen dv parse --force` + `lagen dv relate`/`generate` to propagate corpus-wide (and drop the now-orphaned pre-referat artifacts); the identity index has been reindexed, the rest is a batch the operator runs.]** HD cases that have not yet been published in NJA (eg dom/hd/Ö4337-25/2026-07-14) is listed under övriga/okänt -- they should be listed under "Högsta domstolen (NJA)" but be removed when the NJA referat shows up a couple of months later. The text of these verdicts is only available in PDF during that time, so the PDF should be downloaded, parsed and rendered as HTML. When the NJA referat is published, the PDF should be downloadable and linked from the referat as "Ursprunglig dom"

R3: **[DONE — dv buckets now sort by referat number, not popular name]** HD cases should be sorted by NJA number not popular name.


EU
----------
E1: **[DONE — one deviation to confirm. Fördrag now group by treaty *family* instead of year: the type facet's second axis for Fördrag is "Kategori" (Fördraget om Europeiska unionen (EU-fördraget), EUF-fördraget, EU:s rättighetsstadga, Konsoliderade fördrag, Euratomfördraget, Ändringsfördrag, Anslutningsfördrag, Utträdesavtal, Övriga fördrag), curated order with TEU/TFEU/Charter leading; within each family the current consolidated version tops the list (newest-first). Curated Swedish names come from a new accommodanda/eurlex/data/treaties.json (labels._eurlex + the display column), so 12020W/TXT now reads "Avtalet om Förenade kungarikets utträde ur EU (2020)" instead of the giant AGREEMENT… title. Facet family logic in lib/facets._treaty_family (from the CELEX letter). Regression: test_eurlex_treaty_groups_by_family_not_year, test_eurlex_treaty_bucket_sorts_newest_first, test_eurlex_treaty_uses_the_curated_name. DEVIATION from the brief: the change treaties (Single European Act, Amsterdam, Nice, Lisbon) sit in their own "Ändringsfördrag" family rather than nested under both TEU and TFEU — the flat facet tree can't file one treaty under two parents, and an amending treaty amends both. Say if you want a different arrangement. Needs `lagen all generate --aggregates-only` after relate to publish.]** the index type facet should go Fördrag - Direktiv - Förordningar - Avgöranden - Generaladvokatens förslag (see below). Fördrag shouldn't have a year facet and should use a light hardcoded curation to put TEU, TFEU (in their current consolidated form) and CFR at the top, then all the rest in date descending. I think we'd like to put each version of TEU/TFEU and under their respective headings together  with the change treaties (SEA, amsterdam, nice, lissabon etc) underneath, including their consolidations. Heaadings should make more sense than they currently do, maybe by adjusting namedacts.json (eg 12020W/TXT should not have the title "AGREEMENT on the withdrawal of the United Kingdom of Great Britain and Northern Ireland from the European Union and the European Atomic Energy Community PROTOCOLS PROTOCOL ON IRELAND/NORTHERN IRELAND ANNEX 1 ANNEX 2 ANNEX 3 ANNEX 4 ANNEX 5 ANNEX 6 ANNEX 7 PROTOCOL RELATING TO THE SOVEREIGN BASE AREAS OF THE UNITED KINGDOM OF GREAT BRITAIN AND NORTHERN IRELAND IN CYPRUS PROTOCOL ON GIBRALTAR ANNEXES ANNEX I ANNEX II ANNEX III ANNEX IV ANNEX V ANNEX VI ANNEX VII ANNEX VIII ANNEX IX")

E2: **[DONE — browse keeps only the highest '(NN)' revision per base CELEX]** Acts that have been corrected (eg with celec numbers ending in "(01)" etc) should not be presented alongside of their base act. Instead, only the latest corrected revision should be shown. Currently eurlex/treaty/2019/ shows three treaties with almost exactly the same title but linking to https://ferenda.lagen.nu/celex/12019W/TXT(02) https://ferenda.lagen.nu/celex/12019W/TXT(01) and https://ferenda.lagen.nu/celex/12019W/TXT -- only the first of these should be shown.

E3: **[DONE — short_label already drops issuing body/date/repeal-tail/EEA boilerplate; added '(Konsolidering)' stripping; the full official title now renders as a compact subtitle under the h1 (moved out of dl.meta in C1); a bare 'EUT: L' with no issue number is now suppressed (also covers C1's EUT note)]** Titles should be more agressively shortened even when official or unofficial shortnames don't exist (both in index lists, in the act itself, and when used as label for search result or inbound links). Four categories of text that can be removed: 
* "Europaparlamentets och rådets genomförandeförordning" (and variations like "Kommissionens genomförandeförordning"), 
* "av den 9 mars 2016 om", 
* "och om upphävande av direktiv 2009/142/EG" 
* "(Text av betydelse för EES)" and "(Konsolidering)"

Ie "Europaparlamentets och rådets förordning (EU) 2016/426 av den 9 mars 2016 om anordningar för förbränning av gasformiga bränslen och om upphävande av direktiv 2009/142/EG (Text av betydelse för EES)" -> "(EU) 2016/46 om anordningar för förbränning av gasformiga bränslen" (or possibly only "om anordningar för förbränning av gasformiga bränslen" with "(EU) 2016/46" as a identifier-type property). The full official title should still be shown on the act itself under a "titel" key in dl.meta.

E4: **[DONE — needs `lagen eurlex parse --force` + relate to take corpus-wide effect (doctype reclassification)]** `doctype()` now classifies CC/CV/CP → `opinion`, CO/TO/FO → `order`; `parse_opinion()` parses the Formex `CONCLUSION` structure (prose + numbered paragraphs), so an opinion renders its body, not just its footnotes; `facets._drop_opinions_with_judgment` hides a CC opinion from the index when its CJ judgment exists; `render._eurlex_opinion_link` links a judgment to its opinion; the facet type scheme gained a "Generaladvokatens förslag" bucket and folds orders into judgments. Regression: `test_parse_opinion`, `test_doctype_from_celex`. The Opinions of the advocate-general (document type CC in the celex idwentifier) should not be listed on indexes except if there is not yet a corresponding judgment (document type CJ). If there is, the Opinions of the advocate-general should be viewable through a link from the judgment only (as well as search). The rendering of these opinions currently only show the footnotes not the actual optionion.

Föreskrifter
------------
F1: **[TODO — new task]** Extract proper titles from the föreskrift PDF sources. Today the harvest/parse never captures a föreskrift's subject title, so the artifact's `title` is `null` (e.g. FFFS 2013:1) and the page heading falls back to the bare FS number instead of "Finansinspektionens föreskrifter och allmänna råd om säkerställda obligationer". The title is on the first page of the source PDF (and often in the agency listing); capture it at harvest/parse time so `labels._foreskrift` can render the proper `short_title` (subject name) and `official_title` (with "(FFFS 2013:1)" spliced in). Unblocks the föreskrift half of C2/I2.

Folkrätt
--------
FR1: **[TODO — new task]** Fetch UN treaties from a better source. The UNTC (MTDSG) scrape carries only status/signatory data — a treaty page like /ext/untc/IV-9 shows the participation grid, not the convention text. Fetch the authentic text from a source focused on the instrument itself: [ohchr.org/en/instruments-listings](https://www.ohchr.org/en/instruments-listings) is likely cleaner than treaties.un.org for the human-rights core. Parse it into the same article/subarticle model the CoE/ICRC treaties use so provisions get anchors, inbound links and citation targets.
