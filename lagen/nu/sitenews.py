# -*- coding: utf-8 -*-
from ferenda.sources.general import Sitenews as BaseSitenews

class Sitenews(BaseSitenews):
    def tabs(self):
        if self.config.tabs:
            return [("Nyheter", self.dataset_uri())]
        else:
            return []

    news_feedsets_main_label = "Nyheter om webbtjänsten"
    toc_title = "Alla nyhetsflöden"
    readmore_label = "(läs mer)"
