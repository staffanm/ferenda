from pprint import pprint

import whoosh.index
import whoosh.fields
import whoosh.analysis
import whoosh.query
import whoosh.qparser
import whoosh.writing

from ferenda import util

class FulltextIndex(object):
    """Open a fulltext index (creating it if it
    doesn't already exists).

    :param location: The file path of the fulltext index.
    :type  location: str
    :param docrepos: :py:class:`~ferenda.DocumentRepository` instances, used to create extra fields in the full text index
    :type  docrepos: list

    .. note::

       The docrepos parameter isn't implemented yet.
        """

    def __init__(self,location,docrepos=None):
        default_schema = {'uri':Identifier(),
                          'repo':Label(),
                          'basefile':Label(),
                          'title':Text(boost=4),
                          'identifier':Label(boost=16),
                          'text':Text()}
        if whoosh.index.exists_in(location):
            self._index = whoosh.index.open_dir(location)
        else:
            self._index = self._create_whoosh_index(location,default_schema)
        self._schema = default_schema
        self._writer = None
        self._batchwriter = False

    def _create_whoosh_index(self,location,fields):
        # maps our field classes to concrete whoosh field instances
        mapped_field = {Identifier():   whoosh.fields.ID(unique=True),
                        Label():        whoosh.fields.ID(stored=True),
                        Label(boost=16):whoosh.fields.ID(field_boost=16,stored=True),
                        Text(boost=4):  whoosh.fields.TEXT(field_boost=4,stored=True,
                                                           analyzer=whoosh.analysis.StemmingAnalyzer()),
                        Text():         whoosh.fields.TEXT(stored=True,
                                                           analyzer=whoosh.analysis.StemmingAnalyzer())}
                        
        whoosh_fields = {}
        for key,fieldtype in fields.items():
            whoosh_fields[key] = mapped_field[fieldtype]
        schema = whoosh.fields.Schema(**whoosh_fields)
        util.mkdir(location)
        return whoosh.index.create_in(location,schema)

    def schema(self):
        """Returns the schema in use. A schema is a dict where the keys are field names and the values are any subclass of :py:class:`ferenda.fulltextindex.IndexedType`"""
        return self._schema
    
    def update(self, uri, repo, basefile, title, identifier, text, **kwargs):
        # Other standard attrs: typeof?
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
        :param basefile: The basefile which containsresource
        :type  basefile: str
        :param title: User-displayable title of resource (if applicable). Should not contain the same information as ``identifier``. 
        :type  title: str
        :param identifier: User-displayable short identifier for resource (if applicable)
        :type  identifier: str
        :type  text: The full textual content of the resource, as a plain string.
        :type  text: str

        .. note::

           Calling this method may not directly update the fulltext
           index -- you need to call :meth:`commit` or :meth:`close`
           for that.

        """
        if not self._writer:
            if self._batchwriter:
                # self._writer = self._index.writer(procs=4, limitmb=256, multisegment=True)
                self._writer = whoosh.writing.BufferedWriter(self._index, limit=1000)
                #indexwriter = self._index.writer()
                #stemfilter = indexwriter.schema["text"].analyzer[-1]
                #stemfilter.cachesize = -1
                #stemfilter.clear()
                #indexwriter.close()
            else:
                self._writer = self._index.writer()

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
        """Commit all pending updates to the fulltext index."""
        if self._writer:
            self._writer.commit()
            if not isinstance(self._writer, whoosh.writing.BufferedWriter):
                # A bufferedWriter can be used again after commit(), a regular writer cannot
                self._writer = None

    def close(self):
        """Commits all pending updates and closes the index."""
        self.commit()
        if self._writer:
            self._writer.close()
            self._writer = None
            
    def __del__(self):
        self.close()

    def doccount(self):
        """Returns the number of currently indexed (non-deleted) documents."""
        return self._index.doc_count()

    def query(self,q, **kwargs):
        """Perform a free text query against the full text index, optionally restricted with parameters for individual fields.

        :param q: Free text query, using the selected full text index's prefered query syntax
        :type  q: str
        :param **kwargs: any parameter will be used to match a similarly-named field
        :type **kwargs: dict
        :returns: matching documents, each document as a dict of fields
        :rtype: list

        .. note::

           The *kwargs* parameters do not yet do anything -- only simple full text queries are possible.

        """
        searchfields = ['identifier','title','text']
        mparser = whoosh.qparser.MultifieldParser(searchfields,
                                                  self._index.schema)
        query = mparser.parse(q)
        # query = whoosh.query.Term("text",q)
        with self._index.searcher() as searcher:
            res = self._convert_result(searcher.search(query))

        return res

    def _convert_result(self,res):
        # converts a whoosh.searching.Results object to a plain list of dicts
        l = []
        for hit in res:
            l.append(hit.fields())
        return l

class IndexedType(object):
    def __eq__(self, other):
        return (isinstance(other, self.__class__)
                and self.__dict__ == other.__dict__)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(tuple(v for k, v in sorted(self.__dict__.items())))
    
    def __init__(self,**kwargs):
        self.__dict__ = dict(kwargs)

    def __repr__(self):
        # eg '<Label boost=16>' or '<Identifier>'
        dictrepr = "".join((" %s=%s"%(k,v) for k,v in sorted(self.__dict__.items())))
        return ("<%s%s>" % (self.__class__.__name__, dictrepr))
    
    
class Identifier(IndexedType): pass
class Datetime(IndexedType): pass
class Text(IndexedType): pass
class Label(IndexedType): pass
class Keywords(IndexedType): pass
class Boolean(IndexedType): pass
class URI(IndexedType): pass

class SearchModifier(object): pass
class Less(SearchModifier): pass
class More(SearchModifier): pass
class Between(SearchModifier): pass
