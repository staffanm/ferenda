#!/usr/bin/env python3
"""
Layer-boundary checker for accommodanda/ (rule:lib-never-imports-vertical).

The load-bearing architecture rule made mechanical: `lib/` must never import
a vertical (or `api`); a vertical must never import a sibling vertical or
`api`. Only `build.py` and `api/` compose across the package.

Violations already known when this checker was introduced (review
2026-07-01 §3.1) are allowlisted until their planned fixes land; in
package-wide mode, entries that no longer match are reported as stale so
the allowlist can only shrink.

Usage:
    check-layers.py                    # whole package + stale-entry check
    check-layers.py FILE [FILE ...]    # just these files (Stop-hook mode)

Exit 1 if violations (or stale allowlist entries) were found.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

VERTICALS = {"sfs", "dv", "eurlex", "forarbete", "foreskrift", "avg", "wiki",
             "remisser"}
RESTRICTED = VERTICALS | {"api"}

# (module file relative to the package, imported accommodanda-submodule
# truncated to two components). Review §3.1 OPEN items; delete entries as
# the fixes land.
ALLOWLIST = {
    ("lib/render.py", "dv.naming"),
    ("lib/render.py", "eurlex.structure"),
    ("lib/render.py", "api.app"),
    ("lib/resolve.py", "dv.namedcases"),
    ("wiki/parse.py", "eurlex.structure"),
    ("wiki/annotate.py", "eurlex.structure"),
    ("sfs/correspond.py", "forarbete.kommentar"),
    ("sfs/correspond.py", "forarbete.structure"),
}


def zone(rel: Path) -> str:
    """Which layer a package-relative path belongs to: 'lib', a vertical
    name, 'api', or 'top' (build.py, config.py, ... -- unrestricted)."""
    top = rel.parts[0] if len(rel.parts) > 1 else ""
    if top in RESTRICTED or top == "lib":
        return top
    return "top"


def package_imports(tree: ast.AST, module_parts: tuple[str, ...]):
    """Package-internal imports as dotted paths relative to accommodanda
    ('dv.naming'), resolving explicit relative imports against the module's
    own package."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("accommodanda."):
                    yield alias.name.removeprefix("accommodanda.")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = module_parts[:len(module_parts) - node.level]
                target = ".".join(base + ((node.module,) if node.module else ()))
            else:
                target = node.module or ""
            if target != "accommodanda" and not target.startswith("accommodanda."):
                continue
            stem = target.removeprefix("accommodanda").lstrip(".")
            for alias in node.names:
                yield f"{stem}.{alias.name}".lstrip(".")


def check_file(path: Path, pkg: Path):
    """-> (violation messages, allowlist keys this file still exercises)."""
    rel = path.relative_to(pkg)
    src_zone = zone(rel)
    if src_zone in ("top", "api"):
        return [], set()
    module_parts = ("accommodanda",) + rel.parts[:-1]
    if rel.name != "__init__.py":
        module_parts += (rel.stem,)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        return [f"{rel.as_posix()}: unparseable: {e}"], set()

    violations, matched = [], set()
    for imported in package_imports(tree, module_parts):
        target_zone = imported.split(".", 1)[0]
        if target_zone not in RESTRICTED or target_zone == src_zone:
            continue
        key = (rel.as_posix(), ".".join(imported.split(".")[:2]))
        if key in ALLOWLIST:
            matched.add(key)
            continue
        rule = ("lib/ must never import a vertical or api"
                if src_zone == "lib"
                else f"vertical {src_zone}/ must never import "
                     f"{'api' if target_zone == 'api' else 'a sibling vertical'}")
        violations.append(
            f"{rel.as_posix()}: imports accommodanda.{imported} — {rule} "
            f"(rule:lib-never-imports-vertical). Move the shared machinery "
            f"to lib/, or compose in build.py.")
    return violations, matched


def main(argv: list[str]) -> int:
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    pkg = (project / "accommodanda").resolve()
    assert pkg.is_dir(), f"not a ferenda checkout: {pkg} missing"
    if argv:
        files = [f for f in (Path(a).resolve() for a in argv)
                 if f.suffix == ".py" and f.exists() and f.is_relative_to(pkg)]
        package_wide = False
    else:
        files = sorted(pkg.rglob("*.py"))
        package_wide = True

    violations: list[str] = []
    matched_keys: set[tuple[str, str]] = set()
    for f in files:
        file_violations, matched = check_file(f, pkg)
        violations += file_violations
        matched_keys |= matched

    if package_wide:
        violations += [
            f"stale allowlist entry in check-layers.py: {key} no longer "
            f"matches any import — delete it (the allowlist only shrinks)."
            for key in sorted(ALLOWLIST - matched_keys)]

    for v in violations:
        print(v)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
