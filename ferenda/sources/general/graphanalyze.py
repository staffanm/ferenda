# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function
# This is a set of class methods, originally from
# ferenda.sources.legal.eu.EurlexTreaties, which perform graph
# analysis on documents from ferenda.sources.legal.eu.EurlexCaselaw.
# The main idea is to let them live in
# ferenda.sources.general.GraphAnalyze, generalize them, and make them
# available for any docrepo who wants their stuff analyzed (by
# inheriting from DocumentRepository and this class). For the time
# being though, the code should not be included in tests and coverage
# -- it's dead code, but with a hope of resuscitation.
import os
import subprocess
from datetime import datetime
from operator import itemgetter
from pprint import pprint

from lxml import etree as ET
from rdflib import Graph, Literal, BNode, URIRef, Collection, Namespace, RDF
from whoosh import analysis, fields, query, scoring
from whoosh.filedb.filestore import RamStorage, FileStorage

try:
    import matplotlib.pyplot as plt
    import matplotlib.cm as cm
    import numpy.array as na
    import networkx
    from networkx.algorithms.link_analysis.pagerank_alg import pagerank
    from networkx.algorithms.link_analysis.hits_alg import hits
except ImportError:
    plt = None
    cm = None
    na = None
    networkx = None
    pagerank = None
    hits = None

from ferenda import TripleStore
from ferenda import util

DCT = Namespace(util.ns['dct'])
RINFOEX = Namespace('http://lagen.nu/terms#')


class GraphAnalyze(object):

    def prep_annotation_file(self, basefile):
        goldstandard = self.eval_get_goldstandard(basefile)
        baseline_set = self.eval_get_ranked_set_baseline(basefile)
        baseline_map = self.eval_calc_map(
            self.eval_calc_aps(baseline_set, goldstandard))
        print("Baseline MAP %f" % baseline_map)
        self.log.info("Calculating ranked set (pagerank, unrestricted)")
        pagerank_set = self.eval_get_ranked_set(basefile, "pagerank",
                                                age_compensation=False,
                                                restrict_cited=False)
        pagerank_map = self.eval_calc_map(
            self.eval_calc_aps(pagerank_set, goldstandard))
        print("Pagerank MAP %f" % pagerank_map)
        sets = [{'label': 'Baseline',
                 'data': baseline_set},
                {'label': 'Gold standard',
                 'data': goldstandard},
                {'label': 'PageRank',
                 'data': pagerank_set}]

        g = Graph()
        g.bind('dct', self.ns['dct'])
        g.bind('rinfoex', self.ns['rinfoex'])

        XHT_NS = "{http://www.w3.org/1999/xhtml}"
        tree = ET.parse(self.parsed_path(basefile))
        els = tree.findall("//" + XHT_NS + "div")
        articles = []
        for el in els:
            if 'typeof' in el.attrib and el.attrib['typeof'] == "eurlex:Article":
                article = str(el.attrib['id'][1:])
                articles.append(article)
        for article in articles:
            self.log.info("Results for article %s" % article)
            articlenode = URIRef(
                "http://lagen.nu/ext/celex/12008E%03d" % int(article))
            resultsetcollectionnode = BNode()
            g.add((resultsetcollectionnode, RDF.type, RDF.List))
            rc = Collection(g, resultsetcollectionnode)
            g.add((articlenode, DCT["relation"], resultsetcollectionnode))
            for s in sets:
                resultsetnode = BNode()
                listnode = BNode()
                rc.append(resultsetnode)
                g.add((resultsetnode, RDF.type, RINFOEX[
                      "RelatedContentCollection"]))
                g.add((resultsetnode, DCT["title"], Literal(s["label"])))
                g.add((resultsetnode, DCT["hasPart"], listnode))
                c = Collection(g, listnode)
                g.add((listnode, RDF.type, RDF.List))
                if article in s['data']:
                    print(("    Set %s" % s['label']))
                    for result in s['data'][article]:
                        resnode = BNode()
                        g.add((resnode, DCT["references"], Literal(result[0])))
                        g.add((resnode, DCT["title"], Literal(result[1])))
                        c.append(resnode)
                        print(("        %s" % result[1]))

        return self.graph_to_annotation_file(g, basefile)

    def graph_to_image(self, graph, imageformat, filename):
        import pydot
        import rdflib
        dot = pydot.Dot()
        # dot.progs = {"dot": "c:/Program Files/Graphviz2.26.3/bin/dot.exe"}

        # code from rdflib.util.graph_to_dot, but adjusted to handle unicode
        nodes = {}
        for s, o in graph.subject_objects():
            for i in s, o:
                if i not in list(nodes.keys()):
                    if isinstance(i, rdflib.BNode):
                        nodes[i] = repr(i)[7:]
                    elif isinstance(i, rdflib.Literal):
                        nodes[i] = repr(i)[16:-1]
                    elif isinstance(i, rdflib.URIRef):
                        nodes[i] = repr(i)[22:-2]

        for s, p, o in graph.triples((None, None, None)):
            dot.add_edge(pydot.Edge(nodes[s], nodes[o], label=repr(p)[22:-2]))

        self.log.debug("Writing %s format to %s" % (imageformat, filename))
        util.ensure_dir(filename)
        dot.write(path=filename, prog="dot", format=imageformat)
        self.log.debug("Wrote %s" % filename)

    top_articles = []
    graph_filetype = "png"
    # yields an iterator of Article URIs

    def _articles(self, basefile):
        # Those articles we have gold standard sets for now
        self.top_articles = ['http://lagen.nu/ext/celex/12008E263',
                             'http://lagen.nu/ext/celex/12008E101',
                             'http://lagen.nu/ext/celex/12008E267',
                             'http://lagen.nu/ext/celex/12008E107',
                             'http://lagen.nu/ext/celex/12008E108',
                             'http://lagen.nu/ext/celex/12008E296',
                             'http://lagen.nu/ext/celex/12008E258',
                             'http://lagen.nu/ext/celex/12008E045',
                             'http://lagen.nu/ext/celex/12008E288',
                             'http://lagen.nu/ext/celex/12008E034',
                             ]

        # For evaluation, only return the 20 top cited articles (which
        # analyze_article_citations incidentally compute for us). For
        # full-scale generation, use commented-out code below.
        if not self.top_articles:
            self.top_articles = self.analyze_article_citations(quiet=True)
        return self.top_articles

        # For full-scale processing, return all articles present in e.g. TFEU:
        # XHT_NS = "{http://www.w3.org/1999/xhtml}"
        #tree = ET.parse(self.parsed_path(basefile))
        #els = tree.findall("//"+XHT_NS+"div")
        # for el in els:
        #    if 'typeof' in el.attrib and el.attrib['typeof'] == "eurlex:Article":
        #        yield el.attrib['about']

    # returns a RDFLib.Graph
    def _sameas(self):
        sameas = Graph()
        sameas_rdf = util.relpath(
            os.path.dirname(__file__) + "/../res/eut/sameas.n3")
        sameas.load(sameas_rdf, format="n3")
        return sameas

    def _query_cases(self, article, sameas):
        pred = util.ns['owl'] + "sameAs"
        q = ""
        if article:
            q += "{ ?subj eurlex:cites <%s> }\n" % article
            for equiv in sameas.objects(URIRef(article), URIRef(pred)):
                q += "    UNION { ?subj eurlex:cites <%s> }\n" % equiv

        return """
PREFIX eurlex:<http://lagen.nu/eurlex#>
PREFIX dct:<http://purl.org/dc/terms/>
SELECT DISTINCT ?subj WHERE {
    ?subj ?pred ?obj .
    %s
    FILTER (regex(str(?subj), "^http://lagen.nu/ext/celex/6"))
}
""" % (q)

    # Returns a python list of dicts
    def _query_cites(self, article, sameas, restrict_citing, restrict_cited, year=None):
        if not year:
            year = datetime.datetime.today().year
        pred = util.ns['owl'] + "sameAs"
        q = ""
        if restrict_citing:
            q += "{ ?subj eurlex:cites <%s> }\n" % article
            for equiv in sameas.objects(URIRef(article), URIRef(pred)):
                q += "    UNION { ?subj eurlex:cites <%s> }\n" % equiv

        if restrict_cited:
            if q:
                q += ".\n"
            q = "{?obj eurlex:cites <%s>}\n" % article
            for equiv in sameas.objects(URIRef(article), URIRef(pred)):
                q += "    UNION { ?obj eurlex:cites <%s> }\n" % equiv

        return """
PREFIX eurlex:<http://lagen.nu/eurlex#>
PREFIX dct:<http://purl.org/dc/terms/>
SELECT DISTINCT ?subj ?pred ?obj ?celexnum WHERE {
    ?subj ?pred ?obj .
    ?subj eurlex:celexnum ?celexnum.
    %s
    FILTER (regex(str(?obj), "^http://lagen.nu/ext/celex/6") &&
            ?pred = eurlex:cites &&
            str(?celexnum) < str("6%s"@en))
}
""" % (q, year)

    def temp_analyze(self):
        store = TripleStore(self.config.storetype,
                            self.config.storelocation,
                            self.config.storerepository)
        # sq = self._query_cites('http://lagen.nu/ext/celex/12008E045',self._sameas(),False, True, 2012)
        sq = self._query_cites(None, self._sameas(), False, False, 2012)
        print(sq)
        cites = store.select(sq, format="python")
        self.log.debug(
            "    Citation graph contains %s citations" % (len(cites)))

        # remove duplicate citations, self-citations and pinpoints
        # in citations
        citedict = {}
        for cite in cites:
            # print repr(cite)
            if "-" in cite['obj']:
                cite['obj'] = cite['obj'].split("-")[0]

            if (cite['subj'] != cite['obj']):
                citedict[(cite['subj'], cite['obj'])] = True

        self.log.debug(
            "    Normalized graph contains %s citations" % len(citedict))

        degree = {}
        for citing, cited in list(citedict.keys()):
            if citing not in degree:
                degree[citing] = []
            if cited not in degree:
                degree[cited] = []
            degree[cited].append(citing)

        return

    def analyze(self):
        articles = self.analyze_article_citations(num_of_articles=10)
        # articles = self._articles('tfeu')
        self.analyze_baseline_queries(articles)
        self.analyze_citation_graphs(articles)

    def analyze_article_citations(self, num_of_articles=20, quiet=False):
        """Prints and returns a list of the top 20 most important articles in the
        TFEU treaty, as determined by the number of citing cases."""

        # Create a mapping of article equivalencies, eg Art 28 TEC == Art 34 TFEU
        sameas = self._sameas()
        equivs = {}
        pred = util.ns['owl'] + "sameAs"
        for (s, o) in sameas.subject_objects(URIRef(pred)):
            equivs[str(o)] = str(s)
        self.log.debug(
            "Defined %s equivalent article references" % len(equivs))

        # Select unique articles citings
        store = TripleStore(self.config.storetype,
                            self.config.storelocation,
                            self.config.storerepository)
        sq = """PREFIX eurlex:<http://lagen.nu/eurlex#>
                SELECT DISTINCT ?case ?article WHERE {
                    ?case eurlex:cites ?article .
                    FILTER (regex(str(?article), "^http://lagen.nu/ext/celex/1"))
             }"""
        cites = store.select(sq, format="python")

        citationcount = {}
        unmapped = {}
        self.log.debug("Going through %s unique citations" % len(cites))
        for cite in cites:
            article = cite['article'].split("-")[0]
            if "12008M" in article:
                pass
            elif article in equivs:
                article = equivs[article]
            else:
                if article in unmapped:
                    unmapped[article] += 1
                else:
                    unmapped[article] = 1
                article = None

            # Keep track of the number of citing cases
            if article:
                if article in citationcount:
                    citationcount[article] += 1
                else:
                    citationcount[article] = 1

        # Report the most common cites to older treaty articles that
        # we have no equivalents for in TFEU
        # sorted_unmapped = sorted(unmapped.iteritems(), key=itemgetter(1))[-num_of_articles:]
        # if not quiet:
        #    print "UNMAPPED:"
        #    pprint(sorted_unmapped)

        # Report and return the most cited articles
        sorted_citationcount = sorted(iter(list(
            citationcount.items())), key=itemgetter(1))[-num_of_articles:]
        if not quiet:
            print("CITATION COUNTS:")
            pprint(sorted_citationcount)
        return [x[0] for x in reversed(sorted_citationcount)]

    def analyze_baseline_queries(self, analyzed_articles, num_of_keyterms=5):
        basefile = "tfeu"
        # Helper from http://effbot.org/zone/element-lib.htm

        def flatten(elem, include_tail=0):
            text = elem.text or ""
            for e in elem:
                text += flatten(e, 1)
                if include_tail and elem.tail:
                    text += elem.tail
            return text
        # step 1: Create a temporary whoosh index in order to find out
        # the most significant words for each article

        #ana = analysis.StandardAnalyzer()
        ana = analysis.StemmingAnalyzer()
        # vectorformat = formats.Frequency(ana)
        schema = fields.Schema(article=fields.ID(unique=True),
                               content=fields.TEXT(analyzer=ana,
                                                   stored=True))

        st = RamStorage()
        tmpidx = st.create_index(schema)
        w = tmpidx.writer()

        XHT_NS = "{http://www.w3.org/1999/xhtml}"
        tree = ET.parse(self.parsed_path(basefile))
        els = tree.findall("//" + XHT_NS + "div")
        articles = []
        for el in els:
            if 'typeof' in el.attrib and el.attrib['typeof'] == "eurlex:Article":
                text = util.normalize_space(flatten(el))
                article = str(el.attrib['about'])
                articles.append(article)
                w.update_document(article=article, content=text)
        w.commit()
        self.log.info("Indexed %d articles" % len(articles))

        # Step 2: Open the large whoosh index containing the text of
        # all cases. Then, for each article, use the 5 most distinctive terms
        # (filtering away numbers) to create a query against that index
        tempsearch = tmpidx.searcher()
        g = Graph()
        g.bind('celex', 'http://lagen.nu/ext/celex/')
        g.bind('ir', 'http://lagen.nu/informationretrieval#')
        IR = Namespace('http://lagen.nu/informationretrieval#')
        # celex:12008E264 ir:keyterm "blahonga"@en.

        outfile = self.generic_path("keyterms", "analyzed", ".tex")
        util.ensure_dir(outfile)
        fp = open(outfile, "w")
        fp.write("""
\\begin{tabular}{r|%s}
  \\hline
  \\textbf{Art.} & \\multicolumn{%s}{l}{\\textbf{Terms}} \\\\
  \\hline
""" % ("l" * num_of_keyterms, num_of_keyterms))

        for article in analyzed_articles:
            fp.write(str(int(article.split("E")[1])))
            r = tempsearch.search(query.Term("article", article))
            terms = r.key_terms("content", numterms=num_of_keyterms + 1)
            terms = [t[0] for t in terms if not t[0].isdigit(
            )][:num_of_keyterms]
            for term in terms:
                fp.write(" & " + term)
                g.add((
                    URIRef(article), IR["keyterm"], Literal(term, lang="en")))
            self.log.debug("Article %s:%r" % (article, terms))
            fp.write("\\\\\n")
        fp.write("""
  \\hline
\\end{tabular}
""")
        fp.close()

        outfile = self.generic_path("keyterms", "analyzed", ".n3")
        util.ensure_dir(outfile)
        fp = open(outfile, "w")
        fp.write(g.serialize(format="n3"))
        fp.close()

    def analyze_citation_graphs(self, articles=None):
        # Basic setup
        # articles = self._articles('tfeu')[-1:]
        if not articles:
            articles = [None]
        if None not in articles:
            articles.append(None)
        this_year = datetime.datetime.today().year
        store = TripleStore(self.config.storetype,
                            self.config.storelocation,
                            self.config.storerepository)
        sameas = self._sameas()
        distributions = []

        # For each article (and also for no article = the entire citation graph)
        for article in articles:
            # Get a list of all eligble cases (needed for proper degree distribution)
            sq = self._query_cases(article, sameas)
            # print sq
            cases = {}
            caserows = store.select(sq, format="python")
            for r in caserows:
                cases[r['subj']] = 0

            self.log.info(
                "Creating graphs for %s (%s cases)" % (article, len(cases)))
            # Step 1. SPARQL the graph on the form ?citing ?cited
            # (optionally restricting on citing a particular article)
            if article:
                sq = self._query_cites(
                    article, sameas, True, False, this_year + 1)
            else:
                sq = self._query_cites(
                    None, sameas, False, False, this_year + 1)

            cites = store.select(sq, format="python")
            self.log.debug(
                "    Citation graph contains %s citations" % (len(cites)))

            # remove duplicate citations, self-citations and pinpoints
            # in citations
            citedict = {}
            missingcases = {}
            for cite in cites:
                # print repr(cite)
                if "-" in cite['obj']:
                    cite['obj'] = cite['obj'].split("-")[0]

                if not cite['obj'] in cases:
                    # print "Case %s (cited in %s) does not exist!\n" % (cite['obj'], cite['subj'])
                    missingcases[cite['obj']] = True
                    continue

                if (cite['subj'] != cite['obj']):
                    citedict[(cite['subj'], cite['obj'])] = True

            self.log.debug(
                "    Normalized graph contains %s citations (%s cited cases not found)" %
                (len(citedict), len(missingcases)))
            # pprint(missingcases.keys()[:10])

            # Step 2. Dotify the list (maybe the direction of arrows from
            # cited to citing can improve results?) to create a citation
            # graph
            self.analyse_citegraph_graphviz(list(citedict.keys()), article)

            # Step 3. Create a degree distribution plot
            degree, distribution = self.analyze_citegraph_degree_distribution(
                cases, list(citedict.keys()), article)
            if article:
                distributions.append([article, distribution])

            # Step 4. Create a citation/age scatterplot (or rather hexbin)
            self.analyze_citegraph_citation_age_plot(
                list(citedict.keys()), degree, distribution, article)

        # Step 5. Create a combined degree distribution graph of the
        # distinct citation networks. Also add the degree distribution
        # of gold standard cases

        self.analyze_citegraph_combined_degree_distribution(distributions)

    def analyse_citegraph_graphviz(self, cites, article, generate_graph=False):
        """Create a dot file (that can later be processed with dot or gephi)"""
        from time import time

        filetype = self.graph_filetype
        if article:
            filename = "citegraph_%s" % article.split("/")[-1]
        else:
            filename = "citegraph_all"

        dot_filename = self.generic_path(filename, "analyzed", ".dot")
        self.log.debug("    Writing graphwiz citation graph for %s" % article)
        fp = open(dot_filename, "w")
        fp.write("""digraph G {
                    graph [
                          ];
""")
        cnt = 0
        for citing, cited in cites:
            cnt += 1
            citing = citing.split("/")[-1]
            cited = cited.split("/")[-1]
            try:
                fp.write("  \"%s\" -> \"%s\" ;\n" % (citing, cited))
            except:
                pass
        fp.write("}")
        fp.close()

        if generate_graph:
            graph_filename = self.generic_path(
                dot_filename, "analyzed", "." + filetype)
            engine = "dot"
            start = time()
            cmdline = "%s -T%s -o%s tmp.dot" % (
                engine, filetype, graph_filename)
            self.log.debug("Running %s" % cmdline)
            p = subprocess.Popen(cmdline, shell=True)
            p.wait()
            self.log.info("Graph %s created in %.3f sec" % (
                graph_filename, time() - start))

    def analyze_citegraph_degree_distribution(self, cases, cites, article):
        self.log.debug("    Writing degree distribution graph")
        degree = cases
        # self.log.debug("    %s cases, first elements %r" % (len(cases),cases.values()[:5]))
        # this_year = datetime.datetime.today().year
        maxcites = 40
        # maxage = this_year - 1954

        for citing, cited in cites:
            if citing not in degree:
                degree[citing] = 0
            if cited not in degree:
                degree[cited] = 0
            degree[cited] += 1

        distribution = [0] * (max(degree.values()) + 1)

        for value in list(degree.values()):
            distribution[value] += 1

        fig = plt.figure()
        fig.set_size_inches(8, 4)
        ax = plt.subplot(111)
        ax.set_ylabel('Number of cases being cited <x> times')
        ax.set_xlabel('Number of citing cases (max %s)' % maxcites)
        ax.set_title('Degree distribution of case citations')

        filetype = self.graph_filetype
        if article:
            filename = "degree_distribution_%s" % (article.split("/")[-1])
        else:
            filename = "degree_distribution_all"
        filename = self.generic_path(filename, "analyzed", "." + filetype)

        plt.plot(distribution[:maxcites])
        plt.savefig(filename)
        plt.close()
        self.log.debug("    Created %s" % filename)
        return (degree, distribution)

    def analyze_citegraph_combined_degree_distribution(self, distributions):
        self.log.debug("    Writing combined degree distribution graph")
        # this_year = datetime.datetime.today().year
        maxcites = 40
        # maxnumber = 1000
        # maxage = this_year - 1954

        fig = plt.figure()
        fig.set_size_inches(8, 4)
        ax = plt.subplot(111)
        ax.set_ylabel('Number of cases being cited <x> times')
        ax.set_xlabel('Number of citing cases (max %s)' % maxcites)
        ax.set_title('Degree distribution of case citations concering specific articles')

        filetype = self.graph_filetype
        filename = "degree_distribution_combined"
        filename = self.generic_path(filename, "analyzed", "." + filetype)

        styles = []
        for i in range(1, 5):
            for j in (['-', '--', '-.', ':']):
            # for j in (['-','-','-','-','-']):
                styles.append((i, j))

        cnt = 0
        for (article, distribution) in distributions:
            label = article.split("/")[-1].split("E")[1]
            self.log.debug(
                "        Plotting %s %r" % (label, distribution[:4]))
            if label.isdigit():
                label = "Art. %s" % int(label)
            # label += " (%s uncited)" % distribution[0]
            lw, ls = styles[cnt]
            plt.plot(distribution[:maxcites], label=label,
                     linestyle=ls, linewidth=lw)

        # plt.axis([0,maxcites,0,maxnumber])
        plt.legend(loc='best',
                   markerscale=4,
                   prop={'size': 'x-small'},
                   ncol=int(len(distributions) / 6) + 1)

        plt.savefig(filename)
        plt.close()
        self.log.debug("    Created %s" % filename)

    def analyze_citegraph_citation_age_plot(self, cites, degree, distribution, article):
        self.log.debug("    Writing citation age plot")
        this_year = datetime.datetime.today().year
        maxcites = 40
        maxage = this_year - 1954

        cited_by_age = []
        citations = []
        for case in sorted(degree.keys()):
            try:
                year = int(case[27:31])
                caseage = this_year - year
                if year < 1954:
                    continue
            except ValueError:
                # some malformed URIs/Celexnos
                continue
            if degree[case] <= maxcites:
                cited_by_age.append(caseage)
                citations.append(degree[case])

        cases_by_age = [0] * (maxage + 1)
        for citing, cited in cites:
            year = int(citing[27:31])
            caseage = this_year - year
            if year < 1954:
                continue
            if caseage < 0:
                continue
            cases_by_age[caseage] += 1

        fig = plt.figure()
        fig.set_size_inches(8, 5)
        plt.axis([0, maxage, 0, maxcites])
        ax = plt.subplot(211)
        plt.hexbin(cited_by_age, citations, gridsize=maxcites,
                   bins='log', cmap=cm.hot_r)
        # plt.scatter(age,citations)
        ax.set_title("Distribution of citations by age")
        ax.set_ylabel("# of citations")
        #cb = plt.colorbar()
        # cb.set_label('log(# of cases with # of citations)')
        ax = plt.subplot(212)
        ax.set_title("Distribution of cases by age")
        plt.axis([0, maxage, 0, max(cases_by_age)])
        plt.bar(na.array(list(range(len(cases_by_age)))) + 0.5, cases_by_age)

        filetype = self.graph_filetype
        if article:
            filename = "citation_age_plot_%s" % (article.split("/")[-1])
        else:
            filename = "citation_age_plot_all"
        filename = self.generic_path(filename, "analyzed", "." + filetype)

        plt.savefig(filename)
        plt.close()
        self.log.debug("    Created %s" % filename)

#
# Evaluation

    def evaluate(self):
        result_cache = self.generic_path("result_cache", "eval", ".py")
        if os.path.exists(result_cache):
        # if False:
            self.log.info("Using result cache in %s" % result_cache)
            sets = eval(open(result_cache).read())
        else:
            sets = (
                ('baseline', self.eval_get_ranked_set_baseline('tfeu')),
                ('indegree_uncomp_unrestr', self.eval_get_ranked_set(
                    'tfeu', 'indegree', False, False)),
                ('indegree_uncomp_restr', self.eval_get_ranked_set(
                    'tfeu', 'indegree', False, True)),
                ('indegree_comp_unrestr', self.eval_get_ranked_set(
                    'tfeu', 'indegree', True, False)),
                ('indegree_comp_restr',
                 self.eval_get_ranked_set('tfeu', 'indegree', True, True)),
                ('hits_uncomp_unrestr',
                 self.eval_get_ranked_set('tfeu', 'hits', False, False)),
                ('hits_uncomp_restr',
                 self.eval_get_ranked_set('tfeu', 'hits', False, True)),
                ('hits_comp_unrestr',
                 self.eval_get_ranked_set('tfeu', 'hits', True, False)),
                ('hits_comp_restr',
                 self.eval_get_ranked_set('tfeu', 'hits', True, True)),
                ('pagerank_uncomp_unrestr', self.eval_get_ranked_set(
                    'tfeu', 'pagerank', False, False)),
                ('pagerank_uncomp_restr', self.eval_get_ranked_set(
                    'tfeu', 'pagerank', False, True)),
                ('pagerank_comp_unrestr', self.eval_get_ranked_set(
                    'tfeu', 'pagerank', True, False)),
                ('pagerank_comp_restr',
                 self.eval_get_ranked_set('tfeu', 'pagerank', True, True)),
            )
            util.ensure_dir(result_cache)
            fp = open(result_cache, "w")
            pprint(sets, fp)
            fp.close()

        aps_cache = self.generic_path("aps_cache", "eval", ".py")
        if os.path.exists(aps_cache):
        # if False:
            self.log.info("Using avg precision cache in %s" % aps_cache)
            avg_precisions = eval(open(aps_cache).read())
        else:
            goldstandard = self.eval_get_goldstandard('tfeu')
            avg_precisions = []
            for label, rankedset in sets:
                aps = self.eval_calc_aps(rankedset, goldstandard)
                avg_precisions.append((label, aps))

            fp = open(aps_cache, "w")
            pprint(avg_precisions, fp)
            fp.close()

        self.eval_aps_table(avg_precisions)

        if len(avg_precisions) > 5:
            maps = []
            for label, aps in avg_precisions:
                maps.append(self.eval_calc_map(aps))
            maps.sort(reverse=True)
            thresh = maps[5]
        else:
            thresh = 0.0

        top_avg_precisions = []
        for label, aps in avg_precisions:
            map_ = self.eval_calc_map(aps)
            self.log.info("%25s: MAP %s" % (label, map_))
            if (map_ > thresh) or (label == 'baseline'):
                top_avg_precisions.append(
                    ("%s: MAP %.3f" % (label, map_), aps))

        self.eval_aps_chart(top_avg_precisions)

    def eval_calc_aps(self, rankedset, goldstandard):
        """Calculate a set of average precisions for the given set of
        result sets for some information needs, compared to the gold
        standard for those information needs.

        Both rankedset and goldstandard are dicts with lists as values."""

        aps = []
        for infoneed in list(goldstandard.keys()):
            relevants = goldstandard[infoneed]
            if relevants:
                #self.log.debug("  Calculating AP for %s" % infoneed)
                pass
            else:
                self.log.debug(
                    "   No AP for %s: no gold standard" % (infoneed))
                continue

            ranking = rankedset[infoneed]
            precisions = []

            # for each relevant doc in the gold standard, check at
            # what position in the ranking the doc occurrs. Check the
            # precision of the ranking up to and including that position
            for relevant in relevants:
                try:
                    place = ranking.index(relevant)
                    relevant_cnt = 0
                    for r in ranking[:place + 1]:
                        if r in relevants:
                            relevant_cnt += 1
                    precision = float(relevant_cnt) / float(place + 1)
                    #self.log.debug("    Relevant result %s found at %s (relevant_cnt %s), precision %s" % (relevant.split("/")[-1], place+1,relevant_cnt,precision))
                except ValueError:
                    #self.log.debug("    Relevant result %s not found, precision: 0" % relevant.split("/")[-1])
                    precision = 0

                precisions.append(precision)

            ap = sum(precisions) / float(len(precisions))
            self.log.info("   AP for %s: %s" % (infoneed, ap))
            aps.append(ap)

        return aps

    def eval_calc_map(self, average_precision_set):
        return sum(average_precision_set) / float(len(average_precision_set))

    _graph_cache = {}

    def eval_get_ranked_set(self, basefile, algorithm="pagerank",
                            age_compensation=False, restrict_cited=True):
        # * algorithm: can be "indegree", "hits" or "pagerank".
        # * age_compensation: create one graph per year and average to
        #   compensate for newer cases (that have had less time to gain
        #   citations)
        # * restrict_cited: Use only such citations that exist between
        #   two cases that both cite the same TFEU article (othewise,
        #   use all citations from all cases that cite the TFEU
        #   article, regardless of whether the cited case also cites
        #   the same TFEU article)
        sameas = self._sameas()
        store = TripleStore(self.config.storetype,
                            self.config.storelocation,
                            self.config.storerepository)
        res = {}

        self.log.debug("Creating ranked set (%s,age_compensation=%s,restrict_cited=%s)" %
                       (algorithm, age_compensation, restrict_cited))

        for article in self._articles(basefile):
            article_celex = article.split("/")[-1]
            self.log.debug("    Creating ranking for %s" % (article_celex))
            this_year = datetime.datetime.today().year
            if age_compensation:
                years = list(range(1954, this_year + 1))
                # years = range(this_year-3,this_year) # testing
            else:
                years = list(range(this_year, this_year + 1))

            result_by_years = []
            for year in years:
                restrict_citing = True  # always performs better
                if (article, year, restrict_cited) in self._graph_cache:
                    # self.log.debug("Resuing cached graph (%s) for %s in %s" %
                    #               (restrict_cited, article_celex,year))
                    graph = self._graph_cache[(article, year, restrict_cited)]
                else:
                    # self.log.debug("Calculating graph for %s in %s" %
                    #               (article_celex,year))
                    sq = self._query_cites(article, sameas, restrict_citing,
                                           restrict_cited, year)
                    links = store.select(sq, format="python")
                    graph = self.eval_build_nx_graph(links)
                    self._graph_cache[(article, year, restrict_cited)] = graph
                    self.log.debug("      Citegraph for %s in %s has %s edges, %s nodes" %
                                   (article_celex, year, len(graph.edges()),
                                    len(graph.nodes())))

                if len(graph.nodes()) == 0:
                    continue

                ranked = self.eval_rank_graph(graph, algorithm)
                result_by_years.append({})
                for result, score in ranked:
                    result_by_years[-1][result] = score

            if age_compensation:
                compensated_ranking = {}
                for d, score in ranked:  # the last result set
                    # cut out the year part of the URI
                    celex = d.split("/")[-1]
                    try:
                        age = this_year + 1 - int(
                            celex[1:5])  # cases decided this year has age 1
                        # scores = [0,0,0 ... 3,4,8,22]
                        scores = [result_by_year[d]
                                  for result_by_year
                                  in result_by_years
                                  if d in result_by_year]
                        avg_score = sum(scores) / float(age)
                        # self.log.debug("Result %s (age %s, avg score %s) %r" %
                        #               (d,age,avg_score,scores))
                        compensated_ranking[d] = avg_score
                    except ValueError:
                        continue

            # return just a list of results, no scores
            if age_compensation:
                res[article] = [result for result in sorted(
                    compensated_ranking, key=compensated_ranking.__getitem__, reverse=True)]
            else:
                res[article] = [result[0] for result in ranked]

        return res

    def eval_build_nx_graph(self, links):
        #self.log.debug("Building graph with %s links" % len(links))
        nxgraph = networkx.DiGraph()
        for link in links:
            if "-" in link['obj']:
                nxgraph.add_edge(link['subj'], link['obj'].split("-")[0])
            else:
                nxgraph.add_edge(link['subj'], link['obj'])
        #self.log.debug("Graph has %s nodes" % len (nxgraph.nodes()))
        return nxgraph

    def eval_rank_graph(self, graph, algorithm="pagerank"):
        # should return a list of tuples (result,score) sorted in
        # reversed order (ie highest score first)
        if algorithm == "pagerank":
            ranked = pagerank(graph)
        elif algorithm == "hits":
            ranked = hits(graph, max_iter=10000)[1]  # 0: hubs, 1: authorities
        elif algorithm == "indegree":
            ranked = graph.in_degree()
        else:
            self.log.error(
                "Unknown ranking algorithm %s specified" % algorithm)
        sortedrank = sorted(
            iter(list(ranked.items())), key=itemgetter(1), reverse=True)
        return sortedrank

    # computes a ranked set for each baseline using a naive search
    # (using the most significant words of each article) and the
    # standard BM25F ranking function
    def eval_get_ranked_set_baseline(self, basefile):
        # Step 1: Read the saved keyterms for a subset of articles
        # (created by analyze_baseline_queries)
        g = Graph()
        g.parse(self.generic_path("keyterms", "analyzed", ".n3"), format="n3")

        articles = {}
        for (s, p, o) in g:
            if not str(s) in articles:
                articles[str(s)] = []
            articles[str(s)].append(str(o))

        # Step 2: Open the large whoosh index containing the text of
        # all cases. Then, create a query for each article based on
        # the keyterms.
        connector = query.Or
        indexdir = os.path.sep.join([self.config.datadir, 'ecj', 'index'])
        storage = FileStorage(indexdir)
        idx = storage.open_index()
        searcher = idx.searcher(weighting=scoring.BM25F())

        res = {}

        # for article in sorted(articles.keys()):
        for article in self._articles(basefile):
            terms = articles[article]
            rankedset = []
            #parser = qparser.QueryParser("content", idx.schema)
            #q = parser.parse(connector.join(terms))
            q = query.And([
                # query.Term("articles", article),
                connector([query.Term("content", x) for x in terms])
            ])
            # print q
            # self.log.debug("Article %s: %s", article, " or ".join(terms))
            results = searcher.search(q, limit=None)
            resultidx = 0
            # self.log.info("Keyterms for result: %r" % results.key_terms("content", docs=10, numterms=10))
            for result in results:
                reslbl = "%s (%s)" % (
                    result['basefile'], results.score(resultidx))
                rankedset.append([result['basefile'], reslbl])
                # self.log.debug(u"\t%s: %2.2d" % (result['title'], results.score(resultidx)))
                resultidx += 1
            self.log.info("Created baseline ranked set for %s: Top result %s (of %s)" %
                          (article.split("/")[-1], rankedset[0][0], len(rankedset)))

            # return just a list of URIs, no scoring information. But the
            # full URI isnt available in the whoosh db, so we recreate it.
            res[article] = ["http://lagen.nu/ext/celex/%s" % x[
                0] for x in rankedset]

        return res

    def eval_get_goldstandard(self, basefile):
        goldstandard = Graph()
        goldstandard_rdf = util.relpath(
            os.path.dirname(__file__) + "/../res/eut/goldstandard.n3")
        goldstandard.load(goldstandard_rdf, format="n3")

        pred = util.ns['ir'] + 'isRelevantFor'
        res = {}
        store = TripleStore(self.config.storetype,
                            self.config.storelocation,
                            self.config.storerepository)
        sq_templ = """PREFIX eurlex:<http://lagen.nu/eurlex#>
                      SELECT ?party ?casenum ?celexnum WHERE {
                          <%s> eurlex:party ?party ;
                               eurlex:casenum ?casenum ;
                               eurlex:celexnum ?celexnum .
                      }"""

        self.log.debug(
            "Loading gold standard relevance judgments for %s" % basefile)
        for article in self._articles(basefile):
            res[article] = []
            for o in goldstandard.objects(URIRef(article), URIRef(pred)):
                res[article].append(str(o))
                # Make sure the case exists and is the case we're looking for
                sq = sq_templ % str(o)
                parties = store.select(sq, format="python")
                if parties:
                    pass
                    # self.log.debug("   %s: %s (%s)" %
                    #               (parties[0]['celexnum'],
                    #                parties[0]['casenum'],
                    #                " v ".join([x['party'] for x in parties])))
                else:
                    self.log.warning("Can't find %s in triple store!" % o)
            self.log.debug("    Gold standard for %s: %s relevant docs" %
                           (article, len(res[article])))
            res[article].sort()
        return res

    def eval_aps_chart(self, avg_precisions):
        # Create a chart in PDF format and a equvialent table
        import matplotlib.pyplot as plt
        import numpy as np

        # create linestyle/width array:
        styles = []
        for i in range(1, 5):
            # for j in (['-','--','-.',':']):
            for j in (['-', '-', '-', '-', '-']):
                styles.append((i, j))
        fig = plt.figure()
        fig.set_size_inches(8, 4)
        ax = plt.subplot(111)
        width = len(avg_precisions[0][1])
        plt.axis([0, width - 1, 0, 0.3])
        ind = np.arange(width)
        ax.set_ylabel('Average precision')
        ax.set_title('Average precision for information needs')
        xticklabels = ["Art %d" % int(x.split("E")[-1])
                       for x in self._articles('tfeu')]
        ax.set_xticks(ind)
        ax.set_xticklabels(xticklabels)

        cnt = 0
        for label, aps in avg_precisions:
            # print "%s: %r" % (label, aps)
            lw, ls = styles[cnt]
            plt.plot(ind, aps, label=label, linestyle=ls, linewidth=lw)
            cnt += 1

        plt.legend(loc='best',
                   markerscale=4,
                   prop={'size': 'x-small'},
                   ncol=int(len(avg_precisions) / 8) + 1)

        filetype = self.graph_filetype
        filename = "average_precision"
        filename = self.generic_path(filename, "eval", "." + filetype)

        plt.savefig(filename)
        self.log.info("Created average precision chart as %s" % filename)
        plt.close()
        # plt.show()

    def eval_aps_table(self, avg_precisions):
        filetype = "tex"
        filename = "average_precision"
        filename = self.generic_path(filename, "eval", "." + filetype)

        articles = [x.split("E")[-1] for x in self._articles('tfeu')]
        tblformat = "{r|" + "l|" * len(articles) + "l}"
        tblhead = "".join(["& \\textbf{%s} " % x for x in articles])
        fp = open(filename, "w")
        fp.write("""\\begin{tabular}%s
\\hline
\\textbf{Conf} %s  & \\textbf{MAP}\\\\
\\hline
""" % (tblformat, tblhead))
        for label, aps in avg_precisions:
            if label == "baseline":
                label = "base"
            else:
                label = "".join([x[0].upper() for x in label.split("_")])
            fp.write("%s & " % label)
            for ap in aps:
                fp.write("%.3f & " % ap)
            fp.write("%.3f \\\\ \n" % self.eval_calc_map(aps))
        fp.write("""\\hline
\\end{tabular}
""")
        self.log.info("Created average precision table as %s" % filename)
