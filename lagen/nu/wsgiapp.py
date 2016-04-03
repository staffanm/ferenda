from ferenda import WSGIApp as OrigWSGIApp
from ferenda import elements
from ferenda.elements import html

class WSGIApp(OrigWSGIApp):
    """Subclass that overrides the search() method with specific features for
lagen.nu."""

    def __init__(self, repos, inifile=None, **kwargs):
        super(WSGIApp, self).__init__(repos, inifile, **kwargs)


    def search(self, environ, start_response):
        """WSGI method, called by the wsgi app for requests that matches
           ``searchendpoint``."""
        queryparams = self._search_parse_query(environ['QUERY_STRING'])
        res, pager = self._search_run_query(queryparams)

        if pager['totalresults'] == 1:
            resulthead = "1 träff"
        else:
            resulthead = "%s träffar" % pager['totalresults']
        resulthead += " för '%s'" % queryparams.get("q")

        doc = self._search_create_page(resulthead)
        
        for r in res:
            from pudb import set_trace; set_trace()
            if not 'dcterms_title' in r or r['dcterms_title'] is None:
                r['dcterms_title'] = r['uri']
            if r.get('dcterms_identifier', False):
                r['dcterms_title'] = r['dcterms_identifier'] + ": " + r['dcterms_title']
            doc.body.append(html.Div(
                [html.H2([elements.Link(r['dcterms_title'], uri=r['uri'])]),
                 r.get('text', '')], **{'class': 'hit'}))
        pagerelem = self._search_render_pager(pager, queryparams,
                                              environ['PATH_INFO'])
        doc.body.append(html.Div([
            html.P(["Träff %(firstresult)s-%(lastresult)s "
                    "av %(totalresults)s" % pager]), pagerelem],
                                 **{'class':'pager'}))
        data = self._search_transform_doc(doc)
        return self._return_response(data, start_response)
