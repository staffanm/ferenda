# Citation coverage

The resolver checks document existence against the catalog first.
A held document always wins over document absence rules.
For an absent document, `recognized[].invalid: true` means a configured rule
establishes invalidity. Without that field, existence remains unconfirmed.
Swedish statute and agency-regulation provisions also receive a structure check.

## Swedish provisions

The resolver checks chapters and paragraphs against the presented artifact tree.
For agency regulations, this means the latest parsed consolidated text when available.
For example, `12 kap. 1 § avtalslagen` returns an invalid `#K12P1` citation.
`1 kap. 1 § avtalslagen` reaches `#P1`, because its paragraph numbering is continuous.
The chapter must contain that paragraph; `2 kap. 1 § avtalslagen` remains invalid.

An invalid provision goes in `recognized` with its fragment and `invalid: true`.
It does not produce a normal result merely because the statute exists.
An empty or unstructured artifact cannot establish provision absence.
Finer pinpoints require recorded sibling anchors before missing stycken or points
can be marked invalid. These checks use the presented version, not every historical wording.
EU and international article existence is not checked by this Swedish structure rule.

## Swedish report intervals

The shipped configuration uses these inclusive intervals for absence checks:

| Series | Complete interval assumed |
|---|---|
| NJA page reports | 1981–2025 |
| RÅ reports | 1993–2010 |
| HFD reports | 2011–2025 |
| AD reports | 1993–2024 |
| RH reports | 1993–2025 |
| MÖD reports | 1999–2025 |
| MIG reports | 2006–2025 |
| PMÖD reports | 2016–2025 |
| MD reports | 2004–2016 |
| RK reports | 2008–2025 |

The start years follow the user's supplied
[Domstolsverket coverage guide](https://www.domstol.se/tjanster-och-blanketter/sok-rattspraxis/).
The PRD supplies NJA's initial interval. The other intervals combine the guide
with an inspection of [live DV browse data](https://lagen.nu/api/v1/browse?source=dv)
on 2026-09-14. They are operational completeness assumptions, not an independent
audit of every published report.

The live AD collection ends in 2024. Most active report series reach 2026,
but the configuration does not claim the current year is complete.
MD ends in 2016. RK includes earlier selected decisions, while the guide starts
LEK report coverage in 2008. Empty RK years alone do not prove missing reports.

These intervals apply only to report identifiers. They exclude NJA, RÅ and HFD
notices and ordinary case-number identifiers. The inspected NJA 1981 and RÅ 1993
buckets contain reports but no notices. Selected decisions from kammarrätter,
Patentbesvärsrätten and Rättshjälpsnämnden have no blanket absence rule.
The guide's earlier selected MD decisions also fall outside complete report coverage.

## Other collections

The resolver recognizes these families, but absence alone does not establish invalidity.
Counts and year spans below describe the live catalog inspected on 2026-09-14.
They do not establish complete coverage.

| Collection | Observation | Accepted forms |
|---|---|---|
| [SFS](https://lagen.nu/api/v1/browse?source=sfs) | 5,345 current documents; not all historical enactments | SFS numbers, names, abbreviations, provisions |
| Agency regulations | No complete range established | Registered series, such as `FFFS 2020:1` |
| [EUR-Lex](https://lagen.nu/api/v1/browse?source=eurlex) | 111,510 documents; judgment buckets span 1954–2026 | C/T/F case numbers, CELEX, indexed ECLI, named acts, directives and regulations |
| [HUDOC](https://lagen.nu/api/v1/browse?source=hudoc) | 46,106 documents; judgments span 1960–2026, with gaps | HUDOC IDs; names and application numbers in the citation datasets |
| [CoE](https://lagen.nu/api/v1/browse?source=coe) | 233 documents | ETS/CETS numbers, treaty names and articles |
| [ICRC](https://lagen.nu/api/v1/browse?source=icrc) | 111 instruments; treaty buckets span 1863–2019 | ICRC numbers, Swedish and English treaty names and articles |
| [UNTC](https://lagen.nu/api/v1/browse?source=untc) | 14 selected instruments | Registration numbers, treaty names and articles |
| [ICC](https://lagen.nu/api/v1/browse?source=icc) | 269 selected substantive decisions | Full document numbers, including variant suffixes |
| [ICJ](https://lagen.nu/api/v1/browse?source=icj) | 255 selected judgments, advisory opinions and orders | Decision filename stems and indexed ICJ Reports citations |

Canonical lagen.nu document URIs also resolve. ICC case numbers alone do not
identify one decision. ECLI and ICJ Reports require an alias recorded in a held
document's artifact. Unknown aliases do not produce a guessed document URI.

## Structural rules

`ferenda/lib/data/citation_series.json` also sets publication bounds and positive
identifier components. Future years and non-positive document numbers are invalid
where the configured URI grammar identifies those components.
A large positive page or document number alone is not proof of invalidity.

NJA starts in 1874. [RÅ starts in 1909 and becomes HFD in 2011](https://www.domstol.se/hogsta-forvaltningsdomstolen/om-hogsta-forvaltningsdomstolen/historik/).
[EU court bounds](https://curia.europa.eu/site/jcms/d2_5100/en/history) are 1952 for
the Court of Justice, 1989 for the General Court, and 2005–2016 for the Civil Service Tribunal.
[The ICJ begins in 1946](https://www.icj-cij.org/node/106024).

EU case numbers map to judgment descriptors `CJ`, `TJ` and `FJ`, as defined in
the [EUR-Lex document table](https://eur-lex.europa.eu/content/tools/TableOfSectors/types_of_documents_in_eurlex.html?locale=en).
An ECLI's serial number does not encode a CELEX case number;
the resolver uses [ECLI identities](https://eur-lex.europa.eu/content/help/eurlex-content/ecli.html?locale=en)
from the artifacts instead.

## Update and deploy

1. Ingest and check the source before extending `complete_years`.
2. Set inclusive intervals for the actual deployed corpus. Use `[]` on partial installations.
3. Use `complete_numbers` only after checking a whole numeric interval. All shipped numeric intervals are empty.
4. Restart every API process after changing configuration. The service caches the configuration in memory.

The end year is explicit. It is **not** `current_year - 1`.
Advancing the calendar only changes the future-year check.
Record evidence here when changing a coverage interval.

The catalog schema adds a derived `citation_alias` table. Populate aliases from
existing ECLI and ICJ Reports artifact fields during deployment:

```sh
lagen eurlex relate --force
lagen icj relate --force
```

No source download or parse is needed for these aliases. The ordinary incremental
relate path maintains aliases when artifacts change or disappear.
The general citation parser also requires `artifact/dom/casenumbers.json`,
built with `lagen dv casenumbers`, for Swedish case-number lookup.
REST responses use `Cache-Control: public, no-cache` and a content-based ETag.
Clients must revalidate stored responses; changed answers receive a new ETag.
