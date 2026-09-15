# PRD: Belägg — kontroll av juridiska hänvisningar

**Status:** Draft 2 — API citation extraction

**Product type:** Fristående, statiskt hostad SPA

**Primary market:** Swedish legal professionals and legal-information users

**Working name:** Belägg

## 1. Summary

Belägg is a browser-based service that reviews Swedish legal text for unreliable citations. A user pastes text or uploads a DOCX or PDF. The service finds legal citations, checks whether each cited authority exists through lagen.nu, and—where the source text is available—estimates whether it supports the claim made around the citation.

The distinction is essential:

1. **Citation validity** is deterministic where lagen.nu has complete coverage.
2. **Semantic support** is probabilistic and is presented as evidence for human review, never as a definitive finding of hallucination.

The browser reads document files and extracts text. It sends that text to lagen.nu for citation extraction. The lagen.nu API finds citations and checks their targets. Claim analysis and language-model inference run in the browser. Original files stay on the device; their extracted text leaves it.

The product showcases the lagen.nu API as a reusable service for legal document tools. Other clients can use the same citation extraction endpoint.

## 2. Problem

Legal text produced or assisted by generative AI can fail in at least two ways:

- it cites an authority that does not exist; or
- it cites a real authority that does not support—or may contradict—the proposition attributed to it.

Link checking catches only the first and can be unreliable when the underlying database has incomplete coverage. Manual verification catches both, but is slow and easy to omit when a document contains many references.

## 3. Goals

The MVP should:

- accept pasted text and text-bearing DOCX and PDF files;
- identify Swedish, EU and international citations through the lagen.nu API;
- reliably flag a citation as invalid only when non-existence can be established;
- retrieve the cited text and show the passages most relevant to the user's claim;
- use a small Swedish-capable model in the browser to triage semantic support;
- keep original files and model inference on the user's device, while clearly disclosing text transfer to lagen.nu;
- produce a reviewable report with links and quoted evidence.

## 4. Non-goals

The MVP will not:

- decide whether a legal conclusion is correct;
- replace source review by a lawyer;
- call a server-side LLM or send document text to a model API;
- label a person or document as having “hallucinated”;
- guarantee semantic analysis for every kind of citation or legal proposition;
- provide full OCR quality for poor scans in the first release;
- edit or rewrite the uploaded document.

## 5. Users and key use cases

Primary users are lawyers, judges, clerks, researchers, journalists, students, and editors reviewing Swedish legal material.

Key use cases:

- A lawyer checks a draft brief before filing.
- An editor checks citations in an AI-assisted article.
- A student verifies sources in an essay.
- A reviewer triages a long document and opens only the citations needing attention.

## 6. User experience

### 6.1 Entry

The landing screen offers:

- a large text area;
- drag-and-drop or file selection for `.docx` and `.pdf`;
- a short privacy statement: “Your browser sends extracted text to lagen.nu to find citations. Your text is processed only in temporary memory. It is discarded after processing, never saved, and never forwarded elsewhere. Results are returned only to you. Your original file stays on your device.”;
- a **Check citations** action.

### 6.2 Processing

The UI reports progress in plain stages:

1. Reading document
2. Finding citations through lagen.nu
3. Checking sources
4. Comparing claims with sources

The user may begin reviewing completed results while later citations are still processing. The semantic model is downloaded only when at least one resolvable citation can be analysed.

### 6.3 Results

The report has one row/card per citation and keeps two findings separate:

| Dimension | Possible result | Meaning |
|---|---|---|
| Source | Found | lagen.nu resolved the cited authority. |
| Source | Invalid citation | Non-existence can be established from complete coverage or an impossible year. |
| Source | Unconfirmed | The citation was recognised but available coverage cannot settle existence. |
| Claim | Supported | The cited text appears to support the nearby proposition. |
| Claim | Possible contradiction | The cited text contains evidence that may conflict with the proposition. |
| Claim | Support not found | Relevant passages were searched, but support was not located. This is not proof that the proposition is false. |
| Claim | Could not assess | The claim or source could not be analysed reliably. |

Each expanded result shows:

- the citation exactly as it appeared;
- the surrounding sentence or extracted claim;
- the best matching passage or passages from the source;
- a direct link to the source on lagen.nu;
- a short explanation of the label and its limitations.

Filters allow the user to show all results, invalid citations, semantic review items, or unassessed items. A summary reports counts without presenting a single “hallucination score.”

## 7. Functional requirements

### 7.1 Input and local extraction

- Pasted text is accepted directly.
- DOCX text is extracted locally, preserving paragraphs, headings, tables, footnotes, and endnotes where practical.
- Text-bearing PDFs are parsed locally with page references retained.
- Image-only or substantially scanned PDFs are detected. The MVP explains that OCR is required rather than silently returning no citations. Local OCR is a follow-up capability.
- Default limits: 25 MB, 300 PDF pages, and 250,000 extracted characters. Limits must be visible before processing.
- The UI provides a preview so the user can confirm that extraction succeeded.

### 7.2 Citation detection

- The browser sends extracted text to `POST /api/v1/citations/extract`.
- Pasted text uses `text`. DOCX and PDF inputs use ordered `blocks`, each with a unique id and text.
- The API reuses Ferenda's citation grammar, names and official identifier datasets.
- The scope includes Swedish reports, statutes and agency regulations; EU cases and acts; international cases and treaties.
- The API retains repeated occurrences, contextual references, and candidates whose target cannot be determined.
- One occurrence may have several targets. Recognition does not establish existence.
- The browser sends each distinct target URI to `/api/v1/resolve`. It does not reparse a contextual reference as an isolated query.
- The report retains document positions through block ids and UTF-16 character offsets. A citation crossing blocks has several locations.
- The client supplies blocks in reading order. PDF columns, headers and footnotes need correct text extraction before citation scanning.
- Arbitrary request chunks do not share parser context. Each request contains one document or an independently interpretable section.
- The UI discloses which source series have authoritative absence checks. Unknown targets remain unconfirmed.

### 7.3 Deterministic validation

- A citation returned as a normal resolver result is shown as **Found**.
- A recognised citation carrying `invalid: true` is shown as **Invalid citation**.
- A recognised but unresolved citation without that flag is shown as **Unconfirmed**.
- The SPA may show a non-authoritative warning for unusual patterns such as a page above 2,000, but it must not convert a heuristic into “Invalid citation.”
- The same normalised citation must receive the same result throughout a run.

### 7.4 Claim extraction

For each citation, the client constructs a candidate claim from:

- the sentence containing the citation;
- the preceding sentence when the citation appears alone or in a footnote; and
- limited rule-based handling of signals such as `se`, `jfr`, `enligt`, `se även`, parentheses, and semicolon-separated propositions.

Bibliographies, tables of authorities, and citation-only lists are not semantically assessed. If no reasonably bounded proposition can be isolated, the result is **Could not assess**.

### 7.5 Source retrieval and evidence selection

- Resolved sources are fetched from lagen.nu through the document endpoint, preferably as Markdown.
- Statutory citations should target the exact provision where available.
- Judgments are split into overlapping passages of roughly 2–5 paragraphs, capped around 350 model tokens.
- Lightweight lexical ranking in JavaScript selects the 5–10 passages most relevant to the extracted claim.
- An exact-quote check runs before model inference, with normalisation for whitespace, quotation marks, line breaks, hyphenation, and modest OCR noise.
- Only the top passages are submitted to the local inference model.

### 7.6 Browser-side semantic analysis

- No server-side LLM is used.
- The proposed first model is `alexandrainst/scandi-nli-small`, exported to ONNX and quantised to 8-bit. Target download size is approximately 20–30 MB plus tokenizer assets.
- Inference runs through Transformers.js or ONNX Runtime Web, using WebGPU where supported and WASM as fallback.
- The source passage is the NLI premise; the extracted proposition is the hypothesis.
- Model outputs are mapped to the four claim labels using thresholds calibrated on Swedish legal examples.
- Low-confidence, conflicting, excessively long, or badly extracted inputs abstain as **Could not assess** or **Support not found**.
- The model and tokenizer are cached locally after first use. The UI discloses first-run download size.

The model is a triage mechanism, not the source of truth. Every non-neutral semantic label must be accompanied by visible source evidence.

### 7.7 Export

The MVP allows the report to be printed or saved as PDF using the browser. A later release may add structured JSON/CSV export.

## 8. Lagen.nu API integration

### 8.1 Citation extraction

`POST /api/v1/citations/extract` accepts JSON. It does not accept original PDF or DOCX files.

```json
{"text": "Se NJA 2013 s. 372."}
```

The response retains the source text and its locations:

```json
{
  "offset_unit": "utf-16",
  "occurrences": [{
    "text": "NJA 2013 s. 372",
    "locations": [{"block_id": "text", "start": 3, "end": 18}],
    "targets": [{"uri": "https://lagen.nu/dom/nja/2013s372", "source": "dv"}]
  }]
}
```

Alternatively, send `blocks: [{"id": "page-1", "text": "..."}]`.
Send exactly one of `text` or `blocks`. Undated law names use the current name datasets, as `/resolve` does.
Offsets count UTF-16 units, with an inclusive start and exclusive end, within the original block text.
The API joins adjacent blocks with a newline and retains all locations for citations crossing that boundary.

An empty `targets` list means the candidate has no determined target. It does not mean the citation is invalid.
The publication grammar may report the printed endpoints of a range separately; clients must not invent omitted targets.
The API does not correct OCR errors or infer that an empty extraction proves a document contains no citations.

Limits are 250,000 Unicode characters, 5,000 blocks and 2,000,000 JSON body bytes.
Oversized bodies return 413, unsupported media types return 415, and invalid JSON or fields return 422.
Responses use `Cache-Control: no-store`. Validation errors omit submitted field values.

### 8.2 Citation resolution

Extend the existing `GET /api/v1/resolve?q=…` response without changing current `results` behaviour.

When a citation is recognised, not found, and can be proven not to exist, add one optional boolean to its existing `recognized` item:

```json
{
  "query": "NJA 2013 s. 372",
  "results": [],
  "recognized": [
    {
      "uri": "https://lagen.nu/dom/nja/2013s372",
      "source": "dv",
      "invalid": true
    }
  ]
}
```

That is the resolution response's schema extension. The separate extraction endpoint has the occurrence schema above.
Do not return coverage ranges, confidence scores, explanations, or heuristic warnings per resolution result.

### 8.3 Resolution semantics

- `results` contains a match: the citation exists in lagen.nu.
- `recognized[].invalid === true`: lagen.nu can establish that the citation is invalid.
- recognised but no result and no `invalid` field: existence is unknown from the available data.

`invalid` must be emitted only for deterministic rules, initially:

- the cited NJA year is within a configured complete-coverage interval and no corresponding reference exists;
- the cited year predates the publication series;
- the cited year is later than the current year;
- another structurally impossible value is detected, such as a non-positive page number.

An unusually high but possible page number is not deterministic and must not set `invalid: true`.

Coverage rules are maintained server-side as configuration and documented for API consumers. They can be updated without changing the response shape. For the initial NJA rule, the known complete interval is 1981–2025; the application must not assume that 2026 is complete until the configuration is updated.

### 8.4 Compatibility and operational requirements

- Existing clients that ignore unknown JSON fields continue to work.
- Invalid citations still return HTTP 200 because the request succeeded.
- Public CORS supports GET and the extraction endpoint's POST requests, including JSON preflight requests.
- Resolver responses should be cacheable; coverage configuration changes must invalidate affected cached responses.
- Boundary tests cover 1873/1874, 1980/1981, 2025/2026, the current year, future years, found references, and absent references.

## 9. Technical shape of the SPA

The product can be deployed as static assets with no application backend.

Suggested components:

- PDF.js for browser PDF extraction;
- Mammoth.js or equivalent for DOCX extraction;
- a client for API citation extraction and a local rule-based claim extractor;
- lagen.nu citation extraction, resolve and document endpoints;
- a compact in-browser lexical index;
- Transformers.js or ONNX Runtime Web for NLI;
- IndexedDB/Cache Storage for model and source caching;
- a Web Worker for parsing, ranking, and inference so the interface remains responsive.

Source requests should be deduplicated, concurrency-limited, cancellable, and retried only for transient failures. A service worker is optional; it must not retain user document text beyond the session unless the user explicitly chooses to save locally.

## 10. Privacy, security, and trust

- The browser holds document text for the session and sends extracted text to lagen.nu for citation extraction.
- The UI states this transfer before processing. It must not claim that all document content stays on-device.
- lagen.nu processes submitted text only in temporary memory. It discards the text after processing, never saves it, and never forwards it elsewhere. Results return only to the requesting client.
- Deployment must keep request and response bodies out of disk buffers, caches, logs, tracing and error reports. Ordinary access metadata may still be recorded.
- Extraction responses use `no-store`; the service worker must not cache them.
- Analytics must never contain document text, citations, extracted claims, filenames, or source passages. Product analytics should be optional and limited to aggregate performance/error events.
- Uploaded files are treated as untrusted. The app does not execute embedded content or macros and sanitises all rendered text.
- External links use safe opener settings.
- The UI clearly distinguishes facts returned by lagen.nu from browser-generated assessments.
- The service displays model version, coverage date, and a concise limitation notice.

## 11. Performance and browser support

Target modern evergreen Chrome, Edge, Firefox, and Safari on desktop. Mobile may permit pasted text but is not an MVP optimisation target.

Targets on a typical recent laptop:

- interactive UI throughout processing;
- target citation-list latency is 2 seconds after text extraction for a normal document, including network and API processing;
- initial model payload below 30 MB;
- cached semantic analysis of 10 citations completes within 15 seconds where WebGPU is available;
- graceful WASM fallback and explicit abstention if memory or model loading fails.

## 12. Accessibility and language

- Swedish is the initial interface language.
- Status is conveyed by text and icon as well as colour.
- Keyboard navigation, visible focus, screen-reader labels, and WCAG 2.2 AA contrast are required.
- Evidence passages and source links remain usable without the semantic model.

## 13. Quality and acceptance criteria

### API

- Extraction returns original occurrence text, UTF-16 locations and interpreted targets for supported citation families.
- Repeated citations remain separate occurrences. A shared span can have several targets.
- Context continues across blocks in one request and never leaks between requests.
- Tests cover PDF page splits, DOCX paragraph/footnote blocks, malformed citations and input limits.
- Extraction alone never marks a target found or invalid.
- No regression in existing resolver responses.
- Every missing NJA citation inside configured complete coverage is marked `invalid: true`.
- A missing citation outside complete coverage is never marked invalid solely because it is absent.
- Impossible past/future years are marked invalid.
- Heuristics such as high page numbers never produce an authoritative invalid flag.

### SPA

- Pasted text and representative DOCX/text-PDF fixtures produce stable text and citation locations.
- Target at least 95% recall for supported citation forms in a curated API extraction set, with false positives below 2%.
- Every result displays the original citation and its document location.
- Every semantic warning displays the extracted claim and at least one source passage, or clearly states why evidence is unavailable.
- Document text is sent only to lagen.nu's extraction endpoint, processed temporarily in memory, then discarded. It is never saved or forwarded elsewhere.
- A corrupted or unsupported file fails safely with a useful message.

Before semantic labels are treated as more than an experimental feature, assemble a hand-labelled Swedish legal evaluation set covering accurate support, missing qualifications, wrong party/court attribution, changed outcomes, direct contradictions, neutral mentions, and genuinely unassessable claims. The release threshold should prioritise precision and abstention: a false accusation of contradiction is costlier than sending an item to manual review.

## 14. Delivery phases

### Phase 0 — API and evaluation fixtures

- implement the citation extraction endpoint and its occurrence/offset fixtures;
- implement the optional `invalid` flag;
- add coverage configuration and boundary tests;
- assemble real and synthetic citation fixtures;
- establish a labelled semantic evaluation set.

### Phase 1 — Useful deterministic product

- pasted text, DOCX, and text-PDF extraction;
- API citation extraction and source validation across the supported families;
- source links, document locations, filters, and printable report;
- exact-quote matching and relevant-passage display;
- no probabilistic label needed to ship this phase.

### Phase 2 — Local semantic triage

- quantised browser NLI model;
- claim extraction, passage ranking, evidence display, and abstention;
- public “experimental” label until evaluation thresholds are met.

### Phase 3 — Coverage and input expansion

- additional citation forms and source-specific coverage rules;
- local OCR for scanned PDFs, loaded only when needed;
- improved handling of footnotes and propositions spanning multiple sentences;
- optional local report persistence and structured export.

## 15. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Users interpret NLI output as a legal conclusion | Use review-oriented labels, separate existence from meaning, show evidence, and abstain aggressively. |
| A real case uses wording different from the user's claim | Rank several passages; treat lack of lexical/model support as “not found,” not false. |
| The relevant rule appears in another cited source | Analyse each citation independently and show claim context; do not infer that one citation must carry the whole proposition. |
| PDF/DOCX extraction damages sentence boundaries | Show extraction preview, retain page/paragraph positions, and allow pasted-text fallback. |
| Browser model is too slow or unavailable | Lazy-load, quantise, run in a worker, use WebGPU/WASM fallback, and preserve deterministic checking without the model. |
| Coverage becomes stale | Keep coverage server-side and operationally owned alongside corpus ingestion. |
| Source/API outage | Preserve the locally extracted text and completed results. Show a retryable status; never classify a network failure as an invalid citation. |
| Sensitive document text leaves the device | State the transfer before processing. Keep text only in temporary memory on lagen.nu, discard it after processing, and never save or forward it. |

## 16. Open product decisions

- Which additional citation forms need fixtures, and for which source periods can absence be authoritative?
- Should local OCR be part of the public MVP or a clearly marked follow-up?
- What labelled dataset and review process will govern semantic thresholds?
- Should reports be session-only, downloadable, or optionally stored in browser local storage?
- Is public anonymous use acceptable, or is rate protection needed at the lagen.nu edge?

## 17. Brand-name directions

Availability, domains, and trademarks have not been checked.

| Name | Character | Possible tagline |
|---|---|---|
| **Belägg** | Short, professional, evidence-focused, and broader than citation existence. Recommended. | *Kontrollera att juridiska hänvisningar håller.* |
| **Källkoll** | Immediately understandable and friendly; less specifically legal. | *Snabb kontroll av källa och stöd.* |
| **RättBelägg** | Clearly legal and evidence-oriented, with a useful double meaning. | *Rätt källa för rätt påstående.* |
| **Citatkoll** | Very descriptive and accessible; slightly less distinctive. | *Finns källan, och säger den det?* |
| **Hänvisad** | Polished and brandable, though the function is less obvious without a tagline. | *Granska juridiska hänvisningar i sitt sammanhang.* |
| **Lagfäste** | Distinctive metaphor for grounding a claim in law. | *Ge juridiska påståenden fäste.* |
| **Källspår** | Emphasises traceability and human review. | *Följ påståendet tillbaka till källan.* |
| **Rättvisaren** | Memorable wordplay: legal guidance and a pointer to the source. | *Visar vägen till rätt stöd.* |
| **Domstöd** | Compact and clearly judgment-oriented, but narrower than the eventual product. | *Stämmer påståendet med avgörandet?* |
| **Referensvakten** | Trustworthy and explicit, but more institutional in tone. | *Upptäck tveksamma juridiska referenser.* |

**Recommendation:** use **Belägg** as the working product name. It describes what the product actually supplies—evidence for review—without claiming to determine truth or accusing the author of hallucination. **RättBelägg** is the strongest more explicitly legal alternative, while **Källkoll** is the clearest consumer-facing option.

## 18. Reference implementation inputs

- lagen.nu API documentation: <https://lagen.nu/docs>
- API schema: <https://lagen.nu/openapi.json>
- Proposed compact NLI model: <https://huggingface.co/alexandrainst/scandi-nli-small>
