# Product Requirements Document (PRD): Slopcheck Privacy Mode & lagen.nu Range/Packs API

*Date: September 2026*  
*Status: Revised following backend review — Ready for implementation*  
*Authors: staffanm / Slopcheck & lagen.nu Engineering*

---

## 1. Executive Summary & Vision

Slopcheck checks citations and legal assertions against Swedish and EU legal sources published on **[lagen.nu](https://lagen.nu)**. 

Currently, users have two extraction paths (server-side via `/citations/extract` or client-side via in-browser `LagrumParser`). However, even with local extraction, verifying citation validity via `/api/v1/resolve?q=...` and retrieving source text via `/api/v1/document?uri=...` exposes the exact legal citations to server logs or network observers. In confidential matters (arbitration briefs, M&A contracts, criminal defense, whistleblower disclosures), revealing which specific statutes and court cases are being reviewed creates unacceptable data leakage.

This PRD defines a **Zero-Knowledge Privacy Mode** for Slopcheck and the accompanying backend API endpoints on `lagen.nu`, establishing a formal architectural boundary between **Normal Mode** (server-assisted, feature-rich) and **Privacy Mode** (provable zero-knowledge, decoupled from user identity).

---

## 2. System Architecture: Two Operating Modes

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                NORMAL MODE                                  │
│                                                                             │
│  User Text ──> /citations/extract ──> /resolve?q=... ──> /document?uri=... │
│                          (Server-side pipeline)                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                          PRIVACY MODE (Zero-Knowledge)                      │
│                                                                             │
│  User Text ──> In-Browser LagrumParser (Web Worker, text stays on device)   │
│                     │                                                       │
│                     ▼                                                       │
│         [Opaque Root-Hashed Query]                                          │
│                     │                                                       │
│                     ▼ (via Oblivious HTTP Relay)                            │
│           GET /api/v1/range/{root_prefix}  (k-anonymity ~ 107 docs)         │
│                     │                                                       │
│                     ▼                                                       │
│     [Local Document Cache: Core Statute Pack (~4 MB in CacheStorage)]       │
│                     │                                                       │
│                     ├── Cache Hit: 0 network requests (80-90% of citations) │
│                     │                                                       │
│                     └── Cache Miss: GET /api/v1/packs/{pack_id} (via OHTTP) │
│                                                                             │
│                     ▼                                                       │
│     In-Browser ONNX NLI Comparison (scandi-nli-small via WebAssembly)       │
└─────────────────────────────────────────────────────────────────────────────┘
```

| Dimension | Normal Mode | Privacy Mode |
|---|---|---|
| **Text Processing** | Ephemeral server extraction (`POST /citations/extract`) | 100% on-device (WASM / Web Worker `LagrumParser`) |
| **Citation Verification** | Exact URI lookup (`GET /resolve?q=...`) | $k$-anonymity root-prefixed bucket (`GET /range/{prefix}`) |
| **Document Retrieval** | Direct single-document (`GET /document?uri=...`) | Core Statute Pack (~200 laws offline) + Volume Packs (`/packs/{id}`) |
| **Network Transport** | Direct HTTPS | Oblivious HTTP (OHTTP) via Cloudflare Privacy Gateway |
| **Semantic Claim Check** | Large server-side LLM / cross-encoder | Small in-browser ONNX model (`scandi-nli-small`) |
| **Trust Model** | Trust server not to retain logs | Cryptographic zero-knowledge; no single party sees both IP and query |

---

## 3. Endpoint Specification: `/api/v1/range/{prefix}`

The range endpoint allows clients to verify whether a citation URI (both root document and specific pinpoint fragment) exists in `lagen.nu`'s corpus in a **single round-trip** without disclosing which citation is being queried.

### 3.1 Hashing & Root-Prefix Protocol (Eliminating Cross-Bucket Correlation)
A critical vulnerability of independent pinpoint hashing is that querying a pinpoint bucket and a root bucket simultaneously leaks the exact citation:
$$\frac{14.7\text{M anchors}}{65,536^2} \approx 0.003\text{ candidate pairs}$$
An adversary would correlate the two buckets instantly.

**The Solution: Co-locating the Document and All Its Anchors**
The bucket prefix is derived exclusively from the **root URI**:
1. **Root URI Extraction:** The client extracts the parent document URI (stripping the fragment):
   - `https://lagen.nu/1915:218#P3a` $\rightarrow$ root `https://lagen.nu/1915:218`
   - `https://lagen.nu/celex/32016R0679#32.1` $\rightarrow$ root `https://lagen.nu/celex/32016R0679`
   - `https://lagen.nu/dom/nja/2013s502` $\rightarrow$ root `https://lagen.nu/dom/nja/2013s502`
2. **Exact Case Preservation (No Lowercase Folding):**
   - Do NOT lowercase the path. 233,907 of 437,347 document URIs contain uppercase characters, and four pairs collide if lowercased (e.g. `bet/1980/81:KU25` vs `bet/1980/81:ku25`).
   - Hash the exact canonical URI as returned by the API and minted by the parser.
3. **Hash Calculation:**
   - `root_hash = sha256(root_uri).hexdigest()` (64 hex characters)
   - `target_hash = sha256(target_uri).hexdigest()` (for pinpoint or root)
4. **Partitioning:**
   - **Prefix:** First **3 hex characters** (12 bits, $16^3 = 4,096$ buckets) of `root_hash`.
   - **Suffix:** Truncated to **16 hex characters (8 bytes / 64 bits)** of `target_hash`.
5. **Collision Resistance:**
   - With ~3,600 anchors per bucket, the probability of an accidental collision with a 64-bit suffix is:
     $$\frac{3,600}{2^{64}} \approx 1.95 \times 10^{-16}$$
   - Truncation to 16 hex characters is mathematically collision-free for this corpus while halving payload size.

### 3.2 Corpus Size & Bucket Density
*   **Total Documents:** 437,347
*   **Total Citable Anchors:** ~14.7 million (including all statutory sections, EU articles/recitals, and court anchors)
*   **Total Buckets:** 4,096
*   **Averages per Bucket:**
    *   **~107 parent documents** per bucket ($k \approx 107$).
    *   **~3,600 total anchors** per bucket.
*   **Uncompressed Payload:** $3,600 \times 17\text{ bytes (16 hex + newline)} \approx \mathbf{61\text{ KB}}$ (or packed raw binary: $3,600 \times 8\text{ bytes} \approx \mathbf{28.8\text{ KB}}$).
*   **Gzipped Payload:** $\approx \mathbf{12\text{ to 14 KB}}$.

### 3.3 Backend Index Construction
*   The `catalog.sqlite` database has no anchor table. The range index is generated during the `relate` build phase by walking all artifacts on disk and writing a binary sidecar file (`range-index.bin`).
*   **EU Anchor Ingestion:** EU anchors are not stored exclusively in artifact `body_id_nodes`. For example, GDPR publishes 1,452 anchors, but 1,324 of them (including `#32.1`) exist only in `eu_structure.anchored_blocks`. The indexing script reads **both** `body_id_nodes` and `eu_structure.anchored_blocks`.
*   **Scope of Legal Validity:** The index represents the **current in-force wording** of laws. A citation to a historically repealed provision will be reported as absent.

### 3.4 Bucket Size Padding (Defeating Traffic Analysis)
Bucket populations vary (e.g. from 2,000 to 5,500 anchors). If responses were unpadded or padded only to 256 bytes, the size distribution would leak roughly 60 distinct size classes to an eavesdropper.
*   **Contract:** Every range response is padded with trailing whitespace/zeros to the **maximum bucket size in the corpus** (constant upper bound, e.g. exactly 64 KB or 70 KB).
*   **Result:** Every single range response across all 4,096 buckets has the **exact same byte length**, completely eliminating side-channel size leakage.

### 3.5 Request
```http
GET /api/v1/range/{prefix} HTTP/1.1
Host: lagen.nu
Accept: text/plain, application/json
```
- `{prefix}`: 3-character hex string (`[0-9a-f]{3}`).

### 3.6 Response
Line-delimited 16-hex-character suffixes belonging to any document whose root URI matches the 3-hex prefix:
```text
0012f9b87c20a149
01948ba278c001f3
0238e0192a47ff11
...
[padding to fixed maximum bucket size]
```

### 3.7 Single-Query Decision Logic
A single GET to `/range/{root_prefix}` returns the bucket containing both the root and all its provisions:
1. Does the bucket contain `root_hash[:16]` (first 16 hex characters / 8 bytes)?
   - **No:** The parent document is missing. If the series is closed (e.g. NJA 1981–2025), status is **`invalid`**; otherwise **`unconfirmed`**.
2. Does the bucket contain `target_hash[:16]`?
   - **Yes:** Both root and pinpoint exist $\rightarrow$ **`found`**.
   - **No (and root was found):** The document exists in `lagen.nu`, but that pinpoint does not $\rightarrow$ **`invalid`** (*"Bestämmelsen saknas i författningen"*).

---

## 4. Endpoint Specification: `/api/v1/packs/{pack_id}`

To prevent leaking specific document identities during the semantic text retrieval phase, documents are bundled into **coarse static packs**.

### 4.1 Pack Hierarchy & Naming
1. **`core` (Core Statute Pack):**
   - The **200 statutes with the most inbound citation rows** in `catalog.sqlite` `links` (covers ~80–90% of all real-world statutory citations).
   - Pre-cached by Slopcheck during client initialization.
2. **Statutes (`sfs/{decade}s`):**
   - e.g. `sfs/2020s`, `sfs/2010s`, `sfs/1970s`. (Largest SFS decade is ~11 MB Brotli).
3. **Court Case Volumes:**
   - `nja/{5yr_block}` (e.g. `nja/2020-2024`, `nja/2010-2014` ~5 MB).
   - `dom/hfd/{5yr_block}`, `dom/ad/{5yr_block}`, `dom/rh/{5yr_block}`, `dom/echr/{5yr_block}`.
4. **EU Acquis:**
   - `celex/{year}` (e.g. `celex/2016` ~13.5 MB, covering regulations, directives, decisions, and sector 6 ECJ rulings).
5. **Förarbeten:**
   - `prop/{5yr_block}`, `sou/{decade}s`, `ds/{decade}s`.

### 4.2 Deterministic Client-Side Mapping
The client determines which pack to request without asking the server:
- Is URI in the `core` list? $\rightarrow$ Local `core` pack (0 network calls).
- `https://lagen.nu/{year}:{number}` $\rightarrow$ `sfs/{decade}s`
- `https://lagen.nu/dom/nja/{year}s{page}` $\rightarrow$ `nja/{year_5yr_block}`
- `https://lagen.nu/dom/{court}/{year}...` $\rightarrow$ `dom/{court}/{year_5yr_block}`
- `https://lagen.nu/celex/{sector}{year}...` $\rightarrow$ `celex/{year}`
- `https://lagen.nu/prop/{year_range}...` $\rightarrow$ `prop/{year_5yr_block}`

### 4.3 Markdown with Anchor Maps
Standard `mdtext.document_markdown` writes section titles like `**1 §**` with no ID, making it impossible for the client to reliably locate pinpoints like `#P3a` or `#32.1`.

**Contract:** Every document in a pack includes an **anchor map** beside the markdown:
```json
{
  "pack": "sfs/1910s",
  "version": "2026-09-15",
  "documents": {
    "https://lagen.nu/1915:218": {
      "title": "Lag (1915:218) om avtal och andra rättshandlingar på förmögenhetsrättens område",
      "markdown": "# Lag (1915:218) om avtal...\n\n## 1 kap. Om slutande av avtal\n\n**1 §** Anbud om slutande av avtal...",
      "anchors": {
        "P1": [124, 450],
        "P2": [451, 780],
        "P3": [781, 1020],
        "K1": [35, 1020]
      }
    }
  }
}
```
The `anchors` map provides character offsets `[start, end]` into `markdown`, allowing Slopcheck to slice the exact provision text in 0 ms for semantic NLI evaluation.

### 4.4 Relay Visibility Threat Model
Because volume packs are 5–13.5 MB, padding them to a uniform size is impractical. The Relay can observe that a pack of ~11 MB was downloaded, identifying that the user requested an SFS decade pack.
*   **Privacy Guarantee:** A decade pack contains hundreds or thousands of statutes. The Relay learns the broad *era*, but has zero knowledge of which specific statute or paragraph inside that volume is being analyzed.

---

## 5. Oblivious HTTP (OHTTP) Specification (RFC 9458)

For network calls in Privacy Mode (`/range` queries and volume `/packs`), Slopcheck uses **Oblivious HTTP ([RFC 9458](https://www.rfc-editor.org/rfc/rfc9458))** via Cloudflare Privacy Gateway.

### 5.1 Roles and Trust Model
- **Relay (Cloudflare Privacy Gateway):** Sees user IP and destination host (`lagen.nu`). Cannot decrypt the inner request or response.
- **Gateway (`lagen.nu`):** Sees the decrypted inner request and Cloudflare's egress IP. Cannot see the user's IP.
- **Non-Collusion:** Cloudflare and `lagen.nu` are independent entities with no shared logging infrastructure.

### 5.2 Key Discovery Endpoint: `GET /api/v1/ohttp-keys`

#### HTTP Contract
```http
GET /api/v1/ohttp-keys HTTP/1.1
Host: lagen.nu
Accept: application/ohttp-keys
```

#### Response Headers
```http
HTTP/1.1 200 OK
Content-Type: application/ohttp-keys
Cache-Control: public, max-age=604800, s-maxage=604800, stale-while-revalidate=86400
ETag: "ohttp-2026-09-v1"
```

#### Binary Wire Format (RFC 9458 §3 / RFC 9180)
The response is prefixed with a 2-byte total length, followed by length-prefixed `KeyConfig` structures:

```text
+-------------------------------------------------------+
| Total Length (uint16_t, 2 bytes)                      |
+-------------------------------------------------------+
| KeyConfig Length (uint16_t, 2 bytes)                  |
+-------------------------------------------------------+
| Key ID (uint8_t, 1 byte, e.g. 0x01)                   |
+-------------------------------------------------------+
| KEM ID (uint16_t, 2 bytes, 0x0020 = DHKEM(X25519))    |
+-------------------------------------------------------+
| Public Key (opaque[32], 32 bytes raw X25519 key)      |
+-------------------------------------------------------+
| Symmetric Algorithms Length (uint16_t, 2 bytes)       |
+-------------------------------------------------------+
| KDF ID (uint16_t, 2 bytes, 0x0001 = HKDF-SHA256)      |
+-------------------------------------------------------+
| AEAD ID (uint16_t, 2 bytes, 0x0001 = AES-128-GCM)     |
+-------------------------------------------------------+
```

### 5.3 Gateway Termination Endpoint: `POST /api/v1/ohttp-gateway`

#### HTTP Contract
```http
POST /api/v1/ohttp-gateway HTTP/1.1
Host: lagen.nu
Content-Type: message/ohttp-req
Content-Length: [byte-length]

[Binary Encapsulated Request]
```

#### HPKE Request Decapsulation (RFC 9458 §4.1)
The payload starts with a 7-byte request header:
`header = key_id (1B) || kem_id (2B) || kdf_id (2B) || aead_id (2B)`

Per RFC 9458 Section 4.1, the HPKE `info` parameter MUST be:
$$\text{info} = \text{"message/bhttp request"} \parallel 0\text{x}00 \parallel \text{header}$$
The server decapsulates the HPKE context:
```python
context_r = hpke.setup_base_r(enc, server_private_key, info)
bhttp_request = context_r.open(aad="", ciphertext)
```

#### Response Key Derivation (RFC 9458 §4.2)
Standard Python `cryptography` v50 `hpke.Suite` implements only `seal` and `open` and lacks secret export.
*   **Backend Implementation Requirement:** The backend MUST use a library supporting HPKE secret export (e.g. `pyohttp` / `rust-ohttp` bindings or low-level export):
$$\text{secret} = \text{context\_r.export}(\text{"message/bhttp response"}, \text{secret\_length})$$
The response is encapsulated using this exported secret.

#### Gateway Route Restriction & In-Process Dispatch
To ensure in-process dispatch does not bypass Nginx rate limiting or the facsimile render gate:
*   **Strict Two-Prefix Scope:** The Gateway ONLY accepts:
    - `/api/v1/range/*`
    - `/api/v1/packs/*`
*   Any other path (including `/internal-api/*` and the ops dashboard) is rejected with `HTTP 403 Forbidden`.
*   Method MUST be `GET` or `HEAD` (any mutation returns `HTTP 405`).
*   Sanitize all incoming headers (`X-Forwarded-*`, cookies, auth).

---

## 6. Summary of Changes from Initial Draft

1. **Prefix Source:** Shifted from `sha256(target_uri)[:4]` to `sha256(root_uri)[:3]`. Roots and all their provisions now live in the same bucket, eliminating cross-bucket correlation attacks in a single round-trip.
2. **Suffix Length:** Truncated to 16 hex characters (8 bytes / 64 bits), reducing bucket size while maintaining collision safety ($P \approx 10^{-17}$).
3. **Range Response Padding:** Every range response is padded to the maximum bucket size to prevent size-class leakage.
4. **Anchor Mapping in Packs:** Added `anchors: { id: [start, end] }` offset maps alongside document markdown.
5. **RFC 9458 Compliance:** Fixed HPKE `info` parameter and `application/ohttp-keys` length framing.
6. **Backend Crypto Note:** Documented the requirement for HPKE secret export (`pyohttp`).
7. **Gateway Scoping:** Strictly restricted in-process dispatch to `/range/*` and `/packs/*`.
