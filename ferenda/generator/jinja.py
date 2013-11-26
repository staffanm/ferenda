# extract superclass later

class JinjaGenerator():
    def __init__(docrepo, **kwargs):
        self.docrepo = docrepo
        self.config = LayeredConfig(kwargs)

    def toc():
        template = config.toctemplate
        for page, data in docrepo.toc_pagesets():
            pagename = docrepo.toc_pagefile(page)
            render(template, data, pagename)
        


    def render(
        
