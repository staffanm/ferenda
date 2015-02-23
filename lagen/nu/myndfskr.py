# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

# from ferenda.sources.legal.se import MyndFskr as OrigMyndFskr
from ferenda.sources.legal.se import myndfskr
from ferenda import CompositeRepository

# class MyndFskr(OrigMyndFskr):
#     pass


class CompositeMyndFskr(CompositeRepository):
    subrepos = [myndfskr.SJVFS, myndfskr.FFFS, myndfskr.ELSAKFS,
                myndfskr.NFS, myndfskr.STAFS, myndfskr.SKVFS, myndfskr.DIFS,
                myndfskr.SOSFS, myndfskr.DVFS]
    # might need some way of forcing config.localizeuri,
    # config.urlpath into the subrepos?
    tabs = [("Myndighetsföreskrifter", self.dataset_uri())]
