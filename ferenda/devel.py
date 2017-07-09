# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *
import builtins

from ast import literal_eval
from bz2 import BZ2File
from collections import OrderedDict
from difflib import unified_diff
from itertools import islice
from tempfile import mkstemp
from operator import attrgetter
import codecs
import inspect
import logging
import os
import random
import shutil
import sys

from rdflib import Graph, URIRef, RDF
from layeredconfig import LayeredConfig
from lxml import etree

from ferenda.compat import Mock
from ferenda import (TextReader, TripleStore, FulltextIndex, WSGIApp,
                     CompositeRepository, DocumentEntry, Transformer)
from ferenda.elements import serialize
from ferenda import decorators, util

class DummyStore(object):

    def __init__(self, path, **kwargs):
        pass  # pragma: no cover

    def list_basefiles_for(self, action, basedir=None):
        return []  # pragma: no cover


class Devel(object):

    """Collection of utility commands for developing docrepos.

    This module acts as a docrepo (and as such is easily callable from
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
        print = builtins.print
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
        print = builtins.print
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
#        log.warning('Log message at WARNING level')
#        log.info('Log message at INFO level')
#        log.debug('Log message at DEBUG level')
#        sub = logging.getLogger(__name__+'.sublogger')
#        sub.critical('Sublog message at CRITICAL level')
#        sub.error('Sublog message at ERROR level')
#        sub.warning('Sublog message at WARNING level')
#        sub.info('Sublog message at INFO level')
#        sub.debug('Sublog message at DEBUG level')

    @decorators.action
    def csvinventory(self, alias):
        """Create an inventory of documents, as a CSV file. 

        Only documents that have been parsed and yielded some minimum
        amount of RDF metadata will be included.

        :param alias: Docrepo alias
        :type  alias: str

        """
        predicates = ['basefile',
                      'subobjects',  # sections that have rdf:type
                      'rdf:type',
                      'dcterms:identifier',
                      'dcterms:title',
                      'dcterms:published',
                      'prov:wasGeneratedBy',
                      ]
        import csv
        # if six.PY2:
        #     delimiter = b';'
        #     out = sys.stdout
        # else:
        import codecs
        delimiter = ';'
        out = codecs.getwriter("latin-1")(sys.stdout.detach())
        out.errors = "replace"

        writer = csv.DictWriter(out, predicates, delimiter=delimiter)
        repo = self._repo_from_alias(alias)
        writer.writerow(dict([(p, p) for p in predicates]))
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
                            # if six.PY2:
                            #     fld = fld.encode("latin-1", errors="replace")
                            row[qname] = fld
                row['subobjects'] = len(list(g.subject_objects(RDF.type)))
                writer.writerow(row)

    def _repo_from_alias(self, alias, datadir=None, repoconfig=None):
        #  (FIXME: This uses several undocumented APIs)
        if repoconfig is None:
            mainconfig = self.config._parent
            assert mainconfig is not None, "Devel must be initialized with a full set of configuration"
            repoconfig = getattr(mainconfig, alias)
        from ferenda import manager
        repocls = manager._load_class(getattr(repoconfig, 'class'))
        repo = repocls()
        repo.config = getattr(mainconfig, alias)
        # work in all parameters from get_default_options
        for key, val in repocls.get_default_options().items():
            if key not in repo.config:
                LayeredConfig.set(repo.config, key, val, "defaults")
        if datadir is None:
            datadir = repo.config.datadir + os.sep + repo.alias
        repo.store.datadir = datadir
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
        from pudb import set_trace; set_trace()
        intermediatepath = repo.store.intermediate_path(basefile)
        if repo.config.compress == "bz2":
            intermediatepath += ".bz2"
        if os.path.exists(intermediatepath):
            stage = "intermediate"
            outfile = intermediatepath
        else:
            stage = "download"
            outfile = repo.store.downloaded_path(basefile)

        # 2.1 stash a copy
        fileno, stash = mkstemp()
        with os.fdopen(fileno, "wb") as fp:
            fp.write(util.readfile(outfile, mode="rb"))

        # 2.1 if intermediate: after stashing a copy of the
        # intermediate file, delete the original and run
        # parse(config.force=True) to regenerate the intermediate file
        if stage == "intermediate":
            repo.config.force = True
            util.robust_remove(intermediatepath)
            try:
                repo.parse(basefile)
            except:
                # maybe this throws an error (hopefully after creating
                # the intermediate file)? may be the reason for
                # patching in the first place?
                pass

        # 2.2 if only downloaded: stash a copy, run download_single(config.refresh=True)
        else:
            repo.config.refresh = True
            repo.download_single(basefile)

        # 3. calculate the diff using difflib.

        # Assume that intermediate files use the same encoding as
        # source files
        from pudb import set_trace; set_trace()
        if repo.config.compress == "bz2":
            opener = BZ2File
        else:
            opener = open
        encoding = repo.source_encoding

        with opener(outfile) as fp:
            outfile_lines = [l.decode(encoding) for l in fp.readlines()]
        with opener(stash) as fp:
            stash_lines = [l.decode(encoding) for l in fp.readlines()]
        difflines = list(unified_diff(outfile_lines,
                                      stash_lines,
                                      outfile,
                                      stash))
        os.unlink(stash)
        # 4. calculate place of patch using docrepo.store.
        patchstore = repo.documentstore_class(repo.config.patchdir +
                                              os.sep + repo.alias)
        patchpath = patchstore.path(basefile, "patches", ".patch")

        # 3.1 If comment is single-line, append it on the first hunks
        # @@-control line
        if description.count("\n") == 0:
            for idx, line in enumerate(difflines):
                if line.startswith("@@") and line.endswith("@@\n"):
                    difflines[idx] = difflines[idx].replace("@@\n",
                                                            "@@ " + description + "\n")
                    break
        else:
            # 4.2 if comment is not single-line, write the rest
            # in corresponding .desc file
            descpath = patchstore.path(basefile, "patches", ".desc")
            util.writefile(descpath, description)

        # 4.1 write patch
        patchcontent = "".join(difflines)
        if patchcontent:
            if repo.config.patchformat == "rot13":
                print("rot13:ing the patch at %s" % patchpath)
                patchcontent = codecs.encode(patchcontent, "rot13")
            # write the patch using the same encoding as the
            # downloaded/intermediate files
            util.writefile(patchpath, patchcontent, encoding=encoding)
            # print("Created patch %s" % patchpath)
            return patchpath
        else:
            print("WARNING: patch would be empty, not creating it")

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
        print = builtins.print
        modulename, classname, methodname = functionname.rsplit(".", 2)
        __import__(modulename)
        m = sys.modules[modulename]
        for name, cls in inspect.getmembers(m, inspect.isclass):
            if name == classname:
                break
        method = getattr(cls, methodname)
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
        print = builtins.print
        # from ferenda.sources.legal.se import Propositioner, Direktiv, SOU, Ds, JO, JK, ARN,DV
        # from lagen.nu import MyndFskr, LNMediaWiki, LNKeyword
        # repos = [Propositioner(), Direktiv(), SOU(), Ds(), JO(), JK(), ARN(), DV(), LNKeyword(), MyndFskr(), LNMediaWiki()]
        repos = []
        index = FulltextIndex.connect(self.config.indextype,
                                      self.config.indexlocation, repos)
        rows, pager = index.query(querystring)
        for row in rows:
            print("%s (%s): %s" % (row['label'], row['uri'], row['text']))

    @decorators.action
    def construct(self, template, uri, format="turtle"):
        """Run the specified SPARQL CONSTRUCT query."""
        print = builtins.print
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
        """Run the specified SPARQL SELECT query."""
        sq = util.readfile(template) % {'uri': uri}
        ts = TripleStore.connect(self.config.storetype,
                                 self.config.storelocation,
                                 self.config.storerepository)
        print = builtins.print
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

    @decorators.action
    def destroyindex(self):
        """Clear all data in the fulltext search index."""
        f = FulltextIndex.connect(self.config.indextype,
                                  self.config.indexlocation,
                                  [])
        f.destroy()
        print("%s index at %s destroyed" % (self.config.indextype,
                                            self.config.indexlocation))

    @decorators.action
    def clearstore(self):
        """Clear all data in the current triplestore."""
        store = TripleStore.connect(self.config.storetype,
                                    self.config.storelocation,
                                    self.config.storerepository)
        triplecount = store.triple_count()
        store.clear()
        print("%s triplestore at %s %s cleared (was %s triples, now %s)" %
              (self.config.storetype, self.config.storelocation,
               self.config.storerepository, triplecount, store.triple_count()))

    @decorators.action
    def wsgi(self, path="/"):
        """Runs WSGI calls in-process."""
        globalconfig = self.config._parent
        from ferenda import manager
        classnames = [
            getattr(
                repoconfig,
                'class') for repoconfig in globalconfig._subsections.values() if hasattr(
                repoconfig,
                'class')]
        repos = [
            manager._instantiate_class(
                manager._load_class(x),
                globalconfig) for x in classnames if x != 'ferenda.Devel']
        url = globalconfig.develurl if 'develurl' in globalconfig else globalconfig.url
        app = WSGIApp(repos, manager._find_config_file(), url=url)
        DEFAULT_HTTP_ACCEPT = 'text/xml, application/xml, application/xhtml+xml, text/html;q=0.9, text/plain;q=0.8, image/png,*/*;q=0.5'
        if "?" in path:
            pathinfo, querystring = path.split("?", 1)
        else:
            pathinfo, querystring = path, ""
        environ = {'HTTP_ACCEPT': DEFAULT_HTTP_ACCEPT,
                   'PATH_INFO':   pathinfo,
                   'SERVER_NAME': 'localhost',
                   'SERVER_PORT': '8000',
                   'QUERY_STRING': querystring,
                   'wsgi.url_scheme': 'http'
                   }

        start_response = Mock()
        for chunk in app(environ, start_response):
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8")
            sys.stdout.write(chunk)

    @decorators.action
    def samplerepo(self, alias, sourcedir, sourcerepo=None, destrepo=None, samplesize=None):
        """Copy a random selection of documents from an external docrepo to the current datadir.""" 
        if not samplesize:
            if 'samplesize' in self.config:
                samplesize = int(self.config.samplesize)
            else:
                samplesize = 10
        if sourcerepo is None:
            sourcerepo = self._repo_from_alias(alias, sourcedir)
        if destrepo is None:
            destrepo = self._repo_from_alias(alias)
        randomsample = True
        if randomsample:
            basefiles = list(sourcerepo.store.list_basefiles_for("parse"))
            samplesize = min([len(basefiles), samplesize])
            basefiles = random.sample(basefiles, samplesize)
        else:
            basefiles = islice(sourcerepo.store.list_basefiles_for("parse"),
                               0, samplesize)
        for basefile in basefiles:
            if isinstance(sourcerepo, CompositeRepository):
                sourcerepo = self._repo_from_alias(alias)
                for cls in sourcerepo.subrepos:
                    subsourcerepo = sourcerepo.get_instance(cls)
                    subsdestrepo = destrepo.get_instance(cls)
                    try:
                        self._samplebasefile(sourcerepo, destrepo, basefile)
                        break  # everything OK, no need to copy more
                    except IOError: # or whatever could happen
                        pass  # try the next one or bail
                else:
                    print("None of the subrepos had basefile %s" % basefile)
            else:
                self._samplebasefile(sourcerepo, destrepo, basefile)

    @decorators.action
    def copyrepos(self, sourcedir, basefilelist):
        """Copy some specified documents to the current datadir.

        The documents are specified in BASEFILELIST, and copied from
        the external directory SOURCEDIR.

        To be used with the output of analyze-error-log.py, eg
        $ ../tools/analyze-error-log.py data/logs/20160522-120204.log --listerrors > errors.txt
        $ ./ferenda-build.py devel copyrepos /path/to/big/external/datadir errors.txt
        """
        with open(basefilelist) as fp:
            basefilelist = []
            for line in fp:
                if line.startswith("("):
                    basefilelist.append(literal_eval(line))
                else:
                    # remove comments
                    line = line.rsplit("#", 1)[0].strip()
                    if not line:  # remove blank lines
                        continue
                    basefilelist.append(line.strip().split(" ", 1))
        destrepos = {}
        sourcerepos = {}
        for (alias, basefile) in basefilelist:
            if alias not in destrepos:
                try:
                    destrepos[alias] = self._repo_from_alias(alias)
                    sourcerepos[alias] = self._repo_from_alias(alias, sourcedir + os.sep + alias)
                except AttributeError: # means the repo alias was wrong
                    continue
            destrepo = destrepos[alias]
            sourcerepo = sourcerepos[alias]
            if isinstance(sourcerepo, CompositeRepository):
                for cls in sourcerepo.subrepos:
                    subsourcerepo = sourcerepo.get_instance(cls)
                    subsourcerepo.store.datadir = (sourcedir + os.sep +
                                                   subsourcerepo.alias)
                    if os.path.exists(subsourcerepo.store.downloaded_path(basefile)):
                        subdestrepo = destrepo.get_instance(cls)
                        self._samplebasefile(subsourcerepo, subdestrepo, basefile)
                        break
            else:
                self._samplebasefile(sourcerepo, destrepo, basefile)


    def _samplebasefile(self, sourcerepo, destrepo, basefile):
        print("  %s: copying %s" % (sourcerepo.alias, basefile))
        src = sourcerepo.store.downloaded_path(basefile)
        dst = destrepo.store.downloaded_path(basefile)
        if os.path.splitext(src)[1] != os.path.splitext(dst)[1]:
            # FIX for DV.py (and possibly other multi-suffix
            # repos) this will yield an incorrect suffix (eg ".zip")
            dst = os.path.splitext(dst)[0] + os.path.splitext(src)[1]
        isrc = sourcerepo.store.intermediate_path(basefile)
        if sourcerepo.config.compress == "bz2":
            isrc += ".bz2"
        idst = destrepo.store.intermediate_path(basefile)
        if destrepo.config.compress == "bz2":
            idst += ".bz2"
        copy = shutil.copy2
        if sourcerepo.store.storage_policy == "dir":
            src = os.path.dirname(src)
            dst = os.path.dirname(dst)
            isrc = os.path.dirname(isrc)
            idst = os.path.dirname(idst)
            if os.path.exists(dst):
                shutil.rmtree(dst)
            if os.path.exists(idst):
                shutil.rmtree(idst)
            copy = shutil.copytree
        util.ensure_dir(dst)
        try:
            copy(src, dst)
            if os.path.exists(isrc):
                util.ensure_dir(idst)
                copy(isrc, idst)
        except FileNotFoundError as e:
            print("WARNING: %s" % e)

        # NOTE: For SFS (and only SFS), there exists separate
        # register files under
        # data/sfs/register/1998/204.html. Maybe we should use
        # storage_policy="dir" and handle those things as
        # attachments?
        if os.path.exists(sourcerepo.store.path(basefile, "register", ".html")):
            dst = destrepo.store.path(basefile, "register", ".html")
            util.ensure_dir(dst)
            shutil.copy2(sourcerepo.store.path(basefile, "register", ".html"),
                         dst)
        # also copy the docentry json file
        if os.path.exists(sourcerepo.store.documententry_path(basefile)):
            util.ensure_dir(destrepo.store.documententry_path(basefile))
            shutil.copy2(sourcerepo.store.documententry_path(basefile),
                         destrepo.store.documententry_path(basefile))


    @decorators.action
    def samplerepos(self, sourcedir):
        """Copy a random selection of external documents to the current datadir - for all docrepos.""" 
        # from ferenda.sources.general import Static
        from lagen.nu import Static
        if 'samplesize' in self.config:
            samplesize = int(self.config.samplesize)
        else:
            samplesize = 10
        classes = set([Static,]) # blacklist static because of how it
                              # hardcodes .store.staticdir -- leads to
                              # copy attempts with identical src and
                              # dst
        for alias in self.config._parent._subsections:
            if alias == self.alias:  # ie "devel"
                continue
            destrepo = self._repo_from_alias(alias)
            if destrepo.__class__ in classes:
                print("...skipping class %r" % destrepo.__class__)
                continue
            if ('parse' in self.config._parent._subsections[alias] and
                self.config._parent._subsections[alias].parse in
                (False, 'False')):
                print("...skipping class %r (parse=False)" % destrepo.__class__)
                continue
            if isinstance(destrepo, CompositeRepository):
                sourcerepo = self._repo_from_alias(alias)
                for cls in destrepo.subrepos:
                    subdestrepo = destrepo.get_instance(cls)
                    if isinstance(subdestrepo, CompositeRepository):
                        print("...giving up on nested compositerepository")
                        continue
                    if subdestrepo.__class__ in classes:
                        print("...skipping class %r" % subdestrepo.__class__)
                        continue
                    classes.add(subdestrepo.__class__)
                    subsourcerepo = sourcerepo.get_instance(cls)
                    assert id(subdestrepo) != id(subsourcerepo)
                    subsourcerepo.store.datadir = (sourcedir + os.sep +
                                                   subsourcerepo.alias)
                    alias = subsourcerepo.alias
                    aliasdir = subsourcerepo.store.datadir
                    print("%s/%s: Copying docs from  %s" %
                          (sourcerepo.alias, alias, aliasdir))
                    self.samplerepo(alias, aliasdir, subsourcerepo,
                                    subdestrepo,
                                    samplesize=round(samplesize/
                                                     len(destrepo.subrepos)))
            else:
                classes.add(destrepo.__class__)
                aliasdir = sourcedir+os.sep+alias
                print("%s: Copying docs from %s" % (alias, aliasdir))
                self.samplerepo(alias, aliasdir)

    @decorators.action
    def statusreport(self, alias=None):
        """Generate report on which files parse()d OK, with errors, or failed.

        Creates a servable HTML file containing information about how
        the last parse went for each doc in the given repo (or all
        repos if none given).

        """
        log = logging.getLogger("devel")
        if alias:
            repos = [self._repo_from_alias(alias)]
        else:
            repos = [self._repo_from_alias(alias) for alias in self.config._parent._subsections]
        root = etree.fromstring("<status></status>")

        for repo in sorted(repos, key=attrgetter("alias")):
            # Find out if this repo is outwardly-responsible for
            # parsing -- we check against "False" as well since
            # LayeredConfig may lack typing info for this setting and
            # so interprets the value in the .ini file as a str, not a
            # bool...
            if 'parse' in repo.config and repo.config.parse in (False, "False"):
                continue

            # listing basefiles for the action "news" gives us
            # everyting that has a docentry file.
            basefiles = list(repo.store.list_basefiles_for("news"))
            if not basefiles:
                continue
            repo_el = etree.SubElement(root, "repo", {"alias": repo.alias})
            successcnt = warncnt = failcnt = removecnt = errcnt = 0
            for basefile in basefiles:
                # sys.stdout.write(".")
                # print("%s/%s" % (repo.alias, basefile))
                entrypath = repo.store.documententry_path(basefile)
                if not os.path.exists(entrypath):
                    log.warning("%s/%s: file %s doesn't exist" % (repo.alias, basefile, entrypath))
                    errcnt += 1
                    continue
                elif os.path.getsize(entrypath) == 0:
                    log.warning("%s/%s: file %s is 0 bytes" % (repo.alias, basefile, entrypath))
                    errcnt += 1
                    continue
                try:
                    entry = DocumentEntry(entrypath)
                except ValueError as e:
                    log.error("%s/%s: %s %s" % (repo.alias, basefile, e.__class__.__name__, e))
                    errcnt += 1
                    continue
                if not entry.status:  # an empty dict
                    log.warning("%s/%s: file %s has no status sub-dict" % (repo.alias, basefile, entrypath))
                    errcnt += 1
                    continue
                if "parse" in entry.status and "success" in entry.status["parse"] and entry.status["parse"]["success"] == "removed":
                    log.debug("%s/%s: document was removed in parse" % (repo.alias, basefile))
                    continue
                doc_el = etree.SubElement(repo_el, "basefile",
                                          {"id": basefile})
                # FIXME: we should sort the entries in a reasonable way, eg
                # "download"/"parse"/"relate"/"generate"/any custom
                # action, probably through a custom key func
                for action in sorted(entry.status):
                    status = entry.status[action]
                    if not status:
                        log.warning("%s/%s: file %s has no status data for action %s" % (repo.alias, basefile, entrypath, action))
                        continue
                    if "success" in status and status["success"] == "removed":
                        # this special truthy value indicates that
                        # everything went as OK as it could, but the
                        # actual document doesn't exist (anymore) so we
                        # don't feature it in our overview
                        removecnt += 1
                        continue
                    action_el = etree.SubElement(doc_el, "action",
                                                 {"id": action,
                                                  "success": str(status["success"]),
                                                  "duration": str(status["duration"]),
                                                  "date": status["date"]})
                    if status["success"]:
                        successcnt += 1
                    else:
                        failcnt += 1
                    if "warnings" in status:
                        warncnt += 1

                    # add additional (optional) text data if present
                    for optional in ("warnings", "error", "traceback"):
                        if optional in status:
                            opt_el = etree.SubElement(action_el, optional)
                            opt_el.text = status[optional]
            log.info("%s: %s processed, %s ok (%s w/ warnings), %s failed, %s removed. %s corrupted entries." % (repo.alias, len(basefiles), successcnt, warncnt, failcnt, removecnt, errcnt))
        conffile = os.path.abspath(
            os.sep.join([self.config.datadir, 'rsrc', 'resources.xml']))
        resourceloader = [x.resourceloader for x in repos if hasattr(x, 'resourceloader')][0]
        transformer = Transformer('XSLT', "xsl/statusreport.xsl", "xsl",
                                  resourceloader=resourceloader,
                                  config=conffile)
        xhtmltree = transformer.transform(root, depth=1)
        outfile = os.sep.join([self.config.datadir, 'status', 'status.html'])
        util.ensure_dir(outfile)
        with open(outfile, "wb") as fp:
            fp.write(etree.tostring(xhtmltree, encoding="utf-8", pretty_print=True))
        log.info("Wrote %s" % outfile)

    # FIXME: These are dummy implementations of methods and class
    # variables that manager.py expects all docrepos to have. We don't
    # want to have coverage counting these as missing lines, hence the
    # pragma: no cover comments.
    def __init__(self, config=None, **kwargs):
        self.store = DummyStore(None)
        self.config = config

    documentstore_class = DummyStore
    downloaded_suffix = ".html"
    storage_policy = "file"

    @classmethod
    def get_default_options(cls):
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
    def setup(cls, action, config, *args, **kwargs):
        pass  # pragma: no cover

    @classmethod
    def teardown(cls, action, config, *args, **kwargs):
        pass  # pragma: no cover
