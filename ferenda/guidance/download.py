"""Entry point for the guidance harvest: one run per ``<utgivare>/<serie>``
scope.

The scopes are the source's organs in `lib.harvest.dispatch_scopes`' sense --
separate upstreams sharing nothing but this entry point. A body's series are
separate upstreams even within one body (the EDPB's two open series come off
its sitemap, its closed WP29 series off the Commission newsroom), and two
bodies share nothing at all, so one flat scope namespace covers both splits:
``edpb/riktlinjer``, ``edpb/wp``, ``eba/gl``.

That namespace is also the basefile's head, which is what lets ``--only
edpb/riktlinjer/05-2020`` reach exactly one runner: `dispatch_scopes` gives an
``--only`` to the scope whose name it starts with and withholds it from every
other.

Storage is ``site/data/downloaded/guidance/<utgivare>/`` -- the issuer is the
directory, and the slugged basefile the file stem
(``edpb/edpb-riktlinjer-05-2020.pdf``), which is `lib.harvest`'s record-store
convention with the issuer where `rs` puts the myndighet.
"""

from ..lib.harvest import dispatch_scopes
from . import (
    acer_download,
    berec_download,
    easa_download,
    eba_download,
    edpb_download,
    edps_download,
    eiopa_download,
    enisa_download,
    esma_download,
    euipo_download,
    eurlex_download,
)
from .issuers import (
    ACER,
    BEREC,
    BY_KOD,
    EASA,
    EBA,
    EDPB,
    EDPS,
    EIOPA,
    ENISA,
    ESMA,
    EUIPO,
)

# every scope this source harvests -> the runner that owns it. Built from the
# registry rather than written out, so adding a series to an issuer adds its
# scope here too (rule:second-use-goes-to-lib -- the per-issuer routines stay
# per-issuer, only the dispatch is shared).
# A scope is one upstream, not one series. The EDPB's two open series come off
# its sitemap and its closed WP29 series off the Commission newsroom, so it has
# three; the EBA publishes both of its series in one tree, so it has one and
# the series is a property of the document rather than of the walk. Splitting
# it would walk the same 326 pages twice, concurrently.
# EASA the same, for the same reason: an annex's own name says whether it
# holds AMC, GM or both, so its three series come off one walk of one library.
# ACER has one for a third reason: its three series are three separate listing
# pages, so splitting would duplicate nothing -- but they are one host, and
# three scopes would put three walks on acer.europa.eu at once.
SYNC = {**{"%s/%s" % (EDPB.kod, kod): runner
           for kod, runner in edpb_download.SYNC.items()},
        EBA.kod: eba_download.eba_sync,
        EASA.kod: easa_download.easa_sync,
        ACER.kod: acer_download.acer_sync,
        ENISA.kod: enisa_download.enisa_sync,
        # Esma has one for the first reason again: its library is one paged
        # listing, and its single series comes off one facet of it.
        ESMA.kod: esma_download.esma_sync,
        # Berec for the first reason once more: one register, one category
        # page of it, one series.
        BEREC.kod: berec_download.berec_sync,
        # the EDPS for a fourth reason: its two series *are* two separate
        # views, but every view on edps.europa.eu is behind an AWS WAF
        # challenge, so a second scope would run a second headful Chrome at
        # the same host and pay the challenge twice.
        EDPS.kod: edps_download.edps_sync,
        # Eiopa for the first reason: its two facets are two pages of one
        # library and one leaf can be named by both, so walking them together
        # is what lets the second facet's copy be recognised rather than filed
        # twice.
        EIOPA.kod: eiopa_download.eiopa_sync,
        # EUIPO for a fifth reason: its three series are three produktfamiljer
        # of one delivery app, and which PDFs a family is carried as is read
        # off the same innehållsförteckning walk. One scope also keeps the
        # whole run to about seventy requests at one host.
        EUIPO.kod: euipo_download.euipo_sync,
        # the ECB and the ESRB publish in EUT rather than on their own sites,
        # so they come out of CELLAR (`lib.cellar`) instead of a page walk. One
        # scope per body: a body's works are one enumeration of what CELLAR
        # holds under its corporate-body URI, and the series is a property of
        # the document rather than of the walk.
        **eurlex_download.SYNC}

# What can be harvested today. `issuers.SITE_SCOPES` names one scope per
# <utgivare>/<serie>; this names one per upstream walk, which for ten of the
# twelve bodies covers all of that body's series at once. The default run is
# this map, never the registry, so a series without a walk behind it cannot
# make a whole-source run fail.
SCOPES = tuple(SYNC)

# What a run reports it is harvesting from. This source has no single index --
# each issuing body publishes its own -- so it names the bodies rather than
# printing one body's sitemap over another body's harvest.
# It is derived from SYNC rather than written out, so a new runner cannot
# leave its body off the banner (a hand-kept list had already lost three).
ORIGIN = ", ".join(sorted(
    {eurlex_download.ORIGIN if scope in eurlex_download.SYNC
     else BY_KOD[scope.split("/")[0]].base for scope in SYNC}))


def sync(root, scopes=None, full=False, only=None, limit=None, delay=0.5,
         jobs=1):
    """Download the named scopes (default all of them). Returns
    ``{scope: (seen, new)}``.

    Scopes are separate hosts as well as separate corpora, so they fan out the
    way `rs`/`avg`/`foreskrift` do -- the EDPB, the Commission newsroom and the
    EBA are three upstreams that never wait on each other. Concurrency is
    across scopes only: each runner still paces itself against its own host."""
    return dispatch_scopes(root, scopes, SYNC, SCOPES, full=full,
                           only=only, limit=limit, delay=delay, jobs=jobs,
                           label="guidance download")


__all__ = ["ORIGIN", "SCOPES", "SYNC", "sync"]
