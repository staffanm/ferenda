# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
import sys
if sys.version_info[:2] == (3,2): # remove when py32 support ends
    import uprefix
    uprefix.register_hook()
    from future.builtins import *
    uprefix.unregister_hook()
else:
    from future.builtins import *

from difflib import unified_diff
from tempfile import mkstemp
import inspect
import os

from rdflib import Graph, URIRef, RDF

from ferenda import TextReader, TripleStore, FulltextIndex
from ferenda.elements import serialize
from ferenda import decorators, util

class DummyStore(object):

    def __init__(self, path, **kwargs):
        pass  # pragma: no cover

    def list_basefiles_for(self, action, basedir=None):
        return []  # pragma: no cover


class Devel(object):

    """This module acts as a docrepo (and as such is easily callable from
    ``ferenda-manager.py``), but instead of ``download``, ``parse``,
    ``generate`` et al, contains various tool commands that is useful
    for developing and debugging your own docrepo classes.

    Use it by first enabling it::

        ./ferenda-build.py ferenda.Devel enable

    And then run individual tools like::

        ./ferenda-build.py devel dumprdf path/to/xhtml/rdfa.xhtml

    """

    alias = "devel"

    @decorators.action
    def dumprdf(self, filename, format="turtle"):
        """Extract all RDF data from a parsed file and dump it to stdout.

        :param filename: Full path of the parsed XHTML+RDFa file.
        :type filename: str
        :param format: The serialization format for RDF data (same as for :py:meth:`rdflib.graph.Graph.serialize`)
        :type format: str

        Example::

            ./ferenda-build.py devel dumprdf path/to/xhtml/rdfa.xhtml nt


        """
        g = Graph()
        g.parse(data=util.readfile(filename), format="rdfa")
        # At least the turtle serializer creates UTF-8 data. Fix this!
        print((g.serialize(None, format=format).decode("utf-8")))

    @decorators.action
    def dumpstore(self, format="turtle"):
        """Extract all RDF data from the system triplestore and dump
        it to stdout using the specified format.

        :param format: The serialization format for RDF data (same as
                       for :py:meth:`ferenda.TripleStore.get_serialized`).
        :type format: str

        Example::

            ./ferenda-build.py devel dumpstore nt > alltriples.nt
        """
        # print("Creating store of type %s, location %s, repository %s" %
        #       (self.config.storetype, self.config.storelocation, self.config.storerepository))
        store = TripleStore.connect(self.config.storetype,
                                    self.config.storelocation,
                                    self.config.storerepository)
        print(store.get_serialized(format=format).decode('utf-8'))

#    Not really useful for anything than finding bugs in ferenda itself
#
#    def testlog(self):
#        """Logs a series of messages at various levels, to test that
#        your client code logging configuration behaves as
#        expectedly."""
#        log = logging.getLogger(__name__)
#        log.critical('Log message at CRITICAL level')
#        log.error('Log message at ERROR level')
#        log.warn('Log message at WARN level')
#        log.info('Log message at INFO level')
#        log.debug('Log message at DEBUG level')
#        sub = logging.getLogger(__name__+'.sublogger')
#        sub.critical('Sublog message at CRITICAL level')
#        sub.error('Sublog message at ERROR level')
#        sub.warn('Sublog message at WARN level')
#        sub.info('Sublog message at INFO level')
#        sub.debug('Sublog message at DEBUG level')

    @decorators.action
    def csvinventory(self, alias):
        """Create an inventory of documents, as a CSV file. Only documents
        that have been parsed and yielded some minimum amount of RDF
        metadata will be included.

        :param alias: Docrepo alias
        :type  alias: str
        """
        predicates = ['basefile',
                      'subobjects', # sections that have rdf:type
                      'rdf:type',
                      'dct:identifier',
                      'dct:title',
                      'dct:published',
                      'prov:wasGeneratedBy',
                      ]
        import csv
        if sys.version_info[0] < 3:
            delimiter = b';'
            out = sys.stdout
        else:
            import codecs
            delimiter = ';'
            out = codecs.getwriter("latin-1")(sys.stdout.detach())
            out.errors = "replace"
        
        writer = csv.DictWriter(out, predicates, delimiter=delimiter)
        repo = self._repo_from_alias(alias)
        writer.writerow(dict([(p,p) for p in predicates]))
        for basefile in repo.store.list_basefiles_for("relate"):
            baseuri = URIRef(repo.canonical_uri(basefile))
            with repo.store.open_distilled(basefile) as fp:
                row = {'basefile': basefile}
                g = Graph().parse(fp, format="xml")
                for (p, o) in g.predicate_objects(baseuri):
                    qname = g.qname(p)
                    if qname in predicates:
                        if isinstance(o, URIRef):
                            row[qname] = g.qname(o)
                        else:
                            # it seems py2 CSV modue expects latin-1
                            # encoded bytestrings (for non-ascii
                            # values), while py3 CSV expects unicode
                            # (sensibly)
                            fld = str(o)
                            if sys.version_info[0] < 3:
                                fld = fld.encode("latin-1", errors="replace")
                            row[qname] = fld
                row['subobjects'] = len(list(g.subject_objects(RDF.type)))
                writer.writerow(row)

    def _repo_from_alias(self, alias):
        #  (FIXME: This uses several undocumented APIs)
        mainconfig = self.config._parent
        assert mainconfig is not None, "Devel must be initialized with a full set of configuration"
        repoconfig = getattr(mainconfig, alias)
        from ferenda import manager
        repocls = manager._load_class(getattr(repoconfig, 'class'))
        repo = repocls()
        repo.config = getattr(mainconfig, alias)
        repo.store = repo.documentstore_class(
            repo.config.datadir + os.sep + repo.alias,
            downloaded_suffix=repo.downloaded_suffix,
            storage_policy=repo.storage_policy)
        return repo
        
    @decorators.action
    def mkpatch(self, alias, basefile, description):
        """Create a patch file from downloaded or intermediate files. Before
        running this tool, you should hand-edit the intermediate
        file. If your docrepo doesn't use intermediate files, you
        should hand-edit the downloaded file instead. The tool will
        first stash away the intermediate (or downloaded) file, then
        re-run :py:meth:`~ferenda.DocumentRepository.parse` (or
        :py:meth:`~ferenda.DocumentRepository.download_single`) in
        order to get a new intermediate (or downloaded) file. It will
        then calculate the diff between these two versions and save it
        as a patch file in it's proper place (as determined by
        ``config.patchdir``), where it will be picked up automatically
        by :py:meth:`~ferenda.DocumentRepository.patch_if_needed`.

        :param alias: Docrepo alias
        :type  alias: str
        :param basefile: The basefile for the document to patch
        :type  basefile: str

        Example::

            ./ferenda-build.py devel mkpatch myrepo basefile1 "Removed sensitive personal information"

        """
        # 1. initialize the docrepo indicated by "alias"
        repo = self._repo_from_alias(alias)
        
        # 2. find out if there is an intermediate file or downloaded
        # file for basefile
        if os.path.exists(repo.store.intermediate_path(basefile)):
            stage = "intermediate"
            outfile = repo.store.intermediate_path(basefile)
        else:
            stage = "download"
            outfile = repo.store.downloaded_path(basefile)

        # 2.1 stash a copy
        fileno, stash = mkstemp()
        with os.fdopen(fileno, "wb") as fp:
            fp.write(util.readfile(outfile, mode="rb"))
        
        # 2.1 if intermediate: stash a copy, run parse(config.force=True)
        if stage == "intermediate":
            repo.config.force = True
            repo.parse(basefile)
        # 2.2 if only downloaded: stash a copy, run download_single(config.refresh=True)
        else:
            repo.config.refresh = True
            repo.download_single(basefile)
            
        # 3. calculate the diff using difflib.
        outfile_lines = open(outfile).readlines()
        stash_lines = open(stash).readlines()
        difflines = list(unified_diff(outfile_lines,
                                      stash_lines,
                                      outfile,
                                      stash))
        # 4. calculate place of patch using docrepo.store.
        patchstore = repo.documentstore_class(repo.config.patchdir +
                                              os.sep + repo.alias)
        patchpath = patchstore.path(basefile, "patches", ".patch")

        # 3.1 If comment is single-line, append it on the first hunks
        # @@-control line
        if description.count("\n") == 0:
            for idx,line in enumerate(difflines):
                if line.startswith("@@") and line.endswith("@@\n"):
                    difflines[idx] = difflines[idx].replace("@@\n",
                                                            "@@ "+description+"\n")
                    break
        else:
            # 4.2 if comment is not single-line, write the rest
            # in corresponding .desc file
            descpath = patchstore.path(basefile, "patches", ".desc")
            util.writefile(descpath, description)
            
        # 4.1 write patch
        util.writefile(patchpath, "".join(difflines))
        return patchpath

    @decorators.action
    def parsestring(self, string, citationpattern, uriformatter=None):
        """Parse a string using a named citationpattern and print
        parse tree and optionally formatted uri(s) on stdout.

        :param string: The text to parse
        :type  string: str
        :param citationpattern: The fully qualified name of a citationpattern
        :type  citationpattern: str
        :param uriformatter: The fully qualified name of a uriformatter
        :type  uriformatter: str
        
        .. note::

           This is not implemented yet

        Example::

            ./ferenda-build.py devel parsestring \\
                "According to direktiv 2007/42/EU, ..." \\
                ferenda.citationpatterns.eulaw
        
        """
        raise NotImplementedError

    @decorators.action
    def fsmparse(self, functionname, source):
        """Parse a list of text chunks using a named fsm parser and
        output the parse tree and final result to stdout.

        :param functionname: A function that returns a configured
                             :py:class:`~ferenda.FSMParser`
        :type  functionname: str
        :param source:       A file containing the text chunks, separated
                             by double newlines
        :type source:        str

        """
        modulename, classname, methodname = functionname.rsplit(".", 2)
        __import__(modulename)
        m = sys.modules[modulename]
        for name, cls in inspect.getmembers(m, inspect.isclass):
            if name == classname:
                break
        method = getattr(cls,methodname)
        parser = method()
        parser.debug = True
        tr = TextReader(source)
        b = parser.parse(tr.getiterator(tr.readparagraph))
        print(serialize(b))

    @decorators.action
    def queryindex(self, querystring):
        """Query the system fulltext index and return the IDs/URIs for matching documents.

        :param querystring: The query
        :type querystring: str
        """
        index = FulltextIndex.connect(self.config.indextype,
                                      self.config.indexlocation)
        rows = index.query(querystring)
        for row in rows:
            print("%s (%s): %s" % (row['identifier'], row['about'], row['text']))

    @decorators.action
    def construct(self, template, uri, format="turtle"):
        sq = util.readfile(template) % {'uri': uri}
        ts = TripleStore.connect(self.config.storetype,
                                 self.config.storelocation,
                                 self.config.storerepository)
        print("# Constructing the following from %s, repository %s, type %s" %
              (self.config.storelocation,
               self.config.storerepository,
               self.config.storetype))
        print("".join(["# %s\n" % x for x in sq.split("\n")]))
        p = {}
        with util.logtime(print,
                          "# %(triples)s triples constructed in %(elapsed).3fs",
                          p):
            res = ts.construct(sq)
            p['triples'] = len(res)
            print(res.serialize(format=format).decode('utf-8'))

    @decorators.action
    def select(self, template, uri, format="json"):
        sq = util.readfile(template) % {'uri': uri}
        ts = TripleStore.connect(self.config.storetype,
                                 self.config.storelocation,
                                 self.config.storerepository)

        print("# Constructing the following from %s, repository %s, type %s" %
              (self.config.storelocation,
               self.config.storerepository,
               self.config.storetype))
        print("".join(["# %s\n" % x for x in sq.split("\n")]))
        p = {}
        with util.logtime(print,
                          "# Selected in %(elapsed).3fs",
                          p):
            res = ts.select(sq, format=format)
            # res should be a unicode string, not an encoded bytestring
            # print(res)

            # NO! res must be a bytestring, select should return
            # whatever is the appropriately encoded version for the
            # given format.
            
            print(res.decode('utf-8'))



    # FIXME: These are dummy implementations of methods and class
    # variables that manager.py expects all docrepos to have. We don't
    # want to have coverage counting these as missing lines, hence the
    # pragma: no cover comments.

    def __init__(self, **kwargs):
        self.store = DummyStore(None)
    
    documentstore_class = DummyStore
    downloaded_suffix = ".html"
    storage_policy = "file"

    def get_default_options(self):
        return {}  # pragma: no cover

    def download(self):
        pass  # pragma: no cover

    def parse(self, basefile):
        pass  # pragma: no cover

    def relate(self, basefile):
        pass  # pragma: no cover

    def generate(self, basefile):
        pass  # pragma: no cover

    def toc(self, otherrepos):
        pass  # pragma: no cover

    def news(self, otherrepos):
        pass  # pragma: no cover

    def status(self):
        pass  # pragma: no cover

    @classmethod
    def setup(cls, action, config):
        pass  # pragma: no cover

    @classmethod
    def teardown(cls, action, config):
        pass  # pragma: no cover
