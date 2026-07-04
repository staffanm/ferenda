#!/usr/bin/env python3
"""One-time converter: the lagen.nu MediaWiki SQLite DB -> a git-backed markdown
content repo (PRD step 1, `docs/prd-wiki-markdown-and-guidance.md`).

It replays the wiki's **full per-revision history** as one git commit per
revision, in global chronological order, so `git log`/`git blame` mirror the
real authoring history -- something the latest-only `dump.xml` cannot give.

  * ns0 content pages split two ways:
      - title `SFS/<sfsnr>`  -> `commentary/sfs/<relpath>.md` (frontmatter
        `annotates:`), e.g. `SFS/1915:218` -> `commentary/sfs/1915/218.md` --
        the commentary is filed under the source it annotates and that source's
        basefile->path rule, like every other source's artifacts;
      - everything else      -> `concept/<Name>.md`    (frontmatter `title:`)
  * redirects (`#REDIRECT [[Target]]`) are not files; each becomes an
    `aliases:` entry on its target concept (PRD O3), so links to the old name
    still resolve.
  * the wikitext->markdown body transform **reuses `lib.wikitext`'s own
    stripping/wikilink helpers**, so `markdown -> artifact` is byte-identical to
    the old `wikitext -> artifact` (the migration's safety property, PRD §3.1;
    verified by `tools/wiki_artifact_diff.py`).

Authorship (PRD O7): MediaWiki actor names (`imported>Staffan`, bare IPs) map to
`Name <slug@lagen.nu>`; no external mapping table.

Usage:
  tools/mediawiki_to_markdown.py mediawiki-db/db/lagen.sqlite ../lagen-wiki
  tools/mediawiki_to_markdown.py <db> <out> --limit 200   # smoke test
Idempotent/resumable: re-running continues after the last committed revision.
"""

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from accommodanda.lib import (
    git,  # noqa: E402  (shared git-CLI wrapper)
    layout,  # noqa: E402  (basefile -> storage relpath)
    wikitext,  # noqa: E402  (reused for faithful conversion)
)

RE_REDIRECT_TARGET = re.compile(
    r"^#\s*(?:REDIRECT|OMDIRIGERING)\s*\[\[\s*([^\]|#]+)", re.IGNORECASE)
RE_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$|^[0-9a-f:]+:[0-9a-f:]+$", re.IGNORECASE)
# a frontmatter scalar that needs quoting (leading marker char or edge whitespace)
RE_FM_QUOTE = re.compile(r"^[\s\"'\[\]{}#>|*&!%@`]|[\s]$")

STATE_FILE = ".git/mw_import_state"         # count of replayed revisions, for --resume (out of the tree)

# ns0 titles that are navigation/meta/legacy junk, not begrepp concepts (the
# `SFS/` kommentar pages are the only content with a `/` in the title)
SKIP_TITLE = ("Lagar inom", "Kategori:", "Mall:", "Användare:", "MediaWiki:",
              "Lagen.nu:", "Fil:", "Hjälp:", "Legacy:", "Index.php")


def is_content(title):
    """A ns0 page the pipeline ingests: an `SFS/` kommentar or a begrepp concept
    (no `/` in the title, not a meta/legacy page)."""
    if title.startswith("SFS/"):
        return True
    return "/" not in title and not title.startswith(SKIP_TITLE)


# --------------------------------------------------------------------------
# wikitext -> markdown (pure; shared with the artifact-equality diff)
# --------------------------------------------------------------------------

def _stycke_to_md(raw):
    """One raw paragraph -> markdown, mirroring `wikitext._wikilinks` exactly:
    a concept link emits `[label](begrepp:Target)`, an external link emits the
    standard markdown `[label](url)`. Non-link text and labels go through the
    identical `_strip_inline`, so the markdown parser reconstructs the same
    plaintext + spans (and link runs) the wikitext parser did."""
    parts, last = [], 0
    for m in wikitext.RE_INLINE_LINK.finditer(raw):
        parts.append(wikitext._strip_inline(raw[last:m.start()]))
        last = m.end()
        if m.group("wt") is not None:       # [[concept]] / [[concept|label]]
            target = m.group("wt").strip()
            if target.lower().startswith("kategori:"):
                continue                    # a category -> frontmatter, not inline
            label = wikitext._strip_inline((m.group("wl") or m.group("wt")).strip())
            parts.append("[%s](begrepp:%s)" % (label, _link_target(target)))
        else:                               # [url label] external link
            url = m.group("url").strip()
            label = wikitext._strip_inline((m.group("el") or url).strip())
            parts.append("[%s](%s)" % (label, _link_target(url)))
    parts.append(wikitext._strip_inline(raw[last:]))
    # a paragraph that begins with `#` is a wikitext list item / literal hash, not
    # an ATX heading -- escape it so the markdown parser keeps it as prose
    return re.sub(r"^(\s*)#", r"\1\\#", "".join(parts))


def _link_target(target):
    """Escape the one character that would break a markdown `(target)`: a literal
    `)` -- in a concept name ("Mål (process)") or an external url
    ("…/Spice_(drog)"). The markdown parser percent-decodes it back."""
    return target.replace(")", "%29")


def convert_page(title, wt):
    """`(meta, body)` for one page's wikitext. `meta` carries the frontmatter the
    parser reads as authoritative (title/annotates/categories/author); `aliases`
    are added by the caller from the redirect graph."""
    meta = {}
    if title.startswith("SFS/"):
        meta["annotates"] = title[len("SFS/"):]
    else:
        meta["title"] = title.replace("_", " ")
    cats = wikitext.categories(wt)
    if cats:
        meta["categories"] = cats
    author = wikitext.author(wt)
    if author:
        meta["author"] = author
    body = []
    for block in wikitext.blocks(wt):
        if block[0] == "rubrik":
            body.append("#" * block[1] + " " + block[2])
        else:
            body.append(_stycke_to_md(block[1]))
    return meta, "\n\n".join(body)


def _emit_scalar(value):
    value = str(value)
    return '"%s"' % value.replace('"', '\\"') if RE_FM_QUOTE.search(value) else value


def render_file(meta, body):
    """A full markdown file: frontmatter fence + body. Block lists for
    categories/aliases (robust against names with commas/brackets)."""
    lines = ["---"]
    for key in ("title", "annotates", "author"):
        if meta.get(key):
            lines.append("%s: %s" % (key, _emit_scalar(meta[key])))
    for key in ("categories", "aliases"):
        if meta.get(key):
            lines.append("%s:" % key)
            lines += ["  - %s" % _emit_scalar(v) for v in meta[key]]
    lines.append("---")
    return "\n".join(lines) + "\n" + body + "\n"


# --------------------------------------------------------------------------
# DB access
# --------------------------------------------------------------------------

def _connect(db_path):
    con = sqlite3.connect(db_path)
    con.text_factory = bytes               # titles/text are BLOBs; decode explicitly
    return con


def _u(blob):
    return blob.decode("utf-8") if isinstance(blob, (bytes, bytearray)) else blob


def latest_wikitext(con, page_ids):
    """page_id -> latest-revision wikitext, for the redirect-target lookup."""
    rows = con.execute(
        "SELECT p.page_id, t.old_text FROM page p "
        "JOIN slots s ON s.slot_revision_id = p.page_latest "
        "JOIN content c ON c.content_id = s.slot_content_id "
        "JOIN text t ON ('tt:' || t.old_id) = c.content_address "
        "WHERE p.page_id IN (%s)" % ",".join("?" * len(page_ids)),
        tuple(page_ids))
    return {pid: _u(text) for pid, text in rows}


def all_revisions(con):
    """Every ns0 non-redirect revision, oldest first: dicts with page_id, title,
    timestamp, rev_id, actor, comment, wikitext."""
    rows = con.execute(
        "SELECT r.rev_id, r.rev_timestamp, p.page_id, p.page_title, "
        "       a.actor_name, c.comment_text, t.old_text "
        "FROM revision r "
        "JOIN page p ON r.rev_page = p.page_id "
        "JOIN actor a ON r.rev_actor = a.actor_id "
        "LEFT JOIN comment c ON r.rev_comment_id = c.comment_id "
        "JOIN slots s ON s.slot_revision_id = r.rev_id "
        "JOIN content ct ON ct.content_id = s.slot_content_id "
        "JOIN text t ON ('tt:' || t.old_id) = ct.content_address "
        "WHERE p.page_namespace = 0 AND p.page_is_redirect = 0 "
        "ORDER BY r.rev_timestamp, r.rev_id")
    out = []
    for rev_id, ts, pid, title, actor, comment, text in rows:
        if not is_content(_u(title)):
            continue
        out.append(dict(rev_id=rev_id, ts=_u(ts), page_id=pid,
                        title=_u(title), actor=_u(actor),
                        comment=_u(comment) if comment else "",
                        wikitext=_u(text)))
    return out


def redirect_aliases(con):
    """target page-title (space form) -> sorted list of redirecting page titles.
    A redirect resolves to a concept by MediaWiki's ucfirst rule; only redirects
    whose target is an existing ns0 page are kept (dangling ones dropped)."""
    titles = {_u(t).replace("_", " ")
              for (t,) in con.execute(
                  "SELECT page_title FROM page WHERE page_namespace = 0")
              if is_content(_u(t)) and not _u(t).startswith("SFS/")}
    redirects = con.execute(
        "SELECT p.page_id, p.page_title FROM page p "
        "WHERE p.page_namespace = 0 AND p.page_is_redirect = 1")
    ids = {pid: _u(title) for pid, title in redirects}
    texts = latest_wikitext(con, list(ids)) if ids else {}
    aliases = {}
    for pid, src in ids.items():
        m = RE_REDIRECT_TARGET.match(texts.get(pid, ""))
        if not m:
            continue
        target = m.group(1).strip().replace("_", " ")
        target = target[:1].upper() + target[1:]        # MediaWiki ucfirst
        if target in titles:
            aliases.setdefault(target, []).append(src.replace("_", " "))
    return {t: sorted(set(v)) for t, v in aliases.items()}


# --------------------------------------------------------------------------
# git replay
# --------------------------------------------------------------------------

def git_identity(actor):
    """A MediaWiki actor name -> `(name, email)` (PRD O7: synthesized, no table)."""
    name = actor[len("imported>"):] if actor.startswith("imported>") else actor
    if RE_IP.match(name):
        slug = name
    else:
        slug = re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".") or "okand"
    return name, "%s@lagen.nu" % slug


def mw_timestamp(ts):
    """MediaWiki TS_MW (YYYYMMDDHHMMSS, UTC) -> a git-acceptable ISO string."""
    return ("%s-%s-%sT%s:%s:%s +0000"
            % (ts[0:4], ts[4:6], ts[6:8], ts[8:10], ts[10:12], ts[12:14]))


def page_path(repo, title):
    """The content-repo path for one page: commentary is filed under its source
    and that source's basefile->path rule (`commentary/sfs/1915/218.md`);
    concepts are flat (`concept/<Name>.md`)."""
    if title.startswith("SFS/"):
        rel = layout.relpath("sfs", title[len("SFS/"):])
        return repo / "commentary" / "sfs" / rel.with_name(rel.name + ".md")
    return repo / "concept" / (title.replace("_", " ") + ".md")


def replay(con, repo, limit=None):
    repo = Path(repo)
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        git.run(repo, "init", "-q")

    aliases = redirect_aliases(con)
    revisions = all_revisions(con)
    if limit:
        revisions = revisions[:limit]

    # resume by *count* of revisions already replayed, not by rev_id: the list is
    # in a deterministic total order (ORDER BY rev_timestamp, rev_id), but rev_ids
    # need not be monotonic with timestamp (imported histories), so a rev_id cutoff
    # could wrongly skip a later-timestamp revision with a smaller id.
    state = repo / STATE_FILE
    done = int(state.read_text()) if state.exists() else 0
    total = len(revisions)
    for i, rev in enumerate(revisions, 1):
        if i <= done:
            continue
        meta, body = convert_page(rev["title"], rev["wikitext"])
        if "title" in meta and meta["title"] in aliases:
            meta["aliases"] = aliases[meta["title"]]
        path = page_path(repo, rev["title"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_file(meta, body), encoding="utf-8")
        name, email = git_identity(rev["actor"])
        env = {**os.environ,
               "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
               "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
               "GIT_AUTHOR_DATE": mw_timestamp(rev["ts"]),
               "GIT_COMMITTER_DATE": mw_timestamp(rev["ts"])}
        git.run(repo, "add", "--", str(path.relative_to(repo)), env=env)
        msg = rev["comment"] or ("%s (rev %d)" % (rev["title"], rev["rev_id"]))
        git.run(repo, "commit", "-q", "--allow-empty", "--allow-empty-message",
             "-m", msg, env=env)
        state.write_text(str(i))
        if i % 200 == 0 or i == total:
            print("  %d/%d revisions" % (i, total), file=sys.stderr)
    print("replayed %d revisions into %s (%d aliased concepts)"
          % (total, repo, len(aliases)), file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", help="path to lagen.sqlite")
    ap.add_argument("out", help="content repo path (created/extended)")
    ap.add_argument("--limit", type=int, help="only the first N revisions (smoke test)")
    args = ap.parse_args()
    con = _connect(args.db)
    replay(con, args.out, args.limit)


if __name__ == "__main__":
    main()
