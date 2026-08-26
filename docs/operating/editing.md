# Inline editing (web UI)

The git-backed markdown — legal-source **commentary** (`commentary/…md`),
**concept** pages (`concept/…md`) and the **editorial** site pages
(`site/…md`) — can be edited **inline on the live site** by a logged-in user,
instead of cloning `lagen-wiki` and committing by hand. It is the only
authenticated, mutating part of the service; the public read API stays GET-only.

**Who can edit** is a hand-curated registry in `config.yml` (there is no
self-signup). Each entry maps a login to the git identity its commits are
attributed to and a password hash:

```yaml
editor_secret: <random hex>          # signs the session cookie; unset ⇒ editing off (403)
editors:
  staffan:
    name: Staffan Malmgren           # -> GIT_AUTHOR_NAME / GIT_COMMITTER_NAME
    email: staffan@example.org        # -> GIT_AUTHOR_EMAIL / GIT_COMMITTER_EMAIL
    pwhash: "pbkdf2$260000$…$…"        # never a plaintext password
```

Mint a `pwhash` (nothing is stored in the clear):

```sh
uv run python -m ferenda.api.auth hash '<the password>'   # prints the pbkdf2$… line
```

`editor_secret`/`editors` follow the same env→config.yml precedence as the other
knobs (`EDITOR_SECRET` env; `editors` is config-only). Leaving `editor_secret`
unset disables editing wholesale — every `/internal-api/v1/{auth,edit}/*` route, and the
`/ops` dashboard that rides the same session, answers 403.

The session cookie's `Secure` flag is `cookie_secure` (`EDITOR_COOKIE_SECURE`
env), on by default; flip it off in `config.yml` only for a plain-http dev
serve. A password change (a new `pwhash`, plus a restart) invalidates every
outstanding session for that editor — the cookie embeds a fingerprint of the
current `pwhash`, which is the revocation mechanism (there is no server-side
session table to keep a separate blocklist in).

Login is rate-limited in-process (`api/auth.py`): a per-(IP, username) sliding
window allows 5 free attempts per minute, then backs off exponentially up to
5 minutes (`429` + `Retry-After`), and a hard concurrency cap bounds how many
pbkdf2 hashes run at once — so a flood can't pin CPU behind the password check
and starve the rest of the (small, single-process) server. State is in-memory
only; a restart forgets past attempts.

**How it works.** The static pages are byte-identical for anonymous readers;
`editor.js` (served with the site) grafts the edit UI on client-side after a
`GET /internal-api/v1/auth/me` check, keyed off a `<meta name="lagen-doc">` the renderer
injects. On a statute / EU-act page an ✎ button on a `§`/article edits the
**commentary** for that node (the official text stays read-only) — the `##`
section is created from its heading if none exists, and the file with an
`annotates:` frontmatter if the host has no commentary at all. Concept and
editorial pages edit their whole markdown body. The editor has a link toolbar
that turns a search hit into an `sfs:`/`eurlex:`/`begrepp:` link.

Edits accumulate in a per-user **cart** (`DATA/.build/edits/<user>.json`, kept
out of the working tree so users don't collide). The masthead carries the
logged-in editor's own control — a circle with their initials, beside the
collection and theme circles, badged with the number of uncommitted changes —
and it opens the checkout. Checkout takes a commit message and turns the whole
cart into **one git commit authored as that user** — byte-for-byte the history
a `git clone` + commit would produce — then synchronously re-parses /
re-relates / regenerates just the touched pages (`build.rebuild_after_commit`)
so the edit is live when the request returns. A hunk that changed on disk since
it was carted fails the checkout (409) rather than clobbering.

The routes are same-origin only (the session cookie is `SameSite=Lax`; CORS
stays GET-open for the public read API). No new dependencies — cookie signing
and password hashing are stdlib `hmac`/`hashlib`.

## Reviewing `.graphics` crops

`sfs ai-includegraphics` (see above) writes each recovered graphic/table/formula
as a `generated` `.graphics` entry; `annstore.publishable` keeps it out of the
public render until a human signs it off. `GET /internal-api/v1/graphics/review`
(`api/graphicsedit.py` + `api/graphics.py`) is where an editor does that — same
login and session as the commentary editor above. The page lists every pending
crop (`GET /graphics/queue`) and, for one at a time, shows the crop next to the
whole source page with its rectangle drawn on it (`GET /graphics/page`,
`GET /graphics/crop`): a confident placement on the wrong figure still returns
a clean, plausible picture, and only the full page reveals it. The reviewer
approves it as-is, drags the rectangle and approves the moved one, or declares
the whole page — `POST /graphics/cart` carts the decision through the same
`editcart.py` cart, `base_sha` conflict check and attributed commit the
commentary editor uses (`editcart.py` now dispatches on the draft's *kind*, so
a graphics decision and a markdown edit share the same machinery). Checkout
regenerates only the host statute's page — a reviewed entry needs no reparse or
relate, since the layer is read at generate time
(`page._graphics_index`) — via `build.rebuild_after_commit`'s `graphics`
branch. The page/crop routes deliberately bypass `annstore.publishable` for a
logged-in editor, since an editor has to see an unreviewed crop to judge it;
the public `GET /api/v1/sfs-graphic` still 404s it.
