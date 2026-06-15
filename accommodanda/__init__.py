"""accommodanda — the rebuilt ferenda data pipeline.

Structure:
- ``lib/``   shared horizontal libraries (citation engine, util, errors)
- ``sfs/``   the statutes (acts) vertical — ``from accommodanda.sfs import parse_sfs``
- ``dv/``    the court-decisions (domstol) vertical
- ``build``  the make-like incremental build driver (the ``lagen`` CLI) that
             orchestrates the verticals over file freshness

Each vertical owns its full fetch → parse → artifact chain and its own
document model; shared code lives in ``lib`` and never calls back into a
source.
"""
