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
        if hasattr(res, 'aggregations'):
            doc.body.append(self._search_render_facets(res.aggregations))
        for r in res:
            if 'label' not in r:
                label = r['uri']
            elif isinstance(r['label'], list):
                label = r['label'][0]
            else:
                label = r['label']
            doc.body.append(html.Div(
                [html.B([elements.Link(label, uri=r['uri'])], **{'class': 'lead'}),
                 html.P([r.get('text', '')])], **{'class': 'hit'}))
        pagerelem = self._search_render_pager(pager, queryparams,
                                              environ['PATH_INFO'])
        doc.body.append(html.Div([
            html.P(["Träff %(firstresult)s-%(lastresult)s "
                    "av %(totalresults)s" % pager]), pagerelem],
                                 **{'class':'pager'}))
        data = self._search_transform_doc(doc)
        return self._return_response(data, start_response)

    def _search_render_facets(self, facets):
        facetgroups = []
        for facetresult in ('type', 'creator', 'issued'):
            if facetresult in facets:
                facetgroup = []
                for bucket in facets[facetresult]['buckets']:
                    facetgroup.append(html.LI([
                        "%s(%s)" % (bucket.get('key_as_string', bucket['key']),
                                     bucket['doc_count'])]))
                facetgroups.append(html.LI([html.P([facetresult]),
                                            html.UL(facetgroup)]))
        return html.Div(facetgroups, **{'class': 'facets'})
