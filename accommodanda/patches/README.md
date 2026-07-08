# Patch files

Curated, version-controlled fixes to a document's **source material**, applied at
parse time before the text is turned into the document model. Two uses:

- **correction** — a downloaded source carries a real error the publisher never
  fixed (an OCR slip, a broken table, a mis-encoded character); patch it once so
  every re-parse produces the right document.
- **redaction** — personal data that must not appear (a named party in a court
  decision, a personnummer) is removed. Stored **rot13-obfuscated** so the removed
  text is not itself plain-text googleable in this committed tree.

## Layout

A patch is keyed by `(source, basefile)` with the same path rule as the artifact
tree (`lib.layout.patch`):

```
patches/<source>/<relpath>.patch          # plain unified diff
patches/<source>/<relpath>.rot13.patch    # rot13-obfuscated (redactions); wins over .patch
patches/<source>/<relpath>.desc           # optional multi-line description sidecar
```

e.g. `patches/sfs/2018/585.patch`, `patches/eurlex/2016/32016R0679.rot13.patch`.

The patch is an ordinary `diff -u` / `difflib` unified diff against the document's
**best intermediate format** — the representation its parser reads:

| source | intermediate format patched |
|---|---|
| `sfs` | plain consolidated statute text |
| `dv` | the `innehåll` HTML |
| `eurlex` | the main act's Formex XML |
| `forarbete`, `foreskrift`, `remisser` | the body PDF's `pdftohtml -xml` output |
| `avg` | JO/ARN: the decision PDF's `pdftohtml -xml`; JK: the landing-page HTML |

The `pdftohtml -xml` intermediate is verbose but editable and deterministic for a
given PDF + poppler version, so a diff cut against it re-applies on re-parse (the
fuzzy context matcher absorbs small drift).

A single-line description rides on the first hunk's `@@` header; a multi-line one
goes in the `.desc` sidecar. Exactly one variant (plain **or** rot13) is kept per
document.

## Authoring a patch

From the CLI (`patch-show` dumps the intermediate text with any existing patch
applied; edit it, then `mkpatch` writes the minimal diff):

```sh
lagen sfs patch-show 2018:585 > /tmp/585.txt
$EDITOR /tmp/585.txt
lagen sfs mkpatch 2018:585 /tmp/585.txt "Rättad OCR-felaktighet"
lagen sfs mkpatch 2018:585 /tmp/585.txt "Avidentifierad part" --rot13   # redaction
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
