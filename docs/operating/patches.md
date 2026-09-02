# Patch files (source corrections + redactions)

Controlled, version-controlled fixes to a document's **source material**, applied
at parse time before the text is tokenised — the reference projection's `patch_if_needed`,
re-done. A **correction** fixes a real error in a downloaded source (an OCR slip, a
broken table); a **redaction** removes personal data (a named party, a
personnummer) and is stored **obfuscated** (ROT13 over letters, ROT5 over
digits) so the removed text is not
plain-text googleable in the committed tree.

A patch is an ordinary unified diff against a document's **best intermediate
format** — the representation its parser actually reads and a human can edit: plain
text for `sfs`; the Formex XML for `eurlex` (the OJ HTML for pre-Formex acts); and
the `pdftohtml -xml` output (verbose but editable) for the PDF-bodied sources
(`forarbete`, `foreskrift`, `remisser`, `edpb`, `rs`, and JO/ARN/IMY under `avg`; JK, and KKV's pre-2006 documents, are landing-page/published
HTML). `dv` has three: the **whole API record JSON**, not just its `innehåll`
body — a redaction has to reach `malNummerLista` and the running text alike, or
a redacted party finds their own case again through the field the patch didn't
touch; the court's own PDF as `pdftohtml -xml`, for a verdict published before
its referat (no `innehåll` yet); and the frozen notis XML for a legacy-only
case. A legacy Word referat has no editable text form (read through POI) and
cannot be patched, the same as avg's two Word documents. `dv`'s and `eurlex`'s
intermediates ship their whole body on one line, which a line-based diff can't
usefully target, so both are normalised to one block element per line first
(`lib.markup.block_lines`/`indent_xml`) — a transform that only inserts
newlines *between* elements, so what the parser reads back out is unchanged.

Each vertical's parser applies the patch at that choke point —
`lib.patch.patch_if_needed(...)` for the text/JSON/HTML/XML sources, a `patch_key`
threaded into `lib.pdftext.pdf_pages` for the PDF ones; a patch that no longer
applies is a **fatal** parse error (the source drifted — it must be regenerated,
never silently skipped). The one exception is an archived SFS consolidation (the
`versions` stage): the statute's patch is offered to every superseded wording via
`lib.patch.apply_if_fits`, which skips a **correction** that doesn't fit an older
lydelse (a conflict there is the normal case, not a broken patch) but keeps a
**redaction** fatal — republishing unredacted personal data because an older
wording didn't line up is exactly the harm, so that version is recorded as
skipped rather than published (`sfs.versions.build`). Patches live committed in
the **content repo** at `patches/<source>/<relpath>.patch` (or `.rot18.patch`),
keyed by the same rule as the artifact tree (`layout.patch` — `PATCHES`, which
is `config.WIKI_ROOT/patches`);
they are folded into every patchable source's parse freshness inputs so editing
one re-stales its document.

They sit in the content repo (`../lagen-wiki`, `WIKI_ROOT`) beside `commentary/`,
`concept/` and `ann/` rather than in this code repo, because a patch is the same
kind of thing as a commentary: hand-authored editorial knowledge about one
document, reviewed and versioned on its own. It also gives the running site one
write target instead of two — one mount, one push, one row on the ops dashboard.
The tree has to sit inside a real git checkout at runtime because the web editor
below *commits* what it writes, and the deployed image is built with `.git`
excluded (`.dockerignore`), so an in-image tree could neither commit nor survive
a container replacement. Production bind-mounts the content repo checkout at
`/wiki`, so a save is an ordinary commit that pushes to origin and reaches dev by
pull — exactly as a commentary edit does.

`layout.patch` asserts the tree exists. An absent one is indistinguishable from
"this document has no patch", so a mistyped mount would drop every `.rot18`
redaction and republish the personal data it removes — silently. The ops
dashboard reports the content checkout's unpushed/dirty state, because nothing
pushes it automatically.

The six folkrätt sources — `hudoc`, `coe`, `icrc`, `untc`, `icc`, `icj` — apply a patch
at parse time the same way (`patch.apply` on the stored record/HTML text for
`hudoc`/`icrc`/`untc`, a `patch_key` threaded into `lib.pdftext.pdf_pages` for the
PDF-bodied `coe`/`icc`/`icj`), but none sets `Source.intermediate` on its registration, so
`mkpatch`/the web editor cannot generate a pristine intermediate to diff against
for them — only a hand-written diff against the stored source text applies.

Author them from the CLI or the inline web editor:

```sh
lagen sfs patch-show 2018:585 > /tmp/585.txt   # the intermediate text (patch applied)
$EDITOR /tmp/585.txt                            # edit to the desired final text
lagen sfs mkpatch 2018:585 /tmp/585.txt "Rättad OCR-felaktighet"
lagen dv mkpatch "NJA 2015 s 1" /tmp/case.json "Avidentifierad part" --obfuscated
```

The web surface (`api/patch.py`, gated by the same editor auth as the commentary
editor) serves `GET /internal-api/v1/patch/edit?source=…&basefile=…` — a textarea seeded
with the intermediate text; saving writes the *minimal* diff, commits it attributed
to the editor, and force-reparses the document so the fix is live. Editing the text
back to the pristine source removes the patch. A logged-in editor reaches it from a
**🩹 Patcha källtext** button that `editor.js` grafts next to the *✎ Kommentera
dokumentet* button on any patchable document page (the page's `<meta name="lagen-doc">`
carries the `data-source`/`data-basefile` identity). See `patches/README.md` in
the content repo.

