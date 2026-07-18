# Memory index

- [accommodanda storage layout](accommodanda-storage-layout.md) — target on-disk + URL conventions (dv→dom/, per-type förarbete URLs, shared wiki dump, uniform downloaded/artifact)
- [föreskrifter are as-published, not consolidated](foreskrift-as-published-not-consolidated.md) — immutable docs; consolidation metadata is "last amendment incorporated", never a date/cutoff
- [relocate, don't regenerate](relocate-dont-regenerate.md) — wrong path + correct content ⇒ move the files, never delete-and-rebuild
- [.gitignore blocks .claude/agents+skills](gitignore-blocks-claude-agents-and-skills.md) — new project agents/skills are untracked as configured; needs a decision before they'll survive a clone
- [soukb body re-download](soukb-body-redownload.md) — soukb-scans rebuilds KB SOU bodies (1922-99) from sou.kb.se index; PDF is the body, multi-vol → files list; built, not run at scale
- [forarbete tree year-segmented](forarbete-tree-year-segmented.md) — downloaded+artifact now <typ>/<year>/<slug>; layout.fa_dir/fa_year/fa_record_file; pm→_; ~287k files migrated; URLs unchanged
