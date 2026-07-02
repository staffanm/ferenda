---
name: relocate-dont-regenerate
description: When generated output is in the wrong path but content is correct, move it — never delete-and-regenerate
metadata:
  type: feedback
---

When generated artifacts are in the **wrong location but the content is correct**
(e.g. a parser wrote to a non-canonical dir), **move** them with a one-line
`mv`/`find -exec mv` — do not `rm` them and re-run the generator.

**Why:** I wrote 1218 föreskrift artifacts to `downloaded/{fs}/artifact/` instead
of the canonical `foreskrift/artifact/{fs}/`, then fixed the path bug by deleting
the misplaced files and re-parsing the whole corpus — regenerating identical bytes
and burning minutes of CPU. The files were byte-for-byte correct; only the path
was wrong. A single `find … -exec mv` would have relocated them instantly.

**How to apply:** Before re-running an expensive generator, ask "is the *content*
already correct?" If yes and only the path/name is wrong, relocate. Reserve
regeneration for when the content itself changed (a code fix that alters output).
The same caution the harness states for deletes: look at the target first.
