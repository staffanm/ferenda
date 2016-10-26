from urllib.parse import urlencode
from wsgiref.util import request_uri
from datetime import datetime

from rdflib import URIRef
from rdflib.namespace import SKOS, FOAF

from ferenda import WSGIApp as OrigWSGIApp
from ferenda import elements
from ferenda.elements import html
from ferenda.fulltextindex import Between

class WSGIApp(OrigWSGIApp):
    """Subclass that overrides the search() method with specific features for
lagen.nu."""

    def __init__(self, repos, inifile=None, **kwargs):
        super(WSGIApp, self).__init__(repos, inifile, **kwargs)


    def search(self, environ, start_response):
        """WSGI method, called by the wsgi app for requests that matches
           ``searchendpoint``."""
        queryparams = self._search_parse_query(environ['QUERY_STRING'])
        # massage queryparams['issued'] if present, then restore it
        y = None
        if 'issued' in queryparams:
            y = int(queryparams['issued'])
            queryparams['issued'] = Between(datetime(y, 1, 1),
                                            datetime(y, 12, 31, 23, 59, 59))
        res, pager = self._search_run_query(queryparams)
        if y:
            queryparams['issued'] = str(y)

        if pager['totalresults'] == 1:
            resulthead = "1 träff"
        else:
            resulthead = "%s träffar" % pager['totalresults']
        resulthead += " för '%s'" % queryparams.get("q")

        doc = self._search_create_page(resulthead)
        if hasattr(res, 'aggregations'):
            doc.body.append(self._search_render_facets(res.aggregations, queryparams, environ))
        for r in res:
            if 'label' not in r:
                label = r['uri']
            elif isinstance(r['label'], list):
                label = r['label'][0]
            else:
                label = r['label']
            rendered_hit = html.Div(
                [html.B([elements.Link(label, uri=r['uri'])], **{'class': 'lead'})],
                **{'class': 'hit'})
            if r.get('text'):
                rendered_hit.append(html.P([r.get('text', '')]))
            if 'innerhits' in r:
                for innerhit in r['innerhits']:
                    rendered_hit.append(self._search_render_innerhit(innerhit))
            doc.body.append(rendered_hit)
        pagerelem = self._search_render_pager(pager, queryparams,
                                              environ['PATH_INFO'])
        doc.body.append(html.Div([
            html.P(["Träff %(firstresult)s-%(lastresult)s "
                    "av %(totalresults)s" % pager]), pagerelem],
                                 **{'class':'pager'}))
        data = self._search_transform_doc(doc)
        return self._return_response(data, start_response)

    def _search_render_innerhit(self, innerhit):
        r = innerhit
        r['text'].insert(0, ": ")
        r['text'].insert(0, elements.Link(r.get('label', '(beteckning saknas)'),
                                          uri=r['uri']))
        return html.P(r['text'], **{'class': 'innerhit'})

    repolabels = {'sfs': 'Författningar',
                  'prop': 'Propositioner',
                  'ds': 'Ds',
                  'sou': 'SOU:er',
                  'myndfs': 'Myndighetsföreskrifter',
                  'dir': 'Kommittedirektiv',
                  'mediawiki': 'Lagkommentarer',
                  'arn': 'Beslut från ARN',
                  'dv': 'Domar',
                  'jk': 'Beslut från JK',
                  'jo': 'Beslut från JO',
                  'static': 'Om lagen.nu'}
    facetlabels = {'type': 'Dokumenttyp',
                   'creator': 'Källa',
                   'issued': 'År'}

    
    def _search_render_facets(self, facets, queryparams, environ):
        facetgroups = []
        commondata = self.repos[0].commondata
        searchurl = request_uri(environ, include_query=False)
        for facetresult in ('type', 'creator', 'issued'):
            if facetresult in facets:
                if facetresult in queryparams:
                    # the user has selected a value for this
                    # particular facet, we should not display all
                    # buckets (but offer a link to reset the value)
                    qpcopy = dict(queryparams)
                    del qpcopy[facetresult]
                    href = "%s?%s" % (searchurl, urlencode(qpcopy))
                    val = queryparams[facetresult]
                    if facetresult == "creator":
                        val = self.repos[0].lookup_label(val)
                    elif facetresult == "type":
                        val = self.repolabels.get(val, val)
                    lbl = "%s: %s" % (self.facetlabels.get(facetresult,
                                                           facetresult),
                                      val)
                    facetgroups.append(
                        html.LI([lbl,
                                 html.A("\xa0",
                                        **{'href': href,
                                           'class': 'glyphicon glyphicon-remove'})]))
                else:
                    facetgroup = []
                    for bucket in facets[facetresult]['buckets']:
                        if facetresult == 'type':
                            lbl = self.repolabels.get(bucket['key'], bucket['key'])
                            key = bucket['key']
                        elif facetresult == 'creator':
                            k = URIRef(bucket['key'])
                            pred = SKOS.altLabel if commondata.value(k, SKOS.altLabel) else FOAF.name
                            lbl = commondata.value(k, pred)
                            key = bucket['key']
                        elif facetresult == "issued":
                            lbl = bucket["key_as_string"]
                            key = lbl
                        qpcopy = dict(queryparams)
                        qpcopy[facetresult] = key
                        href = "%s?%s" % (searchurl, urlencode(qpcopy))
                        facetgroup.append(html.LI([html.A(
                            "%s" % (lbl), **{'href': href}),
                                                   html.Span([str(bucket['doc_count'])], **{'class': 'badge pull-right'})]))
                    lbl = self.facetlabels.get(facetresult, facetresult)
                    facetgroups.append(html.LI([html.P([lbl]),
                                                html.UL(facetgroup)]))
        return html.Div(facetgroups, **{'class': 'facets'})

