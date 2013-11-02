# -*- coding: utf-8 -*-
"""Most of these decorators are intended to handle various aspects of
a complete :py:meth:`~ferenda.DocumentRepository.parse`
implementation. Normally you should only use the
:py:func:`~ferenda.decorators.managedparsing` decorator (if you even
override the basic implementation). If you create separate actions
aside from the standards (``download``, ``parse``, ``generate`` et
al), you should also use :py:func:`~ferenda.decorators.action` so that
manage.py will be able to call it.
"""
from __future__ import unicode_literals
from datetime import datetime
import codecs
import functools
import itertools
import os
import time

import six
from rdflib import Graph, URIRef

from ferenda import util
from ferenda import LayeredConfig
from ferenda.errors import DocumentRemovedError, ParseError


def timed(f):
    """Automatically log a statement of how long the function call takes"""
    @functools.wraps(f)
    def wrapper(self, doc):
        start = time.time()
        ret = f(self, doc)
        # FIXME: We shouldn't log this if we don't actually do any
        # work. The easiest way is to make sure parseifneeded wraps
        # timed, not the other way round.
        self.log.info('%s: OK (%.3f sec)', doc.basefile, time.time() - start)
        return ret
    return wrapper


def recordlastdownload(f):
    """Automatically stores current time in ``self.config.lastdownloaded``
    """
    @functools.wraps(f)
    def wrapper(self, *args, **kwargs):
        ret = f(self, *args, **kwargs)
        self.config.lastdownload = datetime.now()
        LayeredConfig.write(self.config)
        return ret
    return wrapper


def parseifneeded(f):
    """Makes sure the parse function is only called if needed, i.e. if
    the outfile is nonexistent or older than the infile(s), or if the
    user has specified in the config file or on the command line that
    it should be re-generated."""
    @functools.wraps(f)
    def wrapper(self, doc):
        # note: We hardcode the use of .downloaded_path, .parsed_path
        # and the 'parseforce' config option, which means that this
        # decorator can only be used sensibly with the .parse()
        # function.
        infile = self.store.downloaded_path(doc.basefile)
        outfile = self.store.parsed_path(doc.basefile)
        force = (self.config.force is True or
                 self.config.parseforce is True)
        if not force and util.outfile_is_newer([infile], outfile):
            self.log.debug("%s: Skipped", doc.basefile)
            return True  # Signals that everything is OK
        else:
            self.log.debug("%s: Starting", doc.basefile)
            return f(self, doc)
    return wrapper


def render(f):
    """Handles the serialization of the :py:class:`~ferenda.Document`
    object to XHTML+RDFa and RDF/XML files. Must be used in
    conjunction with :py:func:`~ferenda.decorators.makedocument`.

    """
    # NOTE: The actual rendering is two lines of code. The bulk of
    # this function validates that the XHTML+RDFa file that we end up
    # with contains the exact same triples as is present in the doc
    # object (including both the doc.meta Graph and any other Graph
    # that might be present on any doc.body object)
    
    def iterate_graphs(node):
        res = []
        if hasattr(node, 'meta') and node.meta is not None:
            res.append(node.meta)
        for subnode in node:
            if not isinstance(subnode, six.string_types):
                res.extend(iterate_graphs(subnode))
        return res

    @functools.wraps(f)
    def wrapper(self, doc):
        ret = f(self, doc)
        updated = self.render_xhtml(doc, self.store.parsed_path(doc.basefile))
        if updated:
            self.log.debug("%s: Created %s" % (doc.basefile, self.store.parsed_path(doc.basefile)))

        # css file + background images + png renderings of text
        self.create_external_resources(doc)

        # Validate that all triples specified in doc.meta and any
        # .meta property on any body object is present in the
        # XHTML+RDFa file.
        distilled_graph = Graph()

        with codecs.open(self.store.parsed_path(doc.basefile),
                         encoding="utf-8") as fp:  # unicode
            distilled_graph.parse(data=fp.read(), format="rdfa",
                                  publicID=doc.uri)
        # The act of parsing from RDFa binds a lot of namespaces
        # in the graph in an unneccesary manner. Particularly it
        # binds both 'dc' and 'dcterms' to
        # 'http://purl.org/dc/terms/', which makes serialization
        # less than predictable. Blow these prefixes away.
        distilled_graph.bind("dc", URIRef("http://purl.org/dc/elements/1.1/"))
        distilled_graph.bind(
            "dcterms",
            URIRef("http://example.org/this-prefix-should-not-be-used"))

        util.ensure_dir(self.store.distilled_path(doc.basefile))
        with open(self.store.distilled_path(doc.basefile),
                  "wb") as distilled_file:
            # print("============distilled===============")
            # print(distilled_graph.serialize(format="turtle").decode('utf-8'))
            distilled_graph.serialize(distilled_file, format="pretty-xml")
        self.log.debug(
            '%s: %s triples extracted to %s', doc.basefile,
            len(distilled_graph), self.store.distilled_path(doc.basefile))

        for g in iterate_graphs(doc.body):
            doc.meta += g

        for triple in distilled_graph:
            # len_before = len(doc.meta)
            doc.meta.remove(triple)
            # len_after = len(doc.meta)

        if doc.meta:
            self.log.warning("%s: %d triple(s) from the original metadata was "
                             "not found in the serialized XHTML file:\n%s",
                             doc.basefile, len(doc.meta),
                             doc.meta.serialize(format="nt").decode('utf-8').strip())
        return ret
    return wrapper


def handleerror(f):
    """Make sure any errors in :py:meth:`ferenda.DocumentRepository.parse`
    are handled appropriately and do not stop the parsing of all documents.
    """
    @functools.wraps(f)
    def wrapper(self, doc):
        try:
            return f(self, doc)
        except DocumentRemovedError as e:
            self.log.info(
                "%s: Document has been removed (%s)", doc.basefile, e)
            util.robust_remove(self.parsed_path(doc.basefile))
            return False
        except KeyboardInterrupt:
            raise
        except ParseError as e:
            self.log.error("%s: ParseError %s", doc.basefile, e)
            if (hasattr(self.config, 'fatalexceptions') and
                    self.config.fatalexceptions):
                raise
            else:
                return False
        except:
            self.log.exception("parse of %s failed", doc.basefile)
            if (hasattr(self.config, 'fatalexceptions') and
                    self.config.fatalexceptions):
                raise
            else:
                return False
    return wrapper


def makedocument(f):
    """Changes the signature of the parse method to expect a Document
    object instead of a basefile string, and creates the object."""
    @functools.wraps(f)
    def wrapper(self, basefile):
        doc = self.make_document(basefile)
        return f(self, doc)
    return wrapper


def managedparsing(f):
    """Use all standard decorators for parse() in the correct order
    (:py:func:`~ferenda.decorators.makedocument`, :py:func:`~ferenda.decorators.parseifneeded`, :py:func:`~ferenda.decorators.timed`, :py:func:`~ferenda.decorators.render`)"""
    return makedocument(
        parseifneeded(
            # handleerror( # is this really a good idea?
            timed(
                render(f))))


def action(f):
    """Decorator that marks a class or instance method as runnable by
    :py:func:`ferenda.manager.run`
    """
    f.runnable = True
    return f


def downloadmax(f):
    """Makes any generator respect the ``downloadmax`` config parameter.

    """
    @functools.wraps(f)
    def wrapper(self, params):
        downloadmax = None
        if 'FERENDA_DOWNLOADMAX' in os.environ:
            downloadmax = int(os.environ['FERENDA_DOWNLOADMAX'])
        elif self.config.downloadmax:
            downloadmax = self.config.downloadmax

        if downloadmax:
            self.log.info("Downloading max %d documents" %
                          (downloadmax))
            generator = itertools.islice(f(self, params),
                                         downloadmax)
        else:
            self.log.debug("Downloading all the docs")
            generator = f(self, params)
        for value in generator:
            yield value
    return wrapper
