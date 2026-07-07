"""accommodanda — the rebuilt ferenda data pipeline.

Structure:
- ``lib/``        shared horizontal libraries (citation engine, catalog,
                   render, search, util, errors, …)
- ``sfs/``        the statutes (acts) vertical
- ``dv/``         the court-decisions (domstol) vertical
- ``forarbete/``  the legislative preparatory-works vertical (prop/sou/ds/dir)
- ``eurlex/``     the EU law vertical (EUR-Lex/CELLAR)
- ``foreskrift/`` the agency-regulations vertical
- ``avg/``        the JO/JK/ARN myndighetsavgöranden vertical
- ``remisser/``   the regeringen.se referral-response vertical
- ``wiki/``       the kommentar + begrepp sources (git-backed markdown)
- ``site/``       the editorial-chrome vertical (frontpage/om/sitenews)
- ``api/``        the REST/OpenAPI service + inline content editor
- ``build``       the make-like incremental build driver (the ``lagen`` CLI)
                   that orchestrates the verticals over file freshness

Each vertical owns its full fetch → parse → artifact chain and its own
document model; shared code lives in ``lib`` and never calls back into a
source.
"""
