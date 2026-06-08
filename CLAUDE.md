## Project overview

Ferenda is a python framework for downloading, parsing and connecting large repositories of interconnected documents. It is primarily used for swedish legal information.

The project is severely outdated and overengineered in all the wrong ways. It has dependencies that are deprecated or irrelevant. Any refactoring may safely assume that we now target python 3.10+, and that deprecated dependencies can be removed (and if needed replaced with modern equivalents)

## Coding conventions

- Avoid fallback code in general -- assert how the environment should be.
- Don't catch exceptions unless you know how to fix and recover from the root cause. Catching just to log (or worse, swallow) is useless
- Only create temporary holding variables where the benefit is obvious - chaining expressions is usually clearer
- Dont use in-function imports. All imports go at the top of the file, grouped by stdlib, third-party, local.
- DRY and focused: consolidate helpers, keep functions small, avoid "just in case" complexity

## AI Agent Behavior

- Act with integrity
- Ask, don't guess
- Questions are not order
- No glazing
- Be critical
- Don't fix whats not broken

# modernization-notes

## Python 2 support removal (completed 2025-01-12)

Successfully modernized the entire codebase from Python 2/3 compatibility to Python 3.10+ only:

### Key findings:
- **Codebase structure**: 85+ Python files with `from __future__ import` statements, primarily in `ferenda/` directory
- **Six library usage**: Limited to 4 files but critical - used for string types, urllib, and iteration compatibility
- **Compatibility patterns**: Heavy use of `collections.Iterable` (needs `collections.abc.Iterable` in Python 3.10+)
- **Setup complexity**: Version-specific dependency injection based on Python version checks

### Files with active six usage (all updated):
- `ferenda/thirdparty/coin.py`: `six.moves.urllib_parse.urljoin`, `six.text_type as str`
- `ferenda/sources/legal/se/swedishlegalsource.py`: `six.text_type as str`, `six.string_types`
- `ferenda/sources/legal/se/offtryck.py`: `six.string_types`
- `test/testDevel.py`: `six.text_type as str`

### Key replacement patterns used:
- `six.text_type` → remove (native `str` in Python 3)
- `six.string_types` → `str`
- `six.moves.urllib_parse` → `urllib.parse`
- `collections.Iterable` → `collections.abc.Iterable`
- Removed `future` dependency from install_requires

### Architecture insights:
- **ferenda/compat.py**: Was a compatibility shim, now simplified for Python 3.10+ testing imports
- **setup.py**: Had complex version-specific dependency logic, now streamlined
- **Requirements**: Separate py2/py3 requirements files, consolidated to Python 3.10+
- **Test structure**: Uses unittest.mock (available in stdlib since 3.3)

### Areas needing attention for future modernization:
- Many dependencies are old (html5lib, rdflib versions from 2014-era)
- Could likely remove some polyfill dependencies like `cached_property` (builtin since 3.8)
- `grako` parser dependency might have modern alternatives
- Some thirdparty/ vendored code could be replaced with modern packages