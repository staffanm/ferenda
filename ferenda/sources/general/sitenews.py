from datetime import datetime
import re
import codecs

import bs4
from rdflib import URIRef, Literal, Namespace
from rdflib.namespace import DCTERMS, RDF
SCHEMA = Namespace("http://schema.org/")

from ferenda import DocumentRepository, Facet, Feedset, DocumentEntry
from ferenda import util
from ferenda.elements import UnorderedList, ListItem, Body
from ferenda.elements.html import elements_from_soup, Div, DL, DT, DD, Img, A
from ferenda.decorators import managedparsing

class Sitenews(DocumentRepository):
    """Generates a set of news documents from a single text file.

    This is a simple way of creating a feed of news about the site
    itself, with permalinks for individual posts and a Atom feed for
    subscribing in a feed reader.

    The text file is loaded by `ferenda.ResourceLoader`, so it can be
    placed in any resource directory for any repo used. By default,
    the resource name is "static/sitenews.txt" but this can be changed
    with `config.newsfile`

    The text file should be structured with each post/entry having a
    header line, followed by a empty line, then the body of the
    post. The body ends when a new header line (or EOF) is
    encountered. The header line should be formatted like `<ISO 8859-1
    datetime> <Entry title>`.

    The body should be a regular HTML fragment.

    """

    alias = "sitenews"
    downloaded_suffix = ".txt"
    rdf_type = SCHEMA.BlogPosting  # or maybe just schema:Article
    namespaces = ['rdf', 'rdfs', 'xsd', 'xsi', 'dcterms', 'prov', 'schema']
    sparql_annotations = None
    news_sortkey = 'published'
    readmore_label = 'Read more...'

    @classmethod
    def get_default_options(cls):
        opts = super(Sitenews, cls).get_default_options()
        opts['newsfile'] = 'static/sitenews.txt'
        return opts

    re_news_subjectline = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.*)').match

    def download(self):
        # do something with static/sitenews.txt --> split into
        # <datadir>/sitenews/<timestamp>.txt
        ofp = None
        with codecs.open(self.resourceloader.filename(self.config.newsfile),
                         encoding="utf-8") as fp:
            for line in fp:
                m = self.re_news_subjectline(line)
                if m:
                    if ofp:
                        ofp.close()
                    d = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    basefile = str(int(d.timestamp()))
                    path = self.store.downloaded_path(basefile)
                    self.log.info("%s: creating news item" % basefile)
                    util.ensure_dir(path)
                    ofp = codecs.open(path, "w", encoding="utf-8")
                ofp.write(line)
            ofp.close()

    @managedparsing
    def parse(self, doc):
        head, body = util.readfile(self.store.downloaded_path(doc.basefile)).split("\n\n", 1)
        datestr, timestr, title = head.split(" ", 2)
        published = datetime.strptime("%s %s" % (datestr, timestr), "%Y-%m-%d %H:%M:%S")

        doc.meta.add((URIRef(doc.uri), RDF.type, self.rdf_type))
        doc.meta.add((URIRef(doc.uri), DCTERMS.issued, Literal(published)))
        doc.meta.add((URIRef(doc.uri), DCTERMS.title, Literal(title, lang=doc.lang)))
        soup = bs4.BeautifulSoup("<div class='sitenews-item'>"+body+"</div>", "lxml")
        doc.body = elements_from_soup(soup.body)
        # move timestamp into dcterms:issued, title into dcterms:title
        # parse body with elements_from_soup
        # set first real para as dcterms:abstract (XMLLiteral)
        doc.body[0][0] = Div([doc.body[0][0]],
                          datatype="rdf:XMLLiteral",
                          property="dcterms:abstract")

        # but we need to add it to doc.meta RIGHT AWAY because of reasons...
        doc.meta.add((URIRef(doc.uri), DCTERMS.abstract,
                      Literal(body.split("\n\n")[0], datatype=RDF.XMLLiteral)))
        self.parse_entry_update(doc) # need to set published and possibly updated
        entry = DocumentEntry(self.store.documententry_path(doc.basefile))
        entry.published = published
        entry.save()
        return True

    def parse_entry_summary(self, doc):
        summary = doc.meta.value(URIRef(doc.uri), DCTERMS.abstract)
        if len(doc.body[0]) > 1:
            if self.readmore_label:
                permalink = self.canonical_uri(doc.basefile)
                readmore_link = " <a href='%s'>%s</a>" % (permalink, self.readmore_label)
                summarytext = summary.replace("</p>", "%s</p>" % readmore_link)
            summary = Literal(summarytext, datatype=summary.datatype)
        return summary


    def facets(self):
        return [Facet(DCTERMS.issued)]

    toc_title = "All news feeds"
    def toc(self, otherrepos):
        documentlist = []
        # create just one single page: no leftnav, contains only a sort-of nested list 
        for repo in [self] + otherrepos:
            if not repo.config.tabs:
                continue
            qname_graph = repo.make_graph()
            feeds = []
            # row = {'alias': repo.alias,
            #        'uri': repo.dataset_uri(feed=True)}
            # item = self.toc_item('alias', row)
            tabs = repo.tabs()
            if tabs:
                item = tabs[0][0]
            else:
                # item = repo.alias
                #
                # if a repo doesn't provide any tabs, it's probably
                # mostly for internal use (like static and mediawiki
                # -- let's just skip it
                continue
            documentlist.append((item, feeds))
            feedsets = repo.news_feedsets(repo.news_facet_entries(),
                                          repo.facets())
            feedcnt = 0
            for feedset in feedsets:
                for feed in feedset.feeds:
                    feedcnt += 1
                    row = {'title': feed.title,
                           'uri': repo.dataset_uri(param=feed.binding,
                                                   value=feed.slug,
                                                   feed=True),
                           'feeduri': repo.dataset_uri(param=feed.binding,
                                                       value=feed.slug,
                                                       feed=".atom")}
                    item = self.toc_item('title', row)
                    feeds.append(item)
            self.log.info("sitenews.toc: Added %s feeds in %s feedsets for %s" %
                          (feedcnt, len(feedsets), repo.alias))
        self.toc_generate_page(None, None, documentlist, [], "index", title=self.toc_title)

    def toc_item(self, binding, row):
        return [A([Img(alt="Atom feed",
                               src="/rsrc/img/atom.png",
                               width=14,
                               height=14)],
                  href=row['feeduri']),
                A(row[binding],
                  href=row['uri'])]
        
    def toc_generate_page_body(self, documentlist, nav):
        ul = UnorderedList([ListItem(x) for x in documentlist], role='main')
        dl = DL(**{'class': 'dl-horizontal'})
        for label, doclist in documentlist:
            dl.append(DT(label))
            for doc in doclist:
                dl.append(DD(doc))
        return Body([nav,
                     dl
        ])

    def tabs(self):
        if self.config.tabs:
            uri = self.dataset_uri()
            return [("News", uri)]
        else:
            return []
