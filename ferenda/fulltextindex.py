# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import math
import re
import shutil
import sys

from six.moves.urllib_parse import quote
import requests
import requests.exceptions
from bs4 import BeautifulSoup

from ferenda import util, errors


class FulltextIndex(object):

    @staticmethod
    def connect(indextype, location, repos=[]):
        """Open a fulltext index (creating it if it
        doesn't already exists).

        :param location: Type of fulltext index ("WHOOSH" or "ELASTICSEARCH")
        :type  location: str
        :param location: The file path of the fulltext index.
        :type  location: str
    """
        # create correct subclass and return it
        return {'WHOOSH': WhooshIndex,
                'ELASTICSEARCH': ElasticSearchIndex}[indextype](location, repos)

    def __init__(self, location, repos):
        self.location = location
        if self.exists():
            self.index = self.open()
        else:
            self.index = self.create(self.get_default_schema(), repos)

    def __del__(self):
        self.close()

    def get_default_schema(self):
        return {'uri': Identifier(),
                'repo': Label(),
                'basefile': Label(),
                'title': Text(boost=4),
                'identifier': Label(boost=16),
                'text': Text()}

    def exists(self):
        """Whether the fulltext index exists."""
        raise NotImplementedError  # pragma: no cover

    def create(self, schema, repos):
        """Creates a fulltext index using the provided default schema."""
        raise NotImplementedError  # pragma: no cover

    def destroy(self):
        """Destroys the index, if created."""
        raise NotImplementedError  # pragma: no cover

    def open(self):
        """Opens the index so that it can be queried."""
        raise NotImplementedError  # pragma: no cover

    def schema(self):
        """Returns the schema that actually is in use. A schema is a dict
           where the keys are field names and the values are any
           subclass of
           :py:class:`ferenda.fulltextindex.IndexedType`
        """
        raise NotImplementedError  # pragma: no cover

    def update(self, uri, repo, basefile, title, identifier, text, **kwargs):
        """Insert (or update) a resource in the fulltext index. A resource may
        be an entire document, but it can also be any part of a
        document that is referenceable (i.e. a document node that has
        ``@typeof`` and ``@about`` attributes). A document with 100
        sections can be stored as 100 independent resources, as long
        as each section has a unique key in the form of a URI.

        :param uri: URI for the resource
        :type  uri: str
        :param repo: The alias for the document repository that the resource is part of
        :type  repo: str
        :param basefile: The basefile which contains resource
        :type  basefile: str
        :param title: User-displayable title of resource (if applicable).
                      Should not contain the same information as
                      ``identifier``. 
        :type  title: str
        :param identifier: User-displayable short identifier for resource (if applicable)
        :type  identifier: str
        :type  text: The full textual content of the resource, as a plain string.
        :type  text: str

        .. note::

           Calling this method may not directly update the fulltext
           index -- you need to call
           :meth:`~ferenda.FulltextIndex.commit` or
           :meth:`~ferenda.FulltextIndex.close` for that.

        """
        raise NotImplementedError  # pragma: no cover

    def commit(self):
        """Commit all pending updates to the fulltext index."""
        raise NotImplementedError  # pragma: no cover

    def close(self):
        """Commits all pending updates and closes the index."""
        raise NotImplementedError  # pragma: no cover

    def doccount(self):
        """Returns the number of currently indexed (non-deleted) documents."""
        raise NotImplementedError  # pragma: no cover

    def query(self, q, **kwargs):
        """Perform a free text query against the full text index, optionally
           restricted with parameters for individual fields.

        :param q: Free text query, using the selected full text index's
                  prefered query syntax
        :type  q: str
        :param \*\*kwargs: any parameter will be used to match a
                         similarly-named field
        :type \*\*kwargs: dict
        :returns: matching documents, each document as a dict of fields
        :rtype: list

        .. note::

           The *kwargs* parameters do not yet do anything -- only
           simple full text queries are possible.

        """
        raise NotImplementedError  # pragma: no cover


class IndexedType(object):

    """Base class for a fulltext searchengine-independent representation
       of indeaxed data.  By using IndexType-derived classes to
       represent the schema, it becomes possible to switch out search
       engines without affecting the rest of the code.

    """

    def __eq__(self, other):
        return (isinstance(other, self.__class__)
                and self.__dict__ == other.__dict__)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(tuple(v for k, v in sorted(self.__dict__.items())))

    def __init__(self, **kwargs):
        self.__dict__ = dict(kwargs)

    def __repr__(self):
        # eg '<Label boost=16>' or '<Identifier>'
        dictrepr = "".join((" %s=%s" % (k, v) for k, v in sorted(self.__dict__.items())))
        return ("<%s%s>" % (self.__class__.__name__, dictrepr))


class Identifier(IndexedType):
    pass


class Datetime(IndexedType):
    pass


class Text(IndexedType):
    pass


class Label(IndexedType):
    pass


class Keywords(IndexedType):
    pass


class Boolean(IndexedType):
    pass


class URI(IndexedType):
    pass


class SearchModifier(object):
    pass


class Less(SearchModifier):
    pass


class More(SearchModifier):
    pass


class Between(SearchModifier):
    pass


import whoosh.index
import whoosh.fields
import whoosh.analysis
import whoosh.query
import whoosh.qparser
import whoosh.writing
import whoosh.highlight

from ferenda.elements import html


class ElementsFormatter(whoosh.highlight.Formatter):

    """Returns a tree of ferenda.elements representing the formatted hit."""

    def __init__(self, wrapelement=html.P, hitelement=html.Strong, classname="match", between=" ... "):
        self.wrapelement = wrapelement
        self.hitelement = hitelement
        self.classname = classname
        self.between = between

    def format(self, fragments, replace=False):
        res = self.wrapelement()
        first = True
        for fragment in fragments:
            if not first:
                res.append(self.between)
            res.extend(self.format_fragment(fragment, replace=replace))
            first = False
        return res

    re_collapse = re.compile("\s+").sub

    def format_fragment(self, fragment, replace):
        output = []
        index = fragment.startchar
        text = fragment.text

        for t in fragment.matches:
            if t.startchar > index:
                output.append(self.re_collapse(" ", text[index:t.startchar]))
            hittext = whoosh.highlight.get_text(text, t, False)
            output.append(self.hitelement([hittext], **{'class': self.classname}))
            index = t.endchar
        if index < len(text):
            output.append(self.re_collapse(" ", text[index:fragment.endchar]))
        return output


class WhooshIndex(FulltextIndex):

    def __init__(self, location, repos):
        super(WhooshIndex, self).__init__(location, repos)
        self._schema = self.get_default_schema()
        self._writer = None

    def exists(self):
        return whoosh.index.exists_in(self.location)

    def open(self):
        return whoosh.index.open_dir(self.location)

    def create(self, schema, repos):
        # maps our field classes to concrete whoosh field instances
        mapped_field = {Identifier():   whoosh.fields.ID(unique=True, stored=True),
                        Label():        whoosh.fields.ID(stored=True),
                        Label(boost=16): whoosh.fields.ID(field_boost=16, stored=True),
                        Text(boost=4):  whoosh.fields.TEXT(field_boost=4, stored=True,
                                                           analyzer=whoosh.analysis.StemmingAnalyzer(
                                                           )),
                        Text():         whoosh.fields.TEXT(stored=True,
                                                           analyzer=whoosh.analysis.StemmingAnalyzer())}

        whoosh_fields = {}
        for key, fieldtype in self.get_default_schema().items():
            whoosh_fields[key] = mapped_field[fieldtype]
        schema = whoosh.fields.Schema(**whoosh_fields)
        util.mkdir(self.location)
        return whoosh.index.create_in(self.location, schema)

    def destroy(self):
        shutil.rmtree(self.location)

    def schema(self):
        # FIXME: This should iterate through self.index (the
        # underlying whoosh index), convert each field to the
        # corresponding IndexedType objects.
        return self._schema

    def update(self, uri, repo, basefile, title, identifier, text, **kwargs):
        if not self._writer:
            self._writer = self.index.writer()

        # A whoosh document is not the same as a ferenda document. A
        # ferenda document may be indexed as several (tens, hundreds
        # or more) whoosh documents
        self._writer.update_document(uri=uri,
                                     repo=repo,
                                     basefile=basefile,
                                     title=title,
                                     identifier=identifier,
                                     text=text,
                                     **kwargs)

    def commit(self):
        if self._writer:
            self._writer.commit()
            if not isinstance(self._writer, whoosh.writing.BufferedWriter):
                # A bufferedWriter can be used again after commit(), a regular writer cannot
                self._writer = None

    def close(self):
        self.commit()
        self.index.close()

    def doccount(self):
        return self.index.doc_count()

    def query(self, q, pagenum=1, pagelen=10, **kwargs):
        searchfields = ['identifier', 'title', 'text']
        mparser = whoosh.qparser.MultifieldParser(searchfields,
                                                  self.index.schema)
        query = mparser.parse(q)
        with self.index.searcher() as searcher:
            page = searcher.search_page(query, pagenum, pagelen)
            res = self._convert_result(page)
            pager = {'pagenum': pagenum,
                     'pagecount': page.pagecount,
                     'firstresult': page.offset + 1,
                     'lastresult': page.offset + page.pagelen,
                     'totalresults': page.total}
        return res, pager

    def _convert_result(self, res):
        # converts a whoosh.searching.ResultsPage object to a plain
        # list of dicts
        l = []
        hl = whoosh.highlight.Highlighter(formatter=ElementsFormatter())
        for hit in res:
            fields = hit.fields()
            fields['text'] = hl.highlight_hit(hit, "text", fields['text'])
            l.append(hit.fields())
        return l

# Base class for a HTTP-based API (eg. ElasticSearch) the base class
# delegate the formulation of queries, updates etc to concrete
# subclasses, expected to return a formattted query/payload etc, and
# be able to decode responses to queries, but the base class handles
# the actual HTTP call, inc error handling.


class RemoteIndex(FulltextIndex):

    # The only real implementation of RemoteIndex has its own exists
    # implementation, no need for a general fallback impl.
    # def exists(self):
    #     pass

    def create(self, schema, repos):
        relurl, payload = self._create_schema_payload(self.get_default_schema(), repos)
        res = requests.put(self.location + relurl, payload)
        try:
            res.raise_for_status()
        except Exception as e:
            raise Exception("%s: %s" % (res.status_code, res.text))

    def schema(self):
        relurl, payload = self._get_schema_payload()
        res = requests.get(self.location + relurl)  # payload is probably never used
        return self._decode_schema(res)

    def update(self, uri, repo, basefile, title, identifier, text, **kwargs):
        relurl, payload = self._update_payload(
            uri, repo, basefile, title, identifier, text, **kwargs)

        res = requests.put(self.location + relurl, payload)
        try:
            res.raise_for_status()
            # print(json.dumps(res.json(), indent=4))
        except requests.exceptions.HTTPError as e:
            raise errors.IndexingError(str(e) + ": '%s'" % res.text)

    def doccount(self):
        relurl, payload = self._count_payload()
        if payload:
            res = requsts.post(self.location + relurl, payload)
        else:
            res = requests.get(self.location + relurl)
        return self._decode_count_result(res)

    def query(self, q, pagenum=1, pagelen=10, **kwargs):
        relurl, payload = self._query_payload(q, pagenum, pagelen, **kwargs)
        if payload:
            # print("POSTing to %s:\n%s" % (relurl, payload))
            res = requests.post(self.location + relurl, payload)
            # print("Recieved:\n%s" % (json.dumps(res.json(),indent=4)))
        else:
            res = requests.get(self.location + relurl)
        try:
            res.raise_for_status()
        except Exception as e:
            raise errors.SearchingError("%s: %s" % (res.status_code, res.text))
        return self._decode_query_result(res, pagenum, pagelen)

    def destroy(self):
        reluri, payload = self._destroy_payload()
        res = requests.delete(self.location + reluri)

    # these don't make no sense for a remote index accessed via HTTP/REST
    def open(self):
        pass

    def commit(self):
        pass

    def close(self):
        pass


class ElasticSearchIndex(RemoteIndex):

    def commit(self):
        r = requests.post(self.location + "_refresh")
        r.raise_for_status()

    def exists(self):
        r = requests.get(self.location + "_mapping/")
        if r.status_code == 404:
            return False
        else:
            return True

    def _update_payload(self, uri, repo, basefile, title, identifier, text, **kwargs):
        safe = ''
        if sys.version_info < (2, 7, 0):
            # urllib.quote in python 2.6 cannot handle unicode values
            # for the safe parameter (not even empty). FIXME: We
            # should create a shim as ferenda.compat.quote and use
            # that
            safe = safe.encode('ascii') # pragma: no cover

        relurl = "%s/%s" % (repo, quote(basefile, safe=safe))  # eg type, id
        if "#" in uri:
            relurl += uri.split("#", 1)[1]
        payload = {"uri": uri,
                   "basefile": basefile,
                   "title": title,
                   "identifier": identifier,
                   "text": text}
        payload.update(kwargs)
        return relurl, json.dumps(payload)

    def _query_payload(self, q, pagenum=1, pagelen=10, **kwargs):
        # relurl = "_search?q=%s&size=%s&from=%s" % (quote(q), pagelen, (pagenum * pagelen) - pagelen)
        relurl = "_search?from=%s&size=%s" % ((pagenum - 1) * pagelen, pagelen)

        # FIXME: Only searches in text, not title or identifier. But
        # can't search on the _all field, because apparently that
        # field isn't set up to use the my_analyzer we've defined...
        payload = {'query': {'match': {'text': q}},
                   'highlight': {'fields': {'text': {}},
                                 'pre_tags': ["<strong class='match'>"],
                                 'post_tags': ["</strong>"],
                                 'fragment_size': '40'}}
        return relurl, json.dumps(payload, indent=4)

    def _decode_query_result(self, response, pagenum, pagelen):
        json = response.json()
        res = []
        for hit in json['hits']['hits']:
            h = hit['_source']
            # wrap highlighted field in P, convert to elements
            hltext = " ... ".join([x.strip() for x in hit['highlight']['text']])
            soup = BeautifulSoup("<p>%s</p>" % re.sub("\s+", " ", hltext))
            h['text'] = html.elements_from_soup(soup.html.body.p)
            res.append(h)
        pager = {'pagenum': pagenum,
                 'pagecount': int(math.ceil(json['hits']['total'] / float(pagelen))),
                 'firstresult': (pagenum - 1) * pagelen + 1,
                 'lastresult': (pagenum - 1) * pagelen + len(json['hits']['hits']),
                 'totalresults': json['hits']['total']}
        return res, pager

    def _count_payload(self):
        return "_count", None

    def _decode_count_result(self, response):
        if response.status_code == 404:
            return 0
        else:
            return response.json()['count']

    # FIXME: This is cheating!
    def schema(self):
        return self.get_default_schema()

    def _get_schema_payload(self):
        return "", None

    def _decode_schema_payload(self, response):
        raise NotImplementedError # pragma: no cover

    # FIXME: For some reason, createing a schema/mapping makes PUTting
    # new documents to the index hang with the folloging error:
    #
    #    UnavailableShardsException[[ferenda][1] [3] shardIt, [0] active : Timeout waiting for [1m]
    #
    # So we skip creating the schema as it isn't neccesary
    # def create(self, schema, repos):
    #    pass

    def _create_schema_payload(self, schema, repos):
        schema = {
            # cargo cult configuration
            "settings": {"number_of_shards": 1,
                         "analysis": {
                             "analyzer": {
                                 "my_analyzer": {
                                     "filter": ["lowercase", "snowball"],
                                     "tokenizer": "standard",
                                     "type": "custom"
                                 }
                             },
                             "filter": {
                                 "snowball": {
                                     "type": "snowball",
                                     "language": "English"
                                 }
                             }
                         }
                         },
            # "mappings": {"_all": {"properties": {"analyzer": "my_analyzer"}}}
            "mappings": {}
        }

        # maps our field classes to concrete ES field properties
        mapped_field = {Identifier():   {"type": "string", "index": "not_analyzed"},  # uri
                        # repo, basefile (note: see below)
                        Label():        {"type": "string", "index": "not_analyzed"},
                        # identifier
                        Label(boost=16): {"type": "string", "boost": 16.0, "analyzer": "my_analyzer"},
                        # title
                        Text(boost=4):  {"type": "string", "boost": 4.0, "analyzer": "my_analyzer"},
                        Text():         {"type": "string", "analyzer": "my_analyzer"}}  # text

        es_fields = {}
        for key, fieldtype in self.get_default_schema().items():
            if key == "repo":
                continue  # not really needed for ES, as type == repo.alias
            es_fields[key] = mapped_field[fieldtype]
        for repo in repos:
            schema["mappings"][repo.alias] = {"_source": {"enabled": True},  # so we can get the text back
                                              "properties": es_fields}
        return "", json.dumps(schema)

    def _destroy_payload(self):

        return "", None
