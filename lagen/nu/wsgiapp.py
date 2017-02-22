from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# sys
import os
import re
from urllib.parse import urlencode
from wsgiref.util import request_uri
from datetime import datetime

# 3rdparty
from rdflib import URIRef, Graph
from rdflib.namespace import SKOS, FOAF, DCTERMS, RDF, RDFS

# own
from ferenda import WSGIApp as OrigWSGIApp
from ferenda import elements
from ferenda.elements import html
from ferenda.fulltextindex import Between
from ferenda.sources.legal.se.legalref import LegalRef
from ferenda.sources.legal.se import SwedishCitationParser


class WSGIApp(OrigWSGIApp):
    """Subclass that overrides the search() method with specific features
       for lagen.nu.

    """

    snippet_length = 160
    def __init__(self, repos, inifile=None, **kwargs):
        super(WSGIApp, self).__init__(repos, inifile, **kwargs)
        sfsrepo = [repo for repo in repos if repo.alias == "sfs"][0]
        self.parser = SwedishCitationParser(
            LegalRef(LegalRef.RATTSFALL, LegalRef.LAGRUM, LegalRef.KORTLAGRUM, LegalRef.FORARBETEN, LegalRef.MYNDIGHETSBESLUT),
            sfsrepo.minter,
            sfsrepo.commondata,
            allow_relative=True)
        graph = Graph().parse(sfsrepo.resourceloader.filename("extra/sfs.ttl"), format="turtle")
        self.lagforkortningar = [str(o) for s, o in graph.subject_objects(DCTERMS.alternate)]
        self.paragraflag = []
        for s, o in graph.subject_objects(DCTERMS.alternate):
            basefile = sfsrepo.basefile_from_uri(str(s))
            distilledpath = sfsrepo.store.distilled_path(basefile)
            if os.path.exists(distilledpath):
                sg = Graph().parse(distilledpath)
                firstpara = URIRef(str(s) + "#P1")
                if sg.value(firstpara, RDF.type):
                    self.paragraflag.append(str(o).lower())
        self.lagnamn = [str(o) for s, o in graph.subject_objects(RDFS.label)]
        self.lagforkortningar_regex = "|".join(sorted(self.lagforkortningar, key=len, reverse=True))
            

    def parse_parameters(self, querystring, idx):
        q, param, pagenum, pagelen, stats = super(WSGIApp,
                                                  self).parse_parameters(querystring, idx)
        # if Autocomple call, transform q to suitable parameters (find
        # uri)
        if querystring.endswith("_ac=true"):
            uri = self.expand_partial_ref(q)
            if uri:
                param['uri'] = uri.lower() + "*"
            else:
                # normalize any page reference ("nja 2015 s 42" => "nja 2015 s. 42")
                q = q.lower()
                q = re.sub("\s*s\s*(\d)", " s. \\1", q)
                q = re.sub("^prop\s+", "prop. ", q)
                param['label'] = q + "*"
            q = None
        return q, param, pagenum, pagelen, stats

    def expand_partial_ref(self, partial_ref):
        # "TF" => "1 kap. tryckfrihetsförordningen: Om tryckfrihet"
        #         "2 kap. tryckfrihetsförordningen: Om allmänna handlingars offentlighet"
        # (https://lagen.nu/1949:105#K <- TF [1:1] -> https://lagen.nu/1949:105#K1P1 - "1P1")
        #
        # "TF 1" => "1 kap. tryckfrihetsförordningen: Om tryckfrihet"
        #           "10 kap. tryckfrihetsförordningen: Om särskilda tvångsmedel"
        # (https://lagen.nu/1949:105#K1 <- TF 1[:1] -> https://lagen.nu/1949:105#K1P1 - "P1"))
        #
        # "TF 1:" => "1 kap. 1 § tryckfrihetsförordningen: Med tryckfrihets förstås..."
        #            "1 kap. 2 § tryckfrihetsförordningen: Någon tryckningen föregående..."
        # (https://lagen.nu/1949:105#K1P <- TF 1:[1] -> https://lagen.nu/1949:105#K1P1 - "1")
        # 
        # "TF 1:1" => "1 kap. 1 § tryckfrihetsförordningen: Med tryckfrihets förstås..."
        #             "1 kap. 10 § tryckfrihetsförordningen: Denna förordning är inte..."
        # (https://lagen.nu/1949:105#K1P1 <- TF 1:1)
        # 
        # "PUL 3" => "3 § personuppgiftslag: I denna lag används följande beteckningar..."
        #            "30 § personuppgiftslagen: Ett personuppgiftsbiträde och den eller..."
        # (https://lagen.nu/1998:204#P3" <- "PUL 3 §"


        m = re.match("(%s) *(\d*\:?\d*)$" % self.lagforkortningar_regex, partial_ref, re.IGNORECASE) 
        if not m:
            return
        law, part = m.groups()
        paragrafmode = law.lower() in self.paragraflag
        if part:
            if paragrafmode:
                extra = " §"
                remove = 0
            else:
                if ":" in part:
                    chap, sect = part.split(":")
                    if sect:
                        extra = ""
                        remove = 0
                    else:
                        extra = "1"
                        remove = 1
                else:
                    extra = ":1"
                    remove = 2
        else:
            if paragrafmode:
                extra = " 1 §"
                remove = 1
            else:
                extra =  " 1:1"
                remove = 3
        partial_ref += extra
        res = self.parser.parse_string(partial_ref)
        uri = ""
        if hasattr(res[0], 'uri'):
            uri = res[0].uri
        if remove:
            uri = uri[:-remove]
        return uri
        
    def query(self, environ):
        ac_query = environ['QUERY_STRING'].endswith("_ac=true")
        res = super(WSGIApp, self).query(environ)
        if ac_query:
            return res['items']
        else:
            return res
        
    def mangle_result(self, hit, ac_query=False):
        if ac_query:
            if 'rpubl_referatrubrik' in hit:
                hit['desc'] = hit['rpubl_referatrubrik'][:self.snippet_length]
                del hit['rpubl_referatrubrik']
            elif 'rdf_type' in hit and hit['rdf_type'].endswith("#Proposition"):
                hit['desc'] = hit['dcterms_title']
                hit['label'] = hit['dcterms_identifier']
            else:
                hit['desc'] = hit['matches']['text'][:self.snippet_length]
            del hit['matches']
            hit['url'] = hit['iri']
            del hit['iri']
        return hit

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
                label = str(r['label']) # flattens any nested element
                                        # structure, eg
                                        # <p><strong><em>foo</em></strong></p>
                                        # -> foo
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
        if 'text' not in r:
            r['text'] = []
        r['text'].insert(0, ": ")
        r['text'].insert(0, elements.LinkMarkup(r.get('label', ['(beteckning saknas)']),
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

