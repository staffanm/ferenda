# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function
import sys
import os
import logging

from rdflib import Graph

from ferenda import TextReader, TripleStore
from ferenda.elements import serialize
from ferenda import decorators
from ferenda import util
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
    # FIXME: manager.py should not strictly require these to be present
    class DummyStore(object):
        def __init__(self, path, **kwargs):
            pass
        def list_basefiles_for(self, action, basedir=None):
            return []
    downloaded_suffix = ".html"
    storage_policy = "file"
    documentstore_class = DummyStore


    
    # Don't document this -- just needed for ferenda.manager compatibility
    def get_default_options(self):
        return {}

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
    def dumpstore(self,format="turtle"):
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
    def mkpatch(self, alias, basefile, description):
        """Create a patch file from intermediate files. Before running this
        tool, you should hand-edit the intermediate file. The tool
        will first stash away the intermediate file, then re-run
        :py:meth:`~ferenda.DocumentRepository.parse` in order to get a
        new intermediate file. It will then calculate the diff between
        these two versions and save it as a patch file in it's proper
        place (as determined by ``config.patchdir``), where it will be
        picked up automatically by
        :py:meth:`~ferenda.DocumentRepository.patch_if_needed`.

        :param alias: Docrepo alias
        :type  alias: str
        :param basefile: The basefile for the document to patch
        :type  basefile: str

        .. note::

           This is currently broken.

        Example::

            ./ferenda-build.py devel mkpatch myrepo basefile1 "Removed sensitive personal information"

        """
        coding = 'utf-8' if sys.stdin.encoding == 'UTF-8' else 'iso-8859-1'
        myargs = [arg.decode(coding) for arg in sys.argv]

        # ask for description and place it alongside

        # copy the modified file to a safe place
        file_to_patch = myargs[1].replace("\\", "/")  # normalize
        tmpfile = mktemp()
        copy2(file_to_patch, tmpfile)

        # Run SFSParser._extractSFST() (and place the file in the correct location)
        # or DVParser.word_to_docbook()
        if "/sfs/intermediate/" in file_to_patch:
            source = "sfs"
            basefile = file_to_patch.split("/sfs/intermediate/")[1]
            import SFS
            p = SFS.SFSParser()
            sourcefile = file_to_patch.replace("/intermediate/", "/downloaded/sfst/").replace(".txt", ".html")
            print(("source %s, basefile %s, sourcefile %s" % (
                source, basefile, sourcefile)))
            plaintext = p._extractSFST([sourcefile])
            f = codecs.open(file_to_patch, "w", 'iso-8859-1')
            f.write(plaintext + "\n")
            f.close()
            print(("Wrote %s bytes to %s" % (len(plaintext), file_to_patch)))

        elif "/dv/intermediate/docbook/" in file_to_patch:
            source = "dv"
            basefile = file_to_patch.split("/dv/intermediate/docbook/")[1]
            import DV
            p = DV.DVParser()
            sourcefile = file_to_patch.replace(
                "/docbook/", "/word/").replace(".xml", ".doc")
            print(("source %r, basefile %r, sourcefile %r" % (
                source, basefile, sourcefile)))
            os.remove(file_to_patch)
            p.word_to_docbook(sourcefile, file_to_patch)

        elif "/dv/intermediate/ooxml/" in file_to_patch:
            source = "dv"
            basefile = file_to_patch.split("/dv/intermediate/ooxml/")[1]
            import DV
            p = DV.DVParser()
            sourcefile = file_to_patch.replace(
                "/ooxml/", "/word/").replace(".xml", ".docx")
            print(("source %r, basefile %r, sourcefile %r" % (
                source, basefile, sourcefile)))
            os.remove(file_to_patch)
            p.word_to_ooxml(sourcefile, file_to_patch)

        # calculate place in patch tree
        patchfile = "patches/%s/%s.patch" % (
            source, os.path.splitext(basefile)[0])
        util.ensure_dir(patchfile)

        # run diff on the original and the modified file, placing the patch right in the patch tree
        cmd = "diff -u %s %s > %s" % (file_to_patch, tmpfile, patchfile)
        print(("Running %r" % cmd))
        (ret, stdout, stderr) = util.runcmd(cmd)

        if os.stat(patchfile).st_size == 0:
            print("FAIL: Patchfile is empty")
            os.remove(patchfile)
        else:
            if sys.platform == "win32":
                os.system("unix2dos %s" % patchfile)
            print(("Created patch file %r" % patchfile))
            print("Please give a description of the patch")
            patchdesc = sys.stdin.readline().decode('cp850')
            fp = codecs.open(
                patchfile.replace(".patch", ".desc"), "w", 'utf-8')
            fp.write(patchdesc)
            fp.close()

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

        .. note::

           The ``functionname`` parameter currently has no effect
           (``ferenda.sources.tech.rfc.RFC.get_parser()`` is always
           used)

        """
        # fixme: do magic import() dance
        print("parsefunc %s (really ferenda.sources.tech.rfc.RFC.get_parser()), source %s)" % (functionname,source))
        import ferenda.sources.tech.rfc
        parser = ferenda.sources.tech.rfc.RFC.get_parser()
        parser.debug = True
        tr=TextReader(source)
        b = parser.parse(tr.getiterator(tr.readparagraph))
        # print("=========
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
            print("%s (%s): %s" % (row['identifier'], row['about']))

    @decorators.action
    def construct(self, template, uri, format="turtle"):
        sq = util.readfile(template) % {'uri': uri}
        ts  = TripleStore.connect(self.config.storetype,
                                  self.config.storelocation,
                                  self.config.storerepository)
        print("# Constructing the following from %s, repository %s, type %s" %
              (self.config.storelocation,
               self.config.storerepository,
               self.config.storetype))
        print("# ", "\n# ".join(sq.split("\n")))
        p = {}
        with util.logtime(print,
                          "# %(triples)s triples constructed in %(elapsed).3f",
                          p):
            res = ts.construct(sq)
            p['triples'] = len(res)
            print(res.serialize(format=format).decode('utf-8'))

    @decorators.action
    def select(self, template, uri, format="json"):
        sq = util.readfile(template) % {'uri': uri}
        ts  = TripleStore.connect(self.config.storetype,
                                  self.config.storelocation,
                                  self.config.storerepository)
        print(sq)
        print("="*70)
        p = {}
        with util.logtime(print,
                          "# %(triples)s triples constructed in %(elapsed).3f",
                          p):
            res = ts.select(sq, format=format)
            p['triples'] = len(res)
            print(res.serialize(format=format).decode('utf-8'))

            
    def download(self): pass
    def parse(self, basefile): pass
    def relate(self, basefile): pass
    def generate(self, basefile): pass
    def toc(self, otherrepos): pass
    def news(self, otherrepos): pass
    def status(self): pass
    def list_basefiles_for(self, command): return []
    @classmethod
    def setup(cls, action, config): pass
    @classmethod
    def teardown(cls, action, config): pass

    
