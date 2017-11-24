# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *
from future import standard_library
standard_library.install_aliases()

from io import StringIO
from traceback import format_tb
import datetime
import hashlib
import json
from json.decoder import JSONDecodeError
import logging
import os
import sys

from rdflib import Literal
from rdflib.namespace import RDF

from ferenda import util
from ferenda.errors import DocumentRemovedError

class DocumentEntry(object):

    """This class has two primary uses -- it is used to represent and store
    aspects of the downloading of each document (when it was initially
    downloaded, optionally updated, and last checked, as well as the URL
    from which it was downloaded). It's also used by the news_* methods
    to encapsulate various aspects of a document entry in an atom
    feed. Some properties and methods are used by both of these use
    cases, but not all.

    :param path: If this file path is an existing JSON file, the object is
                 initialized from that file.
    :type  path: str
    """
    id = None
    """The canonical uri for the document."""

    basefile = None
    """The basefile for the document."""

    orig_created = None
    """The first time we fetched the document from it's original location."""

    orig_updated = None
    """The last time the content at the original location of the
    document was changed."""

    orig_checked = None
    """The last time we accessed the original location of this
    document, regardless of wheter this led to an update."""

    orig_url = None
    """The main url from where we fetched this document."""

    published = None
    """The date our parsed/processed version of the document was published."""

    updated = None
    """The last time our parsed/processed version changed in any way
       (due to the original content being updated, or due to changes
       in our parsing functionality."""

    indexed_ts = None
    """The last time the metadata was indexed in a triplestore"""

    indexed_dep = None
    """The last time the dependent files of the document was indexed"""

    indexed_ft = None
    """The last time the document was indexed in a fulltext index"""

    url = None
    """The URL to the browser-ready version of the page, equivalent to what
    :meth:`~ferenda.DocumentStore.generated_url` returns."""

    title = None
    """A title/label for the document, as used in an Atom feed."""

    summary = None
    """A summary of the document, as used in an Atom feed."""

    content = None
    """A dict that represents metadata about the document file."""

    link = None
    """A dict that represents metadata about the document RDF metadata
    (such as it's URI, length, MIME-type and MD5 hash)."""

    status = None
    """A nested dict containing various info about the latest attempt to
    download/parse/relate/generate the document.

    """

    # files = [{'path': 'data/sfs/downloaded/1999/175.html',
    #           'source': 'http://localhost/1234/567',
    #           'last-modified': '<isodatestring>',
    #           'etag': '234242323424'}]

    def __init__(self, path=None):
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path) as fp:
                hook = util.make_json_date_object_hook('orig_created',
                                                       'orig_updated',
                                                       'orig_checked',
                                                       'published',
                                                       'updated',
                                                       'indexed_ts',
                                                       'indexed_dep',
                                                       'indexed_ft',
                                                       'status.download.date',
                                                       'status.parse.date',
                                                       'status.relate.date',
                                                       'status.generate.date')
                try:
                    d = json.load(fp, object_hook=hook)
                except JSONDecodeError as e:
                    if e.msg == "Extra data":
                        logging.getLogger("documententry").warning("%s exists but has extra data from pos %s" % (path, e.pos))
                        fp.seek(0)
                        jsondata = fp.read(e.pos)
                        d = json.loads(jsondata, object_hook=hook)
                    else:
                        raise e
            if 'summary_type' in d and d['summary_type'] == "html":
                d['summary'] = Literal(d['summary'], datatype=RDF.XMLLiteral)
                del d['summary_type']
            self.__dict__.update(d)
            self._path = path
        else:
            if path and os.path.exists(path):
                logging.getLogger("documententry").warning("%s exists but is empty" % path)
            self.id = None
            self.basefile = None
            self.orig_updated = None
            self.orig_checked = None
            self.orig_url = None
            self.indexed_ts = None
            self.indexed_dep = None
            self.indexed_ft = None
            self.published = None
            self.updated = None
            self.title = None
            self.summary = None
            self.url = None
            if path:
                self._path = path
            # Content src="...": A link to the actual document, or the
            # content inline (Source or refined version?)
            self.content = {}
            # Link rel="alternate": The metadata for this document (and
            # included resources)
            self.link = {}

        # silently upgrade old entry JSON files with a root level
        # parse dict and/or lacking the status dict
        if self.status is None:
            self.status = {}
        if hasattr(self, 'parse'):
            self.status['parse'] = self.parse
            delattr(self, 'parse')

    def __repr__(self):
        return '<%s id=%s>' % (self.__class__.__name__, self.id)

    def save(self, path=None):
        """Saves the state of the documententry to a JSON file at *path*. If
        *path* is not provided, uses the path that the object was initialized
        with.

        """
        if not path:
            path = self._path  # better be there
            
        # The below concise way of creating a dict will yield a
        # future.types.newdict.newdict, whose .keys() method yields a
        # dictionary-keyiterator object, not a standard sortable
        # list. This fails with json.dump(sort_keys=True).
        #
        #  d = dict((k, v) for (k, v) in self.__dict__.items() if k[0] != "_")
        #
        # So we create a standard py2 dict by using literals:
        d = {}
        for (k, v) in self.__dict__.items():
            if k[0] != "_":
                d[k] = v
        if isinstance(self.summary, Literal) and self.summary.datatype == RDF.XMLLiteral:
            d["summary_type"] = "html"

        util.ensure_dir(path)
        with open(path, "w") as fp:
            s = json.dumps(d, default=util.json_default_date, indent=2,
                           separators=(', ', ': '), sort_keys=True)
            fp.write(s)

    # If inline=True, the contents of filename is included in the Atom
    # entry. Otherwise, it just references it.
    #
    # Note that you can only have one content element.
    def set_content(self, filename, url, mimetype=None, inline=False):
        """Sets the ``content`` property and calculates md5 hash for the file

        :param filename: The full path to the document file
        :param url: The full external URL that will be used to get the same
                    document file
        :param mimetype: The MIME-type used in the atom feed. If not provided,
                         guess from file extension.
        :param inline: whether to inline the document content in the file or
                       refer to *url*
        """
        if not mimetype:
            mimetype = self.guess_type(filename)
        self.content['type'] = mimetype
        if inline:
            # there's a difference between actual mimetype and
            # mimetype-as-type-in-atom.
            if mimetype == "application/html+xml":
                mimetype = "xhtml"
            assert mimetype == 'xhtml', "Can't inline non-xhtml content"
            with open(filename) as fp:
                self.content['markup'] = fp.read()
            self.content['src'] = None
            self.content['hash'] = None
        else:
            self.content['markup'] = None
            self.content['src'] = url
            self.content['hash'] = "md5:%s" % self.calculate_md5(filename)

    def set_link(self, filename, url, mimetype=None):
        """Sets the ``link`` property and calculate md5 hash for the RDF metadata.

        :param filename: The full path to the RDF file for a document
        :param url: The full external URL that will be used to get the same
                    RDF file
        :param mimetype: The MIME-type used in the atom feed. If not provided,
                         guess from file extension.
        """
        if not mimetype:
            mimetype = self.guess_type(filename)
        self.link['href'] = url
        self.link['type'] = mimetype
        self.link['length'] = os.path.getsize(filename)
        self.link['hash'] = "md5:%s" % self.calculate_md5(filename)

    def calculate_md5(self, filename):
        """Given a filename, return the md5 value for the file's content."""
        c = hashlib.md5()
        with open(filename, 'rb') as fp:
            c.update(fp.read())
        return c.hexdigest()

    def guess_type(self, filename):
        """Given a filename, return a MIME-type based on the file extension."""
        exts = {'.pdf': 'application/pdf',
                '.rdf': 'application/rdf+xml',
                '.html': 'text/html',
                '.xhtml': 'application/html+xml'}
        for ext, mimetype in list(exts.items()):
            if filename.endswith(ext):
                return mimetype
        return "application/octet-stream"

    @staticmethod
    def updateentry(f, section, entrypath, *args, **kwargs):
        """runs the provided function with the provided arguments, captures
        any logged events emitted, catches any errors, and records the
        result in the entry file under the provided section. The basefile
        is assumed to be the first element in args.

        """

        def clear(key, d):
            if key in d:
                del d[key]
        logstream = StringIO()
        handler = logging.StreamHandler(logstream)
        # FIXME: Think about which format is optimal for storing in
        # docentry. Do we need eg name and levelname? Should we log
        # date as well as time?
        fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
        formatter = logging.Formatter(fmt, datefmt="%H:%M:%S")
        handler.setFormatter(formatter)
        handler.setLevel(logging.WARNING)
        rootlog = logging.getLogger()
        rootlog.addHandler(handler)
        start = datetime.datetime.now()
        try:
            ret = f(*args, **kwargs)
            success = True
        except DocumentRemovedError as e:
            success = "removed"
            raise
        except Exception as e:
            success = False
            errortype, errorval, errortb = sys.exc_info()
            raise
        except KeyboardInterrupt as e:
            success = None
            raise
        else:
            return ret
        finally:
            rootlog.removeHandler(handler)
            if success is not None:
                warnings = logstream.getvalue()
                entry = DocumentEntry(entrypath)
                if section not in entry.status:
                    entry.status[section] = {}
                entry.status[section]['success'] = success
                entry.status[section]['date'] = start
                delta = datetime.datetime.now()-start
                try:
                    duration = delta.total_seconds()
                except AttributeError:
                    # probably on py26, wich lack total_seconds()
                    duration = delta.seconds + (delta.microseconds / 1000000.0)
                entry.status[section]['duration'] = duration
                if warnings:
                    entry.status[section]['warnings'] = warnings
                else:
                    clear('warnings', entry.status[section])
                if not success:
                    entry.status[section]['traceback'] = "".join(format_tb(errortb))
                    entry.status[section]['error'] = "%s: %s (%s)" % (errorval.__class__.__name__,
                                                            errorval, util.location_exception(errorval))
                else:
                    clear('traceback', entry.status[section])
                    clear('error', entry.status[section])
                entry.save()
    
    
