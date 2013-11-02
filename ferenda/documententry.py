# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import hashlib
import json
from datetime import datetime


from ferenda import util


class DocumentEntry(object):

    """This class has two primary uses -- it is used to represent and store
    aspects of the downloading of each document (when it was initially
    downloaded, optionally updated, and last checked, as well as the URL
    from which it was downloaded). It's also used by the news_* methods
    to encapsulate various aspects of a document entry in an atom
    feed. Some properties and methods are used by both of these use
    cases, but not all.

    :param path: If this file path is an existing JSON file, the object is initialized from that file.
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

    url = None
    """The URL to the browser-ready version of the page, equivalent to what
    :meth:`~ferenda.DocumentRepository.generated_url` returns."""

    title = None
    """A title/label for the document, as used in an Atom feed."""

    summary = None
    """A summary of the document, as used in an Atom feed."""

    content = None
    """A dict that represents metadata about the document file."""

    link = None
    """A dict that represents metadata about the document RDF metadata
    (such as it's URI, length, MIME-type and MD5 hash)."""

    def __init__(self, path=None):
        # for json serialization
        def myhook(d):
            for key in ('orig_created', 'orig_updated', 'orig_checked', 'published', 'updated'):
                if key in d and d[key]:
                    try:
                        dt = datetime.strptime(d[key], '%Y-%m-%dT%H:%M:%S.%f')
                    except ValueError:
                        # no fractional part
                        dt = datetime.strptime(d[key], '%Y-%m-%dT%H:%M:%S')
                    d[key] = dt
            return d

        if path and os.path.exists(path):
            with open(path) as fp:
                d = json.load(fp, object_hook=myhook)
            self.__dict__.update(d)
            self._path = path
        else:
            self.id = None
            self.basefile = None
            self.orig_updated = None
            self.orig_checked = None
            self.orig_url = None
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


    def __repr__(self):
        return '<%s id=%s>' % (self.__class__.__name__, self.id)

    def save(self, path=None):
        """Saves the state of the documententry to a JSON file at *path*. If
*path* is not provided, uses the path that the object was initialized
with.

        """

        def mydefault(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError("%r is not JSON serializable" % obj)

        if not path:
            path = self._path  # better be there
        d = dict((k, v) for (k, v) in self.__dict__.items() if k[0] != "_")
        util.ensure_dir(path)
        with open(path, "w") as fp:
            json.dump(d, fp, default=mydefault, indent=2, sort_keys=True)
    # If inline=True, the contents of filename is included in the Atom
    # entry. Otherwise, it just references it.
    #
    # Note that you can only have one content element.

    def set_content(self, filename, url, mimetype=None, inline=False):
        """Sets the ``content`` property and calculates md5 hash for the file

        :param filename: The full path to the document file
        :param url: The full external URL that will be used to get the same document file
        :param mimetype: The MIME-type used in the atom feed. If not provided, guess from file extension.
        :param inline: whether to inline the document content in the file or refer to *url*
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
        :param url: The full external URL that will be used to get the same RDF file
        :param mimetype: The MIME-type used in the atom feed. If not provided, guess from file extension.
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
