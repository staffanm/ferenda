# PRD — definite-form subdomains (`avtals.lagen.nu`)

Status: **certbot side done, nginx/generation not started** · Owner: Staffan
· Scope: the prod nginx front (`docker/nginx/`), the `certbot` service in
`docker-compose.yml` (done 2026-08-31: wildcard DNS-01 lineage alongside the
existing webroot one, see `docker/certbot/`), one new top-level module in
`ferenda/`, one line in `ferenda/lib/templates/page.html`.

(This PRD referred to the package as `accommodanda/` throughout; the active
code lives in `ferenda/` — see `CLAUDE.md`. Every path below is corrected.)

Four domains as of 2026-08-30: `lagen.nu`, `direktivet.nu`, `förordningen.nu`
(A-label `xn--frordningen-rfb.nu`) and its diacritic-free twin
`forordningen.nu`. All four are registered at NuNames.NU and all four delegate
to `instant1.dnsmaster.io` / `instant2.dnsmaster.io`.

## 1. The idea

A Swedish law's definite form is how people name it. Give every act that has
one its own host, and let that host **show** the document rather than
redirect to it.

| Host | Shows |
| --- | --- |
| `avtals.lagen.nu` | the whole of `/1915:218` |
| `upphovsrätts.lagen.nu` | the whole of `/1960:729` |
| `hyres.lagen.nu` | 12 kap. of `/1970:994`, with a hand-written introduction |
| `samtyckes.lagen.nu` | 6 kap. 1 § of `/1962:700` |
| `jante.lagen.nu` | Jantelagen — an easter egg, not Swedish law |
| `kamomilla.lagen.nu` | Kamomilla stad — an easter egg, not Swedish law |
| `tryckfrihets.förordningen.nu` | the whole of `/1949:105` |
| `dataskydds.förordningen.nu` | the whole of the GDPR, `32016R0679` |
| `nis2.direktivet.nu` | the whole of the NIS2 directive, `32022L2555` |
| `cer.direktivet.nu` | the whole of the CER directive, `32022L2557` |

The three zones do the same job for three kinds of document: `lagen.nu` for a
`lag`, `förordningen.nu` for a Swedish `förordning` **and** an EU regulation,
`direktivet.nu` for an EU directive. The reader's own word for the document
picks the zone.

**O2, decided — the naming rule is generated, not a hand-picked list.** Two
curated tables the citation engine already reads carry a `label` and often an
`abbr` for a well-known act: `ferenda/sfs/data/namedlaws.json` (321 entries,
e.g. `"1915:218": {"label": "avtalslagen", "abbr": "AvtL"}`) and
`ferenda/eurlex/data/namedacts.json` (30 entries, e.g. `"32016R0679":
{"abbr": "GDPR", "label": ["Dataskyddsförordningen"]}`). A subdomain slug
comes from walking both tables:

- `label` ends in `lagen` → `lagen.nu`, slug = the label with that suffix cut
  (`avtalslagen` → `avtals`).
- `label` ends in `förordningen` → `förordningen.nu`, same cut. This is how
  both a Swedish `förordning` (`tryckfrihetsförordningen`) and an EU
  regulation whose curated label happens to end the same way
  (`Dataskyddsförordningen`) land in the same zone.
- An EU directive's curated `abbr` (`NIS2`, `CER`) → `direktivet.nu`, slug =
  the abbreviation, lowercased.
- An SFS act with `rpubl:upphavandedatum` set (repealed) is excluded — only
  gällande rätt gets a front door.

A nickname that is not the act's own populärnamn — `hyreslagen` names 12 kap.
of Jordabalken, not an act titled Jordabalken — cannot be derived this way,
and stays a short, explicitly named exception (section 6). So does `jante`,
which names no act at all.

The reader types a name they already know and lands on the law. Nothing about
the corpus changes.

## 2. What a subdomain serves

Exactly one page. `https://avtals.lagen.nu/` shows the act's page with its own
content, its own URL in the address bar, and no redirect.

**O3, decided — `<base href>` to the apex.** The landing page carries
`<base href="https://ferenda.lagen.nu/">` (swapped to `https://lagen.nu/` at
the September cutover, alongside the other CUTOVER edits in
`docker-compose.yml`). Every root-relative link and asset reference the
templates already emit (`href="/1960:729"`, `href="/style.css"` —
`ferenda/lib/templates/page.html`) then resolves straight to the apex, in the
browser, before any request is made. `<base>` only changes how a *relative*
URL found in the document resolves; it has no TLS or CORS effect, and it
leaves the document's own address-bar URL and `rel=canonical` (section 7,
always written absolute) untouched. The result: clicking any link on the
landing page, including `/style.css`, goes straight to the apex. No redirect,
no double-fetch.

**Every path requested directly on the subdomain still answers 301 to the
apex.** `<base>` only governs links found *inside* an already-loaded page — a
reader who edits the address bar, an old bookmark, or a crawler guessing a
path all hit the subdomain's nginx block with no page loaded yet to rewrite
them. Without the 301 the whole corpus would be reachable under every
subdomain: N duplicate copies for a crawler, N hostnames splitting one page's
numbers in Matomo, and N places to bookmark the same law.

## 3. TLS

One wildcard per zone, from Let's Encrypt, over **DNS-01**. Wildcard issuance
has no HTTP-01 form. Eight names on one certificate:

```
lagen.nu                  *.lagen.nu
direktivet.nu             *.direktivet.nu
forordningen.nu           *.forordningen.nu
xn--frordningen-rfb.nu    *.xn--frordningen-rfb.nu
```

`*.lagen.nu` does not cover the apex, so every apex stays in the list. The
limit is 100 names per certificate, so this leaves room. One lineage means one
renewal and one `ssl_certificate` pair. The cost is coupling: a DNS failure in
any one zone fails the whole renewal.

### InstantDNS cannot answer a DNS-01 challenge automatically

Checked 2026-08-30:

- The two nameservers run BIND 9.11.4 on CentOS 7 — `version.bind` reports
  `9.11.4-26.P2.el7_9.16` — on Liquid Web addresses (`67.225.131.86`,
  `67.227.210.221`). Both the software and the OS are past end of life.
- The customer panel is a WHMCS install at `my.nudomain.nu`. Its entire
  InstantDNS documentation is one knowledgebase article, which says "the only
  functionality available with InstantDNS is custom DNS records" and describes
  no API.
- Certbot's bundled DNS plugins are cloudflare, digitalocean, dnsimple,
  dnsmadeeasy, gehirn, google, linode, luadns, nsone, ovh, rfc2136, route53
  and sakuracloud. None of them speaks to NuNames.

`instantdns.nu` serves a `*.cloudns.net` certificate, which looks at first like
a ClouDNS white-label with ClouDNS's API behind it. It is not. The name is a
parked web redirect to `nunames.nu`; the nameservers that actually answer for
these zones are the BIND hosts above.

Two things could still keep the zones where they are. Each costs one email to
NuNames support, and both are worth asking before moving anything:

- **RFC 2136.** BIND 9.11 does dynamic updates and certbot's bundled
  `dns-rfc2136` plugin drives them. It needs a TSIG key and an `allow-update`
  rule per zone. A free registrar service is unlikely to offer this.
- **A `_acme-challenge` CNAME.** If the record editor accepts an underscore
  name, delegate just the challenge name to a zone that does have an API.
  `tomtebo.org` at Joker is the candidate. This keeps three of the four zones
  untouched and needs one hand-made CNAME per zone, once.

Neither is documented. Assume the answer is no and plan for the move.

### The move

Registration stays at NuNames; only the delegation changes. Point all four
zones at one provider with a certbot plugin — Cloudflare is the default choice:
free for four zones, official plugin, one API token scoped to all four.

| Option | Cost |
| --- | --- |
| Wildcard, DNS-01, zones moved | One nameserver change per zone at NuNames, and an API token in the certbot container. After that a new law is a config edit and no cert work at all. |
| SAN list, HTTP-01, zones stay | Keeps today's webroot flow and needs no DNS move. Each new law means an A record, a re-run of `certonly` with the **full** `-d` list, and a reload. Every name is published in the Certificate Transparency log. |

Take the wildcard. Notes for whoever issues it:

- Today's certificate is one SAN lineage covering `lagen.nu` and
  `ferenda.lagen.nu`, issued by the `certbot` service in `docker-compose.yml`
  and read by nginx from `/mnt/data/lagen/certificates/` (a deploy-hook copies
  it there). It expires 2026-09-30. Issue the wildcard as a **second lineage**
  and point `ssl_certificate` at it, so the existing certificate keeps serving
  until the switch.
- The renew loop passes `--webroot -w /var/www/certbot`
  (`docker-compose.yml:247`). Certbot applies a command-line authenticator to
  **every** lineage, so those flags would force HTTP-01 on the wildcard and
  fail it. Remove them; bare `certbot renew` uses each lineage's own stored
  authenticator.
- The deploy-hook hardcodes `live/lagen.nu/` (`docker-compose.yml:248`). Key it
  on `$RENEWED_LINEAGE` so both lineages copy, the wildcard into
  `certificates/wildcard/`.
- The image must carry the plugin: `certbot/dns-cloudflare:v3.1.0` in place of
  `certbot/certbot:v3.1.0`. It contains the full certbot, so the existing
  webroot lineage keeps working.
- nginx does not reload itself on renewal (no docker.sock access, by design).
  The weekly reload cron already exists on prod — `0 4 * * 0 docker compose -f
  ~/wds/ferenda/docker-compose.yml exec -T nginx nginx -s reload`.
- The HSTS header carries no `includeSubDomains`
  (`docker/nginx/ferenda.lagen.nu.conf:58`), so a subdomain that is not ready
  yet fails softly rather than being hard-blocked by the browser.
- `*.lagen.nu` matches one label. `avtals.lagen.nu` is covered;
  `en.avtals.lagen.nu` is not, and nothing needs it.
- No zone has a CAA record today, so nothing blocks Let's Encrypt.

DNS records, once the zones move: a wildcard `A` and `AAAA` per zone.

| Record | Value |
| --- | --- |
| `*.lagen.nu`, `lagen.nu` (and the other three zones) `A` | `130.236.254.142` |
| the same names, `AAAA` | `2001:6b0:17:f0a0::8e` |

There is no `AAAA` anywhere today. The host has one — `ens18` carries
`2001:6b0:17:f0a0::8e` with a default route via `2001:6b0:17:f0a0::1` — and
the front already accepts v6: `[::]:80` and `[::]:443` are listening. Read
section 3a before publishing the record.

### 3a. IPv6 reaches nginx through the userland proxy

`/etc/docker/daemon.json` on prod sets only `data-root`. IPv6 is off in the
daemon, so there are no `ip6tables` DNAT rules and v6 traffic arrives through
`docker-proxy -host-ip ::`, which rewrites the source address to the bridge
gateway. Every IPv6 visitor would then log as one internal address: the nginx
access log, the rate limits and Matomo all see `172.19.0.1`.

Publish one `AAAA` first, fetch a page over v6, and read the access log. If the
source is the gateway, add `"ipv6": true`, a `fixed-cidr-v6` and
`"ip6tables": true` to `daemon.json` and restart the daemon before publishing
the rest.

## 4. Non-ASCII names

`upphovsrätts.lagen.nu` is an IDN, and this is where a silent failure lives.

- **The Host header is always the A-label.** The browser sends
  `xn--upphovsrtts-s8a.lagen.nu` in `Host` and in SNI. A `server_name` or a map
  key written in UTF-8 never matches, and the miss looks exactly like an
  unmapped subdomain: the apex redirect, nothing in the error log. So the
  generated map is keyed on the A-label.

  | Slug | Map key |
  | --- | --- |
  | `upphovsrätts` | `xn--upphovsrtts-s8a.lagen.nu` |
  | `föräldrabalks` | `xn--frldrabalks-m8a6u.lagen.nu` |
  | `rättegångsbalks` | `xn--rttegngsbalks-bfbt.lagen.nu` |

  Keep the human slug in the table and let the build emit the key with
  `slug.encode("idna")`. Keep the on-disk directory name ASCII as well.
- **Normalize the slug to NFC.** `ä` typed on macOS can arrive as `a` plus a
  combining diaeresis. The stdlib `encode("idna")` quietly normalizes it to the
  same A-label, but the `idna` package (installed here, IDNA2008) raises
  `Label must be in Normalization Form C`. Store NFC and the two agree.
- **Print the U-label, link the A-label.** A reader should see
  `upphovsrätts.lagen.nu`. A UTF-8 `href` works, because the browser's URL
  parser converts it; a `Location:` header and a `rel=canonical` must carry the
  `xn--` form. Mail clients and chat apps often show the raw `xn--`, which
  reads as a phishing domain to someone who has not seen it before.
- **Map the diacritic-free twin (O5, decided).** `upphovsratts.lagen.nu`
  points at the same page. It costs nothing under the wildcard and it is what
  people type. Generated, not listed: strip combining marks from the NFC slug
  (`unicodedata.normalize("NFKD", slug)`, drop category `Mn`, re-join) —
  `upphovsrätts` → `upphovsratts`. The build never writes the twin by hand.
- **The twin exists at the zone level too.** `förordningen.nu` and
  `forordningen.nu` are two registered domains, not one domain with two
  spellings. Both need the delegation, the wildcard records, their two names in
  the certificate, and their own keys in the map file. So one förordning is
  four map keys, not one: `tryckfrihets` and `tryckfrihets`-with-diacritics,
  each in each zone — all four computed from the one `namedlaws.json`/
  `namedacts.json` entry, none hand-listed.

## 5. Routing

**Done (2026-08-31):** `docker/nginx/subdomains.conf` — one extra vhost that
terminates TLS for every subdomain of the four zones, plus a second block for
the three bare apexes (O6). Not reproduced here; read the file, it is short
and comments its own reasoning. Wired into `docker-compose.yml`'s `nginx`
service the same way `default.conf`/`ferenda.lagen.nu.conf` already are (a
bind mount, so an edit needs no image rebuild), plus a new read-only mount,
`/mnt/forstor/ferenda/generated:/usr/share/nginx/generated:ro` — the `dumps`
mount already does the same thing for a different subtree of the same
data_root. Verified against a full local mirror of prod's nginx file layout
before it ever touched prod: unmapped host (root path and any other path),
the bare-apex block, an IDN A-label, and a mapped slug both at `/` (serves)
and at any other path (still redirects) — all six behaved as designed.

A second defect found the same way, after `ferenda/subdomains.py` existed to
generate a real map entry: the prod host stores a generated page `.br`-only
(confirmed for both `1915:218.html.br` and `eurlex/32016R0679.html.br`, no
plain fallback), and this image is plain `nginx:1.27-alpine` — no
`brotli_static` module to serve a compressed sibling automatically. A first
attempt (`try_files /index.html$sub_root_br_ext /index.html @apex`, one
`add_header` keyed on `$http_accept_encoding`) had the header wrong whenever
`try_files` actually fell through to a different variant than the one the
header assumed — a browser told a plain page was brotli-compressed cannot
decode it. Fixed with a `try_files`/named-location chain, one location per
variant (`@try_br`, `@try_gz`), each stamping its own `Content-Encoding` only
when it is the one actually serving the file, and redirecting to the apex
instead of serving raw compressed bytes to a client that cannot decode them.
Verified end-to-end against a real brotli-compressed fixture: a client
sending `Accept-Encoding: br` gets `Content-Encoding: br` and decodes it
(checked with `curl --compressed`); a client that doesn't gets a 302 to the
apex, not corrupted output.

One bug found doing that verification and fixed in the shipped file: the
root-path location's `try_files /index.html =404` fell through to a bare 404
for an unmapped host, contradicting this section's own "apex redirect, not a
404" rule below. Fixed with a named `@apex` fallback location instead of
`=404`.

`ferenda/subdomains.py` (section 6) is not implemented yet, so
`generated/subdomains.map` is an empty placeholder file and
`generated/_sub/` an empty directory — both created by hand on prod for now,
which is enough for every subdomain to answer a valid-TLS apex redirect.
Real content is section 6's job, not this one's.

Notes:

- The regex `server_name` must sort **after** the exact `lagen.nu` and
  `ferenda.lagen.nu` blocks in nginx's matching order, which it does: nginx
  prefers an exact name over a regex.
- The regex is written on A-labels, so `xn--frordningen-rfb` appears literally
  and the `--` in it is not special. A subdomain label with diacritics arrives
  as `xn--rttegngsbalks-bfbt` and matches `[a-z0-9-]+` unchanged.
- Three of the four apexes are not matched by the first block, hence the
  second one (O6); `lagen.nu` needs neither, since it is already the site.
- An unmapped subdomain gets an empty `$sub_root` and a missing file. Answer it
  with the apex redirect rather than a 404 — a mistyped law name should land
  the reader on the site.
- `include`ing a build-generated map is an established pattern on this host:
  the legacy vhost already includes `dv/generated/uri.map`
  (`docker/nginx/default.conf:5`).
- The live `ferenda.lagen.nu` vhost proxies everything to the app
  (`docker/nginx/ferenda.lagen.nu.conf`), so a landing page served from
  `_sub/` is the first static file this front serves. The other front
  (`docker/vps/nginx/ferenda.conf`) serves a **brotli-only** generated tree —
  if that one is the deployment target, the `location = /` block needs the same
  `Content-Encoding: br` stamping its `.br` locations use, and a client that
  does not accept brotli must fall through to the app.

## 6. Content: one derived file, three kinds

**Whole-act half done (2026-08-31):** `ferenda/subdomains.py`'s
`whole_act_rows()` and `write_sub_tree()`, tested against the real
`namedlaws.json`/`namedacts.json` in `test/test_subdomains.py` — both
`avtals.lagen.nu` and `dataskydds.förordningen.nu` resolve exactly, a
regulation's `abbr` (GDPR) no longer leaks a spurious `direktivet.nu` row (a
real bug caught running this against the live data, fixed by checking the
CELEX type letter), and a superseded name (`sjölagen`) resolves to today's
act only. `write_sub_tree` symlinks a `.br`-only generated page as-is and
still writes a correct map — the nginx side of that (section 5) needed its
own fix once this existed to generate a real entry. Not wired into
`build.GENERATE_CODE` yet, and the curated (chapter/standalone) half below is
not implemented — both need the `site` vertical's fourth/fifth basefiles
first.

`ferenda/subdomains.py` writes one derived artifact, `generated/subdomains.json`
— a flat map, slug → target URI:

```json
{
  "tryckfrihets.förordningen.nu": "/1949:105",
  "dataskydds.förordningen.nu": "/celex/32016R0679",
  "nis2.direktivet.nu": "/celex/32022L2555",
  "hyres.lagen.nu": "/1970:994#K12",
  "samtyckes.lagen.nu": "/1962:700#K6P1",
  "jante.lagen.nu": "/subdomain/lagen.nu/jante"
}
```

The URI's own shape says which of the three kinds it is — no separate `kind`
field, and nothing to keep in sync if it drifted from the target: a bare act
id is a whole act, a `#K..`/`#K..P..` fragment is a chapter (the fragment
grammar `ferenda/lib/lagrum.py:1004-1006`/`1473-1476` already mints for
in-text citations — `#K12` for a whole chapter, `#K6P1` for chapter+section),
and `/subdomain/...` is a standalone editorial page. `/celex/<CELEX>` is how
this site already addresses an EU act (`ferenda/lib/lagrum.py:66-67,1559`).

This file has two kinds of row, built two different ways:

**Generated (whole-act only).** Walk `ferenda/sfs/data/namedlaws.json` and
`ferenda/eurlex/data/namedacts.json`; match each `label` against its zone's
suffix (section 1), or an EU act's `abbr` against `direktivet.nu` when its
own CELEX type letter says directive (`L`, not `R` — an abbreviation alone
doesn't say which, and a regulation's abbreviation must not also mint a
`direktivet.nu` row); reuse `lib.lagrum.load_namedlaws`'s existing
current-name resolution rather than a separate repeal check, since it
already answers exactly "which act does this name mean today" for the
citation engine and a superseded name (`sjölagen`) must resolve the same way
here. Two matches for the same slug is a build-time error, not a silent pick
— surface it and let a person fix the source table. `namedlaws.json` and
`namedacts.json` are themselves hand-curated (deliberately sparse, per their
own `_comment` entries), so widening coverage later means adding entries
there, which the citation engine already reads them for anyway.

**Curated (chapter and standalone).** A slug that names part of a law, or no
law at all, cannot be derived — `hyreslagen` is a media nickname for 12 kap.
of Jordabalken, not that act's own populärnamn, and `jante` names no act. This
half of the table lives as data in the `lagen-wiki` content repo, alongside
the kommentar/begrepp/site markdown it already holds
(`docker-compose.yml`'s `/wiki` mount), not in this module, in two pieces:

- The chapter kind's target (act + fragment) is one row per slug in a fourth
  fixed basefile added to the existing editorial `site` source,
  `site/subdomains.md` (`ferenda/site/parse.py` already has exactly three —
  `frontpage`, `om/<slug>`, `sitenews` — in the same curated-markdown-list
  style as `frontpage.md`'s law list).
- The standalone kind's actual content is **not** an `om/<slug>` page,
  deliberately: `om/` is the general about-page mechanism (`/om/mcp`, and
  whatever else lands there), and an easter egg's markdown must not become
  reachable just by existing in that folder — nothing should make
  `mcp.lagen.nu` resolve because `om/mcp.md` happens to exist. So it is its
  own fifth fixed basefile pattern, namespaced by zone:
  `site/subdomain/<zone>/<slug>.md` → `site/subdomain/lagen.nu/jante.md`.
  Reachable as a subdomain only because `generated/subdomains.json` says so,
  same as every other kind — never by folder convention alone.

`ferenda/subdomains.py` merges both halves into `generated/subdomains.json`
and is added to `build.GENERATE_CODE` (`ferenda/build.py:4866`), so editing
either the SFS/eurlex tables, `site/subdomains.md`, or a
`site/subdomain/<zone>/<slug>.md` file re-stales the pages it produces. The
nginx host map (section 5) is a further, mechanical projection of this same
file — A-label hostname → directory name — generated alongside it, not a
second source of truth.

| Kind | Example | How the page is made |
| --- | --- | --- |
| whole act | `avtals` → `/1915:218` | `generate` writes `_sub/avtals/index.html` as a **symlink** to the act's own generated file. The file name is stable across builds, so the link never goes stale and no bytes are copied. |
| chapter | `hyres` → `/1970:994#K12` | Filter the **artifact**, not the HTML: give `sfs.render.render` a node filter (`ferenda/sfs/render.py:316`) and render the chapter as its own page. |
| standalone page | `jante`, `kamomilla` → `/subdomain/lagen.nu/jante`, `/subdomain/lagen.nu/kamomilla` | The `site` source renders `site/subdomain/<zone>/<slug>.md` the same way it already renders an `om/<slug>.md` page (same block-tree model, a new namespace, not new rendering logic). |

The introduction on a chapter page is a `kommentar` on the chapter node — the
hand-written, git-backed markdown layer that already exists and already has an
editing surface (`ferenda/lib/render.py:196`). `site/subdomains.md` names
*where* `hyres.lagen.nu` points; the kommentar is what explains, on the page
itself, that it is part of a larger law. Neither is a new authoring path.

Filtering the artifact rather than post-processing the generated HTML matters:
`api/pdf.py` already prunes a generated page's DOM for print, and a second
HTML-mangling path is how the two drift apart.

## 7. Canonical and analytics

Every landing page carries `<link rel="canonical">` to the apex URL.
`page.html` emits none today, and this feature is what makes it necessary: the
same act is now reachable at `https://ferenda.lagen.nu/1915:218` and at
`https://avtals.lagen.nu/`.

**O4, decided.** The landing hit folds onto the act's own page rather than
sitting in a separate row per host: the tracker snippet calls Matomo's
`setCustomUrl` with the canonical URL, the same URL `rel=canonical` already
carries, before tracking the pageview. `/1915:218`'s numbers then include a
reader who arrived through `avtals.lagen.nu`; which front door was used is not
tracked separately. If that turns out to matter later, it is a one-line
addition — a custom dimension set to the subdomain's own host — not a
redesign.

## 8. Easter eggs

Each is a `site/subdomain/lagen.nu/<slug>.md` page in the wiki repo (section
6) — hand-written prose, not a legal document, rendered the same way an
`/om/` about page always has been, but in its own namespace: nothing should
make a subdomain resolve just because a same-named file exists somewhere, so
this content sits apart from the general `om/` about pages on purpose.

**Jantelagen.** Its text on Wikipedia is CC BY-SA. A page that reproduces it
must carry the attribution and the same licence, which binds that one page's
terms. Writing our own summary of the ten rules avoids the licence entirely.
The easter egg works either way.

**Kamomilla.** `kamomilla.lagen.nu` is the same idea for Thorbjørn Egner's
"Folk och rövare i Kamomilla stad" — a fictional town with its own whimsical
laws. Different legal basis from Jantelagen: not a licence to work around, but
22 § upphovsrättslagen — the citaträtt (right to quote a published work "i
överensstämmelse med god sed och i den omfattning som motiveras av
ändamålet"). That permits quoting the town's actual laws, in the amount the
easter egg's purpose justifies, not reproducing the book.

## 9. Decisions (2026-08-31, were open questions)

- **O1 — which site backs the subdomains. Decided.** The subdomains launch
  against `ferenda.lagen.nu`, as its first public surface beyond that
  hostname itself. They do not wait for the September cutover; the gate
  is DNS, not the promotion — `*.lagen.nu` needs `lagen.nu` itself moved to
  Cloudflare before that zone's wildcard can be issued (section 3), same as
  the other three zones.
- **O2 — the naming rule. Decided.** Generated from `namedlaws.json` /
  `namedacts.json` by suffix/abbreviation match, filtered to gällande rätt.
  See section 1. Not a curated top-ten; coverage grows by adding to those two
  files, which the citation engine already maintains for other reasons.
- **O3 — assets on the landing page. Decided.** `<base href>` to the apex.
  See section 2.
- **O4 — Matomo. Decided.** Fold onto the act's own URL via `setCustomUrl`.
  See section 7.
- **O5 — the twin names. Decided.** Computed from the slug at build time, not
  listed. See section 4.
- **O6 — what the apexes serve. Decided.** A 301 to `https://ferenda.lagen.nu/`
  for the three zones with no site of their own. See section 5.

## 10. Phases

0. **DNS.** Ask NuNames about RFC 2136 and about an underscore CNAME. On a no,
   move the four zones to Cloudflare, publish the wildcard `A` and `AAAA`
   records, and check the IPv6 source address in the access log (section 3a).
   Nothing else can start until this lands.

   Moving the *registrations* is a separate question and does not belong in
   this phase. The registrar holds the domain; the DNS host answers queries.
   Only the second one blocks this PRD. Cloudflare Registrar carries 377 TLDs
   and `.nu` is not among them (nor `.se`, `.dk`, `.no`, `.fi`, `.de`), so the
   registration stays with an IIS-accredited registrar either way.

   If the registrations do move, the order is forced. IIS Registry Lock blocks
   redelegations, transfers and deregistration, and `lagen.nu` is
   `registry-lock: unlocked` today. So: change the delegation first, transfer
   second, turn on Registry Lock third. Rehearse the transfer on
   `direktivet.nu` — it is one day old — and do `lagen.nu` last. Auth codes for
   `.se`/`.nu` are set by the registry and expire after 14 days.
1. **Whole-act kind only.** The generation step over `namedlaws.json`/
   `namedacts.json` (section 1), the generated map file, the symlink step, the
   nginx block, the wildcard certificate, `rel=canonical`, `<base href>`, the
   Matomo `setCustomUrl` call. That delivers every gällande act whose label or
   abbreviation matches its zone — `avtals.lagen.nu`, `upphovsrätts.lagen.nu`,
   `dataskydds.förordningen.nu`, `nis2.direktivet.nu` and
   `cer.direktivet.nu` among them — and needs no renderer work.
2. **Chapter kind.** The fourth `site` basefile, `site/subdomains.md`
   (section 6), the node filter in `sfs.render.render`, plus the `kommentar`
   introduction on the chapter node. Delivers `hyres` and `samtyckes`.
3. **Standalone page.** `jante` and `kamomilla`, once each one's licence
   question is settled (section 8) — the fifth `site` basefile,
   `site/subdomain/<zone>/<slug>`, reuses the `om/<slug>` rendering, so this
   is a small parser addition plus content, not a new rendering path.
