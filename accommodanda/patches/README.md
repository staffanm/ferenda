# Patch files

Curated, version-controlled fixes to a document's **source material**, applied at
parse time before the text is turned into the document model. Two uses:

- **correction** — a downloaded source carries a real error the publisher never
  fixed (an OCR slip, a broken table, a mis-encoded character); patch it once so
  every re-parse produces the right document.
- **redaction** — personal data that must not appear (a named party in a court
  decision, a personnummer) is removed. Stored **obfuscated** so the removed text
  is not itself plain-text searchable in this committed tree: letters rotate 13
  and digits rotate 5 (ROT13 + ROT5, "ROT18"). Plain ROT13 would leave every
  digit untouched — and a personnummer, an organisationsnummer or a telephone
  number is all digits, which is most of what these patches remove.

## Layout

A patch is keyed by `(source, basefile)` with the same path rule as the artifact
tree (`lib.layout.patch`):

```
patches/<source>/<relpath>.patch          # plain unified diff
patches/<source>/<relpath>.rot18.patch    # obfuscated (redactions); wins over .patch
patches/<source>/<relpath>.desc           # optional multi-line description sidecar
```

e.g. `patches/sfs/2018/585.patch`, `patches/dv/NJA_2015_s_1.rot18.patch`.

The patch is an ordinary `diff -u` / `difflib` unified diff against the document's
**best intermediate format** — the representation its parser reads:

| source | intermediate format patched |
|---|---|
| `sfs` | plain consolidated statute text |
| `dv` | the whole API record JSON; an unpublished verdict's own PDF as `pdftohtml -xml`; a legacy-only notisfall's frozen intermediate XML |
| `eurlex` | the main act's Formex XML, or the OJ HTML for the pre-Formex acts |
| `forarbete`, `foreskrift`, `remisser` | the body PDF's `pdftohtml -xml` output |
| `avg` | JO/ARN: the decision PDF's `pdftohtml -xml`; JK: the landing-page HTML |

A legacy dv **Word referat** has no editable text form (it is read through POI),
so it cannot be patched — the same as avg's two Word documents.

dv patches the **whole record**, not just its body, because the two are one
document: a målnummer a court published in the clear sits in `malNummerLista`
*and* in the running text, and redacting one but not the other is how a
redacted party finds their own case again. The record is presented as its own
JSON, pretty-printed one field per line, with `innehall` split into a list of
block elements so a paragraph is one line of the diff
(`dv.parse.record_intermediate`).

The `pdftohtml -xml` intermediate is verbose but editable and deterministic for a
given PDF + poppler version, so a diff cut against it re-applies on re-parse (the
fuzzy context matcher absorbs small drift).

### Markup sources are normalised first

A unified diff is a diff over *lines*, so a source that ships its whole body on
one line cannot be patched usefully. Two sources do exactly that — a tenth of
dv's records, and eurlex's manifestations as a rule (45 000 characters on the
median line). Both are therefore normalised to **one block element per line**
(`lib.markup`) before a patch is cut against them, and `dv.parse` / `eurlex.parse`
normalise identically before applying it. The transform only adds newlines
*between* elements, never inside a block's text — an element's own text stays on
one line, so what the parser reads out is unchanged.

A long paragraph is therefore still a long line: the unit of a dv diff is the
paragraph, because breaking one apart would change the stycke the parser builds
(dv's `collapse` keeps newlines — that is how `<br>` survives).

A single-line description rides on the first hunk's `@@` header; a multi-line one
goes in the `.desc` sidecar. Exactly one variant (plain **or** obfuscated) is kept
per document.

## Authoring a patch

From the CLI (`patch-show` dumps the intermediate text with any existing patch
applied; edit it, then `mkpatch` writes the minimal diff):

```sh
lagen sfs patch-show 2018:585 > /tmp/585.txt
$EDITOR /tmp/585.txt
lagen sfs mkpatch 2018:585 /tmp/585.txt "Rättad OCR-felaktighet"
lagen sfs mkpatch 2018:585 /tmp/585.txt "Avidentifierad part" --obfuscated  # redaction
```

Or in the web UI (a logged-in editor): `GET /api/v1/patch/edit?source=…&basefile=…`
shows the intermediate text in a textarea; saving diffs it, writes the minimal
patch, commits it attributed to the editor, and force-reparses the document.

Editing the text back to the pristine source removes the patch.

## How it is applied

Each vertical's parser calls `lib.patch.patch_if_needed(source, basefile, text)` at
its intermediate-text choke point. A patch that no longer applies (the source
drifted) is a **fatal** parse error — it must be regenerated, never silently
skipped. See `accommodanda/lib/patch.py` and `accommodanda/patchsource.py`.

The one exception is an **archived SFS consolidation** (the `versions` stage),
where the statute's patch is offered to every superseded wording of the same act.
There a conflict is the normal case, not a broken patch — a lost blank line or an
OCR slip entered the source at some amendment and was corrected at another, so
the patch fits the lydelser in between and no others. Those use
`patch.apply_if_fits`, which skips instead of raising. A patch is authored against
the *current* text; the historical reach is whatever it happens to have.
