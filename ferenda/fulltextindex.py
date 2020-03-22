# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *
from future import standard_library
standard_library.install_aliases()

from datetime import date, datetime, MAXYEAR, MINYEAR
from urllib.parse import quote, unquote
from copy import deepcopy
import itertools
import json
import math
import re
import shutil
import tempfile

import requests
import requests.exceptions
from bs4 import BeautifulSoup

from ferenda import util, errors
import logging

class FulltextIndex(object):

    """This is the abstract base class for a fulltext index. You use it by
       calling the static method FulltextIndex.connect, passing a
       string representing the underlying fulltext engine you wish to
       use. It returns a subclass on which you then call further
       methods.

    """


    indextypes = {}  # this is repopulated at the very end of this
                     # module, when the classes we need to specify are
                     # defined.
    
    @classmethod
    def connect(cls, indextype, location, repos):
        """Open a fulltext index (creating it if it doesn't already exists).

        :param location: Type of fulltext index ("WHOOSH" or "ELASTICSEARCH")
        :type  location: str
        :param location: The file path of the fulltext index.
        :type  location: str

        """
        # create correct subclass and return it
        return cls.indextypes[indextype](location, repos)

    def __init__(self, location, repos):
        self.location = location
        if self.exists():
            self.index = self.open()
        else:
            assert repos, "Attempt to create a fulltext index, but no repos were provided, index schema would be empty"
            self.index = self.create(repos)
        self.log = logging.getLogger("ferenda.fulltextindex")

    def __del__(self):
        self.close()

    def make_schema(self, repos):
        s = self.get_default_schema()
        for repo in repos:
            g = repo.make_graph()  # for qname lookup
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
                        raise errors.SchemaConflictError(
                            "Repo %s wanted to add a field named %s, but it was already present with a different IndexType" %
                            (repo, fld))
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

    def query(self, q=None, pagenum=1, pagelen=10, ac_query=False, exclude_repos=None, boost_repos=None, include_fragments=False, **kwargs):
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
    alternate_fieldmapping = ()

    def to_native_field(self, fieldobject):
        """Given a abstract field (an instance of a IndexedType-derived
        class), convert to the corresponding native type for the
        fulltextindex in use.
        """

        for abstractfield, nativefield in self.fieldmapping:
            if fieldobject == abstractfield:
                return nativefield
        raise errors.SchemaMappingError(
            "Field %s cannot be mapped to a native field" %
            fieldobject)

    def from_native_field(self, fieldobject):
        """Given a fulltextindex native type, convert to the corresponding
        IndexedType object."""
        for abstractfield, nativefield in self.fieldmapping:
            # whoosh field objects do not implement __eq__ sanely --
            # whoosh.fields.ID() == whoosh.fields.DATETIME() is true
            # -- so we do an extra check on the type as well.
            if (isinstance(fieldobject, type(nativefield)) and
                    fieldobject == nativefield):
                return abstractfield
        for abstractfield, nativefield in self.alternate_fieldmapping:
            if fieldobject == nativefield:
                return abstractfield
        raise errors.SchemaMappingError("Native field %s cannot be mapped, fieldmapping: %r " % (fieldobject, self.fieldmapping))


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


class Integer(IndexedType):
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


class RegexString(str):
    # Parameters of this type are interpreted as using regexp
    # semantics, not globbing semantics. I.e. "foo.*" instead of "foo*"
    pass

class Results(list):
    # this is just so that we can add arbitrary attributes to a
    # list-like object.
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

    def __init__(self, wrapelement=html.P, hitelement=html.Strong,
                 classname="match", between=" ... "):
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
            g = repo.make_graph()  # for qname lookup
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
                    kwargs[key] = list(
                        itertools.chain.from_iterable([(x['iri'], x['label'])for x in kwargs[key]]))
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

    def query(self, q=None, pagenum=1, pagelen=10, ac_query=False, exclude_repos=None, boost_repos=None, include_fragments=False, **kwargs):
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
        l = Results()
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
                        fields[key] = [{'iri': x[0], 'label': x[1]}
                                       for x in zip(fields[key][0::2], fields[key][1::2])]
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
    defaultheaders = {}
    # The only real implementation of RemoteIndex has its own exists
    # implementation, no need for a general fallback impl.
    # def exists(self):
    #     pass

    def create(self, repos):
        relurl, payload = self._create_schema_payload(repos)
        # print("\ncreate: PUT %s\n%s\n" % (self.location + relurl, payload))
        res = requests.put(self.location + relurl, payload, headers=self.defaultheaders)
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
        # print("update: PUT %s\n%s\n" % (self.location + relurl, payload[:80]))
        res = requests.put(self.location + relurl, payload, headers=self.defaultheaders)
        try:
            res.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise errors.IndexingError(str(e) + ": '%s'" % res.text)

    def doccount(self):
        relurl, payload = self._count_payload()
        if payload:
            res = requsts.post(self.location + relurl, payload, headers=self.defaultheaders)
        else:
            res = requests.get(self.location + relurl)
        return self._decode_count_result(res)

    def query(self, q=None, pagenum=1, pagelen=10, ac_query=False,
              exclude_repos=None, boost_repos=None, include_fragments=False,
              **kwargs):
        relurl, payload = self._query_payload(q, pagenum, pagelen,
                                              ac_query, exclude_repos, boost_repos,
                                              include_fragments, **kwargs)
        if payload:
            # print("query: POST %s:\n%s" % (self.location + relurl, payload))
            res = requests.post(self.location + relurl, payload, headers=self.defaultheaders)
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
    defaultheaders = {"Content-Type": "application/json"}


    # maps our field classes to concrete ES field properties
    fieldmapping = ((Identifier(),
                     {"type": "text", "store": True, "analyzer": "lowercase_keyword"}),  # uri -- using type=text with analyzer=keyword (instead of type=keyword) enables us to use regex queries on this field, which is nice for autocomplete
                    (Label(),
                     {"type": "keyword", "copy_to": ["all"]}),  # repo, basefile
                    (Label(boost=16),
                     {"type": "text", "copy_to": ["all"], "boost": 16.0, "fields": {
                         "keyword": {"type": "text", "analyzer": "lowercase_keyword"}
                     }}),  # identifier
                    (Text(boost=4),
                     {"type": "text", "copy_to": ["all"], "boost": 4.0}),  # title
                    (Text(boost=2),
                     {"type": "text", "copy_to": ["all"], "boost": 2.0}),  # abstract
                    (Text(),
                     {"type": "text", "copy_to": ["all"], "store": True}),  # text
                    (Datetime(),
                     {"type": "date", "format": "strict_date_optional_time"}),
                    (Boolean(),
                     {"type": "boolean"}),
                    (Resource(),
                     {"properties": {"iri": {"type": "keyword"},
                                     "label": {"type": "keyword", "copy_to": ["all"]}}}),
                    (Keyword(),
                     {"type": "keyword", "copy_to": ["keyword", "all"]}),
                    (URI(),
                     {"type": "keyword", "boost": 1.1, "norms": True}),
                    (Integer(),
                     {"type": "long"}),
                    )

    # used whenever ElasticSearch changes the mapping behind our backs...
    alternate_fieldmapping = (
        (Label(boost=16),
         {'fields': {'keyword':
                     {'ignore_above': 256, 'type': 'keyword'}},
          'type': 'text'}),
        )
    term_excludes = "excludes"  # this key changed name 
                                # "exclude"->"excludes" from 2.* to
                                # 5.*

    fragment_size = 150
    # a list of fieldnames (possibly with boost factors)
    default_fields = ("label^3", "text")

    def __init__(self, location, repos):
        self._writer = None
        self._repos = repos
        super(ElasticSearchIndex, self).__init__(location, repos)

    def close(self):
        return self.commit()

    def commit(self):
        if not self._writer:
            return  # no pending changes to commit
        self._writer.seek(0)
        res = requests.put(self.location + "/_bulk", data=self._writer, headers=self.defaultheaders)
        self._writer.close()
        self._writer = None
        try:
            res.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise errors.IndexingError(str(e) + ": '%s'" % res.text)
        # if the errors field is set to True, errors might have
        # occurred even though the status code was 200
        if res.json().get("errors"):
            raise errors.IndexingError("%s errors when committing, first was %r" %
                                       (len(res.json()["items"]),
                                        res.json()["items"][0]))
        # make sure everything is really comitted (available for
        # search) before continuing? TODO: Check if this slows
        # multi-basefile (and multi-threaded) indexing down noticably,
        # we could just do it at the end of relate_all_teardown.
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
        # relurl is really the doc id, from elasticsearchs point of view
        relurl = "%s%s%s" % (repo, "/", quote(basefile.encode("utf-8"), safe=safe))
        payload = {"uri": uri,
                   "repo": repo,
                   "basefile": basefile,
                   "text": text,
                   "join": "parent"
        }
        if "#" in uri:
            baseuri, extra = uri.split("#", 1)
            payload["join"] = {"name": "child",
                               "parent": unquote(relurl)}
            relurl += "#" + extra

        payload.update(kwargs)
        return relurl, json.dumps(payload, default=util.json_default_date)

    def update(self, uri, repo, basefile, text, **kwargs):
        if not self._writer:
            self._writer = tempfile.TemporaryFile()
        relurl, payload = self._update_payload(
            uri, repo, basefile, text, **kwargs)
        relurl = unquote(relurl)
        metadata = {"index": {"_id": relurl,
                              # the need for this is badly documented and
                              # might go away in future ES versions
                              "_type": "_doc"}
        }
        extra = ""
        if "#" in uri:
            # metadata["index"]['_id'] += uri.split("#", 1)[1]
            metadata["index"]["routing"] = relurl.split("#")[0]

            extra = " (parent: %s)" % basefile

        # print("index: %s, id: %s, uri: %s %s" % (metadata["index"]['_type'],
        #                                          metadata["index"]['_id'],
        #                                          uri, extra))
        # print("Label: %s" % kwargs['label'])
        # print("Text: %s" % text[:72])
        # print("---------------------------------------")

        metadata = json.dumps(metadata) + "\n"
        assert "\n" not in payload, "payload contains newlines, must be encoded for bulk API"
        self._writer.write(metadata.encode("utf-8"))
        self._writer.write(payload.encode("utf-8"))
        # if "#" not in uri:
        # print("----")
        # print(metadata)
        # print("-----")
        # print(payload)
        self._writer.write(b"\n")

    def _query_payload(self, q, pagenum=1, pagelen=10, ac_query=False,
                       exclude_repos=None, boost_repos=None, include_fragments=False, **kwargs):
        if kwargs.get("type"):
            types = [kwargs.get("type")]
        else:
            types = [repo.alias for repo in self._repos if repo.config.relate]
        relurl = "_search?from=%s&size=%s" % ((pagenum - 1) * pagelen,
                                              pagelen)

        # 1: Filter on all specified fields
        filterterms = {}
        filterregexps = {}
        schema = self.schema()
        for k, v in kwargs.items():
            if isinstance(v, SearchModifier):
                continue
            elif k.endswith(".keyword"):
                pass  # leave as-is, don't try to look this up in schema
            elif isinstance(schema[k], Resource):
                # also map k to "%s.iri" % k if k is Resource
                k += ".iri"
            if isinstance(v, RegexString):
                filterregexps[k] = str(v)
            elif isinstance(v, str) and "*" in v:
                # if v contains "*", make it a {'regexp': '.*/foo'} instead of a {'term'}
                # also transform * to .* and escape '#' and '.'
                filterregexps[k] = v.replace(".", "\\.").replace("#", "\\#").replace("*", ".*")
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
        inner_hits = {"_source": {self.term_excludes: "text"}}
        highlight = None
        if q:
            if not ac_query:
                # NOTE: we need to specify highlight parameters for each
                # subquery when using has_child, see
                # https://github.com/elastic/elasticsearch/issues/14999 --
                # still seems to be an issue with ES5.*
                match['fields'] = self.default_fields
                match['query'] = q
                match['default_operator'] = "and"
                highlight = {'fields': {'text': {},
                                        'label': {}},
                             'fragment_size': self.fragment_size,
                             'number_of_fragments': 2
                }
                inner_hits["highlight"] = highlight
                submatches = [{"simple_query_string": deepcopy(match)}]
                submatches.append(
                    {"has_child": {
                        "type": "child",
                        "inner_hits": inner_hits,
                        "query": {
                            "bool": {
                                "must": {"simple_query_string": deepcopy(match)},
                                # some documents are put into the
                                # index purely to support ac_query
                                # (autocomplete), eg page-oriented
                                # documents from FixedLayoutSource
                                # that uses the autocomplete
                                # functionality to match and display
                                # the first few lines of eg
                                # "prop. 2018/19:42 s 12". We don't
                                # need them in our main search
                                # results.
                                "must_not": {"term": {"role": "autocomplete"}}
                }}}})
                match = {"bool": {"should": submatches}}
            else:
                # ac_query -- need to work in inner_hits somehow
                # also: sort by order if present
                pass
        else:
            match = {"bool": {}}

        if boost_repos:
            boost_functions = []
            for _type, boost in boost_repos:
                boost_functions.append({"filter": {"term": {"repo": _type}},
                                        "weight": boost})
                # FIXME: provide a more general way for the caller to
                # constrol these score-altering functions. This boosts
                # expired SFS docs by 0.5 (ie halves teh score)
                if _type == "sfs":
                    boost_functions.append({"filter": {"term": {"role": "expired"}},
                                            "weight": 0.5})

        if filterterms or filterregexps or filterranges:
            filters = []
            for key, val in (("term", filterterms),
                             ("regexp", filterregexps),
                             ("range", filterranges)):
                filters.extend([{key: {k: v}} for (k, v) in val.items()])
            if len(filters) > 1:
                match["bool"]["must"] = {"bool": {"must": filters}}
            else:
                match["bool"]["must"] = filters[0]
            if exclude_repos:
                match["bool"]["must_not"] = []
                for exclude_type in exclude_repos:
                    # Not entirely sure this works for filtering out
                    # multiple repos -- we only ever filter out the
                    # mediawiki repo (and even then we probably
                    # shouldn't index that in the first place)
                    match["bool"]["must_not"].append({"term": {"repo": exclude_type}})

        if boost_repos:
            payload = {'query': {'function_score': {'functions': boost_functions,
                                                    'query': match}}}
        else:
            payload = {'query': match}
        if not ac_query:
            payload['aggs'] = self._aggregation_payload()
        if q and "must" in match["bool"]:
            # fixes staffanm/lagen.nu#69 by making sure that documents
            # that matches the filter query (as a must clause) but
            # does not score anything in the should query aren't
            # counted. This shouldn't be used in AC queries since they
            # only use filters, not freetext query parameters
            #
            # since we express our filter as a must clause (not a
            # filter clause) it will add 1 to the score. We therefore
            # require something more than just 1 in score.
            payload["min_score"] = 1.01
        else:
            # in other context, we use a fulter clause to make sure
            # only parent documents are selected. However, that seems
            # to make sure every document that passes the filter is
            # included, even though they get 0 score from the should
            # clause. A low low min score filters those out.x
            payload["min_score"] = 0.01
        # make sure only parent documents are returned in the main
        # list of hits (child documents appear as inner_hits on their
        # parent documents hit).
        if "filter" not in match["bool"]:
            match["bool"]["filter"] = []
        if not ac_query:
            # autocomplete queries must match 
            match["bool"]["filter"].append({"term": {"join": "parent"}})
        # Don't include the full text of every document in every hit
        if not ac_query:
            payload['_source'] = {self.term_excludes: ['text']}
        # extra workaround, solution adapted from comments in
        # https://github.com/elastic/elasticsearch/issues/14999 --
        # revisit once Elasticsearch 2.4 is released.
        if highlight:
            payload['highlight'] = deepcopy(highlight)

        if ac_query and q is None:
            if 'uri' in kwargs:
                # for autocomplete queries when not using any "natural
                # language" queries (ie. only query based on a
                # identifer like "TF 2:" that gets transformed into a
                # URI)-- in these cases we'd like to use natural order
                # of the results if available
                payload['sort'] = [{"order": "asc"},
                                   "_score"]
            elif not include_fragments:
                # if we don't have an autocomplete query of this kind,
                # exclude fragments (here identified by having a non-zero
                # order). 
                match["bool"]["filter"].append({"term": {"join": "parent"}})

            if "must_not" not in match["bool"]:
                match["bool"]["must_not"] = []
            # FIXME: This is very specific to lagen.nu and should
            # preferably be controlled through some sort of extra
            # arguments
            # match['bool']['must_not'].append({"term": {"role": "expired"}})

        return relurl, json.dumps(payload, indent=4, default=util.json_default_date)

    def _aggregation_payload(self):
        aggs = {'type': {'terms': {'field': 'repo', 'size': 100}}}
        for repo in self._repos:
            if not repo.config.relate:
                continue
            for facet in repo.facets():
                if (facet.dimension_label in ('creator', 'issued') and
                    facet.dimension_label not in aggs and
                    facet.dimension_type in ('year', 'ref', 'type')):
                    if facet.dimension_type == "year":
                        agg = {'date_histogram': {'field': facet.dimension_label,
                                                  'interval': 'year',
                                                  'format': 'yyyy',
                                                  'min_doc_count': 1}}
                    else:
                        agg = {'terms': {'field': facet.dimension_label, 'size': 100}}
                    aggs[facet.dimension_label] = agg
        return aggs

    def _decode_query_result(self, response, pagenum, pagelen):
        
        # attempt to decode iso-formatted datetimes
        # ("2013-02-14T14:06:00"). Note that this will incorrectly
        # decode anything that looks like a ISO date, even though it
        # might be typed as a string. We have no typing information
        # (at this stage -- we could look at self.schema() though)
        jsonresp = json.loads(response.text,
                              object_hook=util.make_json_date_object_hook())
        res = Results()
        for hit in jsonresp['hits']['hits']:
            h = self._decode_query_result_hit(hit)
            if "inner_hits" in hit:
                for inner_hit_type in hit["inner_hits"].keys():
                    for inner_hit in hit["inner_hits"][inner_hit_type]["hits"]["hits"]:
                        if not "innerhits" in h:
                            h["innerhits"] = []
                        h["innerhits"].append(self._decode_query_result_hit(inner_hit))
            res.append(h)
        pager = {'pagenum': pagenum,
                 'pagecount': int(math.ceil(jsonresp['hits']['total']['value'] / float(pagelen))),
                 'firstresult': (pagenum - 1) * pagelen + 1,
                 'lastresult': (pagenum - 1) * pagelen + len(jsonresp['hits']['hits']),
                 'totalresults': jsonresp['hits']['total']['value']}
        setattr(res, 'pagenum', pager['pagenum'])
        setattr(res, 'pagecount', pager['pagecount'])
        setattr(res, 'lastresult', pager['lastresult'])
        setattr(res, 'totalresults', pager['totalresults'])
        if 'aggregations' in jsonresp:
            setattr(res, 'aggregations', jsonresp['aggregations'])
        return res, pager

    def _decode_query_result_hit(self, hit):
        h = hit['_source']
        # h['repo'] = hit['_type']
        if "join" in h:
            del h["join"]
            
        if 'highlight' in hit:
            for hlfield in ('text', 'label'):
                if hlfield in hit['highlight']:
                    # wrap highlighted field in P, convert to
                    # elements.
                    hltext = re.sub("\s+", " ", " ... ".join([x.strip() for x in hit['highlight'][hlfield]]))
                    hltext = hltext.replace("<em>", "<strong class='match'>").replace("</em>", " </strong>")
                    # FIXME: BeautifulSoup/lxml returns empty soup if
                    # first char is '§' or some other non-ascii char (like
                    # a smart quote). Padding with a space makes problem
                    # disappear, but need to find root cause.
                    soup = BeautifulSoup("<p> %s</p>" % hltext, "lxml")
                    h[hlfield] = html.elements_from_soup(soup.html.body.p)
        return h

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
        mappings = response.json()[indexname]["mappings"]["properties"]
        schema = {}
        for fieldname, fieldobject in mappings.items():
            if fieldname in ('keyword', 'all', 'join', 'parent'):
                # our copy_to: keyword definition for the Keyword
                # indexed type dynamically creates a new
                # field. Skip that.
                continue
            schema[fieldname] = self.from_native_field(fieldobject)
        schema["repo"] = self.get_default_schema()['repo']
        return schema

    def _create_schema_payload(self, repos):
        language = {'en': 'English',
                    'sv': 'Swedish'}.get(repos[0].lang, "English")
        payload = {
            "settings": {
                "highlight": {
                    "max_analyzed_offset": 10000000
                },
                "analysis": {
                    "analyzer": {
                        "default": {
                            "filter": ["lowercase", "snowball"],
                            "tokenizer": "standard",
                            "type": "custom"
                        },
                        "lowercase_keyword": {
                            "tokenizer": "keyword",
                            "filter": ["lowercase"]
                        }
                    },
                    "filter": {
                        "snowball": {
                            "type": "snowball",
                        "language": language
                        }
                    }
                }
            },
            "mappings": {}
        }
        fields = {}
        es_fields = {"all": {"type": "text", "store": "false"},
                     "join": {"type": "join", "relations": {"parent": "child"}},
                     # "parent": self.to_native_field(Identifier())
        }
        for repo in repos:
            if not repo.config.relate:
                continue
            facets = repo.facets()
            if not facets:
                continue
            g = repo.make_graph()  # for qname lookup
            schema = self.get_default_schema()
            childschema = self.get_default_schema()
            for facet in facets:
                if facet.dimension_label:
                    fld = facet.dimension_label
                else:
                    fld = g.qname(facet.rdftype).replace(":", "_")
                idxtype = facet.indexingtype
                schema[fld] = idxtype
                if not facet.toplevel_only:
                    childschema[fld] = idxtype

            schema.update(childschema)
            for key, fieldtype in schema.items():
                native = self.to_native_field(fieldtype)
                if key not in es_fields:
                    es_fields[key] = native
                    assert es_fields[key] == native, "incompatible fields for key %s: %s != %s" % (key, es_fields[key], native)
        # _source enabled so we can get the text back
        payload["mappings"] = {"_source": {"enabled": True},
                               "properties": es_fields}
        return "", json.dumps(payload, indent=4)

    def _destroy_payload(self):
        return "", None

FulltextIndex.indextypes = {'WHOOSH': WhooshIndex,
                            'ELASTICSEARCH': ElasticSearchIndex}
