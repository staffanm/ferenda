# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import math
import re
import shutil
import itertools
from datetime import date, datetime, MAXYEAR, MINYEAR

import six
from six.moves.urllib_parse import quote
from six import text_type as str
import requests
import requests.exceptions
from bs4 import BeautifulSoup

from ferenda import util, errors


class FulltextIndex(object):
    """This is the abstract base class for a fulltext index. You use it by
       calling the static method FulltextIndex.connect, passing a
       string representing the underlying fulltext engine you wish to
       use. It returns a subclass on which you then call further
       methods.

    """
    
    @staticmethod
    def connect(indextype, location, repos):
        """Open a fulltext index (creating it if it doesn't already exists).

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
            assert repos, "Attempt to create a fulltext index, but no repos were provided, index schema would be empty"
            self.index = self.create(repos)

    def __del__(self):
        self.close()

    def make_schema(self, repos):
        s = self.get_default_schema()
        for repo in repos:
            g = repo.make_graph() # for qname lookup
            for facet in repo.facets():
                if facet.dimension_label:
                    fld = facet.dimension_label
                else:
                    fld = g.qname(facet.rdftype).replace(":", "_")
                idxtype = facet.indexingtype
                if fld in s:
                    # multiple repos can provide the same indexed
                    # properties ONLY if the indextype match
                    if s[fld] != idxtype:
                        raise errors.SchemaConflictError("Repo %s wanted to add a field named %s, but it was already present with a different IndexType" % (repo, fld))
                else:
                    s[fld] = idxtype
        return s

    def get_default_schema(self):
        return {'uri': Identifier(),
                'repo': Label(),
                # 'rdftype': Label(),
                'basefile': Label(),
                # 'title': Text(boost=4),
                # 'identifier': Label(boost=16),
                'text': Text()
        }

    def exists(self):
        """Whether the fulltext index exists."""
        raise NotImplementedError  # pragma: no cover

    def create(self, repos):
        """Creates a fulltext index using the provided schema."""
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

    def update(self, uri, repo, basefile, text, **kwargs):
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

    def query(self, q=None, pagenum=1, pagelen=10, **kwargs):
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

    # subclasses can override fieldmapping, and have
    # to_native_field/from_native_field work on their overridden
    # fieldmapping

    fieldmapping = ()
    """A tuple of ``(abstractfield, nativefield)`` tuples. Each
    ``abstractfield`` should be a instance of a IndexedType-derived
    class. Each ``nativefield`` should be whatever kind of object that
    is used with the native fullltextindex API.

    The methods :py:meth:`to_native_field` and
    :py:meth:`from_native_field` uses this tuple of tuples to convert
    fields.

    """

    def to_native_field(self, fieldobject):
        """Given a abstract field (an instance of a IndexedType-derived
        class), convert to the corresponding native type for the
        fulltextindex in use.
        """
    
        for abstractfield, nativefield in self.fieldmapping:
            if fieldobject == abstractfield:
                return nativefield
        raise errors.SchemaMappingError("Field %s cannot be mapped to a native field" % fieldobject)
        

    def from_native_field(self, fieldobject):
        """Given a fulltextindex native type, convert to the corresponding
        IndexedType object."""
        for abstractfield, nativefield in self.fieldmapping:
            # whoosh field objects do not implement __eq__ sanely --
            # whoosh.fields.ID() == whoosh.fields.DATETIME() is true
            # -- so we do an extra check on the type as well.
            if (type(fieldobject) == type(nativefield) and
                fieldobject == nativefield):
                return abstractfield
        raise errors.SchemaMappingError("Native field %s cannot be mapped" % fieldobject)


class IndexedType(object):

    """Base class for a fulltext searchengine-independent representation
       of indexed data.  By using IndexType-derived classes to
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
    """An identifier is a string, normally in the form of a URI, which uniquely identifies an indexed document."""
    pass


class Datetime(IndexedType):
    pass


class Text(IndexedType):
    pass


class Label(IndexedType):
    pass


class Keyword(IndexedType):
    """A keyword is a single string from a controlled vocabulary."""
    pass


class Boolean(IndexedType):
    pass


class URI(IndexedType):
    """Any URI (except the URI that identifies a indexed document -- use Identifier for that)."""
    pass


class Resource(IndexedType):
    """A fulltextindex.Resource is a URI that also has a human-readable
       label.

    """
    # eg. a particular object/subject with it's own rdfs:label,
    # foaf:name, skos:prefLabel etc


class SearchModifier(object):
    def __init__(self, *values):
        self.values = values

class Less(SearchModifier):
    def __init__(self, max):
        super(Less, self).__init__(*[max])
        self.max = max

class More(SearchModifier):
    def __init__(self, min):
        super(More, self).__init__(*[min])
        self.min = min

class Between(SearchModifier):
    def __init__(self, min, max):
        super(Between, self).__init__(*[min, max])
        self.min = min
        self.max = max


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

    fieldmapping = ((Identifier(),    whoosh.fields.ID(unique=True, stored=True)),
                    (Label(),         whoosh.fields.ID(stored=True)),
                    (Label(boost=16), whoosh.fields.ID(field_boost=16, stored=True)),
                    (Text(boost=4),   whoosh.fields.TEXT(field_boost=4, stored=True,
                                                           analyzer=whoosh.analysis.StemmingAnalyzer(
                                                           ))),
                    (Text(boost=2),   whoosh.fields.TEXT(field_boost=2, stored=True,
                                                           analyzer=whoosh.analysis.StemmingAnalyzer(
                                                           ))),
                    (Text(),          whoosh.fields.TEXT(stored=True,
                                                           analyzer=whoosh.analysis.StemmingAnalyzer())),
                    (Datetime(),      whoosh.fields.DATETIME(stored=True)),
                    (Boolean(),       whoosh.fields.BOOLEAN(stored=True)),
                    (URI(),           whoosh.fields.ID(stored=True, field_boost=1.1)),
                    (Keyword(),       whoosh.fields.KEYWORD(stored=True)),
                    (Resource(),      whoosh.fields.IDLIST(stored=True)),
                    )

    def __init__(self, location, repos):
        self._writer = None
        super(WhooshIndex, self).__init__(location, repos)
        self._multiple = {}
        # Initialize self._multiple so that we know which fields may
        # contain multiple values. FIXME: v. similar to the code in
        # make_schema
        for repo in repos:
            g = repo.make_graph() # for qname lookup
            for facet in repo.facets():
                if facet.dimension_label:
                    fld = facet.dimension_label
                else:
                    fld = g.qname(facet.rdftype).replace(":", "_")
                self._multiple[fld] = facet.multiple_values

    def exists(self):
        return whoosh.index.exists_in(self.location)

    def open(self):
        return whoosh.index.open_dir(self.location)

    def create(self, repos):
        schema = self.make_schema(repos)
        whoosh_fields = {}
        for key, fieldtype in schema.items():
            whoosh_fields[key] = self.to_native_field(fieldtype)

        schema = whoosh.fields.Schema(**whoosh_fields)
        util.mkdir(self.location)
        return whoosh.index.create_in(self.location, schema)

    def destroy(self):
        shutil.rmtree(self.location)

    def schema(self):
        used_schema = {}
        for fieldname, field_object in self.index.schema.items():
            used_schema[fieldname] = self.from_native_field(field_object)
        return used_schema

    def update(self, uri, repo, basefile, text, **kwargs):
        if not self._writer:
            self._writer = self.index.writer()

        s = self.schema()
        for key in kwargs:
            # special-handling of the Resource type -- this is provided as
            # a dict with 'iri' and 'label' keys, and we flatten it to a
            # 2-element list (stored in an IDLIST)
            if isinstance(s[key], Resource):
                # might be multiple values, in which case we create a
                # n-element list, still stored as IDLIST
                if isinstance(kwargs[key], list):
                # or if self._multiple[key]:
                    kwargs[key] = list(itertools.chain.from_iterable([(x['iri'], x['label'])for x in kwargs[key]]))
                else:
                    kwargs[key] = [kwargs[key]['iri'],
                                   kwargs[key]['label']]
            elif isinstance(s[key], Datetime):
                if (isinstance(kwargs[key], date) and
                    not isinstance(kwargs[key], datetime)):
                    # convert date to datetime
                    kwargs[key] = datetime(kwargs[key].year,
                                           kwargs[key].month,
                                           kwargs[key].day)


        self._writer.update_document(uri=uri,
                                     repo=repo,
                                     basefile=basefile,
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

    def query(self, q=None, pagenum=1, pagelen=10, **kwargs):
        # 1: Filter on all specified fields (exact or by using ranges)
        filter = []
        for k, v in kwargs.items():
            if isinstance(v, SearchModifier):
                # Create a Range query
                if isinstance(v.values[0], datetime):
                    cls = whoosh.query.DateRange
                    max = datetime(MAXYEAR, 12, 31)
                    min = datetime(MINYEAR, 1, 1)
                else:
                    cls = whoosh.query.NumericRange
                    max = datetime(2**31)
                    min = datetime(0)
                if isinstance(v, Less):
                    start = min
                    end = v.max
                elif isinstance(v, More):
                    start = v.min
                    end = max
                elif isinstance(v, Between):
                    start = v.min
                    end = v.max
                filter.append(cls(k, start, end))
            elif isinstance(v, str) and "*" in v:
                filter.append(whoosh.query.Wildcard(k, v))
            else:
                # exact field match
                # 
                # Things to handle: Keyword, Boolean, Resource (must
                # be able to match on iri only)
                filter.append(whoosh.query.Term(k, v))

        # 3: If freetext param given, query on that
        freetext = None
        if q or not kwargs:
            if not q:
                q = "*"
            searchfields = []
            for fldname, fldtype in self.index.schema.items():
                if isinstance(fldtype, whoosh.fields.TEXT):
                    searchfields.append(fldname)
            mparser = whoosh.qparser.MultifieldParser(searchfields,
                                                  self.index.schema)
            freetext = mparser.parse(q)

        if filter:
            if freetext:
                filter.append(freetext)
            query = whoosh.query.And(filter)
        elif freetext:
            query = freetext
        else:
            raise ValueError("Neither q or kwargs specified")
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
        resourcefields = []
        for key, fldobj in self.schema().items():
            if isinstance(fldobj, Resource):
                resourcefields.append(key)

        for hit in res:
            fields = hit.fields()
            highlighted = hl.highlight_hit(hit, "text", fields['text'])
            if highlighted:
                fields['text'] = highlighted
            else:
                del fields['text']
            # de-marschal Resource objects from list to dict
            for key in resourcefields:
                if key in fields:
                    # need to return a list of dicts if
                    # multiple_values was specified, and a simple dict
                    # otherwise... (note that just examining if
                    # len(fields[key]) == 2 isn't enough)
                    if self._multiple[key]:
                        fields[key] = [{'iri': x[0], 'label': x[1]} for x in zip(fields[key][0::2], fields[key][1::2])]
                    else:
                        fields[key] = {'iri': fields[key][0],
                                       'label': fields[key][1]}
            l.append(fields)
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

    def create(self, repos):
        relurl, payload = self._create_schema_payload(repos)
        # print("\ncreate: PUT %s\n%s\n" % (self.location + relurl, payload))
        res = requests.put(self.location + relurl, payload)
        try:
            res.raise_for_status()
        except Exception as e:
            raise Exception("%s: %s" % (res.status_code, res.text))

    def schema(self):
        relurl, payload = self._get_schema_payload()
        res = requests.get(self.location + relurl)  # payload is
                                                    # probably never
                                                    # used
        # print("GET %s" % relurl)
        # print(json.dumps(res.json(), indent=4))
        return self._decode_schema(res)


    def update(self, uri, repo, basefile, text, **kwargs):
        relurl, payload = self._update_payload(
            uri, repo, basefile, text, **kwargs)
        # print("update: PUT %s\n%s\n" % (self.location + relurl, payload))
        res = requests.put(self.location + relurl, payload)
        try:
            res.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise errors.IndexingError(str(e) + ": '%s'" % res.text)

    def doccount(self):
        relurl, payload = self._count_payload()
        if payload:
            res = requsts.post(self.location + relurl, payload)
        else:
            res = requests.get(self.location + relurl)
        return self._decode_count_result(res)

    def query(self, q=None, pagenum=1, pagelen=10, **kwargs):
        relurl, payload = self._query_payload(q, pagenum, pagelen, **kwargs)
        if payload:
            # print("query: POST %s:\n%s" % (self.location + relurl, payload))
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

    # maps our field classes to concrete ES field properties
    fieldmapping = ((Identifier(),
                     {"type": "string", "index": "not_analyzed", "store": True}),  # uri
                    (Label(),
                     {"type": "string", "index": "not_analyzed", }),  # repo, basefile
                    (Label(boost=16),
                     {"type": "string", "boost": 16.0, "index": "not_analyzed", "norms": {"enabled": True}}),# identifier
                    (Text(boost=4),
                     {"type": "string", "boost": 4.0, "index": "not_analyzed", "norms": {"enabled": True}}),  # title
                    (Text(boost=2),
                     {"type": "string", "boost": 2.0, "index": "not_analyzed", "norms": {"enabled": True}}),  # abstract
                    (Text(),
                     {"type": "string", "analyzer": "my_analyzer"}),  # text
                    (Datetime(),
                     {"type": "date", "format": "dateOptionalTime"}),
                    (Boolean(),
                     {"type": "boolean"}),
                    (Resource(),
                     {"properties": {"iri": {"type": "string", "index": "not_analyzed"},
                                     "label": {"type": "string", "index": "not_analyzed"}}}),
                    (Keyword(),
                     {"type": "string", "index_name": "keyword"}),
                    (URI(),
                     {"type": "string", "index": "not_analyzed", "boost": 1.1, "norms": {"enabled": True}}),
                    )

    def commit(self):
        r = requests.post(self.location + "_refresh")
        r.raise_for_status()

    def exists(self):
        r = requests.get(self.location + "_mapping/")
        if r.status_code == 404:
            return False
        else:
            return True

    def _update_payload(self, uri, repo, basefile, text, **kwargs):
        safe = ''
        if six.PY2:
            # urllib.quote in python 2.6 cannot handle unicode values
            # for the safe parameter (not even empty). urllib.quote in
            # python 2.7 handles it, but may fail later on. FIXME: We
            # should create a shim as ferenda.compat.quote and use
            # that
            safe = safe.encode('ascii') # pragma: no cover

        # quote (in python 2) only handles characters from 0x0 - 0xFF,
        # and basefile might contain characters outside of that (eg
        # u'MO\u0308D/P11463-12', which is MÖD/P11463-12 on a system
        # which uses unicode normalization form NFD). To be safe,
        # encodethe string to utf-8 beforehand (Which is what quote on
        # python 3 does anyways)
        relurl = "%s/%s" % (repo, quote(basefile.encode("utf-8"), safe=safe))  # eg type, id
        if "#" in uri:
            relurl += uri.split("#", 1)[1]
        payload = {"uri": uri,
                   "basefile": basefile,
                   "text": text}
        payload.update(kwargs)
        return relurl, json.dumps(payload, default=util.json_default_date, indent=4)

    def _query_payload(self, q, pagenum=1, pagelen=10, **kwargs):
        relurl = "_search?from=%s&size=%s" % ((pagenum - 1) * pagelen, pagelen)

        # 1: Filter on all specified fields
        filterterms = {}
        filterregexps = {}
        schema = self.schema()
        for k, v in kwargs.items():
            if isinstance(v, SearchModifier):
                continue
            if k == "repo":
                k = "_type"
            elif isinstance(schema[k], Resource):
                # also map k to "%s.iri" % k if k is Resource
                k += ".iri"

            if isinstance(v, str) and "*" in v:
                # if v contains "*", make it a {'regexp': '.*/foo'} instead of a {'term'}
                # also transform * to .*
                filterregexps[k] = v.replace("*", ".*")
            else:
                filterterms[k] = v

        # 2: Create filterranges if SearchModifier objects are used
        filterranges = {}
        for k, v in kwargs.items():
            if not isinstance(v, SearchModifier):
                continue
            if isinstance(v, Less):
                filterranges[k] = {"lt": v.max}
            elif isinstance(v, More):
                filterranges[k] = {"gt": v.min}
            elif isinstance(v, Between):
                filterranges[k] = {"lt": v.max,
                                   "gt": v.min}

        # 3: If freetext param given, search on that
        match = {}
        if q:
            # NOTE: 
            match['_all'] = q

        if filterterms or filterregexps or filterranges:
            query = {"filtered":
                     {"filter": {}
                      }
                     }
            if filterterms:
                if len(filterterms) > 1:
                    ts = [{"term": {k: v}} for (k, v) in filterterms.items()]
                    query["filtered"]["filter"]["bool"] = {"must": ts}
                else:
                    query["filtered"]["filter"]["term"] = filterterms
            if filterregexps:
                query["filtered"]["filter"]["regexp"] = filterregexps
            if filterranges:
                query["filtered"]["filter"]["range"] = filterranges
            if match:
                query["filtered"]["query"] = {"match": match}
        else:
            if match:
                query = {"match": match}
            else:
                query = {"match_all": match}
            
        payload = {'query': query}
        if q:
            payload['highlight'] = {'fields': {'text': {}},
                                    'pre_tags': ["<strong class='match'>"],
                                    'post_tags': ["</strong>"],
                                    'fragment_size': '40'}
        # Don't include the full text of every document in every hit
        payload['_source'] = {'exclude': ['text']}
        return relurl, json.dumps(payload, indent=4, default=util.json_default_date)

    def _decode_query_result(self, response, pagenum, pagelen):
        # attempt to decode iso-formatted datetimes
        # ("2013-02-14T14:06:00"). Note that this will incorrectly
        # decode anything that looks like a ISO date, even though it
        # might be typed as a string. We have no typing information
        # (at this stage -- we could look at self.schema() though)
        jsonresp = json.loads(response.text,
                              object_hook=util.make_json_date_object_hook())

        res = []
        for hit in jsonresp['hits']['hits']:
            h = hit['_source']
            h['repo'] = hit['_type']
            if 'highlight' in hit:
                # wrap highlighted field in P, convert to
                # elements. FIXME: should work for other fields than
                # 'text'
                hltext = " ... ".join([x.strip() for x in hit['highlight']['text']])
                soup = BeautifulSoup("<p>%s</p>" % re.sub("\s+", " ", hltext))
                h['text'] = html.elements_from_soup(soup.html.body.p)
            res.append(h)
        pager = {'pagenum': pagenum,
                 'pagecount': int(math.ceil(jsonresp['hits']['total'] / float(pagelen))),
                 'firstresult': (pagenum - 1) * pagelen + 1,
                 'lastresult': (pagenum - 1) * pagelen + len(jsonresp['hits']['hits']),
                 'totalresults': jsonresp['hits']['total']}
        return res, pager

    def _count_payload(self):
        return "_count", None

    def _decode_count_result(self, response):
        if response.status_code == 404:
            return 0
        else:
            return response.json()['count']

    def _get_schema_payload(self):
        return "_mapping", None

    def _decode_schema(self, response):
        indexname = self.location.split("/")[-2]
        mappings = response.json()[indexname]["mappings"]
        schema = {}
        # flatten the existing types (pay no mind to duplicate fields):
        for typename, mapping in mappings.items():
            for fieldname, fieldobject in mapping["properties"].items():
                try:
                    schema[fieldname] = self.from_native_field(fieldobject)
                except errors.SchemaMappingError as e:
                    raise errors.SchemaMappingError("%s: %s" % (fieldname, str(e)))
                schema["repo"] = self.get_default_schema()['repo']
        return schema

    def _create_schema_payload(self, repos):
        payload = {
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
            "mappings": {}
        }
        for repo in repos:
            g = repo.make_graph() # for qname lookup
            es_fields = {}
            schema = self.get_default_schema()
            for facet in repo.facets():
                if facet.dimension_label:
                    fld = facet.dimension_label
                else:
                    fld = g.qname(facet.rdftype).replace(":", "_")
                idxtype = facet.indexingtype
                schema[fld] = idxtype
            
            for key, fieldtype in schema.items():
                if key == "repo":
                    continue  # not really needed for ES, as type == repo.alias
                es_fields[key] = self.to_native_field(fieldtype)
            # _source enabled so we can get the text back
            payload["mappings"][repo.alias] = {"_source": {"enabled": True},
                                               "_all": {"analyzer": "my_analyzer"},
                                               "properties": es_fields}
        return "", json.dumps(payload, indent=4)

    def _destroy_payload(self):
        return "", None
